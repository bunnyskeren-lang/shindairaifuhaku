import os
import json
import random
import traceback as _traceback
import hashlib
import hmac
import base64
import secrets as py_secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

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

from sqlalchemy import select, func, delete, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, AsyncSessionLocal
from models import MessageLog, Course, PendingReview, UserPreference, UserProfile, UserActivity, ErrorLog, PushSubscription

from dotenv import load_dotenv
load_dotenv()

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
REVIEW_FORM_URL = os.environ.get("REVIEW_FORM_URL", "https://shindairaifuhaku-1.onrender.com")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
def _parse_max_reviews(val: str, default: int = 3, lo: int = 1, hi: int = 10) -> int:
    try:
        n = int(val)
    except (ValueError, TypeError):
        return default
    return max(lo, min(hi, n))

MAX_REVIEWS = _parse_max_reviews(os.environ.get("MAX_REVIEWS", "3"))

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)
security = HTTPBasic()
JST = timezone(timedelta(hours=9))

templates = Jinja2Templates(directory="templates")

def _to_jst(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST).strftime("%m/%d %H:%M")

templates.env.filters["jst"] = _to_jst
templates.env.globals["VAPID_PUBLIC_KEY"] = VAPID_PUBLIC_KEY

try:
    import pykakasi as _pykakasi
    _kks = _pykakasi.kakasi()
    def _reading(text: str) -> str:
        result = _kks.convert(text)
        hira = ''.join(item.get('hira', '') for item in result)
        roma = ''.join(item.get('hepburn', '') for item in result)
        return f"{hira} {roma}".lower().strip()
except Exception:
    def _reading(text: str) -> str:
        return ""

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
    if direction == "in":
        now = datetime.now(timezone.utc)
        stmt = (
            pg_insert(UserActivity)
            .values(user_id=user_id, action=message[:200], count=1, last_at=now)
            .on_conflict_do_update(
                index_elements=["user_id", "action"],
                set_={"count": UserActivity.count + 1, "last_at": now},
            )
        )
        await session.execute(stmt)
    await session.commit()


async def save_error_log(exc: Exception, user_id: str | None = None, action: str | None = None):
    try:
        async with AsyncSessionLocal() as session:
            session.add(ErrorLog(
                user_id=user_id,
                action=action[:200] if action else None,
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
                traceback=_traceback.format_exc()[:4000],
            ))
            await session.commit()
    except Exception:
        pass


# ── FlexMessage builder ─────────────────────────────────────────

EASE_STARS = {"SS": "★★★★★", "S": "★★★★☆", "A": "★★★☆☆", "B": "★★☆☆☆", "C": "★☆☆☆☆"}


def make_course_bubble(
    name: str,
    instructor: str,
    classification: str,
    avg_rating: Optional[float],
    top_ease: Optional[str],
    comments: list[str],
    grading_methods: list[str] = [],
    review_url: str = "",
    syllabus_url: str = "",
) -> FlexBubble:
    body_contents = []

    # 分類タグ
    if classification:
        body_contents.append(
            FlexBox(
                layout="horizontal",
                contents=[
                    FlexBox(
                        layout="vertical",
                        contents=[FlexText(text=f"📂 {classification}", size="xs", color="#6366f1")],
                        background_color="#eef2ff",
                        corner_radius="20px",
                        padding_all="sm",
                    )
                ],
                margin="sm",
            )
        )

    # 学びになった度（星評価）
    if avg_rating is not None:
        body_contents.append(
            FlexBox(
                layout="horizontal",
                contents=[
                    FlexText(text="学びになった度", size="xs", color="#64748b"),
                    FlexText(text=stars(round(avg_rating)), size="xs", color="#f59e0b", margin="sm"),
                ],
                margin="md",
            )
        )
    else:
        body_contents.append(
            FlexText(text="⭐ まだレビューなし", size="sm", color="#94a3b8", margin="md")
        )

    # 楽単度（星のみ）
    if top_ease:
        body_contents.append(
            FlexBox(
                layout="horizontal",
                contents=[
                    FlexText(text="楽単度", size="xs", color="#64748b"),
                    FlexText(text=EASE_STARS.get(top_ease, ""), size="xs", color="#f59e0b", margin="sm"),
                ],
                margin="xs",
            )
        )

    # 成績評価方法（全件）
    if grading_methods:
        body_contents.append(
            FlexBox(
                layout="horizontal",
                contents=[
                    FlexText(text="📝 成績評価", size="xs", color="#64748b", flex=2),
                    FlexText(text="・".join(grading_methods), size="xs", color="#334155", wrap=True, flex=5),
                ],
                margin="xs",
            )
        )

    # コメント
    if comments:
        body_contents.append(FlexSeparator(margin="lg"))
        body_contents.append(
            FlexText(text="💬 コメント", size="xs", color="#64748b", weight="bold", margin="sm")
        )
        for comment in comments:
            preview = comment[:80] + ("..." if len(comment) > 80 else "")
            body_contents.append(
                FlexBox(
                    layout="vertical",
                    contents=[FlexText(text=f"「{preview}」", size="sm", wrap=True, color="#334155")],
                    background_color="#f8fafc",
                    corner_radius="8px",
                    padding_all="sm",
                    margin="sm",
                )
            )

    footer_contents = []
    if syllabus_url:
        footer_contents.append(
            FlexButton(
                action=URIAction(label="📄 シラバス", uri=syllabus_url),
                style="secondary",
                height="sm",
            )
        )
    footer_contents.append(
        FlexButton(
            action=URIAction(label="✏️ レビューを投稿", uri=review_url or REVIEW_FORM_URL),
            style="primary",
            color="#6366f1",
            height="sm",
        )
    )

    return FlexBubble(
        header=FlexBox(
            layout="vertical",
            contents=[
                FlexText(text=name, weight="bold", size="lg", color="#ffffff", wrap=True),
                FlexText(text=f"👨‍🏫 {instructor or '未設定'}", size="sm", color="#c7d2fe", margin="xs"),
            ],
            background_color="#6366f1",
            padding_all="lg",
        ),
        body=FlexBox(layout="vertical", contents=body_contents, padding_all="lg"),
        footer=FlexBox(layout="vertical", contents=footer_contents, padding_all="md", spacing="sm"),
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

    grading_rows = (await session.execute(
        select(PendingReview.grading_method).distinct()
        .where(
            PendingReview.course_name == course.name,
            PendingReview.is_approved == True,
            PendingReview.grading_method.isnot(None),
        )
    )).scalars().all()
    all_grading_methods = [g for g in grading_rows if g]

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
        grading_methods=all_grading_methods,
        review_url=url,
        syllabus_url=course.syllabus_url or "",
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


# ── Ranking bubble ──────────────────────────────────────────────

RANK_MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}


def make_ranking_bubble(title: str, items: list[dict]) -> FlexBubble:
    # items: [{"rank": int, "name": str, "stars": str}]
    body_contents = []
    for i, item in enumerate(items):
        if i > 0:
            body_contents.append(FlexSeparator(margin="sm"))
        medal = RANK_MEDAL.get(item["rank"], f"{item['rank']}位")
        body_contents.append(
            FlexBox(
                layout="horizontal",
                contents=[
                    FlexText(text=medal, size="sm", flex=1, align="center", gravity="center"),
                    FlexBox(
                        layout="vertical",
                        contents=[
                            FlexText(
                                text=item["name"],
                                size="sm",
                                wrap=True,
                                weight="bold",
                                color="#1e293b",
                                action=MessageAction(
                                    label=item["name"][:20],
                                    text=item["name"],
                                ),
                            ),
                            FlexText(text=item["stars"], size="xs", color="#f59e0b", margin="xs"),
                        ],
                        flex=5,
                    ),
                ],
                spacing="md",
                margin="sm",
            )
        )
    return FlexBubble(
        header=FlexBox(
            layout="vertical",
            contents=[FlexText(text=title, weight="bold", color="#ffffff", size="md")],
            background_color="#6366f1",
            padding_all="lg",
        ),
        body=FlexBox(layout="vertical", contents=body_contents, padding_all="lg"),
        footer=FlexBox(
            layout="vertical",
            contents=[FlexText(text="科目名をタップすると詳細が見られます", size="xs", color="#94a3b8", align="center")],
            padding_all="md",
        ),
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

    CAROUSEL_MAX = 12
    all_groups = list(groups.items())
    visible_groups = all_groups[:CAROUSEL_MAX]
    overflow_groups = all_groups[CAROUSEL_MAX:]

    bubbles = []
    for classification, courses in visible_groups:
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
    result = []
    if len(bubbles) == 1:
        result.append(FlexMessage(alt_text=alt, contents=bubbles[0]))
    else:
        result.append(FlexMessage(alt_text=alt, contents=FlexCarousel(contents=bubbles)))

    if overflow_groups:
        names = "、".join(cl for cl, _ in overflow_groups)
        result.append(TextMessage(
            text=f"他 {len(overflow_groups)} 分類（{names}）は科目名で直接検索できます。\n例：「微分積分学」と送ってください。"
        ))

    return result


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
        items = [
            {"rank": i, "name": name, "stars": stars(round(float(avg)))}
            for i, (name, avg) in enumerate(rows, 1)
        ]
        return [FlexMessage(
            alt_text="🏆 人気の授業 TOP5",
            contents=make_ranking_bubble("🏆 人気の授業 TOP5", items),
        )]

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
        items = [
            {"rank": i, "name": name, "stars": EASE_STARS.get(ease, "")}
            for i, (name, ease) in enumerate(top5, 1)
        ]
        return [FlexMessage(
            alt_text="😴 楽単ランキング TOP5",
            contents=make_ranking_bubble("😴 楽単ランキング TOP5", items),
        )]

    if t in ["ヘルプ", "help", "使い方", "？", "?"]:
        return [TextMessage(text=HELP_TEXT)]

    if t in ["問い合わせ", "連絡", "contact", "お問い合わせ"]:
        return [TextMessage(text=CONTACT_TEXT)]

    # Course keyword search — split on spaces (half-width and full-width) for multi-token AND match
    import re as _re
    tokens = [tok for tok in _re.split(r'[\s　]+', t.strip()) if tok]
    def _escape(tok: str) -> str:
        return tok.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    stmt = select(Course)
    for tok in tokens:
        stmt = stmt.where(Course.name.ilike(f"%{_escape(tok)}%", escape="\\"))
    courses = (await session.execute(stmt.limit(3))).scalars().all()
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

    if random.random() < 0.02:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        async with AsyncSessionLocal() as session:
            await session.execute(delete(MessageLog).where(MessageLog.created_at < cutoff))
            await session.commit()

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

                try:
                    await save_log(session, user_id, "in", user_text)
                    messages = await handle_message(session, user_text, user_id)
                    await save_log(session, user_id, "out", f"[{len(messages)} msg(s)]")
                    await line_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=messages[:5],
                        )
                    )
                except Exception as exc:
                    await save_error_log(exc, user_id=user_id, action=user_text)
                    try:
                        await line_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(text="エラーが発生しました。しばらくしてからもう一度お試しください。")],
                            )
                        )
                    except Exception:
                        pass

    return {"status": "ok"}


@app.get("/sw.js")
async def service_worker():
    from fastapi.responses import Response
    js = """
self.addEventListener('push', function(e) {
  const d = e.data ? e.data.json() : {};
  e.waitUntil(self.registration.showNotification(d.title || '新着レビュー', {
    body: d.body || '',
    icon: 'https://cdn-icons-png.flaticon.com/512/1041/1041916.png',
    badge: 'https://cdn-icons-png.flaticon.com/512/1041/1041916.png',
  }));
});
self.addEventListener('notificationclick', function(e) {
  e.notification.close();
  e.waitUntil(clients.openWindow('/admin/courses'));
});
""".strip()
    return Response(content=js, media_type="application/javascript")


@app.post("/admin/push/subscribe")
async def admin_push_subscribe(request: Request, _: str = Depends(check_admin)):
    data = await request.json()
    async with AsyncSessionLocal() as session:
        stmt = pg_insert(PushSubscription).values(
            endpoint=data["endpoint"],
            p256dh=data["keys"]["p256dh"],
            auth=data["keys"]["auth"],
        ).on_conflict_do_update(
            index_elements=["endpoint"],
            set_={"p256dh": data["keys"]["p256dh"], "auth": data["keys"]["auth"]},
        )
        await session.execute(stmt)
        await session.commit()
    return {"ok": True}


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, _: str = Depends(check_admin), page: int = Query(default=1, ge=1)):
    per_page = 50
    async with AsyncSessionLocal() as session:
        total = (await session.execute(select(func.count(MessageLog.id)))).scalar_one()
        logs = (await session.execute(
            select(MessageLog).order_by(MessageLog.created_at.desc())
            .offset((page - 1) * per_page).limit(per_page)
        )).scalars().all()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse("admin/logs.html", {
        "request": request,
        "logs": logs,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "url_prefix": "/admin?page=",
    })


@app.get("/admin/courses", response_class=HTMLResponse)
async def admin_courses(request: Request, _: str = Depends(check_admin), msg: str = "", q: str = Query(default="")):
    q = q.strip()

    def _search_filter(q: str):
        q_safe = q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        return or_(
            Course.name.ilike(f"%{q_safe}%", escape="\\"),
            Course.instructor.ilike(f"%{q_safe}%", escape="\\"),
            Course.reading.ilike(f"%{q_safe}%", escape="\\"),
        )

    async with AsyncSessionLocal() as session:
        base_stmt = select(Course)
        if q:
            base_stmt = base_stmt.where(_search_filter(q))

        courses = (await session.execute(
            base_stmt.order_by(Course.classification, Course.name)
        )).scalars().all()
        total = len(courses)
        classifications = (await session.execute(
            select(Course.classification).distinct().order_by(Course.classification)
        )).scalars().all()
        class_counts = dict((await session.execute(
            select(Course.classification, func.count(Course.id))
            .where(Course.classification != "")
            .group_by(Course.classification)
            .order_by(Course.classification)
        )).all())

    existing = [c for c in classifications if c]
    courses_data = (
        json.dumps({
            c.id: {
                "name": c.name,
                "instructor": c.instructor or "",
                "classification": c.classification or "",
                "category": c.category,
                "syllabus_url": c.syllabus_url or "",
            }
            for c in courses
        }, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )

    course_names = [c.name for c in courses]
    if course_names:
        reviews_raw = (await session.execute(
            select(PendingReview)
            .where(PendingReview.course_name.in_(course_names))
            .order_by(PendingReview.is_approved, PendingReview.created_at.desc())
        )).scalars().all()
    else:
        reviews_raw = []
    from collections import defaultdict
    reviews_by_course: dict = defaultdict(list)
    for r in reviews_raw:
        reviews_by_course[r.course_name].append(r)

    return templates.TemplateResponse("admin/courses.html", {
        "request": request,
        "courses": courses,
        "classifications": existing,
        "class_counts": class_counts,
        "courses_data": courses_data,
        "reviews_by_course": reviews_by_course,
        "error": msg,
        "total": total,
        "q": q,
    })


@app.post("/admin/reviews/approve/{review_id}")
async def admin_review_approve(review_id: int, request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        review = await session.get(PendingReview, review_id)
        if review:
            review.is_approved = True
            await session.commit()
    return RedirectResponse(request.headers.get("Referer", "/admin/courses"), status_code=303)


@app.post("/admin/reviews/reject/{review_id}")
async def admin_review_reject(review_id: int, request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        review = await session.get(PendingReview, review_id)
        if review:
            await session.delete(review)
            await session.commit()
    return RedirectResponse(request.headers.get("Referer", "/admin/courses"), status_code=303)


@app.post("/admin/courses/add")
async def admin_courses_add(
    _: str = Depends(check_admin),
    name: str = Form(...),
    instructor: str = Form(""),
    classification: str = Form(""),
    category: str = Form("専門"),
    syllabus_url: str = Form(""),
):
    name_s = name.strip()
    instructor_s = instructor.strip()
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(Course).where(
                Course.name == name_s,
                Course.instructor == instructor_s,
            )
        )).scalar_one_or_none()
        if existing:
            return RedirectResponse(
                url=f"/admin/courses?error={py_secrets.token_urlsafe(4)}&msg=duplicate",
                status_code=303,
            )
        session.add(Course(
            name=name_s, instructor=instructor_s,
            classification=classification.strip(), category=category,
            syllabus_url=syllabus_url.strip() or None,
            reading=_reading(name_s),
        ))
        await session.commit()
    return RedirectResponse(url="/admin/courses", status_code=303)


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        users = (await session.execute(
            select(
                MessageLog.user_id,
                func.max(MessageLog.created_at).label("last_seen"),
                UserProfile.name,
                UserProfile.student_id,
            )
            .outerjoin(UserProfile, UserProfile.line_user_id == MessageLog.user_id)
            .where(MessageLog.direction == "in")
            .group_by(MessageLog.user_id, UserProfile.name, UserProfile.student_id)
            .order_by(func.max(MessageLog.created_at).desc())
        )).all()

        prefs = {p.user_id: p.max_reviews for p in (await session.execute(
            select(UserPreference)
        )).scalars().all()}

    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "users": users,
        "prefs": prefs,
        "max_reviews": MAX_REVIEWS,
    })


@app.get("/admin/errors", response_class=HTMLResponse)
async def admin_errors(request: Request, _: str = Depends(check_admin), page: int = Query(default=1, ge=1)):
    per_page = 50
    async with AsyncSessionLocal() as session:
        total = (await session.execute(select(func.count(ErrorLog.id)))).scalar_one()
        errors = (await session.execute(
            select(
                ErrorLog.id,
                ErrorLog.created_at,
                ErrorLog.user_id,
                UserProfile.name,
                UserProfile.student_id,
                ErrorLog.action,
                ErrorLog.error_type,
                ErrorLog.error_message,
                ErrorLog.traceback,
            )
            .outerjoin(UserProfile, UserProfile.line_user_id == ErrorLog.user_id)
            .order_by(ErrorLog.created_at.desc())
            .offset((page - 1) * per_page).limit(per_page)
        )).all()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse("admin/errors.html", {
        "request": request,
        "errors": errors,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "url_prefix": "/admin/errors?page=",
    })


@app.get("/admin/activity", response_class=HTMLResponse)
async def admin_activity(request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(
                UserActivity.user_id,
                UserProfile.name,
                UserProfile.student_id,
                UserActivity.action,
                UserActivity.count,
                UserActivity.last_at,
            )
            .outerjoin(UserProfile, UserProfile.line_user_id == UserActivity.user_id)
            .order_by(UserActivity.user_id, UserActivity.count.desc())
        )).all()

    return templates.TemplateResponse("admin/activity.html", {
        "request": request,
        "rows": rows,
    })


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


@app.post("/admin/courses/classification/rename")
async def rename_classification(
    _: str = Depends(check_admin),
    old_name: str = Form(...),
    new_name: str = Form(...),
):
    new_name = new_name.strip()
    if not new_name or new_name == old_name:
        return RedirectResponse(url="/admin/courses", status_code=303)
    async with AsyncSessionLocal() as session:
        courses = (await session.execute(
            select(Course).where(Course.classification == old_name)
        )).scalars().all()
        for course in courses:
            course.classification = new_name
        await session.commit()
    return RedirectResponse(url="/admin/courses", status_code=303)


@app.post("/admin/courses/classification/delete")
async def delete_classification(
    _: str = Depends(check_admin),
    classification: str = Form(...),
):
    async with AsyncSessionLocal() as session:
        courses_in_class = (await session.execute(
            select(Course).where(Course.classification == classification)
        )).scalars().all()
        for course in courses_in_class:
            course.classification = ""
        await session.commit()
    return RedirectResponse(url="/admin/courses", status_code=303)


@app.post("/admin/courses/update/{course_id}")
async def admin_courses_update(
    course_id: int,
    _: str = Depends(check_admin),
    name: str = Form(...),
    instructor: str = Form(""),
    classification: str = Form(""),
    category: str = Form("専門"),
    syllabus_url: str = Form(""),
):
    async with AsyncSessionLocal() as session:
        course = (await session.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
        if course:
            course.name = name.strip()
            course.instructor = instructor.strip()
            course.classification = classification.strip()
            course.category = category
            course.syllabus_url = syllabus_url.strip() or None
            course.reading = _reading(name.strip())
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
@app.head("/health")
async def health():
    return {"status": "healthy"}
