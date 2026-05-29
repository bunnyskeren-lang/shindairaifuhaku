import os
import hashlib
import hmac
import base64
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from linebot.v3 import WebhookParser
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    ReplyMessageRequest,
    TextMessage,
    ImageMessage,
    FlexMessage,
    FlexBubble,
    FlexBox,
    FlexButton,
    FlexText,
    MessageAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, AsyncSessionLocal
from models import MessageLog
from keywords import get_rule, DEFAULT_REPLY

from dotenv import load_dotenv
load_dotenv()

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(lifespan=lifespan)


def verify_signature(body: bytes, signature: str) -> bool:
    hash_value = hmac.new(
        CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256
    ).digest()
    expected = base64.b64encode(hash_value).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def build_messages(rule: dict | None) -> list:
    if rule is None:
        return [TextMessage(text=DEFAULT_REPLY)]

    messages = []

    if rule.get("buttons"):
        body_contents = []
        if rule.get("reply"):
            body_contents.append(
                FlexText(text=rule["reply"], weight="bold", wrap=True)
            )
        body_contents.extend([
            FlexButton(
                action=MessageAction(label=btn["label"], text=btn["text"]),
                height="sm",
                margin="sm",
            )
            for btn in rule["buttons"]
        ])
        messages.append(
            FlexMessage(
                alt_text=rule.get("reply", "メニュー"),
                contents=FlexBubble(
                    body=FlexBox(
                        layout="vertical",
                        contents=body_contents,
                        spacing="sm",
                    )
                ),
            )
        )
    elif rule.get("reply"):
        messages.append(TextMessage(text=rule["reply"]))

    if rule.get("image_url"):
        messages.append(
            ImageMessage(
                original_content_url=rule["image_url"],
                preview_image_url=rule["image_url"],
            )
        )

    return messages if messages else [TextMessage(text=DEFAULT_REPLY)]


async def save_log(session: AsyncSession, user_id: str, direction: str, message: str):
    log = MessageLog(user_id=user_id, direction=direction, message=message)
    session.add(log)
    await session.commit()


@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()

    if not verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        events = parser.parse(body.decode("utf-8"), signature)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    async with AsyncSessionLocal() as session:
        async with AsyncApiClient(configuration) as api_client:
            line_api = AsyncMessagingApi(api_client)

            for event in events:
                if not isinstance(event, MessageEvent):
                    continue
                if not isinstance(event.message, TextMessageContent):
                    continue

                user_id = event.source.user_id
                user_text = event.message.text
                rule = get_rule(user_text)
                messages = build_messages(rule)
                reply_text = rule["reply"] if rule and rule.get("reply") else DEFAULT_REPLY

                await save_log(session, user_id, "in", user_text)
                await save_log(session, user_id, "out", reply_text)

                await line_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=messages,
                    )
                )

    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "healthy"}
