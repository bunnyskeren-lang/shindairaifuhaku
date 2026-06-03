import os
import html as _html
import json
import hashlib
import hmac
import base64
import secrets as py_secrets
from contextlib import asynccontextmanager
from typing import Optional

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
    FlexMessage,
    FlexBubble,
    FlexBox,
    FlexText,
    FlexButton,
    FlexSeparator,
    FlexCarousel,
    URIAction,
    MessageAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, AsyncSessionLocal
from models import MessageLog, Course, PendingReview, UserPreference

from dotenv import load_dotenv
load_dotenv()

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
REVIEW_FORM_URL = os.environ.get("REVIEW_FORM_URL", "https://shindairaifuhaku-1.onrender.com")
MAX_REVIEWS = int(os.environ.get("MAX_REVIEWS", "3"))

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
    comments: list[str],
    review_url: str = "",
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

    if comments:
        body_contents.append(FlexSeparator(margin="md"))
        body_contents.append(
            FlexText(text=f"💬 レビュー（{len(comments)}件）", size="xs", color="#888888", margin="md")
        )
        for comment in comments:
            preview = comment[:80] + ("..." if len(comment) > 80 else "")
            body_contents.append(
                FlexText(text=preview, size="sm", wrap=True, color="#444444", margin="sm")
            )

    footer_contents = [
        FlexButton(
            action=URIAction(label="レビューを投稿", uri=review_url or REVIEW_FORM_URL),
            style="primary",
            color="#6366f1",
            height="sm",
        )
    ]

    return FlexBubble(
        body=FlexBox(layout="vertical", contents=body_contents, padding_all="lg"),
        footer=FlexBox(layout="vertical", contents=footer_contents, padding_all="md"),
    )


async def get_user_max_reviews(session: AsyncSession, user_id: str) -> int:
    pref = (await session.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )).scalar_one_or_none()
    return pref.max_reviews if pref else MAX_REVIEWS


async def get_course_flex(session: AsyncSession, course: Course, user_id: str) -> FlexMessage:
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

    limit = await get_user_max_reviews(session, user_id)
    comments = (await session.execute(
        select(PendingReview.comment)
        .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
        .order_by(PendingReview.created_at.desc())
        .limit(limit)
    )).scalars().all()

    url = f"{REVIEW_FORM_URL}?uid={user_id}" if user_id else REVIEW_FORM_URL
    bubble = make_course_bubble(
        course.name, course.instructor, course.classification,
        avg_rating, top_ease, list(comments),
        review_url=url,
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


# ── Course list carousel ────────────────────────────────────────

async def handle_course_list(session: AsyncSession, category: str = "") -> list:
    from collections import defaultdict
    stmt = select(Course).order_by(Course.classification, Course.name)
    if category:
        stmt = stmt.where(Course.category == category)
    rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        label = f"{category}の" if category else ""
        return [TextMessage(text=f"まだ{label}科目が登録されていません。")]

    groups: dict[str, list] = defaultdict(list)
    for course in rows:
        groups[course.classification or "その他"].append(course)

    bubbles = []
    for classification, courses in list(groups.items())[:10]:
        btn_contents = []
        for course in courses[:8]:
            label = course.name if len(course.name) <= 20 else course.name[:19] + "…"
            btn_contents.append(
                FlexButton(
                    action=MessageAction(label=label, text=course.name),
                    style="link",
                    height="sm",
                )
            )
        bubbles.append(FlexBubble(
            size="kilo",
            header=FlexBox(
                layout="vertical",
                contents=[FlexText(text=classification, weight="bold", color="#ffffff", size="sm")],
                background_color="#6366f1",
                padding_all="md",
            ),
            body=FlexBox(
                layout="vertical",
                contents=btn_contents,
                spacing="xs",
                padding_all="md",
            ),
        ))

    alt = f"📚 {category}一覧" if category else "📚 科目一覧"
    if len(bubbles) == 1:
        return [FlexMessage(alt_text=alt, contents=bubbles[0])]
    return [FlexMessage(alt_text=alt, contents=FlexCarousel(contents=bubbles))]


# ── Message handler ─────────────────────────────────────────────

async def handle_message(session: AsyncSession, text: str, user_id: str = "") -> list:
    t = text.strip()

    if t in ["科目一覧", "科目", "授業一覧", "一覧"]:
        return await handle_course_list(session)

    if t in ["教養", "教養科目", "教養一覧"]:
        return await handle_course_list(session, category="教養")

    if t in ["専門科目", "専門", "専門一覧"]:
        return await handle_course_list(session, category="専門")

    if t in ["レビュー投稿", "レビュー", "投稿"]:
        url = f"{REVIEW_FORM_URL}?uid={user_id}" if user_id else REVIEW_FORM_URL
        return [TextMessage(text=f"📝 以下のフォームからレビューを投稿できます！\n\n{url}")]

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
        return [await get_course_flex(session, c, user_id) for c in courses]

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
                messages = await handle_message(session, user_text, user_id)
                await save_log(session, user_id, "out", f"[{len(messages)} msg(s)]")

                await line_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=messages[:5],
                    )
                )

    return {"status": "ok"}


ADMIN_STYLE = """
body{font-family:sans-serif;padding:16px;background:#f3f4f6;max-width:700px;margin:auto}
h1{font-size:18px;font-weight:bold;margin-bottom:4px}
h2{font-size:15px;font-weight:bold;margin:20px 0 8px}
nav{margin-bottom:16px;font-size:13px}
nav a{color:#6366f1;text-decoration:none;margin-right:12px}
table{width:100%;border-collapse:collapse;background:white;border-radius:8px;overflow:hidden;font-size:13px}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #e5e7eb}
th{background:#6366f1;color:white}
.card{background:white;border-radius:8px;padding:16px;margin-bottom:12px}
input,select{width:100%;padding:8px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:13px;box-sizing:border-box;margin-top:4px}
label{font-size:13px;font-weight:600;color:#374151}
.btn{padding:8px 16px;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:bold}
.btn-primary{background:#6366f1;color:white}
.btn-danger{background:#ef4444;color:white}
"""


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
<html lang="ja"><head><meta charset="UTF-8"><title>管理画面</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{ADMIN_STYLE}</style></head><body>
<h1>🛡️ 管理画面</h1>
<nav><a href="/admin">📊 ログ</a><a href="/admin/courses">📚 科目管理</a><a href="/admin/users">👤 ユーザー設定</a></nav>
<h2>メッセージログ（最新50件）</h2>
<table><tr><th>ユーザー</th><th>方向</th><th>メッセージ</th><th>日時</th></tr>
{rows_html or '<tr><td colspan="4" style="color:#999;text-align:center;padding:16px">ログなし</td></tr>'}
</table></body></html>"""


@app.get("/admin/courses", response_class=HTMLResponse)
async def admin_courses(_: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        courses = (await session.execute(
            select(Course).order_by(Course.classification, Course.name)
        )).scalars().all()
        classifications = (await session.execute(
            select(Course.classification).distinct().order_by(Course.classification)
        )).scalars().all()

    existing = [c for c in classifications if c]
    options_html = "<option value=''>（未分類）</option>" + "".join(
        f"<option value='{c}'>{c}</option>" for c in existing
    ) + "<option value='__new__'>＋ 新しい分類を入力...</option>"

    courses_data = json.dumps({
        c.id: {
            "name": c.name,
            "instructor": c.instructor or "",
            "classification": c.classification or "",
            "category": c.category,
        }
        for c in courses
    }, ensure_ascii=False)

    def course_row(c):
        badge_color = '#6366f1' if c.category == '教養' else '#10b981'
        e = _html.escape
        return (
            f"<tr>"
            f"<td>{e(c.name)}</td>"
            f"<td>{e(c.instructor or '―')}</td>"
            f"<td>{e(c.classification or '―')}</td>"
            f"<td><span style='background:{badge_color};color:#fff;padding:2px 8px;border-radius:999px;font-size:11px'>{e(c.category)}</span></td>"
            f"<td style='white-space:nowrap'>"
            f"<button type='button' class='btn btn-primary' style='padding:4px 10px;margin-right:4px' onclick='openEdit({c.id})'>編集</button>"
            f"<form method='post' action='/admin/courses/delete/{c.id}' style='display:inline;margin:0' onsubmit='return confirm(\"削除しますか？\")'>"
            f"<button type='submit' class='btn btn-danger' style='padding:4px 10px'>削除</button></form>"
            f"</td></tr>"
        )

    rows_html = "".join(course_row(c) for c in courses)

    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>科目管理</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{ADMIN_STYLE}
#editModal{{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:100;display:none;align-items:center;justify-content:center}}
#editModal.open{{display:flex}}
#editBox{{background:#fff;border-radius:12px;padding:24px;width:90%;max-width:480px}}
</style></head><body>
<h1>📚 科目管理</h1>
<nav><a href="/admin">📊 ログ</a><a href="/admin/courses">📚 科目管理</a><a href="/admin/users">👤 ユーザー設定</a></nav>

<div class="card">
  <h2 style="margin-top:0">科目を追加</h2>
  <form method="post" action="/admin/courses/add">
    <div style="margin-bottom:10px">
      <label>科目名 *<input type="text" name="name" required maxlength="200" placeholder="例：データサイエンス基礎学"></label>
    </div>
    <div style="margin-bottom:10px">
      <label>担当教員<input type="text" name="instructor" maxlength="100" placeholder="例：山田教授"></label>
    </div>
    <div style="margin-bottom:10px">
      <label>カテゴリ *
        <select name="category" style="margin-top:4px">
          <option value="専門">専門科目</option>
          <option value="教養">教養科目</option>
        </select>
      </label>
    </div>
    <div style="margin-bottom:14px">
      <label>分類
        <select id="classSelect" style="margin-top:4px" onchange="onClassChange(this)">
          {options_html}
        </select>
        <input type="text" name="classification" id="classInput" maxlength="50"
               placeholder="新しい分類名を入力" style="margin-top:6px;display:none">
      </label>
    </div>
    <button type="submit" class="btn btn-primary">➕ 追加する</button>
  </form>
</div>

<h2>登録済み科目（{len(courses)}件）</h2>
<table><tr><th>科目名</th><th>教員</th><th>分類</th><th>カテゴリ</th><th></th></tr>
{rows_html or '<tr><td colspan="5" style="color:#999;text-align:center;padding:16px">科目なし</td></tr>'}
</table>

<!-- 編集モーダル -->
<div id="editModal">
  <div id="editBox">
    <h2 style="margin-top:0">✏️ 科目を編集</h2>
    <form id="editForm" method="post">
      <div style="margin-bottom:10px">
        <label>科目名 *<input type="text" id="editName" name="name" required maxlength="200"></label>
      </div>
      <div style="margin-bottom:10px">
        <label>担当教員<input type="text" id="editInstructor" name="instructor" maxlength="100"></label>
      </div>
      <div style="margin-bottom:10px">
        <label>カテゴリ *
          <select id="editCategory" name="category">
            <option value="専門">専門科目</option>
            <option value="教養">教養科目</option>
          </select>
        </label>
      </div>
      <div style="margin-bottom:14px">
        <label>分類<input type="text" id="editClassification" name="classification" maxlength="50"></label>
      </div>
      <div style="display:flex;gap:8px">
        <button type="submit" class="btn btn-primary">💾 保存</button>
        <button type="button" class="btn" style="background:#e5e7eb;color:#374151" onclick="closeModal()">キャンセル</button>
      </div>
    </form>
  </div>
</div>

<script>
const COURSES = {courses_data};
function openEdit(id) {{
  const c = COURSES[id];
  document.getElementById('editName').value = c.name;
  document.getElementById('editInstructor').value = c.instructor;
  document.getElementById('editClassification').value = c.classification;
  document.getElementById('editCategory').value = c.category;
  document.getElementById('editForm').action = '/admin/courses/update/' + id;
  document.getElementById('editModal').classList.add('open');
}}
function closeModal() {{
  document.getElementById('editModal').classList.remove('open');
}}
document.getElementById('editModal').addEventListener('click', function(e) {{
  if (e.target === this) closeModal();
}});
function onClassChange(sel) {{
  const inp = document.getElementById('classInput');
  if (sel.value === '__new__') {{
    inp.style.display = 'block';
    inp.required = true;
    inp.value = '';
    inp.focus();
  }} else {{
    inp.style.display = 'none';
    inp.required = false;
    inp.value = sel.value;
  }}
}}
document.getElementById('classSelect').dispatchEvent(new Event('change'));
document.querySelector('form').addEventListener('submit', function() {{
  const sel = document.getElementById('classSelect');
  const inp = document.getElementById('classInput');
  if (sel.value !== '__new__') inp.value = sel.value;
}});
</script>
</body></html>"""


@app.post("/admin/courses/add")
async def admin_courses_add(
    _: str = Depends(check_admin),
    name: str = Form(...),
    instructor: str = Form(""),
    classification: str = Form(""),
    category: str = Form("専門"),
):
    async with AsyncSessionLocal() as session:
        session.add(Course(name=name.strip(), instructor=instructor.strip(), classification=classification.strip(), category=category))
        await session.commit()
    return RedirectResponse(url="/admin/courses", status_code=303)


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(_: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        users = (await session.execute(
            select(MessageLog.user_id, func.max(MessageLog.created_at).label("last_seen"))
            .where(MessageLog.direction == "in")
            .group_by(MessageLog.user_id)
            .order_by(func.max(MessageLog.created_at).desc())
        )).all()

        prefs = {p.user_id: p.max_reviews for p in (await session.execute(
            select(UserPreference)
        )).scalars().all()}

    rows_html = "".join(
        f"<tr><td style='font-size:11px'>{u.user_id}</td>"
        f"<td>{u.last_seen.strftime('%m/%d %H:%M')}</td>"
        f"<td>"
        f"<form method='post' action='/admin/users/set' style='margin:0;display:flex;gap:6px;align-items:center'>"
        f"<input type='hidden' name='user_id' value='{u.user_id}'>"
        f"<input type='number' name='max_reviews' value='{prefs.get(u.user_id, MAX_REVIEWS)}' min='1' max='10' style='width:60px'>"
        f"<button type='submit' class='btn btn-primary' style='padding:4px 10px'>保存</button>"
        f"</form></td></tr>"
        for u in users
    )
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>ユーザー設定</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{ADMIN_STYLE}</style></head><body>
<h1>👤 ユーザー設定</h1>
<nav><a href="/admin">📊 ログ</a><a href="/admin/courses">📚 科目管理</a><a href="/admin/users">👤 ユーザー設定</a></nav>
<p style="font-size:13px;color:#666">デフォルトのコメント表示件数：<b>{MAX_REVIEWS}件</b>（MAX_REVIEWS環境変数）</p>
<table><tr><th>ユーザーID</th><th>最終アクセス</th><th>コメント表示件数</th></tr>
{rows_html or '<tr><td colspan="3" style="color:#999;text-align:center;padding:16px">ユーザーなし</td></tr>'}
</table></body></html>"""


@app.post("/admin/users/set")
async def admin_users_set(
    _: str = Depends(check_admin),
    user_id: str = Form(...),
    max_reviews: int = Form(...),
):
    async with AsyncSessionLocal() as session:
        pref = (await session.execute(
            select(UserPreference).where(UserPreference.user_id == user_id)
        )).scalar_one_or_none()
        if pref:
            pref.max_reviews = max(1, min(10, max_reviews))
        else:
            session.add(UserPreference(user_id=user_id, max_reviews=max(1, min(10, max_reviews))))
        await session.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/courses/update/{course_id}")
async def admin_courses_update(
    course_id: int,
    _: str = Depends(check_admin),
    name: str = Form(...),
    instructor: str = Form(""),
    classification: str = Form(""),
    category: str = Form("専門"),
):
    async with AsyncSessionLocal() as session:
        course = (await session.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
        if course:
            course.name = name.strip()
            course.instructor = instructor.strip()
            course.classification = classification.strip()
            course.category = category
            await session.commit()
    return RedirectResponse(url="/admin/courses", status_code=303)


@app.post("/admin/courses/delete/{course_id}")
async def admin_courses_delete(course_id: int, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Course).where(Course.id == course_id))
        course = result.scalar_one_or_none()
        if course:
            await session.delete(course)
            await session.commit()
    return RedirectResponse(url="/admin/courses", status_code=303)


@app.get("/health")
async def health():
    return {"status": "healthy"}
