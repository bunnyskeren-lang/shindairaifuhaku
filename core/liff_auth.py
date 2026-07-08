import httpx

from core.config import LIFF_ID, REGISTER_LIFF_ID, REVIEW_LIFF_ID, TIMETABLE_LIFF_ID

LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"


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
    async with httpx.AsyncClient(timeout=6.0) as client:
        for client_id in LIFF_CHANNEL_IDS:
            try:
                resp = await client.post(
                    LINE_VERIFY_URL,
                    data={"id_token": id_token, "client_id": client_id},
                )
            except httpx.HTTPError:
                continue
            if resp.status_code == 200:
                sub = resp.json().get("sub")
                if sub:
                    return sub
    return None
