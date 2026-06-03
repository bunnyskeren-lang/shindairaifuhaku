import os
import hashlib
import hmac
import base64
import secrets as py_secrets
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from linebot.v3 import WebhookParser
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    ReplyMessageRequest,
    TextMessage,
    FlexMessage,
    FlexBubble,
    FlexBox,
    FlexText,
    FlexButton,
    FlexSeparator,
    URIAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, AsyncSessionLocal
from models import MessageLog, Course, PendingReview

from dotenv import load_dotenv
load_dotenv()

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
REVIEW_FORM_URL = os.environ.get("REVIEW_FORM_URL", "https://shindairaifuhaku-1.onrender.com")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)
security = HTTPBasic()

EASE_ORDER = {"SS": 0, "S": 1, "A": 2, "B": 3, "C": 4}
EASE_LABEL = {"SS": "超楽 😴😴", "S": "楽 😴", "A": "普通 😊", "B": "きつめ 😤", "C": "激ムズ 😰"}


def stars(n: int) -> str:
    n = max(1, min(5, n))
    return "★" * n + "☆" * (5 - n)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
        print("DB OK", flush=True)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"DB ERROR: {e}", flush=True)
    yield


app = FastAPI(lifespan=lifespan)


def verify_signature(body: bytes, signature: str) -> bool:
    digest = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def check_admin(creds: HTTPBasicCredentials = Depends(security)):
    if not py_secrets.compare_digest(creds.password, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})
    return creds.username


async def save_log(session: AsyncSession, user_id: str, direction: str, message: str):
    session.add(MessageLog(user_id=user_id, direction=direction, message=message))
    await session.commit()


# ── FlexMessage builder ─────────────────────────────────────────

def make_course_bubble(
    name: str,
    instructor: str,
    classification: str,
    avg_rating: Optional[float],
    top_ease: Optional[str],
    latest_comment: Optional[str],
) -> FlexBubble:
    body_contents = [
        FlexText(text=name, weight="bold", size="lg", wrap=True),
        FlexText(
            text=f"👨‍🏫 {instructor or '未設定'}  📂 {classification or '未分類'}",
            size="sm", color="#888888", wrap=True, margin="sm",
        ),
        FlexSeparator(margin="md"),
    ]

    if avg_rating is not None:
        body_contents.append(
            FlexText(
                text=f"⭐ {stars(round(avg_rating))}  {avg_rating:.1f}/5.0",
                size="sm", margin="md",
            )
        )
    else:
        body_contents.append(
            FlexText(text="⭐ まだレビューなし", size="sm", color="#aaaaaa", margin="md")
        )

    if top_ease:
        body_contents.append(
            FlexText(
                text=f"楽単度: {EASE_LABEL.get(top_ease, top_ease)}",
                size="sm", margin="xs",
            )
        )

    if latest_comment:
        preview = latest_comment[:80] + ("..." if len(latest_comment) > 80 else "")
        body_contents += [
            FlexSeparator(margin="md"),
            FlexText(text="💬 最新レビュー", size="xs", color="#888888", margin="md"),
            FlexText(text=preview, size="sm", wrap=True, color="#444444", margin="xs"),
        ]

    footer_contents = [
        FlexButton(
            action=URIAction(label="レビューを投稿", uri=REVIEW_FORM_URL),
            style="primary",
            color="#6366f1",
            height="sm",
        )
    ]

    return FlexBubble(
        body=FlexBox(layout="vertical", contents=body_contents, padding_all="lg"),
        footer=FlexBox(layout="vertical", contents=footer_contents, padding_all="md"),
    )


async def get_course_flex(session: AsyncSession, course: Course) -> FlexMessage:
    agg = (await session.execute(
        select(func.avg(PendingReview.rating), func.count(PendingReview.id))
        .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
    )).first()
    avg_rating = float(agg[0]) if agg and agg[0] else None

    ease_rows = (await session.execute(
        select(PendingReview.ease_rating, func.count(PendingReview.id))
        .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
        .group_by(PendingReview.ease_rating)
    )).all()
    top_ease = None
    if ease_rows:
        top_ease = sorted(ease_rows, key=lambda r: EASE_ORDER.get(r[0], 99))[0][0]

    latest = (await session.execute(
        select(PendingReview.comment)
        .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
        .order_by(PendingReview.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    bubble = make_course_bubble(
        course.name, course.instructor, course.classification,
        avg_rating, top_ease, latest,
    )
    return FlexMessage(alt_text=f"📖 {course.name}", contents=bubble)


# ── Static reply texts ──────────────────────────────────────────

HELP_TEXT = """📖 神大授業ナビ の使い方

▼ リッチメニューのボタン
・科目一覧 → 登録科目リスト
・レビュー投稿 → 投稿フォームへ
・人気の授業 → 学び度 TOP5
・楽単ランキング → 楽単 TOP5
・ヘルプ → この画面
・問い合わせ → 管理者への連絡先

▼ 科目名で検索
科目名をそのまま送ってください！
例：「英語」「情報処理」など

投稿したレビューは確認後に公開されます✨"""

CONTACT_TEXT = (
    "📬 お問い合わせ\n\n"
    "ご意見・ご要望は管理者までどうぞ。\n"
    "✉️ bunnyskeren@gmail.com"
)


# ── Message handler ─────────────────────────────────────────────

async def handle_message(session: AsyncSession, text: str) -> list:
    t = text.strip()

    if t in ["科目一覧", "科目", "授業一覧", "一覧"]:
        names = (await session.execute(
            select(Course.name).order_by(Course.name).limit(20)
        )).scalars().all()
        if not names:
            return [TextMessage(text="まだ科目が登録されていません。")]
        body = "\n".join(f"・{n}" for n in names)
        return [TextMessage(text=f"📚 登録されている科目\n\n{body}\n\n科目名を送ると詳細が見られます！")]

    if t in ["レビュー投稿", "レビュー", "投稿"]:
        return [TextMessage(text=f"📝 以下のフォームからレビューを投稿できます！\n\n{REVIEW_FORM_URL}")]

    if t in ["人気の授業", "人気授業", "人気", "おすすめ"]:
        rows = (await session.execute(
            select(PendingReview.course_name, func.avg(PendingReview.rating).label("avg"))
            .where(PendingReview.is_approved == True)
            .group_by(PendingReview.course_name)
            .order_by(func.avg(PendingReview.rating).desc())
            .limit(5)
        )).all()
        if not rows:
            return [TextMessage(text=f"まだ承認済みレビューがありません。\nレビューを投稿してください！\n\n{REVIEW_FORM_URL}")]
        body = "🏆 人気の授業（学び度 TOP5）\n\n"
        for i, (name, avg) in enumerate(rows, 1):
            body += f"{i}. {name}\n   {stars(round(float(avg)))} {float(avg):.1f}\n"
        body += "\n科目名を送ると詳細が見られます！"
        return [TextMessage(text=body)]

    if t in ["楽単ランキング", "楽単", "楽"]:
        rows = (await session.execute(
            select(PendingReview.course_name, PendingReview.ease_rating, func.count(PendingReview.id))
            .where(PendingReview.is_approved == True)
            .group_by(PendingReview.course_name, PendingReview.ease_rating)
        )).all()
        if not rows:
            return [TextMessage(text=f"まだ承認済みレビューがありません。\nレビューを投稿してください！\n\n{REVIEW_FORM_URL}")]
        course_ease: dict[str, str] = {}
        for name, ease, _ in rows:
            if name not in course_ease or EASE_ORDER.get(ease, 99) < EASE_ORDER.get(course_ease[name], 99):
                course_ease[name] = ease
        top5 = sorted(course_ease.items(), key=lambda x: EASE_ORDER.get(x[1], 99))[:5]
        body = "😴 楽単ランキング TOP5\n\n"
        for i, (name, ease) in enumerate(top5, 1):
            body += f"{i}. {name}\n   {EASE_LABEL.get(ease, ease)}\n"
        body += "\n科目名を送ると詳細が見られます！"
        return [TextMessage(text=body)]

    if t in ["ヘルプ", "help", "使い方", "？", "?"]:
        return [TextMessage(text=HELP_TEXT)]

    if t in ["問い合わせ", "連絡", "contact", "お問い合わせ"]:
        return [TextMessage(text=CONTACT_TEXT)]

    # Course keyword search
    courses = (await session.execute(
        select(Course).where(Course.name.ilike(f"%{t}%")).limit(3)
    )).scalars().all()
    if courses:
        return [await get_course_flex(session, c) for c in courses]

    return [TextMessage(
        text=f"「{t}」に一致する科目が見つかりませんでした。\n\n「科目一覧」で登録科目を確認するか、「ヘルプ」で使い方をご確認ください。"
    )]


# ── Routes ──────────────────────────────────────────────────────

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

                await save_log(session, user_id, "in", user_text)
                messages = await handle_message(session, user_text)
                await save_log(session, user_id, "out", f"[{len(messages)} msg(s)]")

                await line_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=messages[:5],
                    )
                )

    return {"status": "ok"}


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(_: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        logs = (await session.execute(
            select(MessageLog).order_by(MessageLog.created_at.desc()).limit(50)
        )).scalars().all()

    rows_html = "".join(
        f"<tr><td>{l.user_id[:10]}…</td>"
        f"<td>{'→' if l.direction == 'in' else '←'}</td>"
        f"<td>{l.message[:60]}</td>"
        f"<td>{l.created_at.strftime('%m/%d %H:%M')}</td></tr>"
        for l in logs
    )
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>管理画面 | 神大授業ナビ</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:sans-serif;padding:16px;background:#f3f4f6;max-width:700px;margin:auto}}
h1{{font-size:18px;font-weight:bold;margin-bottom:12px}}
table{{width:100%;border-collapse:collapse;background:white;border-radius:8px;overflow:hidden;font-size:13px}}
th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid #e5e7eb}}
th{{background:#6366f1;color:white}}
</style></head><body>
<h1>📊 メッセージログ（最新50件）</h1>
<table>
<tr><th>ユーザー</th><th>方向</th><th>メッセージ</th><th>日時</th></tr>
{rows_html or '<tr><td colspan="4" style="color:#999;text-align:center;padding:16px">ログなし</td></tr>'}
</table></body></html>"""


@app.get("/health")
async def health():
    return {"status": "healthy"}
