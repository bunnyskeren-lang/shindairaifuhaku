import asyncio

from linebot.v3 import WebhookParser
from linebot.v3.messaging import AsyncApiClient, AsyncMessagingApi, Configuration, ReplyMessageRequest

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
    global _client
    if _client:
        await _client.close()


async def reply(reply_token: str, messages: list) -> None:
    await _api.reply_message(ReplyMessageRequest(reply_token=reply_token, messages=messages))


async def self_ping() -> None:
    if not SELF_URL:
        return
    import httpx
    await asyncio.sleep(30)
    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.get(f"{SELF_URL}/health")
        except Exception:
            pass
        await asyncio.sleep(60)
