import asyncio

import httpx

from core.config import LIFF_ID, REGISTER_LIFF_ID, REVIEW_LIFF_ID, TIMETABLE_LIFF_ID

LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"

_http_client: httpx.AsyncClient | None = None


async def startup() -> None:
    global _http_client
    _http_client = httpx.AsyncClient(timeout=6.0)


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
        _channel_id(LIFF_ID), _channel_id(TIMETABLE_LIFF_ID),
        _channel_id(REGISTER_LIFF_ID), _channel_id(REVIEW_LIFF_ID),
    ) if cid
}


async def verify_liff_id_token(id_token: str) -> str | None:
    """LINEのID tokenをLINE側のverifyエンドポイントで検証し、
    真正なLINEユーザーID(sub)を返す。検証失敗時はNoneを返す。

    クライアントが送ってくる line_user_id / uid は偽装可能なため、
    書き込み系・個人情報を返すエンドポイントでは必ずこちらを使うこと。
    """
    if not id_token or not LIFF_CHANNEL_IDS:
        return None
    client = _http_client
    if client is None:
        # startup()未実行（テスト等）の場合のフォールバック
        client = httpx.AsyncClient(timeout=6.0)
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
                await asyncio.sleep(0.5)
        if resp is not None and resp.status_code == 200:
            sub = resp.json().get("sub")
            if sub:
                return sub
    return None
