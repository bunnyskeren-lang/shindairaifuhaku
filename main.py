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
    URIAction,
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


EASE_TO_STARS = {
    "SS": "★★★★★",
    "S": "★★★★☆",
    "A": "★★★☆☆",
    "B": "★★☆☆☆",
    "C": "★☆☆☆☆",
}


def build_course_card(name: str, course: dict) -> FlexMessage:
    rakutan_stars = "★" * course["rating"] + "☆" * (5 - course["rating"])
    manabi_stars = EASE_TO_STARS.get(course["ease_rating"], "─────")

    footer_contents = []
    if course.get("syllabus_url"):
        footer_contents.append(
            FlexButton(
                action=URIAction(label="📖 シラバスを見る", uri=course["syllabus_url"]),
                style="primary",
                color="#5C6BC0",
                height="sm",
            )
        )

    return FlexMessage(
        alt_text=name,
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                background_color="#5C6BC0",
                padding_all="lg",
                contents=[
                    FlexText(
                        text=course["classification"],
                        size="xs",
                        color="#C5CAE9",
                    ),
                    FlexText(
                        text=name,
                        size="xl",
                        weight="bold",
                        color="#FFFFFF",
                        wrap=True,
                        margin="sm",
                    ),
                    FlexText(
                        text=f"担当: {course['instructor']}　{course['format']}",
                        size="xs",
                        color="#C5CAE9",
                        margin="xs",
                    ),
                ],
            ),
            body=FlexBox(
                layout="vertical",
                spacing="sm",
                contents=[
                    FlexBox(
                        layout="horizontal",
                        margin="sm",
                        contents=[
                            FlexBox(
                                layout="vertical",
                                flex=1,
                                contents=[
                                    FlexText(text="楽単度", size="xs", color="#888888", align="center"),
                                    FlexText(text=rakutan_stars, size="sm", color="#FFB300", align="center", margin="xs"),
                                ],
                            ),
                            FlexBox(
                                layout="vertical",
                                flex=1,
                                contents=[
                                    FlexText(text="学びになる度", size="xs", color="#888888", align="center"),
                                    FlexText(text=manabi_stars, size="sm", color="#26C6DA", align="center", margin="xs"),
                                ],
                            ),
                        ],
                    ),
                    FlexSeparator(margin="md"),
                    FlexText(text="📋 授業内容", size="sm", weight="bold", color="#5C6BC0", margin="md"),
                    FlexText(text=course["content"], size="sm", wrap=True, color="#333333"),
                    FlexSeparator(margin="md"),
                    FlexText(text="📝 評価方法", size="sm", weight="bold", color="#5C6BC0", margin="md"),
                    FlexText(text=course["evaluation"], size="sm", wrap=True, color="#333333"),
                    FlexSeparator(margin="md"),
                    FlexText(text="💬 先輩コメント", size="sm", weight="bold", color="#5C6BC0", margin="md"),
                    FlexText(text=course["comment"], size="sm", wrap=True, color="#333333"),
                ],
            ),
            footer=FlexBox(
                layout="vertical",
                contents=footer_contents if footer_contents else [
                    FlexText(text="シラバスURL未設定", size="xs", color="#AAAAAA", align="center"),
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
            style="secondary",
        )
        for name in COURSES
    ]

    return FlexMessage(
        alt_text="科目一覧",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                background_color="#5C6BC0",
                padding_all="lg",
                contents=[
                    FlexText(text="📚 科目一覧", weight="bold", color="#FFFFFF", size="lg"),
                    FlexText(text=f"全{len(COURSES)}科目", size="xs", color="#C5CAE9", margin="xs"),
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
