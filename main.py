import os
import hashlib
import hmac
import base64
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
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
    FlexCarousel,
    FlexBox,
    FlexButton,
    FlexText,
    FlexSeparator,
    MessageAction,
    URIAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, AsyncSessionLocal
from models import MessageLog, Course
from keywords import get_rule, DEFAULT_REPLY

from dotenv import load_dotenv
load_dotenv()

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)
security = HTTPBasic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(lifespan=lifespan)


# ── 管理者認証 ──────────────────────────────────────────────
def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ok = (
        secrets.compare_digest(credentials.username, "admin")
        and secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    )
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="認証失敗",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ── 管理ページ HTML ─────────────────────────────────────────
STYLE = """
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:sans-serif;padding:20px;max-width:1100px;margin:0 auto}
h1{color:#5C6BC0}
table{width:100%;border-collapse:collapse;margin-top:16px}
th,td{padding:8px 12px;border:1px solid #ddd;text-align:left;font-size:14px}
th{background:#5C6BC0;color:#fff}
tr:hover{background:#f0f4ff}
.btn{display:inline-block;padding:5px 12px;border:none;border-radius:4px;
     cursor:pointer;font-size:13px;text-decoration:none;margin:2px}
.add{background:#43A047;color:#fff;padding:10px 20px;font-size:15px}
.edit{background:#5C6BC0;color:#fff}
.del{background:#e53935;color:#fff}
.save{background:#43A047;color:#fff;padding:10px 24px;border:none;
      border-radius:4px;cursor:pointer;font-size:15px}
.back{color:#5C6BC0;text-decoration:none;font-size:14px}
input,select,textarea{width:100%;padding:8px;margin:4px 0 12px;
  border:1px solid #ccc;border-radius:4px;box-sizing:border-box}
textarea{height:80px}
label{font-weight:bold;font-size:14px}
</style>
"""


def course_form(course=None, action="", title=""):
    v = lambda f, d="": getattr(course, f, d) if course else d
    r, er, fmt = v("rating", 3), v("ease_rating", "B"), v("format", "対面")
    formats = ["対面", "リアルタイム配信", "オンデマンド"]
    ratings = list(range(1, 6))
    ease_ratings = ["SS", "S", "A", "B", "C"]
    return f"""<html><head>{STYLE}<title>{title}</title></head><body>
    <h1>{title}</h1>
    <a href="/admin" class="back">← 一覧に戻る</a>
    <form method="post" action="{action}" style="margin-top:16px">
      <label>科目名</label>
      <input name="name" value="{v('name')}" required {"readonly" if course else ""}>
      <label>担当教員</label>
      <input name="instructor" value="{v('instructor')}" required>
      <label>授業形態</label>
      <select name="format">
        {"".join(f'<option {"selected" if fmt==o else ""}>{o}</option>' for o in formats)}
      </select>
      <label>分類</label>
      <input name="classification" value="{v('classification')}" required>
      <label>授業内容</label>
      <textarea name="content">{v('content')}</textarea>
      <label>評価方法</label>
      <textarea name="evaluation">{v('evaluation')}</textarea>
      <label>楽単度（1〜5）</label>
      <select name="rating">
        {"".join(f'<option {"selected" if r==i else ""}>{i}</option>' for i in ratings)}
      </select>
      <label>学びになる度（SS/S/A/B/C）</label>
      <select name="ease_rating">
        {"".join(f'<option {"selected" if er==o else ""}>{o}</option>' for o in ease_ratings)}
      </select>
      <label>先輩コメント</label>
      <textarea name="comment">{v('comment')}</textarea>
      <label>シラバスURL（任意）</label>
      <input name="syllabus_url" value="{v('syllabus_url')}">
      <br>
      <button type="submit" class="save">保存する</button>
    </form></body></html>"""


# ── 管理ページ ルート ────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_list(username: str = Depends(verify_admin)):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Course).order_by(Course.classification, Course.name)
        )
        courses = result.scalars().all()

    rows = "".join(
        f"""<tr>
          <td>{c.name}</td><td>{c.instructor}</td><td>{c.format}</td>
          <td>{c.classification}</td>
          <td>{"★"*c.rating}{"☆"*(5-c.rating)}</td>
          <td>{c.ease_rating}</td>
          <td>
            <a href="/admin/edit/{c.id}" class="btn edit">編集</a>
            <form method="post" action="/admin/delete/{c.id}" style="display:inline"
              onsubmit="return confirm('削除しますか？')">
              <button type="submit" class="btn del">削除</button>
            </form>
          </td>
        </tr>"""
        for c in courses
    )

    return HTMLResponse(f"""<html><head>{STYLE}<title>科目管理</title></head><body>
    <h1>📚 科目管理</h1>
    <a href="/admin/add" class="btn add">+ 科目を追加</a>
    <table>
      <tr><th>科目名</th><th>担当</th><th>形態</th><th>分類</th>
          <th>楽単度</th><th>学びになる度</th><th>操作</th></tr>
      {rows}
    </table></body></html>""")


@app.get("/admin/add", response_class=HTMLResponse)
async def admin_add_form(username: str = Depends(verify_admin)):
    return HTMLResponse(course_form(action="/admin/add", title="科目を追加"))


@app.post("/admin/add")
async def admin_add(
    username: str = Depends(verify_admin),
    name: str = Form(...), instructor: str = Form(...),
    format: str = Form(...), classification: str = Form(...),
    content: str = Form(...), evaluation: str = Form(...),
    rating: int = Form(...), ease_rating: str = Form(...),
    comment: str = Form(...), syllabus_url: str = Form(""),
):
    async with AsyncSessionLocal() as session:
        session.add(Course(
            name=name, instructor=instructor, format=format,
            classification=classification, content=content,
            evaluation=evaluation, rating=rating, ease_rating=ease_rating,
            comment=comment, syllabus_url=syllabus_url,
        ))
        await session.commit()
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/edit/{course_id}", response_class=HTMLResponse)
async def admin_edit_form(course_id: int, username: str = Depends(verify_admin)):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Course).where(Course.id == course_id))
        course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404)
    return HTMLResponse(course_form(
        course=course, action=f"/admin/edit/{course_id}", title="科目を編集"
    ))


@app.post("/admin/edit/{course_id}")
async def admin_edit(
    course_id: int, username: str = Depends(verify_admin),
    name: str = Form(...), instructor: str = Form(...),
    format: str = Form(...), classification: str = Form(...),
    content: str = Form(...), evaluation: str = Form(...),
    rating: int = Form(...), ease_rating: str = Form(...),
    comment: str = Form(...), syllabus_url: str = Form(""),
):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Course).where(Course.id == course_id))
        course = result.scalar_one_or_none()
        if not course:
            raise HTTPException(status_code=404)
        course.name = name
        course.instructor = instructor
        course.format = format
        course.classification = classification
        course.content = content
        course.evaluation = evaluation
        course.rating = rating
        course.ease_rating = ease_rating
        course.comment = comment
        course.syllabus_url = syllabus_url
        await session.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/delete/{course_id}")
async def admin_delete(course_id: int, username: str = Depends(verify_admin)):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Course).where(Course.id == course_id))
        course = result.scalar_one_or_none()
        if course:
            await session.delete(course)
            await session.commit()
    return RedirectResponse("/admin", status_code=303)


# ── LINE Bot ────────────────────────────────────────────────
def verify_signature(body: bytes, signature: str) -> bool:
    hash_value = hmac.new(
        CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256
    ).digest()
    expected = base64.b64encode(hash_value).decode("utf-8")
    return hmac.compare_digest(expected, signature)


EASE_TO_STARS = {
    "SS": "★★★★★", "S": "★★★★☆",
    "A": "★★★☆☆", "B": "★★☆☆☆", "C": "★☆☆☆☆",
}

CATEGORY_GROUPS = {
    "📚 教養科目": ["教養", "教養言語", "外国語"],
    "🔬 共通専門科目": ["共通専門科目"],
    "⚙️ 学科専門科目": ["専門必修", "専門選択"],
}


def build_course_card(course: Course) -> FlexMessage:
    rakutan_stars = "★" * course.rating + "☆" * (5 - course.rating)
    manabi_stars = EASE_TO_STARS.get(course.ease_rating, "─────")
    footer_contents = (
        [FlexButton(
            action=URIAction(label="シラバスはこちら", uri=course.syllabus_url),
            style="primary", color="#5C6BC0", height="sm",
        )]
        if course.syllabus_url
        else [FlexText(text="シラバスURL未設定", size="xs", color="#AAAAAA", align="center")]
    )
    return FlexMessage(
        alt_text=course.name,
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical", background_color="#5C6BC0", padding_all="lg",
                contents=[
                    FlexText(text=course.classification, size="xs", color="#C5CAE9"),
                    FlexText(text=course.name, size="xl", weight="bold",
                             color="#FFFFFF", wrap=True, margin="sm"),
                    FlexText(text=f"担当: {course.instructor}　{course.format}",
                             size="xs", color="#C5CAE9", margin="xs"),
                ],
            ),
            body=FlexBox(
                layout="vertical", spacing="sm",
                contents=[
                    FlexBox(layout="horizontal", margin="sm", contents=[
                        FlexBox(layout="vertical", flex=1, contents=[
                            FlexText(text="楽単度", size="xs", color="#888888", align="center"),
                            FlexText(text=rakutan_stars, size="sm", color="#FFB300",
                                     align="center", margin="xs"),
                        ]),
                        FlexBox(layout="vertical", flex=1, contents=[
                            FlexText(text="学びになる度", size="xs", color="#888888", align="center"),
                            FlexText(text=manabi_stars, size="sm", color="#26C6DA",
                                     align="center", margin="xs"),
                        ]),
                    ]),
                    FlexSeparator(margin="md"),
                    FlexText(text="📋 授業内容", size="sm", weight="bold",
                             color="#5C6BC0", margin="md"),
                    FlexText(text=course.content, size="sm", wrap=True, color="#333333"),
                    FlexSeparator(margin="md"),
                    FlexText(text="📝 評価方法", size="sm", weight="bold",
                             color="#5C6BC0", margin="md"),
                    FlexText(text=course.evaluation, size="sm", wrap=True, color="#333333"),
                    FlexSeparator(margin="md"),
                    FlexText(text="💬 先輩コメント", size="sm", weight="bold",
                             color="#5C6BC0", margin="md"),
                    FlexText(text=course.comment, size="sm", wrap=True, color="#333333"),
                ],
            ),
            footer=FlexBox(layout="vertical", contents=footer_contents),
        ),
    )


async def build_course_list(session: AsyncSession) -> FlexMessage:
    result = await session.execute(select(Course).order_by(Course.name))
    all_courses = result.scalars().all()
    bubbles = []
    for category, classifications in CATEGORY_GROUPS.items():
        courses = [c for c in all_courses if c.classification in classifications]
        if not courses:
            continue
        bubbles.append(FlexBubble(
            header=FlexBox(
                layout="vertical", background_color="#5C6BC0", padding_all="lg",
                contents=[
                    FlexText(text=category, weight="bold", color="#FFFFFF", size="md"),
                    FlexText(text=f"{len(courses)}科目", size="xs",
                             color="#C5CAE9", margin="xs"),
                ],
            ),
            body=FlexBox(
                layout="vertical", spacing="sm",
                contents=[
                    FlexButton(
                        action=MessageAction(label=c.name[:20], text=c.name),
                        height="sm", margin="sm", style="secondary",
                    )
                    for c in courses
                ],
            ),
        ))
    return FlexMessage(alt_text="科目一覧", contents=FlexCarousel(contents=bubbles))


def build_messages_from_rule(rule: dict | None) -> list:
    if rule is None:
        return [TextMessage(text=DEFAULT_REPLY)]
    messages = []
    if rule.get("buttons"):
        body_contents = []
        if rule.get("reply"):
            body_contents.append(FlexText(text=rule["reply"], weight="bold", wrap=True))
        body_contents.extend([
            FlexButton(action=MessageAction(label=b["label"], text=b["text"]),
                       height="sm", margin="sm")
            for b in rule["buttons"]
        ])
        messages.append(FlexMessage(
            alt_text=rule.get("reply", "メニュー"),
            contents=FlexBubble(body=FlexBox(
                layout="vertical", contents=body_contents, spacing="sm"
            )),
        ))
    elif rule.get("reply"):
        messages.append(TextMessage(text=rule["reply"]))
    if rule.get("image_url"):
        messages.append(ImageMessage(
            original_content_url=rule["image_url"],
            preview_image_url=rule["image_url"],
        ))
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

                course_result = await session.execute(
                    select(Course).where(Course.name == user_text)
                )
                course = course_result.scalar_one_or_none()

                if course:
                    messages = [build_course_card(course)]
                    log_text = f"course:{user_text}"
                else:
                    rule = get_rule(user_text)
                    if rule and rule.get("action") == "course_list":
                        messages = [await build_course_list(session)]
                        log_text = "course_list"
                    else:
                        messages = build_messages_from_rule(rule)
                        log_text = rule.get("reply", DEFAULT_REPLY) if rule else DEFAULT_REPLY

                await save_log(session, user_id, "in", user_text)
                await save_log(session, user_id, "out", log_text)
                await line_api.reply_message(ReplyMessageRequest(
                    reply_token=event.reply_token, messages=messages,
                ))

    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "healthy"}
