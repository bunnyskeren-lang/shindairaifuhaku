import os
import math
import re as _re
import json
import asyncio
import time
from urllib.parse import quote as _url_quote
import random
import traceback as _traceback
import hashlib
import hmac
import base64
import secrets as py_secrets
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import FastAPI, Request, HTTPException, Depends, Form, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse as _JSONResponse
from fastapi.responses import HTMLResponse, RedirectResponse
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
    PostbackAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent, PostbackEvent

from sqlalchemy import select, func, delete, or_, update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, AsyncSessionLocal
from models import MessageLog, Course, PendingReview, UserProfile, UserActivity, ErrorLog, PushSubscription, CourseInstructor, ClassificationOrder, RichMenuTap, CourseView

from dotenv import load_dotenv
load_dotenv()

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
REVIEW_FORM_URL = os.environ.get("REVIEW_FORM_URL", "https://shindairaifuhaku.onrender.com")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_EMAIL = os.environ.get("VAPID_EMAIL", "admin@example.com")
SELF_URL = os.environ.get("SELF_URL", "").rstrip("/")
LIFF_ID = os.environ.get("LIFF_ID", "2010406205-emxo5rhE")
APP_URL = os.environ.get("APP_URL", "https://shindairaifuhaku.onrender.com")
STUDENT_ID_RE = _re.compile(r'^\d{7}(MM|ME|MH|[LHJEBSTAZX])$')

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)
JST = timezone(timedelta(hours=9))

ADMIN_COOKIE = "admin_tok"
ADMIN_TOKEN_TTL = 4 * 3600
_HMAC_KEY = hashlib.sha256((CHANNEL_SECRET + ADMIN_PASSWORD).encode()).digest()

def _make_admin_token() -> str:
    ts = int(datetime.now(timezone.utc).timestamp())
    sig = hmac.new(_HMAC_KEY, f"admin:{ts}".encode(), hashlib.sha256).hexdigest()
    return f"{ts}:{sig}"

def _verify_admin_token(token: str) -> bool:
    try:
        ts_str, sig = token.split(":", 1)
        ts = int(ts_str)
        if datetime.now(timezone.utc).timestamp() - ts > ADMIN_TOKEN_TTL:
            return False
        expected = hmac.new(_HMAC_KEY, f"admin:{ts_str}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False

def check_admin(request: Request):
    if not _verify_admin_token(request.cookies.get(ADMIN_COOKIE, "")):
        raise HTTPException(status_code=307, headers={"Location": f"/admin/login?next={request.url.path}"})

templates = Jinja2Templates(directory="templates")

def _to_jst(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST).strftime("%m/%d %H:%M")

templates.env.filters["jst"] = _to_jst
templates.env.globals["VAPID_PUBLIC_KEY"] = VAPID_PUBLIC_KEY
IS_DEV = os.environ.get("ENV", "prod") == "dev"
templates.env.globals["IS_DEV"] = IS_DEV

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

_CLS_ORDER_KEYS = ["基盤", "人文", "社会", "自然", "総合", "健康", "外国語"]

def _normalize_instructor_name(name: str) -> str:
    if any('぀' <= c <= '鿿' for c in name):
        return name.replace(' ', '')
    return name


def _cls_order(name: str) -> int:
    for i, kw in enumerate(_CLS_ORDER_KEYS):
        if kw in (name or ""):
            return i
    return len(_CLS_ORDER_KEYS)


_cls_order_map_cache: dict = {}
_cls_order_map_at: float = 0.0

async def _get_cls_order_map(session=None) -> dict:
    global _cls_order_map_cache, _cls_order_map_at
    if _cls_order_map_cache and time.monotonic() - _cls_order_map_at < _CLS_CACHE_TTL:
        return _cls_order_map_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(ClassificationOrder).order_by(ClassificationOrder.sort_order)
        )).scalars().all()
    _cls_order_map_cache = {r.name: r.sort_order for r in rows}
    _cls_order_map_at = time.monotonic()
    return _cls_order_map_cache


def _make_cls_sort(cls_map: dict):
    def key(name: str) -> int:
        if name in cls_map:
            return cls_map[name]
        return _cls_order(name) + 100000
    return key

EASE_ORDER = {"SS": 0, "S": 1, "A": 2, "B": 3, "C": 4}
EASE_LABEL = {"SS": "天国", "S": "楽々", "A": "標準", "B": "大変", "C": "修羅場"}
EASE_COLOR = {"SS": "#10b981", "S": "#6366f1", "A": "#f59e0b", "B": "#f97316", "C": "#ef4444"}


def stars(n: int) -> str:
    n = max(1, min(5, n))
    return "★" * n + "☆" * (5 - n)


async def send_push_notification(course_name: str, rating: int, ease_rating: str, comment: str):
    if not VAPID_PRIVATE_KEY:
        return
    import json as _json
    from pywebpush import webpush, WebPushException
    from sqlalchemy import delete as _sa_delete
    async with AsyncSessionLocal() as session:
        subs = (await session.execute(select(PushSubscription))).scalars().all()
    if not subs:
        return
    _stars = "★" * rating + "☆" * (5 - rating)
    payload = _json.dumps({
        "title": f"📝 新着レビュー: {course_name}",
        "body": f"{_stars}  楽単: {ease_rating}\n{comment[:80]}",
    })
    expired_ids = []
    for sub in subs:
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{VAPID_EMAIL}"},
            )
        except WebPushException as e:
            if e.response is not None and e.response.status_code == 410:
                expired_ids.append(sub.id)
        except Exception:
            pass
    if expired_ids:
        async with AsyncSessionLocal() as session:
            await session.execute(_sa_delete(PushSubscription).where(PushSubscription.id.in_(expired_ids)))
            await session.commit()


async def _self_ping():
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
        print("DB OK", flush=True)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"DB ERROR: {e}", flush=True)
    ping_task = asyncio.create_task(_self_ping())
    yield
    ping_task.cancel()
    try:
        await ping_task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    asyncio.create_task(save_error_log(
        exc,
        action=f"validation:{request.method} {request.url.path}",
    ))
    return _JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, next: str = "/admin"):
    return templates.TemplateResponse("admin/login.html", {"request": request, "next": next, "error": False})


@app.post("/admin/login")
async def admin_login(request: Request, password: str = Form(...), next: str = Form(default="/admin")):
    if not py_secrets.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
        return templates.TemplateResponse("admin/login.html", {"request": request, "next": next, "error": True})
    safe_next = next if next.startswith("/admin") else "/admin"
    response = RedirectResponse(safe_next, status_code=303)
    response.set_cookie(ADMIN_COOKIE, _make_admin_token(), httponly=True, samesite="strict")
    return response


@app.post("/admin/logout")
async def admin_logout():
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE)
    return response


def verify_signature(body: bytes, signature: str) -> bool:
    digest = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)




_cls_cache: set[str] = set()
_cls_cache_at: float = 0.0
_CLS_CACHE_TTL = 300

async def _get_cls_set() -> set[str]:
    global _cls_cache, _cls_cache_at
    if _cls_cache and time.monotonic() - _cls_cache_at < _CLS_CACHE_TTL:
        return _cls_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(Course.classification).distinct())).scalars().all()
    _cls_cache = {r for r in rows if r}
    _cls_cache_at = time.monotonic()
    return _cls_cache


# ── In-memory course & review cache (60s TTL) ───────────────────
_COURSE_CACHE_TTL = 60
_course_by_name: dict[str, Any] = {}
_course_list_all: list = []
_course_cache_at: float = 0.0

_reviewed_cache: set[str] = set()
_reviewed_cache_at: float = 0.0
_reviewed_cache_init: bool = False

async def _get_courses_cached():
    global _course_by_name, _course_list_all, _course_cache_at
    if _course_by_name and time.monotonic() - _course_cache_at < _COURSE_CACHE_TTL:
        return _course_by_name, _course_list_all
    async with AsyncSessionLocal() as s:
        courses = (await s.execute(
            select(Course).order_by(Course.sort_order, Course.name)
        )).scalars().all()
    _course_list_all = courses
    _course_by_name = {c.name: c for c in courses}
    _course_cache_at = time.monotonic()
    return _course_by_name, _course_list_all

async def _get_reviewed_cached() -> set[str]:
    global _reviewed_cache, _reviewed_cache_at, _reviewed_cache_init
    if _reviewed_cache_init and time.monotonic() - _reviewed_cache_at < _COURSE_CACHE_TTL:
        return _reviewed_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(PendingReview.course_name).where(PendingReview.is_approved == True).distinct()
        )).scalars().all()
    _reviewed_cache = set(rows)
    _reviewed_cache_at = time.monotonic()
    _reviewed_cache_init = True
    return _reviewed_cache


async def _save_log_bg(user_id: str, direction: str, message: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
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
    except Exception as exc:
        await save_error_log(exc, user_id=user_id, action=f"save_log_{direction}")


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




async def get_course_flex(course: Course, user_id: str) -> FlexMessage:
    async def _instrs():
        async with AsyncSessionLocal() as s:
            return (await s.execute(
                select(CourseInstructor).where(CourseInstructor.course_id == course.id)
            )).scalars().all()

    async def _count():
        async with AsyncSessionLocal() as s:
            return (await s.execute(
                select(func.count(PendingReview.id).label("cnt"))
                .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
            )).one()

    async def _ease():
        async with AsyncSessionLocal() as s:
            return (await s.execute(
                select(PendingReview.ease_rating, func.count(PendingReview.id).label("cnt"))
                .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
                .where(PendingReview.ease_rating.isnot(None))
                .group_by(PendingReview.ease_rating)
            )).all()

    instructors, count_row, ease_rows = await asyncio.gather(_instrs(), _count(), _ease())

    instructor_str = "・".join(i.name for i in instructors) or course.instructor or "未設定"
    liff_url = f"{APP_URL}/liff/course?course_id={course.id}"
    review_count = count_row.cnt or 0
    top_ease_flex: Optional[str] = None
    if ease_rows:
        top_ease_flex = sorted(ease_rows, key=lambda r: (-r[1], EASE_ORDER.get(r[0], 99)))[0][0]

    meta_parts = []
    if getattr(course, "term", None):
        meta_parts.append(course.term)
    if getattr(course, "credits", None):
        meta_parts.append(f"{course.credits}単位")
    if course.classification:
        meta_parts.append(course.classification)

    header_contents = [
        FlexText(text=course.name, weight="bold", size="lg", color="#ffffff", wrap=True),
        FlexText(text=instructor_str, size="xs", color="#c7d2fe", margin="xs"),
    ]
    if meta_parts:
        header_contents.append(
            FlexText(text="  ".join(meta_parts), size="xxs", color="#a5b4fc", margin="xs")
        )

    body_contents = []
    if top_ease_flex is not None:
        body_contents.append(
            FlexBox(
                layout="horizontal",
                contents=[
                    FlexText(text="楽単度", size="xs", color="#94a3b8", flex=0),
                    FlexText(text=EASE_STARS.get(top_ease_flex, ""), size="xl", color="#FFD700", flex=0, margin="sm"),
                    FlexText(text=f"({review_count}件)", size="xs", color="#94a3b8", margin="sm"),
                ],
                align_items="center",
            )
        )
    elif review_count == 0:
        body_contents.append(
            FlexText(text="まだレビューはありません", size="sm", color="#94a3b8")
        )
    body_contents.append(
        FlexText(text="タップして詳細・レビューを確認", size="xs", color="#b0bec5", margin="md")
    )

    bubble = FlexBubble(
        header=FlexBox(layout="vertical", contents=header_contents,
                       background_color="#4f46e5", padding_all="lg"),
        body=FlexBox(layout="vertical", contents=body_contents, padding_all="lg"),
        footer=FlexBox(layout="vertical", contents=[
            FlexButton(action=URIAction(label="詳細・レビューを見る", uri=liff_url),
                       style="primary", color="#4f46e5", height="sm")
        ], padding_all="md"),
    )
    return FlexMessage(alt_text=f"📖 {course.name}", contents=bubble)


def make_no_review_flex(course: Course, user_id: str = "") -> FlexMessage:
    form_url = f"{REVIEW_FORM_URL}?course={_url_quote(course.name)}"
    if user_id:
        form_url += f"&uid={user_id}"
    liff_url = f"{APP_URL}/liff/course?course_id={course.id}"
    return FlexMessage(
        alt_text=f"📖 {course.name}",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text=course.name, weight="bold", size="md", color="#ffffff", wrap=True),
                ],
                background_color="#94a3b8",
                padding_all="lg",
            ),
            body=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text="まだレビューがありません 😢", weight="bold", size="sm", color="#475569"),
                    FlexText(
                        text="あなたが最初のレビュワーになりませんか？🌟",
                        size="xs", color="#64748b", margin="sm", wrap=True,
                    ),
                ],
                padding_all="lg",
            ),
            footer=FlexBox(
                layout="vertical",
                spacing="sm",
                padding_all="md",
                contents=[
                    FlexButton(
                        action=URIAction(label="✏️ レビューを投稿する", uri=form_url),
                        style="primary", color="#6366f1", height="sm",
                    ),
                    FlexButton(
                        action=URIAction(label="📖 科目詳細を見る", uri=liff_url),
                        style="secondary", height="sm",
                    ),
                ],
            ),
        ),
    )


# ── Static reply texts ──────────────────────────────────────────

PRIVACY_URL = os.environ.get("APP_URL", "https://shindairaifuhaku.onrender.com") + "/privacy"
CONTACT_EMAIL = "bunnyskeren@gmail.com"

def make_help_flex() -> FlexMessage:
    def row(icon: str, title: str, desc: str) -> FlexBox:
        return FlexBox(
            layout="horizontal",
            contents=[
                FlexText(text=icon, size="lg", flex=0),
                FlexBox(
                    layout="vertical",
                    contents=[
                        FlexText(text=title, weight="bold", size="sm", color="#1f2937"),
                        FlexText(text=desc, size="xs", color="#6b7280", wrap=True),
                    ],
                    flex=1,
                    margin="md",
                ),
            ],
            margin="lg",
        )

    return FlexMessage(
        alt_text="📖 神大ライフハック 使い方ガイド",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text="神大ライフハック", weight="bold", color="#ffffff", size="xl"),
                    FlexText(text="使い方ガイド", color="#c7d2fe", size="sm"),
                ],
                background_color="#6366f1",
                padding_all="xl",
            ),
            body=FlexBox(
                layout="vertical",
                contents=[
                    row("🔍", "科目を検索", '科目名をそのまま送ってください\n例：「英語」「データサイエンス」'),
                    FlexSeparator(margin="lg"),
                    row("📚", "科目一覧", '「科目一覧」と送ると\n全科目を分類別に表示'),
                    FlexSeparator(margin="lg"),
                    row("✏️", "レビュー投稿", '「レビュー投稿」と送ると\n投稿フォームのURLが届きます'),
                    FlexSeparator(margin="lg"),
                    row("🏆", "ランキング", '「人気」→ 高評価 TOP5\n「楽単」→ 楽単 TOP5'),
                ],
                padding_all="lg",
            ),
            footer=FlexBox(
                layout="vertical",
                contents=[
                    FlexButton(
                        action=URIAction(label="📬 問い合わせ", uri=f"mailto:{CONTACT_EMAIL}"),
                        style="secondary",
                        height="sm",
                        color="#f3f4f6",
                    ),
                    FlexButton(
                        action=URIAction(label="📋 プライバシーポリシー", uri=PRIVACY_URL),
                        style="link",
                        height="sm",
                    ),
                ],
                padding_all="md",
                spacing="sm",
            ),
        ),
    )


def make_welcome_flex() -> FlexMessage:
    return FlexMessage(
        alt_text="🎓 神大ライフハックへようこそ！",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text="🎓 神大ライフハックへ", weight="bold", color="#ffffff", size="xl"),
                    FlexText(text="ようこそ！", color="#c7d2fe", size="lg", weight="bold"),
                ],
                background_color="#6366f1",
                padding_all="xl",
            ),
            body=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(
                        text="先輩のリアルなレビューで\n授業選びをサポートします📖",
                        wrap=True,
                        size="sm",
                        color="#374151",
                    ),
                    FlexSeparator(margin="lg"),
                    FlexText(text="できること", weight="bold", size="xs", color="#9ca3af", margin="lg"),
                    FlexText(text="🔍  科目名を送って検索", size="sm", color="#4b5563", margin="sm"),
                    FlexText(text="📚  「科目一覧」で全科目を表示", size="sm", color="#4b5563", margin="sm"),
                    FlexText(text="✏️  「レビュー投稿」で口コミを投稿", size="sm", color="#4b5563", margin="sm"),
                    FlexText(text="🏆  「人気」「楽単」でランキング表示", size="sm", color="#4b5563", margin="sm"),
                    FlexText(text="❓  「ヘルプ」で使い方を確認", size="sm", color="#4b5563", margin="sm"),
                ],
                padding_all="lg",
            ),
            footer=FlexBox(
                layout="vertical",
                contents=[
                    FlexButton(
                        action=MessageAction(label="📚 科目一覧を見る", text="科目一覧"),
                        style="primary",
                        color="#6366f1",
                        height="sm",
                    ),
                ],
                padding_all="md",
            ),
        ),
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

VARIANT_ICONS = {0: "🅰", 1: "🅱", 2: "🅲", 3: "🅳"}
VARIANT_COLORS = ["#6366f1", "#0d9488", "#f59e0b", "#ef4444"]


def _variant_suffix(base: str, full: str) -> str:
    if full.startswith(base):
        return full[len(base):]
    for b_ch, f_ch in zip(base, full):
        if b_ch != f_ch:
            return f_ch
    return full[-1]


def make_variant_selection_bubble(base_name: str, variant_names: list[str], reviewed_names: set[str] = frozenset()) -> FlexMessage:
    suffix_str = " / ".join(_variant_suffix(base_name, n) for n in variant_names)
    rows = []
    for name in variant_names:
        color = "#4f46e5" if name in reviewed_names else "#94a3b8"
        rows.append(
            FlexBox(
                layout="vertical",
                action=PostbackAction(label=name[:40], data=name),
                contents=[FlexText(text=name, wrap=True, size="sm", color=color)],
                padding_top="sm",
                padding_bottom="sm",
            )
        )
    return FlexMessage(
        alt_text=f"📚 {base_name} — {suffix_str} どれを見ますか？",
        contents=FlexBubble(
            size="kilo",
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text=base_name, weight="bold", color="#ffffff", size="sm", wrap=True),
                ],
                background_color="#6366f1",
                padding_all="md",
            ),
            body=FlexBox(
                layout="vertical",
                contents=rows,
                spacing="xs",
                padding_all="md",
            ),
        ),
    )


def make_classification_select_flex(classifications: list[str], reviewed_cls: set | None = None) -> FlexMessage:
    if reviewed_cls is None:
        reviewed_cls = set()
    btns = [
        FlexBox(
            layout="vertical",
            action=PostbackAction(label=cls[:40], data=cls),
            contents=[
                FlexText(
                    text=cls,
                    size="lg",
                    color="#4f46e5" if cls in reviewed_cls else "#475569",
                    weight="bold",
                    align="center",
                )
            ],
            background_color="#eef2ff" if cls in reviewed_cls else "#f8fafc",
            border_width="2px",
            border_color="#4f46e5" if cls in reviewed_cls else "#cbd5e1",
            corner_radius="20px",
            padding_all="md",
        )
        for cls in classifications
    ]
    return FlexMessage(
        alt_text="📚 教養科目 — 系統を選んでください",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text="📚 教養科目", weight="bold", color="#ffffff", size="lg"),
                    FlexText(text="系統を選んでください", color="#c7d2fe", size="sm"),
                ],
                background_color="#6366f1",
                padding_all="lg",
            ),
            body=FlexBox(
                layout="vertical",
                contents=btns,
                spacing="sm",
                padding_all="md",
            ),
        ),
    )


async def handle_course_list(category: str = "", classification: str = "") -> list:
    from collections import defaultdict
    async with AsyncSessionLocal() as session:
        stmt = select(Course).order_by(Course.sort_order, Course.name)
        if category:
            stmt = stmt.where(Course.category == category)
        if classification:
            stmt = stmt.where(Course.classification == classification)
        rows = (await session.execute(stmt)).scalars().all()
        cls_map = await _get_cls_order_map(session)
        _cls_sort = _make_cls_sort(cls_map)
        rows = sorted(rows, key=lambda c: (_cls_sort(c.classification or ""), c.sort_order, c.name or ""))

        reviewed_names: set[str] = set((await session.execute(
            select(PendingReview.course_name)
            .where(PendingReview.is_approved == True)
            .distinct()
        )).scalars().all())

        if not rows:
            label = f"{category}の" if category else ""
            return [TextMessage(text=f"まだ{label}科目が登録されていません。")]

        course_name_set = {c.name for c in rows}
        seen_base: set[str] = set()

        # Pre-compute numeric variant groups (plain digits OR letter+digits e.g. T1)
        _VNUM = _re.compile(r'^(.*?)[\s　]*([A-Z]?\d+)$')
        _num_bases: dict[str, list] = defaultdict(list)
        for _c in rows:
            _m = _VNUM.match(_c.name)
            if _m:
                _b = _m.group(1).strip()
                _sk = int(_re.search(r'\d+', _m.group(2)).group())
                _num_bases[_b].append((_c.name, _sk))
        _num_variant_names = {n for _b, _items in _num_bases.items() if len(_items) >= 2 for n, _ in _items}
        _num_base_for = {n: _b for _b, _items in _num_bases.items() if len(_items) >= 2 for n, _ in _items}
        seen_num_base: set[str] = set()

        # Pre-compute seminar variant groups e.g. 外国語セミナーA(英語) → 外国語セミナー(英語) (A/B/C/D)
        _VSEM = _re.compile(r'^(.*?セミナー)([A-Z]|\d+)(\([^)]+\))$')
        _sem_bases: dict[str, list] = defaultdict(list)
        for _c in rows:
            _m = _VSEM.match(_c.name)
            if _m:
                _base_lang = _m.group(1) + _m.group(3)
                _sem_bases[_base_lang].append((_c.name, _m.group(2)))
        _sem_variant_names = {n for _b, _items in _sem_bases.items() if len(_items) >= 2 for n, _ in _items}
        _sem_base_for = {n: _b for _b, _items in _sem_bases.items() if len(_items) >= 2 for n, _ in _items}
        seen_sem_base: set[str] = set()

        groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
        cls_category: dict[str, str] = {}
        for course in rows:
            name = course.name
            classification = course.classification or "その他"
            cls_category[classification] = course.category or ""
            if name in _sem_variant_names:
                base = _sem_base_for[name]
                if base not in seen_sem_base:
                    seen_sem_base.add(base)
                    items_sorted = sorted(_sem_bases[base], key=lambda x: x[1])
                    suffix = "/".join(sk for _, sk in items_sorted)
                    groups[classification].append((base, f"variant:{suffix}"))
                continue
            if name and name[-1] in ('A', 'B', 'C', 'D') and len(name) > 1:
                base = name[:-1]
                variants = [s for s in 'ABCD' if base + s in course_name_set]
                if len(variants) >= 2:
                    if base not in seen_base:
                        seen_base.add(base)
                        suffix = "/".join(variants)
                        groups[classification].append((base, f"variant:{suffix}"))
                    continue
            if name in _num_variant_names:
                base = _num_base_for[name]
                if base not in seen_num_base:
                    seen_num_base.add(base)
                    nums_sorted = sorted(_num_bases[base], key=lambda x: x[1])
                    suffix = "/".join(
                        _m2.group(2) if (_m2 := _VNUM.match(n)) else str(sk)
                        for n, sk in nums_sorted
                    )
                    groups[classification].append((base, f"numvariant:{suffix}"))
                continue
            groups[classification].append((name, "single"))

        def _cat_order(cls: str) -> int:
            return 0 if cls_category.get(cls, "") == "教養" else 1
        all_groups = sorted(groups.items(), key=lambda x: (_cat_order(x[0]), _cls_sort(x[0])))

        def _entry_has_review(name: str, kind: str) -> bool:
            if kind == "single":
                return name in reviewed_names
            if kind.startswith("variant:"):
                suffixes = kind.split(":", 1)[1].split("/")
                if any(name + s in reviewed_names for s in suffixes):
                    return True
                if name in _sem_bases:
                    return any(n in reviewed_names for n, _ in _sem_bases[name])
                return False
            if kind.startswith("numvariant:"):
                if name in _num_bases:
                    return any(n in reviewed_names for n, _ in _num_bases[name])
                return False
            return False

        def _make_bubble(classification: str, entries: list) -> FlexBubble:
            btn_contents = []
            for name, kind in entries:
                if kind.startswith("variant:") or kind.startswith("numvariant:"):
                    suffix = kind.split(":", 1)[1]
                    display = f"{name} ({suffix})"
                else:
                    display = name
                has_review = _entry_has_review(name, kind)
                text_color = "#4f46e5" if has_review else "#94a3b8"
                btn_contents.append(
                    FlexBox(
                        layout="vertical",
                        action=PostbackAction(label=display[:40], data=name),
                        contents=[FlexText(text=display, wrap=True, size="sm", color=text_color)],
                        padding_top="sm",
                        padding_bottom="sm",
                    )
                )
            return FlexBubble(
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
            )

        SPLIT_THRESHOLD = 10
        bubbles = []
        for cls, ents in all_groups:
            if cls == "教養(総合)":
                others = [(n, k) for n, k in ents if "GCP" not in n]
                gcps   = [(n, k) for n, k in ents if "GCP" in n]
                if others:
                    bubbles.append(_make_bubble("教養(総合)", others))
                if gcps:
                    bubbles.append(_make_bubble("教養(総合) GCP", gcps))
            elif len(ents) > SPLIT_THRESHOLD:
                mid = (len(ents) + 1) // 2
                bubbles.append(_make_bubble(cls + "①", ents[:mid]))
                bubbles.append(_make_bubble(cls + "②", ents[mid:]))
            else:
                bubbles.append(_make_bubble(cls, ents))

        alt = f"📚 {category}一覧" if category else "📚 科目一覧"
        if not bubbles:
            return [TextMessage(text="科目が登録されていません。")]

        # 12バブルずつ複数カルーセルに分割（LINE上限）、最大5メッセージ
        result = []
        for chunk in [bubbles[i:i+12] for i in range(0, min(len(bubbles), 60), 12)]:
            if len(chunk) == 1:
                result.append(FlexMessage(alt_text=alt, contents=chunk[0]))
            else:
                result.append(FlexMessage(alt_text=alt, contents=FlexCarousel(contents=chunk)))
        return result


# ── Message handler ─────────────────────────────────────────────

async def handle_message(text: str, user_id: str = "") -> list:
    t = text.strip()

    if t in ["科目一覧", "科目", "授業一覧", "一覧"]:
        return await handle_course_list()

    if t in ["教養", "教養科目", "教養一覧"]:
        cls_map = await _get_cls_order_map()
        _cls_sort = _make_cls_sort(cls_map)
        reviewed_names_edu, (_, _all_courses) = await asyncio.gather(
            _get_reviewed_cached(),
            _get_courses_cached(),
        )
        edu_courses = [c for c in _all_courses if c.category == "教養" and c.classification]
        clss = sorted({c.classification for c in edu_courses}, key=_cls_sort)
        reviewed_cls = {c.classification for c in edu_courses if c.name in reviewed_names_edu}
        if clss:
            return [make_classification_select_flex(clss, reviewed_cls)]
        return await handle_course_list(category="教養")

    if t.startswith("教養:"):
        cls = t[len("教養:"):]
        return await handle_course_list(category="教養", classification=cls)

    # 分類名の直接タップ（例：「教養(社会)」）
    if t in await _get_cls_set():
        return await handle_course_list(classification=t)

    if t == "専門comingsoon":
        return [TextMessage(text="🚧 専門科目一覧は現在準備中です。\nもうしばらくお待ちください！")]

    if t in ["専門科目", "専門", "専門一覧"]:
        return await handle_course_list(category="専門")

    if t in ["レビュー投稿", "レビュー", "投稿"] or "レビュー投稿" in t:
        url = f"{REVIEW_FORM_URL}?uid={user_id}" if user_id else REVIEW_FORM_URL
        return [TextMessage(text=f"📝 以下のフォームからレビューを投稿できます！\n\n{url}")]

    if t in ["ヘルプ", "help", "使い方", "？", "?"]:
        return [make_help_flex()]

    if t in ["問い合わせ", "連絡", "contact", "お問い合わせ"]:
        return [make_help_flex()]

    async with AsyncSessionLocal() as session:
        if t in ["人気の授業", "人気授業", "人気", "おすすめ"]:
            rows = (await session.execute(
                select(PendingReview.course_name, func.avg(PendingReview.rating).label("avg"))
                .where(
                    PendingReview.is_approved == True,
                    PendingReview.course_name.in_(select(Course.name)),
                )
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
                .where(
                    PendingReview.is_approved == True,
                    PendingReview.course_name.in_(select(Course.name)),
                )
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

        # キャッシュから取得（DBクエリ不要）
        _reviewed_names, (cbn, call) = await asyncio.gather(
            _get_reviewed_cached(),
            _get_courses_cached(),
        )

        # Exact course name match
        exact = cbn.get(t)
        if exact:
            if exact.name not in _reviewed_names:
                return [make_no_review_flex(exact, user_id)]
            return [await get_course_flex(exact, user_id)]

        # Seminar group e.g. 外国語セミナー(英語) → 外国語セミナーA(英語), B(英語)...
        _vsem_m = _re.match(r'^(.*?セミナー)(\([^)]+\))$', t)
        if _vsem_m:
            _sem_prefix, _sem_lang = _vsem_m.group(1), _vsem_m.group(2)
            _sem_pat = _re.compile(
                r'^' + _re.escape(_sem_prefix) + r'.+' + _re.escape(_sem_lang) + r'$', _re.IGNORECASE
            )
            _sem_courses = sorted([c for c in call if _sem_pat.match(c.name)], key=lambda c: c.name)
            if len(_sem_courses) == 1:
                return [await get_course_flex(_sem_courses[0], user_id)]
            if len(_sem_courses) >= 2:
                return [make_variant_selection_bubble(t, [c.name for c in _sem_courses], _reviewed_names)]

        # Variant group (A/B/C/D...)
        _variant_names_set = {t + s for s in ('A', 'B', 'C', 'D')}
        variant_courses = sorted([c for c in call if c.name in _variant_names_set], key=lambda c: c.name)
        if len(variant_courses) >= 2:
            return [make_variant_selection_bubble(t, [c.name for c in variant_courses], _reviewed_names)]

        # Numeric variant group (e.g. 「英語1」「英語2」or「第三外国語(ドイツ語)T1」)
        _num_pat = _re.compile(r'^' + _re.escape(t) + r'(?<!\d)[\s　]*[A-Z]?\d+$')
        _num_variants = sorted(
            [c for c in call if c.name.startswith(t) and _num_pat.match(c.name)],
            key=lambda c: int(_re.search(r'\d+', c.name[len(t):]).group()),
        )
        if len(_num_variants) >= 2:
            return [make_variant_selection_bubble(t, [c.name for c in _num_variants], _reviewed_names)]

        # Keyword search
        _PUNCT = '・･、。「」『』【】（）()／/〜~'
        def _normalize_q(s: str) -> str:
            for ch in _PUNCT:
                s = s.replace(ch, '')
            return s

        tokens = [tok for tok in _re.split(r'[\s　]+', t.strip()) if tok]
        def _escape(tok: str) -> str:
            return tok.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        stmt = select(Course)
        for tok in tokens:
            e = _escape(tok)
            stmt = stmt.where(or_(
                Course.name.ilike(f"%{e}%", escape="\\"),
                Course.reading.ilike(f"%{e}%", escape="\\"),
            ))
        courses = (await session.execute(stmt.limit(6))).scalars().all()

        # 句読点を除去した正規化検索（フォールバック）
        # ユーザーが・などを省略 → DB側の科目名を正規化して比較
        if not courses:
            norm_col = Course.name
            for ch in ('・', '･', '（', '）', '(', ')'):
                norm_col = func.replace(norm_col, ch, '')
            e_norm = _escape(_normalize_q(t))
            courses = (await session.execute(
                select(Course).where(norm_col.ilike(f"%{e_norm}%", escape="\\")).limit(6)
            )).scalars().all()
        if courses:
            # Letter variants (A/B/C/D)
            potential_bases = {
                c.name[:-1] for c in courses
                if c.name and c.name[-1] in ('A', 'B', 'C', 'D') and len(c.name) > 1
            }
            base_variants: dict[str, list[str]] = defaultdict(list)
            if potential_bases:
                all_variant_names = [b + s for b in potential_bases for s in ('A', 'B', 'C', 'D')]
                variant_rows = (await session.execute(
                    select(Course.name).where(Course.name.in_(all_variant_names)).order_by(Course.name)
                )).scalars().all()
                for vname in variant_rows:
                    base_variants[vname[:-1]].append(vname)

            # Numeric variants
            _kw_num_bases: dict[str, list[str]] = defaultdict(list)
            for c in courses:
                _m = _re.match(r'^(.*?)[\s　]*(\d+)$', c.name)
                if _m:
                    _kw_num_bases[_m.group(1).strip()].append(c.name)

            seen_base: set[str] = set()
            seen_num_base: set[str] = set()
            result = []
            for c in courses:
                name = c.name
                if name and name[-1] in ('A', 'B', 'C', 'D') and len(name) > 1:
                    base = name[:-1]
                    if base in seen_base:
                        continue
                    variants = base_variants.get(base, [])
                    if len(variants) >= 2:
                        seen_base.add(base)
                        result.append(make_variant_selection_bubble(base, variants, _reviewed_names))
                        continue
                _m2 = _re.match(r'^(.*?)[\s　]*[A-Z]?\d+$', name)
                if _m2:
                    base = _m2.group(1).strip()
                    if base in seen_num_base:
                        continue
                    num_vs = _kw_num_bases.get(base, [])
                    if len(num_vs) >= 2:
                        seen_num_base.add(base)
                        result.append(make_variant_selection_bubble(base, sorted(num_vs), _reviewed_names))
                        continue
                result.append(await get_course_flex(c, user_id))
            return result[:5]

    return [TextMessage(
        text=f"「{t}」に一致する科目が見つかりませんでした。\n\n「科目一覧」で登録科目を確認するか、「ヘルプ」で使い方をご確認ください。"
    )]


# ── Routes ──────────────────────────────────────────────────────

async def _process_events(events) -> None:
    try:
        if random.random() < 0.02:
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            async with AsyncSessionLocal() as session:
                await session.execute(delete(MessageLog).where(MessageLog.created_at < cutoff))
                await session.commit()
    except Exception as exc:
        await save_error_log(exc, action="cleanup")

    try:
        async with AsyncApiClient(configuration) as api_client:
            line_api = AsyncMessagingApi(api_client)
            for event in events:
                if isinstance(event, FollowEvent):
                    try:
                        await line_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[make_welcome_flex()],
                            )
                        )
                    except Exception as exc:
                        await save_error_log(exc, action="follow")
                    continue

                if isinstance(event, PostbackEvent):
                    user_id = event.source.user_id
                    data = event.postback.data
                    try:
                        asyncio.create_task(_save_log_bg(user_id, "in", f"[postback]{data}"))
                        messages = await asyncio.wait_for(
                            handle_message(data, user_id),
                            timeout=25.0,
                        )
                        await line_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=messages[:5],
                            )
                        )
                        asyncio.create_task(_save_log_bg(user_id, "out", f"[{len(messages)} msg(s)]"))
                    except asyncio.TimeoutError:
                        await save_error_log(Exception("handle_message timeout"), user_id=user_id, action=data)
                        try:
                            await line_api.reply_message(ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(text="処理に時間がかかりすぎました。もう一度お試しください。")],
                            ))
                        except Exception:
                            pass
                    except Exception as exc:
                        await save_error_log(exc, user_id=user_id, action=f"postback:{data}")
                        try:
                            await line_api.reply_message(ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(text="エラーが発生しました。もう一度お試しください。")],
                            ))
                        except Exception:
                            pass
                    continue

                if not isinstance(event, MessageEvent):
                    continue
                if not isinstance(event.message, TextMessageContent):
                    continue

                user_id = event.source.user_id
                user_text = event.message.text

                try:
                    asyncio.create_task(_save_log_bg(user_id, "in", user_text))
                    messages = await asyncio.wait_for(
                        handle_message(user_text, user_id),
                        timeout=25.0,
                    )
                    await line_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=messages[:5],
                        )
                    )
                    asyncio.create_task(_save_log_bg(user_id, "out", f"[{len(messages)} msg(s)]"))
                except asyncio.TimeoutError:
                    await save_error_log(Exception("handle_message timeout"), user_id=user_id, action=user_text)
                    try:
                        await line_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(text="処理に時間がかかりすぎました。もう一度お試しください。")],
                            )
                        )
                    except Exception:
                        pass
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
    except Exception as exc:
        await save_error_log(exc, action="process_events")


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

    task = asyncio.create_task(_process_events(events))
    task.add_done_callback(
        lambda t: t.exception() if not t.cancelled() and t.exception() else None
    )
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, uid: str = Query(default="")):
    is_new_user = False
    stored_name = ""
    stored_student_id = ""
    if uid:
        async with AsyncSessionLocal() as session:
            profile = (await session.execute(
                select(UserProfile).where(UserProfile.line_user_id == uid)
            )).scalar_one_or_none()
            is_new_user = profile is None
            if profile:
                stored_name = profile.name
                stored_student_id = profile.student_id
    response = templates.TemplateResponse(
        "form_index.html",
        {
            "request": request,
            "uid": uid,
            "is_new_user": is_new_user,
            "stored_name": stored_name,
            "stored_student_id": stored_student_id,
            "IS_DEV": IS_DEV,
        },
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


_FORM_PUNCT = '・･（）()'
def _normalize_form_q(s: str) -> str:
    for ch in _FORM_PUNCT:
        s = s.replace(ch, '')
    return s

@app.get("/api/courses")
async def search_courses(q: str = ""):
    async with AsyncSessionLocal() as session:
        if q.strip():
            tokens = [tok for tok in _re.split(r'[\s　]+', q.strip()) if tok]
            def _escape(tok: str) -> str:
                return tok.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            stmt = select(Course)
            for tok in tokens:
                t = _escape(tok)
                stmt = stmt.where(or_(
                    Course.name.ilike(f"%{t}%", escape="\\"),
                    Course.reading.ilike(f"%{t}%", escape="\\"),
                ))
            stmt = stmt.order_by(Course.name)
            courses = (await session.execute(stmt)).scalars().all()
            if not courses:
                norm_col = Course.name
                for ch in ('・', '･', '（', '）', '(', ')'):
                    norm_col = func.replace(norm_col, ch, '')
                norm_tokens = [_normalize_form_q(tok) for tok in tokens]
                stmt2 = select(Course)
                for tok in norm_tokens:
                    t = _escape(tok)
                    stmt2 = stmt2.where(norm_col.ilike(f"%{t}%", escape="\\"))
                courses = (await session.execute(stmt2.order_by(Course.name))).scalars().all()
        else:
            stmt = select(Course).order_by(Course.name).limit(30)
            courses = (await session.execute(stmt)).scalars().all()
        course_ids = [c.id for c in courses]
        instructors_raw = []
        if course_ids:
            instructors_raw = (await session.execute(
                select(CourseInstructor)
                .where(CourseInstructor.course_id.in_(course_ids))
                .order_by(CourseInstructor.name)
            )).scalars().all()
        insts_by_course: dict = {}
        for inst in instructors_raw:
            insts_by_course.setdefault(inst.course_id, []).append({"name": inst.name, "url": inst.url or ""})
    return {"courses": [
        {"id": c.id, "name": c.name, "instructors": insts_by_course.get(c.id, [])}
        for c in courses
    ]}


@app.get("/api/preload")
async def api_preload():
    async with AsyncSessionLocal() as session:
        courses_res, insts_res = await asyncio.gather(
            session.execute(select(Course).order_by(Course.name)),
            session.execute(select(CourseInstructor).order_by(CourseInstructor.name)),
        )
        courses = courses_res.scalars().all()
        insts_raw = insts_res.scalars().all()
    insts_by_course: dict = {}
    inst_courses: dict = {}
    course_by_id = {c.id: c.name for c in courses}
    for inst in insts_raw:
        insts_by_course.setdefault(inst.course_id, []).append({"name": inst.name})
        cname = course_by_id.get(inst.course_id)
        if cname:
            bucket = inst_courses.setdefault(inst.name, [])
            if not any(x["name"] == cname for x in bucket):
                bucket.append({"name": cname})
    course_list = [
        {"id": c.id, "name": c.name, "reading": c.reading or "", "instructors": insts_by_course.get(c.id, [])}
        for c in courses
    ]
    instructor_list = [
        {"name": name, "courses": clist}
        for name, clist in sorted(inst_courses.items())
    ]
    from fastapi.responses import JSONResponse
    res = JSONResponse({"courses": course_list, "instructors": instructor_list})
    res.headers["Cache-Control"] = "public, max-age=300"
    return res


@app.get("/api/instructors")
async def search_instructors(q: str = ""):
    if not q.strip():
        return {"instructors": []}
    async with AsyncSessionLocal() as session:
        def _esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        q_clean = q.replace("　", " ").strip()
        escaped = _esc(q_clean)
        insts_raw = (await session.execute(
            select(CourseInstructor.name)
            .where(CourseInstructor.name.ilike(f"%{escaped}%", escape="\\"))
            .distinct()
        )).scalars().all()
        insts = sorted(insts_raw, key=lambda n: (0 if n.lower().startswith(q_clean.lower()) else 1, n))
        if not insts:
            norm_col = CourseInstructor.name
            for ch in ('・', '･', '（', '）', '(', ')'):
                norm_col = func.replace(norm_col, ch, '')
            escaped_norm = _esc(_normalize_form_q(q_clean))
            insts_raw = (await session.execute(
                select(CourseInstructor.name)
                .where(norm_col.ilike(f"%{escaped_norm}%", escape="\\"))
                .distinct()
            )).scalars().all()
            insts = sorted(insts_raw, key=lambda n: (0 if n.lower().startswith(q_clean.lower()) else 1, n))

        result = []
        for name in insts:
            courses = (await session.execute(
                select(Course)
                .join(CourseInstructor, CourseInstructor.course_id == Course.id)
                .where(CourseInstructor.name == name)
                .order_by(Course.name)
            )).scalars().all()
            result.append({
                "name": name,
                "courses": [{"id": c.id, "name": c.name} for c in courses],
            })

        if not result:
            # Course.instructor フィールドからも検索
            fallback_courses = (await session.execute(
                select(Course)
                .where(Course.instructor.ilike(f"%{escaped}%", escape="\\"))
                .order_by(Course.name)
                .limit(10)
            )).scalars().all()
            instr_map: dict[str, list] = {}
            for c in fallback_courses:
                if c.instructor:
                    instr_map.setdefault(c.instructor, []).append({"id": c.id, "name": c.name})
            for instr_name, cs in instr_map.items():
                result.append({"name": instr_name, "courses": cs})

    return {"instructors": result}


@app.get("/api/autofill")
async def autofill_profile(uid: str = Query(default=""), student_id: str = Query(default="")):
    uid = uid.strip()
    sid = student_id.strip().upper()
    if not uid or not sid or not STUDENT_ID_RE.match(sid):
        return {"found": False}
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(UserProfile).where(UserProfile.line_user_id == uid)
        )).scalar_one_or_none()
        if existing:
            return {"found": True, "name": existing.name}
        row = (await session.execute(
            select(PendingReview.submitter_name)
            .where(PendingReview.student_id == sid)
            .order_by(PendingReview.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        if not row:
            return {"found": False}
        taken = (await session.execute(
            select(UserProfile.line_user_id).where(UserProfile.student_id == sid)
        )).scalar_one_or_none()
        if not taken:
            try:
                session.add(UserProfile(line_user_id=uid, name=row, student_id=sid))
                await session.commit()
            except Exception:
                await session.rollback()
        return {"found": True, "name": row}


@app.post("/submit")
async def submit(
    request: Request,
    submitter_name: str = Form(...),
    course_name: str = Form(...),
    rating: int = Form(...),
    ease_rating: str = Form(...),
    grading_method: str = Form(default=""),
    comment: str = Form(...),
    line_user_id: str = Form(default=""),
    reg_name: str = Form(default=""),
    student_id: str = Form(default=""),
    selected_instructor: str = Form(default=""),
    nickname: str = Form(default=""),
    academic_year: int = Form(default=0),
):
    def _form_error(msg: str):
        return templates.TemplateResponse(
            "form_error.html", {"request": request, "message": msg}, status_code=400
        )

    if not (1 <= rating <= 5):
        return _form_error("評価が不正です")
    if ease_rating not in ("SS", "S", "A", "B", "C"):
        return _form_error("楽単度が不正です")
    if not (2000 <= academic_year <= 2100):
        return _form_error("受講年度を選択してください")
    if not comment.strip():
        return _form_error("コメントを入力してください")

    sid = student_id.strip().upper()
    if not STUDENT_ID_RE.match(sid):
        return _form_error("学籍番号の形式が正しくありません（例：2345678S、医学部は2345678MM）")

    async with AsyncSessionLocal() as session:
        course_exists = (await session.execute(
            select(Course.id).where(Course.name == course_name.strip())
        )).scalar_one_or_none()
        if not course_exists:
            return _form_error("指定された科目が見つかりません")

        uid = line_user_id.strip()
        if uid:
            existing = (await session.execute(
                select(UserProfile).where(UserProfile.line_user_id == uid)
            )).scalar_one_or_none()
            if existing is None:
                if not reg_name.strip():
                    return _form_error("お名前を入力してください")
                try:
                    session.add(UserProfile(
                        line_user_id=uid,
                        name=reg_name.strip()[:100],
                        student_id=sid,
                    ))
                    await session.flush()
                except Exception:
                    await session.rollback()
            else:
                if existing.student_id != sid:
                    return _form_error("学籍番号が登録情報と一致しません")

        review = PendingReview(
            submitter_name=submitter_name.strip()[:20],
            course_name=course_name.strip()[:200],
            rating=rating,
            ease_rating=ease_rating,
            grading_method=grading_method.strip()[:500] or None,
            comment=comment.strip()[:500],
            selected_instructor=selected_instructor.strip()[:100] or None,
            nickname=nickname.strip()[:30] or None,
            academic_year=academic_year,
            student_id=sid or None,
            is_approved=False,
        )
        session.add(review)
        await session.commit()

    await send_push_notification(
        course_name=course_name.strip(),
        rating=rating,
        ease_rating=ease_rating,
        comment=comment.strip(),
    )

    return templates.TemplateResponse(
        "form_success.html", {"request": request, "course_name": course_name}
    )


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
async def admin_courses(request: Request, _: str = Depends(check_admin), msg: str = "", q: str = Query(default=""), category: str = Query(default="")):
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
        if category:
            base_stmt = base_stmt.where(Course.category == category)
        if q:
            base_stmt = base_stmt.where(_search_filter(q))

        courses = (await session.execute(
            base_stmt.order_by(Course.sort_order, Course.name)
        )).scalars().all()
        cls_map = await _get_cls_order_map(session)
        _cls_sort = _make_cls_sort(cls_map)
        courses = sorted(courses, key=lambda c: (_cls_sort(c.classification or ""), c.sort_order, c.name or ""))
        total = len(courses)
        classifications = (await session.execute(
            select(Course.classification).distinct().order_by(Course.classification)
        )).scalars().all()
        class_counts_raw = dict((await session.execute(
            select(Course.classification, func.count(Course.id))
            .where(Course.classification != "")
            .group_by(Course.classification)
            .order_by(Course.classification)
        )).all())
        class_counts = {k: class_counts_raw[k] for k in sorted(class_counts_raw, key=_cls_sort)}

        course_ids = [c.id for c in courses]
        course_names = [c.name for c in courses]

        if course_ids:
            instructors_raw = (await session.execute(
                select(CourseInstructor).where(CourseInstructor.course_id.in_(course_ids))
            )).scalars().all()
        else:
            instructors_raw = []

        if course_names:
            reviews_raw = (await session.execute(
                select(PendingReview)
                .where(PendingReview.course_name.in_(course_names))
                .order_by(PendingReview.is_approved, PendingReview.created_at.desc())
                .limit(500)
            )).scalars().all()
        else:
            reviews_raw = []

    existing = sorted([c for c in classifications if c], key=_cls_sort)
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

    instructors_by_course: dict = defaultdict(list)
    for inst in sorted(instructors_raw, key=lambda i: i.name):
        instructors_by_course[inst.course_id].append(inst)

    reviews_by_course: dict = defaultdict(list)
    for r in reviews_raw:
        reviews_by_course[r.course_name].append(r)

    # groupby順を保持するため事前グループ化
    grouped_courses: dict = defaultdict(list)
    for c in courses:
        grouped_courses[c.classification or "（未分類）"].append(c)

    return templates.TemplateResponse("admin/courses.html", {
        "request": request,
        "courses": courses,
        "grouped_courses": list(grouped_courses.items()),
        "active_category": category,
        "classifications": existing,
        "class_counts": class_counts,
        "courses_data": courses_data,
        "reviews_by_course": reviews_by_course,
        "instructors_by_course": instructors_by_course,
        "error": msg,
        "total": total,
        "q": q,
    })


@app.post("/admin/courses/{course_id}/instructors/add")
async def add_instructor(course_id: int, request: Request, name: str = Form(...), url: str = Form(""), _: str = Depends(check_admin)):
    from fastapi.responses import JSONResponse
    name_s = _normalize_instructor_name(name.strip())
    url_s = url.strip() or None
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if name_s:
        async with AsyncSessionLocal() as session:
            existing = (await session.execute(
                select(CourseInstructor).where(
                    CourseInstructor.course_id == course_id,
                    CourseInstructor.name == name_s,
                )
            )).scalar_one_or_none()
            if existing:
                if is_ajax:
                    return JSONResponse({"ok": False, "error": "duplicate"})
                referer = request.headers.get("Referer", "/admin/courses")
                sep = "&" if "?" in referer else "?"
                return RedirectResponse(f"{referer}{sep}inst_err={course_id}", status_code=303)
            inst = CourseInstructor(course_id=course_id, name=name_s, url=url_s)
            session.add(inst)
            await session.commit()
            await session.refresh(inst)
            if is_ajax:
                return JSONResponse({"ok": True, "id": inst.id, "name": inst.name, "url": inst.url or ""})
    if is_ajax:
        return JSONResponse({"ok": False, "error": "empty"})
    return RedirectResponse(request.headers.get("Referer", "/admin/courses"), status_code=303)


@app.post("/admin/courses/{course_id}/instructors/delete/{instructor_id}")
async def delete_instructor(course_id: int, instructor_id: int, request: Request, _: str = Depends(check_admin)):
    from fastapi.responses import JSONResponse
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    async with AsyncSessionLocal() as session:
        inst = await session.get(CourseInstructor, instructor_id)
        if inst:
            await session.delete(inst)
            await session.commit()
    if is_ajax:
        return JSONResponse({"ok": True})
    return RedirectResponse(request.headers.get("Referer", "/admin/courses"), status_code=303)


@app.post("/admin/reviews/cleanup")
async def admin_reviews_cleanup(_: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        course_names = (await session.execute(select(Course.name))).scalars().all()
        await session.execute(
            delete(PendingReview).where(PendingReview.course_name.not_in(course_names))
        )
        await session.commit()
    return RedirectResponse("/admin/courses", status_code=303)


@app.post("/admin/courses/migrate-third-language")
async def migrate_third_language(_: str = Depends(check_admin)):
    LANGS = ["ドイツ語", "フランス語"]
    NUMS = [1, 2, 3, 4]
    async with AsyncSessionLocal() as session:
        to_delete = (await session.execute(
            select(Course).where(Course.name.contains("第三外国語"))
        )).scalars().all()
        for c in to_delete:
            await session.execute(delete(PendingReview).where(PendingReview.course_name == c.name))
            await session.execute(delete(CourseInstructor).where(CourseInstructor.course_id == c.id))
            await session.delete(c)
        for lang in LANGS:
            for n in NUMS:
                name = f"第三外国語({lang})T{n}"
                existing = (await session.execute(
                    select(Course).where(Course.name == name)
                )).scalar_one_or_none()
                if not existing:
                    session.add(Course(
                        name=name, instructor="",
                        classification="外国語", category="教養",
                        reading=_reading(name),
                    ))
        await session.commit()
    return RedirectResponse("/admin/courses", status_code=303)


@app.post("/admin/courses/strip-trailing-numbers")
async def strip_trailing_numbers(
    request: Request,
    _: str = Depends(check_admin),
    prefix: str = Form(default=""),
):
    import re as _re2
    async with AsyncSessionLocal() as session:
        stmt = select(Course)
        if prefix.strip():
            stmt = stmt.where(Course.name.contains(prefix.strip()))
        courses = (await session.execute(stmt)).scalars().all()

        groups: dict[str, list] = defaultdict(list)
        for course in courses:
            base = _re2.sub(r'[\s　]*\d+$', '', course.name).strip()
            if base != course.name:
                groups[base].append(course)

        for base_name, dups in groups.items():
            existing = (await session.execute(
                select(Course).where(Course.name == base_name)
            )).scalar_one_or_none()

            if existing:
                survivor_id = existing.id
            else:
                survivor = dups[0]
                survivor.name = base_name
                survivor.reading = _reading(base_name)
                survivor_id = survivor.id
                dups = dups[1:]

            for dup in dups:
                await session.execute(
                    sa_update(PendingReview)
                    .where(PendingReview.course_name == dup.name)
                    .values(course_name=base_name)
                )
                await session.execute(
                    delete(CourseInstructor).where(CourseInstructor.course_id == dup.id)
                )
                await session.delete(dup)

        await session.commit()
    return RedirectResponse("/admin/courses", status_code=303)


@app.get("/admin/reviews", response_class=HTMLResponse)
async def admin_reviews(request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        pending = (await session.execute(
            select(PendingReview)
            .where(PendingReview.is_approved == False)
            .order_by(PendingReview.created_at.desc())
        )).scalars().all()
        approved = (await session.execute(
            select(PendingReview)
            .where(PendingReview.is_approved == True)
            .order_by(PendingReview.created_at.desc())
            .limit(50)
        )).scalars().all()
    return templates.TemplateResponse("admin/reviews.html", {
        "request": request,
        "pending": pending,
        "approved": approved,
    })


@app.post("/admin/reviews/approve/{review_id}")
async def admin_review_approve(review_id: int, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        review = await session.get(PendingReview, review_id)
        if review:
            review.is_approved = True
            await session.commit()
    return RedirectResponse("/admin/reviews", status_code=303)


@app.post("/admin/reviews/reject/{review_id}")
async def admin_review_reject(review_id: int, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        review = await session.get(PendingReview, review_id)
        if review:
            await session.delete(review)
            await session.commit()
    return RedirectResponse("/admin/reviews", status_code=303)


@app.post("/admin/courses/add")
async def admin_courses_add(
    _: str = Depends(check_admin),
    name: str = Form(...),
    classification: str = Form(""),
    category: str = Form("専門"),
    term: str = Form(""),
    credits: float = Form(0),
    syllabus_url: str = Form(""),
):
    name_s = name.strip()
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(Course).where(Course.name == name_s)
        )).scalar_one_or_none()
        if existing:
            return RedirectResponse(
                url=f"/admin/courses?error={py_secrets.token_urlsafe(4)}&msg=duplicate",
                status_code=303,
            )
        session.add(Course(
            name=name_s, instructor="",
            classification=classification.strip(), category=category,
            reading=_reading(name_s),
            term=term.strip() or None,
            credits=credits if credits else None,
            syllabus_url=syllabus_url.strip() or None,
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

    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "users": users,
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


@app.post("/admin/courses/classification/move")
async def admin_cls_move(request: Request, _=Depends(check_admin)):
    from fastapi.responses import JSONResponse
    data = await request.json()
    name = data.get("name", "")
    direction = data.get("direction", "")
    if not name or direction not in ("up", "down"):
        return JSONResponse({"ok": False})

    async with AsyncSessionLocal() as session:
        all_cls = sorted(
            [c for c in (await session.execute(
                select(Course.classification).distinct()
            )).scalars().all() if c],
        )
        cls_map = await _get_cls_order_map(session)
        _cls_sort = _make_cls_sort(cls_map)
        sorted_cls = sorted(all_cls, key=_cls_sort)

        try:
            idx = sorted_cls.index(name)
        except ValueError:
            return JSONResponse({"ok": False})

        delta = -1 if direction == "up" else 1
        swap_idx = idx + delta
        if swap_idx < 0 or swap_idx >= len(sorted_cls):
            return JSONResponse({"ok": True})

        sorted_cls[idx], sorted_cls[swap_idx] = sorted_cls[swap_idx], sorted_cls[idx]

        for i, cls_name in enumerate(sorted_cls):
            existing = (await session.execute(
                select(ClassificationOrder).where(ClassificationOrder.name == cls_name)
            )).scalar_one_or_none()
            if existing:
                existing.sort_order = i
            else:
                session.add(ClassificationOrder(name=cls_name, sort_order=i))
        await session.commit()
    return JSONResponse({"ok": True})


@app.post("/admin/courses/{course_id}/move")
async def admin_course_move(course_id: int, request: Request, _=Depends(check_admin)):
    from fastapi.responses import JSONResponse
    data = await request.json()
    direction = data.get("direction", "")
    if direction not in ("up", "down"):
        return JSONResponse({"ok": False})

    async with AsyncSessionLocal() as session:
        course = await session.get(Course, course_id)
        if not course:
            return JSONResponse({"ok": False})

        all_in_cls = list((await session.execute(
            select(Course)
            .where(Course.classification == (course.classification or ""))
            .order_by(Course.sort_order, Course.name)
        )).scalars().all())

        try:
            idx = next(i for i, c in enumerate(all_in_cls) if c.id == course_id)
        except StopIteration:
            return JSONResponse({"ok": False})

        delta = -1 if direction == "up" else 1
        swap_idx = idx + delta
        if swap_idx < 0 or swap_idx >= len(all_in_cls):
            return JSONResponse({"ok": True})

        all_in_cls[idx], all_in_cls[swap_idx] = all_in_cls[swap_idx], all_in_cls[idx]
        for i, c in enumerate(all_in_cls):
            c.sort_order = i
        await session.commit()
    return JSONResponse({"ok": True})


@app.post("/admin/courses/update/{course_id}")
async def admin_courses_update(
    course_id: int,
    _: str = Depends(check_admin),
    name: str = Form(...),
    classification: str = Form(""),
    category: str = Form("専門"),
    term: str = Form(""),
    credits: float = Form(0),
    syllabus_url: str = Form(""),
):
    async with AsyncSessionLocal() as session:
        course = (await session.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
        if course:
            old_name = course.name
            new_name = name.strip()
            course.name = new_name
            course.classification = classification.strip()
            course.category = category
            course.reading = _reading(new_name)
            course.term = term.strip() or None
            course.credits = credits if credits else None
            course.syllabus_url = syllabus_url.strip() or None
            if old_name != new_name:
                await session.execute(
                    sa_update(PendingReview)
                    .where(PendingReview.course_name == old_name)
                    .values(course_name=new_name)
                )
            await session.commit()
    return RedirectResponse(url="/admin/courses", status_code=303)


@app.post("/admin/courses/delete/{course_id}")
async def admin_courses_delete(course_id: int, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        course = (await session.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
        if course:
            await session.execute(delete(PendingReview).where(PendingReview.course_name == course.name))
            await session.execute(delete(CourseInstructor).where(CourseInstructor.course_id == course_id))
            await session.delete(course)
            await session.commit()
    return RedirectResponse(url="/admin/courses", status_code=303)


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})


@app.get("/liff/course", response_class=HTMLResponse)
async def liff_course(request: Request):
    try:
        return templates.TemplateResponse("liff/course.html", {
            "request": request,
            "liff_id": LIFF_ID,
            "review_form_url": REVIEW_FORM_URL,
            "base_url": APP_URL,
            "IS_DEV": IS_DEV,
        })
    except Exception as exc:
        await save_error_log(exc, action="liff_course")
        raise


@app.get("/api/course/{course_id}")
async def api_course(course_id: int):
    try:
        async with AsyncSessionLocal() as session:
            course = (await session.execute(
                select(Course).where(Course.id == course_id)
            )).scalar_one_or_none()
            if not course:
                raise HTTPException(status_code=404, detail="course not found")

            async def _instrs():
                async with AsyncSessionLocal() as s:
                    return (await s.execute(
                        select(CourseInstructor).where(CourseInstructor.course_id == course.id)
                    )).scalars().all()

            async def _agg():
                async with AsyncSessionLocal() as s:
                    return (await s.execute(
                        select(func.avg(PendingReview.rating), func.count(PendingReview.id))
                        .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
                    )).first()

            async def _ease():
                async with AsyncSessionLocal() as s:
                    return (await s.execute(
                        select(PendingReview.ease_rating, func.count(PendingReview.id))
                        .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
                        .group_by(PendingReview.ease_rating)
                    )).all()

            async def _reviews():
                async with AsyncSessionLocal() as s:
                    return (await s.execute(
                        select(PendingReview)
                        .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
                        .limit(50)
                    )).scalars().all()

            async def _record_view():
                async with AsyncSessionLocal() as s:
                    await s.execute(
                        pg_insert(CourseView).values(
                            course_id=course.id,
                            course_name=course.name,
                            view_count=1,
                            last_viewed_at=datetime.now(timezone.utc),
                        ).on_conflict_do_update(
                            index_elements=["course_id"],
                            set_={"view_count": CourseView.view_count + 1,
                                  "last_viewed_at": datetime.now(timezone.utc)},
                        )
                    )
                    await s.commit()

            instructors, agg, ease_rows, reviews_raw, _ = await asyncio.gather(
                _instrs(), _agg(), _ease(), _reviews(), _record_view()
            )
            instructor_str = "・".join(i.name for i in instructors) or course.instructor or ""
            avg_rating = float(agg[0]) if agg and agg[0] else None
            top_ease = None
            if ease_rows:
                top_ease = sorted(ease_rows, key=lambda r: (-r[1], EASE_ORDER.get(r[0], 99)))[0][0]
            reviews = sorted(
                reviews_raw,
                key=lambda r: (r.selected_instructor or "￿", -(r.academic_year or 0))
            )[:20]

            return {
                "id": course.id,
                "name": course.name,
                "instructor": instructor_str,
                "classification": course.classification or "",
                "category": course.category or "",
                "term": getattr(course, "term", None) or "",
                "credits": getattr(course, "credits", None) or 0,
                "syllabus_url": course.syllabus_url or "",
                "avg_rating": avg_rating,
                "top_ease": top_ease,
                "reviews": [
                    {
                        "rating": r.rating,
                        "ease_rating": r.ease_rating,
                        "grading_method": r.grading_method or "",
                        "comment": r.comment,
                        "instructor": r.selected_instructor or "",
                        "nickname": r.nickname or "",
                        "academic_year": r.academic_year or 0,
                    }
                    for r in reviews
                ],
            }
    except HTTPException:
        raise
    except Exception as exc:
        await save_error_log(exc, action=f"api_course/{course_id}")
        raise


_RICHMENU_URLS: dict[str, str] = {
    "review":    REVIEW_FORM_URL,
    "beefplus":  "https://beefplus.center.kobe-u.ac.jp/login",
    "uribop":    "https://www.uriboportal.ofc.kobe-u.ac.jp/",
    "shokudo":   "https://west2-univ.jp/sp/kobe-univ.php",
    "toshokan":  "https://lib.kobe-u.ac.jp/services/barcode/",
    "bus":       "https://kotsu.city.kobe.lg.jp/",
    "kyoyoin":   "https://www.iphe.kobe-u.ac.jp/general-education-courses/",
}

@app.get("/r/{name}")
async def richmenu_redirect(name: str):
    url = _RICHMENU_URLS.get(name)
    if not url:
        raise HTTPException(status_code=404)
    async with AsyncSessionLocal() as session:
        session.add(RichMenuTap(button=name))
        await session.commit()
    return RedirectResponse(url=url, status_code=302)


@app.get("/admin/richmenu-stats")
async def admin_richmenu_stats(request: Request, _=Depends(check_admin)):
    RICHMENU_LABELS = {
        "review":    "レビューを投稿",
        "beefplus":  "BEEFplus",
        "uribop":    "うりぼーポータル",
        "shokudo":   "食堂メニュー",
        "toshokan":  "図書館スマホ入館",
        "bus":       "市バス時刻表",
        "kyoyoin":   "教養教育院",
    }
    MSG_LABELS = {
        "レビュー投稿": "レビューを投稿",
        "教養":         "教養科目一覧",
        "専門comingsoon": "専門（Coming Soon）",
        "ヘルプ":       "ヘルプ",
    }
    async with AsyncSessionLocal() as session:
        uri_rows = (await session.execute(
            select(RichMenuTap.button, func.count(RichMenuTap.id).label("cnt"))
            .group_by(RichMenuTap.button)
            .order_by(func.count(RichMenuTap.id).desc())
        )).all()
        msg_rows = (await session.execute(
            select(UserActivity.action, func.sum(UserActivity.count).label("cnt"))
            .where(UserActivity.action.in_(list(MSG_LABELS.keys())))
            .group_by(UserActivity.action)
            .order_by(func.sum(UserActivity.count).desc())
        )).all()

    uri_stats = [{"label": RICHMENU_LABELS.get(r.button, r.button), "count": r.cnt} for r in uri_rows]
    msg_stats = [{"label": MSG_LABELS.get(r.action, r.action), "count": int(r.cnt or 0)} for r in msg_rows]
    all_stats = uri_stats + msg_stats
    max_count = max((s["count"] for s in all_stats), default=1)

    return templates.TemplateResponse("admin/richmenu.html", {
        "request": request,
        "uri_stats": uri_stats,
        "msg_stats": msg_stats,
        "max_count": max_count,
        "IS_DEV": IS_DEV,
        "VAPID_PUBLIC_KEY": VAPID_PUBLIC_KEY,
    })


@app.get("/admin/usage-stats")
async def admin_usage_stats(request: Request, _=Depends(check_admin)):
    RICHMENU_LABELS = {
        "review":   "レビューを投稿",
        "beefplus": "BEEFplus",
        "uribop":   "うりぼーポータル",
        "shokudo":  "食堂メニュー",
        "toshokan": "図書館スマホ入館",
        "bus":      "市バス時刻表",
        "kyoyoin":  "教養教育院",
    }
    MSG_BTN_LABELS = {
        "教養":           "教養科目一覧",
        "専門comingsoon": "専門（Coming Soon）",
        "ヘルプ":         "ヘルプ",
    }
    async with AsyncSessionLocal() as session:
        uri_rows, msg_btn_rows, activity_joined, course_view_rows = await asyncio.gather(
            session.execute(
                select(RichMenuTap.button, func.count(RichMenuTap.id).label("cnt"))
                .group_by(RichMenuTap.button)
                .order_by(func.count(RichMenuTap.id).desc())
            ),
            session.execute(
                select(UserActivity.action, func.sum(UserActivity.count).label("cnt"))
                .where(UserActivity.action.in_(list(MSG_BTN_LABELS.keys())))
                .group_by(UserActivity.action)
                .order_by(func.sum(UserActivity.count).desc())
            ),
            session.execute(
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
            ),
            session.execute(
                select(CourseView).order_by(CourseView.view_count.desc())
            ),
        )
        uri_rows = uri_rows.all()
        msg_btn_rows = msg_btn_rows.all()
        activity_joined = activity_joined.all()
        course_view_rows = course_view_rows.scalars().all()

    uri_stats = [
        {"label": RICHMENU_LABELS.get(r.button, r.button), "count": r.cnt}
        for r in uri_rows
    ]
    msg_btn_stats = [
        {"label": MSG_BTN_LABELS.get(r.action, r.action), "count": int(r.cnt or 0)}
        for r in msg_btn_rows
    ]

    action_totals: dict[str, int] = defaultdict(int)
    for row in activity_joined:
        action_totals[row.action] += row.count
    msg_ranking = sorted(action_totals.items(), key=lambda x: x[1], reverse=True)[:20]

    all_bar_counts = [s["count"] for s in uri_stats] + [s["count"] for s in msg_btn_stats] + [c for _, c in msg_ranking]
    max_bar = max(all_bar_counts, default=1)

    return templates.TemplateResponse("admin/usage_stats.html", {
        "request": request,
        "uri_stats": uri_stats,
        "msg_btn_stats": msg_btn_stats,
        "msg_ranking": msg_ranking,
        "activity_rows": activity_joined,
        "course_view_rows": course_view_rows,
        "max_bar": max_bar,
        "IS_DEV": IS_DEV,
        "VAPID_PUBLIC_KEY": VAPID_PUBLIC_KEY,
    })


@app.get("/health")
@app.head("/health")
async def health():
    return {"status": "healthy", "version": "88cf130"}
