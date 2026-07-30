import asyncio
import time

import httpx
from fastapi import Request

from core.activity_log import save_error_log
from core.config import LIFF_ID, REGISTER_LIFF_ID, REVIEW_LIFF_ID
from core.rate_limit import client_ip

LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"

_http_client: httpx.AsyncClient | None = None

# 同一ページロード内で同じid_tokenが何度も検証リクエストに使われるため、
# 短時間だけ検証結果をキャッシュしLINE側への往復を減らす。
_VERIFY_CACHE_TTL = 120
_verify_cache: dict[str, tuple[str, float]] = {}


async def startup() -> None:
    global _http_client
    # 修正理由: デフォルトのhttpx接続プール上限(max_connections=100)のままだと、
    # 履修登録開始時等の一斉アクセスでLINE検証APIへの同時リクエストが100を超えた分は
    # 空き接続待ちでキューイングされ、レスポンス全体の遅延に直結する。
    # 単一dyno・単一ワーカーでも数千同時接続を捌けるよう上限を引き上げる。
    _http_client = httpx.AsyncClient(
        timeout=3.0,
        limits=httpx.Limits(max_connections=500, max_keepalive_connections=100),
    )


async def shutdown() -> None:
    if _http_client:
        await _http_client.aclose()


def _channel_id(liff_id: str) -> str | None:
    if not liff_id or "-" not in liff_id:
        return None
    return liff_id.split("-", 1)[0]


# LIFF ID は "{channelId}-{appId}" の形式。同一Messaging APIチャンネル配下の
# 複数LIFFアプリは基本的に同じchannelIdを共有するため、重複は自然に1件へ集約される。
LIFF_CHANNEL_IDS = {
    cid for cid in (
        _channel_id(LIFF_ID), _channel_id(REGISTER_LIFF_ID), _channel_id(REVIEW_LIFF_ID),
    ) if cid
}


async def verify_liff_id_token(id_token: str, request: Request | None = None) -> str | None:
    """LINEのID tokenをLINE側のverifyエンドポイントで検証し、
    真正なLINEユーザーID(sub)を返す。検証失敗時はNoneを返す。

    クライアントが送ってくる line_user_id / uid は偽装可能なため、
    書き込み系・個人情報を返すエンドポイントでは必ずこちらを使うこと。

    request を渡すと、トークンが指定されているのに検証に失敗したケース（改ざん・
    期限切れ・なりすまし試行等）をIPアドレス付きでerror_logsに記録する。
    未ログイン状態でid_tokenが空のケースは正常系のため記録しない。
    """
    if not id_token or not LIFF_CHANNEL_IDS:
        return None

    cached = _verify_cache.get(id_token)
    if cached is not None:
        sub, cached_at = cached
        if time.monotonic() - cached_at < _VERIFY_CACHE_TTL:
            return sub
        del _verify_cache[id_token]

    client = _http_client
    if client is None:
        # startup()未実行（テスト等）の場合のフォールバック
        client = httpx.AsyncClient(timeout=3.0)
    for client_id in LIFF_CHANNEL_IDS:
        resp = None
        # タイムアウト・接続エラーなど一時的な障害のみ1回だけ再試行する。
        # 非200応答（トークン不正等）は正当な拒否のため再試行しない。
        for attempt in range(2):
            try:
                resp = await client.post(
                    LINE_VERIFY_URL,
                    data={"id_token": id_token, "client_id": client_id},
                )
                break
            except httpx.HTTPError:
                if attempt == 1:
                    resp = None
                    break
                await asyncio.sleep(0.3)
        if resp is not None and resp.status_code == 200:
            sub = resp.json().get("sub")
            if sub:
                if len(_verify_cache) > 1000:
                    now = time.monotonic()
                    for key, (_, at) in list(_verify_cache.items()):
                        if now - at >= _VERIFY_CACHE_TTL:
                            del _verify_cache[key]
                _verify_cache[id_token] = (sub, time.monotonic())
                return sub
    if request is not None:
        await save_error_log(
            RuntimeError("LIFF ID token verification failed"),
            action=f"liff_auth_failed:{client_ip(request)}:{request.url.path}",
        )
    return None
