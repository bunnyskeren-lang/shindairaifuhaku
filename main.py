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
    FlexSeparator,
    MessageAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, AsyncSessionLocal
from models import MessageLog
from keywords import get_rule, DEFAULT_REPLY
from courses import COURSES

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


EASE_COLORS = {
    "SS": "#C8A000", "S": "#C8A000",
    "A": "#2196F3", "B": "#4CAF50", "C": "#FF9800",
}


def build_course_card(name: str, course: dict) -> FlexMessage:
    pass_rate_rows = [
        FlexBox(
            layout="horizontal",
            contents=[
                FlexText(text=f"{year}年度", size="sm", color="#555555", flex=3),
                FlexText(
                    text=f"{rate} ({fraction})",
                    size="sm",
                    color="#111111",
                    flex=4,
                    align="end",
                ),
            ],
        )
        for year, rate, fraction in course["pass_rates"]
    ]

    ease_color = EASE_COLORS.get(course["ease_rating"], "#555555")

    return FlexMessage(
        alt_text=name,
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                background_color="#2B2B2B",
                padding_all="lg",
                contents=[
                    FlexText(
                        text=f"Search ID:{course['search_id']}",
                        size="xs",
                        color="#4CAF50",
                    ),
                    FlexText(
                        text=name,
                        size="xl",
                        weight="bold",
                        color="#FFFFFF",
                        wrap=True,
                        margin="sm",
                    ),
                    FlexSeparator(margin="md", color="#555555"),
                    FlexBox(
                        layout="horizontal",
                        margin="md",
                        contents=[
                            FlexText(text="開講部局", size="xs", color="#AAAAAA", flex=2),
                            FlexText(text=course["department"], size="xs", color="#FFFFFF", flex=3),
                        ],
                    ),
                    FlexBox(
                        layout="horizontal",
                        margin="xs",
                        contents=[
                            FlexText(text="群", size="xs", color="#AAAAAA", flex=1),
                            FlexText(text=course["group"], size="xs", color="#FFFFFF", flex=2),
                            FlexText(text="単位数", size="xs", color="#AAAAAA", flex=1),
                            FlexText(text=course["credits"], size="xs", color="#FFFFFF", flex=2),
                        ],
                    ),
                ],
            ),
            body=FlexBox(
                layout="vertical",
                spacing="sm",
                contents=[
                    FlexText(text="単位取得率", size="xs", color="#888888"),
                    *pass_rate_rows,
                ],
            ),
            footer=FlexBox(
                layout="vertical",
                background_color="#F5F5F5",
                contents=[
                    FlexBox(
                        layout="horizontal",
                        contents=[
                            FlexText(text="らくたん判定", size="sm", flex=3),
                            FlexText(
                                text=course["ease_rating"],
                                size="sm",
                                weight="bold",
                                color=ease_color,
                                flex=1,
                                align="end",
                            ),
                        ],
                    ),
                ],
            ),
        ),
    )


def build_course_list() -> FlexMessage:
    buttons = [
        FlexButton(
            action=MessageAction(label=name[:20], text=name),
            height="sm",
            margin="sm",
        )
        for name in COURSES
    ]

    return FlexMessage(
        alt_text="科目一覧",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                background_color="#2B2B2B",
                padding_all="lg",
                contents=[
                    FlexText(text="📚 科目一覧", weight="bold", color="#FFFFFF", size="lg"),
                ],
            ),
            body=FlexBox(
                layout="vertical",
                contents=buttons,
                spacing="sm",
            ),
        ),
    )


def build_messages(rule: dict | None) -> list:
    if rule is None:
        return [TextMessage(text=DEFAULT_REPLY)]

    action = rule.get("action")

    if action == "course_list":
        return [build_course_list()]

    if action == "course_detail":
        name = rule["course_name"]
        return [build_course_card(name, COURSES[name])]

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
                log_text = rule.get("reply") or rule.get("action", "response") if rule else DEFAULT_REPLY

                await save_log(session, user_id, "in", user_text)
                await save_log(session, user_id, "out", log_text)

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
