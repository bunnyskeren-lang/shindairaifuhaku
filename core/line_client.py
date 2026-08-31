import asyncio

from linebot.v3 import WebhookParser
from linebot.v3.messaging import AsyncApiClient, AsyncMessagingApi, Configuration, ReplyMessageRequest
from linebot.v3.messaging.exceptions import ApiException

from core.activity_log import save_error_log
from core.config import CHANNEL_ACCESS_TOKEN, CHANNEL_SECRET, SELF_URL

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)

_client: AsyncApiClient | None = None
_api: AsyncMessagingApi | None = None


async def startup() -> None:
    global _client, _api
    _client = AsyncApiClient(configuration)
    _api = AsyncMessagingApi(_client)


async def shutdown() -> None:
    if _client:
        await _client.close()


async def _with_retry(coro_fn, *, retries: int = 2, base_delay: float = 0.5):
    """LINE API呼び出しを一時的な障害（タイムアウト・5xx）に限り指数バックオフで再試行する。

    4xx（トークン不正・権限不足等）は再試行しても成功しないため即座に上げる。
    """
    for attempt in range(retries + 1):
        try:
            return await coro_fn()
        except ApiException as e:
            if e.status is not None and e.status < 500:
                raise
            if attempt == retries:
                raise
        except (asyncio.TimeoutError, OSError):
            if attempt == retries:
                raise
        await asyncio.sleep(base_delay * (2 ** attempt))


async def reply(reply_token: str, messages: list) -> None:
    await _with_retry(lambda: _api.reply_message(ReplyMessageRequest(reply_token=reply_token, messages=messages)))


async def link_rich_menu(user_id: str, rich_menu_id: str) -> None:
    if not rich_menu_id:
        # RICHMENU_ID_MAIN/RICHMENU_ID_PREREGISTER未設定のまま呼ばれると、LINE側の
        # デフォルトリッチメニュー（登録前用）が変わらず残り続け、ユーザーが気づかない
        # まま詰む（2026-08-31、デフォルトを登録前メニューに倒す設計変更で発覚）。
        # 呼び出し元は例外を握りつぶさずログで検知できるようにする
        await save_error_log(
            RuntimeError("link_rich_menu called with empty rich_menu_id"),
            user_id=user_id, action="link_rich_menu_missing_id",
        )
        return
    await _with_retry(lambda: _api.link_rich_menu_id_to_user(user_id, rich_menu_id))


async def unlink_rich_menu(user_id: str) -> None:
    await _with_retry(lambda: _api.unlink_rich_menu_id_from_user(user_id))


async def self_ping() -> None:
    if not SELF_URL:
        return
    import httpx
    await asyncio.sleep(30)
    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.get(f"{SELF_URL}/health")
        except Exception as e:
            await save_error_log(e, action="self_ping")
        await asyncio.sleep(60)
