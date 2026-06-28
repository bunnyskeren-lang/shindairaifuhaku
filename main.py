import os
import io
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
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import FastAPI, Request, HTTPException, Depends, Form, Query, UploadFile, File
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
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

from sqlalchemy import select, func, delete, or_, and_, update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, AsyncSessionLocal, engine
from models import (
    MessageLog, Subject, Review, UserProfile, UserActivity, ErrorLog,
    PushSubscription, Instructor, CourseSection, ClassificationOrder,
    RichMenuTap, CourseSectionView, Syllabus, Schedule, UserSyllabus,
    CreditRequirement, SubjectCreditCategory, UserSeisekiRaw,
    TimetableProfile,
)

from dotenv import load_dotenv
load_dotenv()

try:
    import pdfplumber as _pdfplumber
    _PDFPLUMBER_OK = True
except ImportError:
    _PDFPLUMBER_OK = False

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or ""
if not ADMIN_PASSWORD:
    raise RuntimeError("環境変数 ADMIN_PASSWORD が未設定です")
REVIEW_FORM_URL = os.environ.get("REVIEW_FORM_URL", "https://shindairaifuhaku.onrender.com")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_EMAIL = os.environ.get("VAPID_EMAIL", "admin@example.com")
SELF_URL = os.environ.get("SELF_URL", "").rstrip("/")
LIFF_ID = os.environ.get("LIFF_ID", "2010406205-emxo5rhE")
TIMETABLE_LIFF_ID = os.environ.get("TIMETABLE_LIFF_ID", "")
try:
    KYOYO_REQUIRED_CREDITS = int(os.environ.get("KYOYO_REQUIRED_CREDITS", "1"))
except ValueError:
    KYOYO_REQUIRED_CREDITS = 1
APP_URL = os.environ.get("APP_URL", "https://shindairaifuhaku.onrender.com")
STUDENT_ID_RE = _re.compile(r'^\d{7}(MM|ME|MH|[LHJEBSTAZX])$')
_LINE_USER_ID_RE = _re.compile(r'^U[0-9a-f]{32}$')

_SYLLABUS_FACULTY_PATH = {"U": "20", "B": "06", "X": "15"}

def _make_syllabus_url(timetable_code: str) -> str:
    if not timetable_code or len(timetable_code) < 2:
        return ""
    path = _SYLLABUS_FACULTY_PATH.get(timetable_code[1].upper(), "")
    if not path:
        return ""
    return f"https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026/{path}/data/2026_{timetable_code}.html"

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)
JST = timezone(timedelta(hours=9))

_line_api_client: AsyncApiClient | None = None
_line_api: AsyncMessagingApi | None = None

ADMIN_COOKIE = "admin_tok"
ADMIN_TOKEN_TTL = 4 * 3600
_HMAC_KEY = hashlib.sha256((CHANNEL_SECRET + ADMIN_PASSWORD).encode()).digest()

def _make_admin_token() -> str:
    ts = int(datetime.now(timezone.utc).timestamp())
    nonce = py_secrets.token_hex(8)
    sig = hmac.new(_HMAC_KEY, f"admin:{ts}:{nonce}".encode(), hashlib.sha256).hexdigest()
    return f"{ts}:{nonce}:{sig}"

def _verify_admin_token(token: str) -> bool:
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return False
        ts_str, nonce, sig = parts
        ts = int(ts_str)
        if datetime.now(timezone.utc).timestamp() - ts > ADMIN_TOKEN_TTL:
            return False
        expected = hmac.new(_HMAC_KEY, f"admin:{ts_str}:{nonce}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False

def check_admin(request: Request):
    if not _verify_admin_token(request.cookies.get(ADMIN_COOKIE, "")):
        raise HTTPException(status_code=302, headers={"Location": f"/admin/login?next={request.url.path}"})

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
_cls_parent_map_cache: dict = {}
_cls_parent_map_at: float = 0.0

async def _get_cls_order_map() -> dict:
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

async def _get_cls_parent_map() -> dict[str, str]:
    global _cls_parent_map_cache, _cls_parent_map_at
    if _cls_parent_map_cache and time.monotonic() - _cls_parent_map_at < _CLS_CACHE_TTL:
        return _cls_parent_map_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(ClassificationOrder.name, ClassificationOrder.parent_group)
            .where(ClassificationOrder.parent_group.isnot(None))
            .where(ClassificationOrder.parent_group != "")
        )).all()
    _cls_parent_map_cache = {r.name: r.parent_group for r in rows}
    _cls_parent_map_at = time.monotonic()
    return _cls_parent_map_cache

def _invalidate_cls_caches():
    global _cls_order_map_cache, _cls_order_map_at, _cls_parent_map_cache, _cls_parent_map_at
    global _cls_cache, _cls_cache_at
    _cls_order_map_cache = {}
    _cls_order_map_at = 0.0
    _cls_parent_map_cache = {}
    _cls_parent_map_at = 0.0
    _cls_cache = set()
    _cls_cache_at = 0.0

def _invalidate_courses_cache():
    global _course_by_name, _course_list_all, _course_cache_at
    global _all_instructors_cache, _all_instructors_cache_at
    global _course_flex_cache, _course_list_cache, _ranking_cache
    _course_by_name = {}
    _course_list_all = []
    _course_cache_at = 0.0
    _all_instructors_cache = {}
    _all_instructors_cache_at = 0.0
    _course_flex_cache = {}
    _course_list_cache = {}
    _ranking_cache = {}

# senmon_group キャッシュ（PDFパーサーが同期的に参照する）
_senmon_name_to_group: dict[str, str] = {}

async def _reload_senmon_cache():
    global _senmon_name_to_group
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Subject.name, Subject.senmon_group).where(Subject.senmon_group.isnot(None))
        )).all()
    _senmon_name_to_group = {r[0]: r[1] for r in rows}

def _invalidate_senmon_cache():
    global _senmon_name_to_group
    _senmon_name_to_group = {}


def _invalidate_review_cache():
    global _reviewed_cache, _reviewed_cache_at, _reviewed_cache_init
    global _all_review_stats_cache, _all_review_stats_cache_at
    global _course_flex_cache, _ranking_cache, _course_list_cache
    _reviewed_cache = set()
    _reviewed_cache_at = 0.0
    _reviewed_cache_init = False
    _all_review_stats_cache = {}
    _all_review_stats_cache_at = 0.0
    _course_flex_cache = {}
    _ranking_cache = {}
    _course_list_cache = {}


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
            else:
                await save_error_log(e, action="push_notification")
        except Exception as e:
            await save_error_log(e, action="push_notification")
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
    global _line_api_client, _line_api
    try:
        await init_db()
        print("DB OK", flush=True)
        asyncio.create_task(_reload_senmon_cache())
        asyncio.create_task(_prewarm_caches())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"DB ERROR: {e}", flush=True)
        await engine.dispose()
        print("Engine disposed and reset after startup error", flush=True)
    _line_api_client = AsyncApiClient(configuration)
    _line_api = AsyncMessagingApi(_line_api_client)
    ping_task = asyncio.create_task(_self_ping())
    yield
    ping_task.cancel()
    try:
        await ping_task
    except asyncio.CancelledError:
        pass
    if _line_api_client:
        await _line_api_client.close()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://liff.line.me", "https://access.line.me"],
    allow_origin_regex=r"https://.*\.line\.me",
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    await save_error_log(exc, action=f"validation:{request.method} {request.url.path}")
    return _JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code >= 500:
        await save_error_log(exc, action=f"HTTP{exc.status_code} {request.method} {request.url.path}")
    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    await save_error_log(exc, action=f"{request.method} {request.url.path}")
    return _JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, next: str = "/admin"):
    return templates.TemplateResponse("admin/login.html", {"request": request, "next": next, "error": False})


@app.post("/admin/login")
async def admin_login(request: Request, password: str = Form(...), next: str = Form(default="/admin")):
    if not py_secrets.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
        return templates.TemplateResponse("admin/login.html", {"request": request, "next": next, "error": True})
    safe_next = next if (next.startswith("/admin") and ".." not in next) else "/admin"
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
_CLS_CACHE_TTL = 3600

async def _get_cls_set() -> set[str]:
    global _cls_cache, _cls_cache_at
    if _cls_cache and time.monotonic() - _cls_cache_at < _CLS_CACHE_TTL:
        return _cls_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(Subject.classification).distinct())).scalars().all()
    _cls_cache = {r for r in rows if r}
    _cls_cache_at = time.monotonic()
    return _cls_cache


# ── In-memory course & review cache ─────────────────────────────
_COURSE_CACHE_TTL = 3600
_course_by_name: dict[str, Any] = {}
_course_list_all: list = []
_course_cache_at: float = 0.0

_reviewed_cache: set[str] = set()
_reviewed_cache_at: float = 0.0
_reviewed_cache_init: bool = False

_course_flex_cache: dict[int, tuple] = {}
_course_list_cache: dict[str, tuple] = {}
_ranking_cache: dict[str, tuple] = {}
_COURSE_FLEX_TTL = 3600
_COURSE_LIST_TTL = 3600
_RANKING_TTL = 3600

_syllabus_url_cache: dict[int, str] = {}
_syllabus_url_cache_at: float = 0.0

_all_instructors_cache: dict[int, list] = {}
_all_instructors_cache_at: float = 0.0
_all_review_stats_cache: dict[str, tuple] = {}
_all_review_stats_cache_at: float = 0.0

async def _get_courses_cached():
    global _course_by_name, _course_list_all, _course_cache_at
    if _course_by_name and time.monotonic() - _course_cache_at < _COURSE_CACHE_TTL:
        return _course_by_name, _course_list_all
    async with AsyncSessionLocal() as s:
        courses = (await s.execute(
            select(Subject).order_by(Subject.sort_order, Subject.name)
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
            select(Subject.name).distinct()
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.is_approved == True)
        )).scalars().all()
    _reviewed_cache = set(rows)
    _reviewed_cache_at = time.monotonic()
    _reviewed_cache_init = True
    return _reviewed_cache


async def _get_all_instructors_cached() -> dict[int, list]:
    global _all_instructors_cache, _all_instructors_cache_at
    if _all_instructors_cache and time.monotonic() - _all_instructors_cache_at < _COURSE_CACHE_TTL:
        return _all_instructors_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(CourseSection, Instructor)
            .join(Instructor, Instructor.id == CourseSection.instructor_id)
        )).all()
    d: dict[int, list] = {}
    for cs, instr in rows:
        d.setdefault(cs.subject_id, []).append(instr)
    _all_instructors_cache = d
    _all_instructors_cache_at = time.monotonic()
    return _all_instructors_cache


async def _get_all_review_stats_cached() -> dict[str, tuple]:
    global _all_review_stats_cache, _all_review_stats_cache_at
    if _all_review_stats_cache and time.monotonic() - _all_review_stats_cache_at < _COURSE_CACHE_TTL:
        return _all_review_stats_cache
    async with AsyncSessionLocal() as s:
        count_rows = (await s.execute(
            select(Subject.name, func.count(Review.id).label("cnt"))
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.is_approved == True)
            .group_by(Subject.name)
        )).all()
        ease_rows = (await s.execute(
            select(Subject.name, Review.ease_rating, func.count(Review.id).label("cnt"))
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.is_approved == True, Review.ease_rating.isnot(None))
            .group_by(Subject.name, Review.ease_rating)
        )).all()
    ease_map: dict[str, list] = {}
    for name, ease, cnt in ease_rows:
        ease_map.setdefault(name, []).append((ease, cnt))
    result = {}
    for name, cnt in count_rows:
        top_ease = None
        if name in ease_map:
            top_ease = sorted(ease_map[name], key=lambda r: (-r[1], EASE_ORDER.get(r[0], 99)))[0][0]
        result[name] = (cnt, top_ease)
    _all_review_stats_cache = result
    _all_review_stats_cache_at = time.monotonic()
    return _all_review_stats_cache


async def _get_syllabus_urls_cached() -> dict[int, str]:
    global _syllabus_url_cache, _syllabus_url_cache_at
    if _syllabus_url_cache and time.monotonic() - _syllabus_url_cache_at < _COURSE_CACHE_TTL:
        return _syllabus_url_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(CourseSection.subject_id, CourseSection.syllabus_url)
            .where(CourseSection.syllabus_url.isnot(None))
        )).all()
    _syllabus_url_cache = {sid: url for sid, url in rows}
    _syllabus_url_cache_at = time.monotonic()
    return _syllabus_url_cache


async def _prewarm_caches():
    await asyncio.sleep(0.5)
    try:
        await asyncio.gather(
            _get_cls_order_map(),
            _get_cls_parent_map(),
            _get_cls_set(),
            _get_courses_cached(),
            _get_reviewed_cached(),
            _get_all_instructors_cached(),
            _get_all_review_stats_cached(),
            _get_syllabus_urls_cached(),
        )
    except Exception as e:
        print(f"Prewarm failed: {e}", flush=True)
    try:
        _, all_courses = await _get_courses_cached()
        for course in all_courses:
            await get_course_flex(course, "")
    except Exception as e:
        print(f"Prewarm flex cache failed: {e}", flush=True)
    print("Cache pre-warm complete", flush=True)


async def _cleanup_old_logs():
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        async with AsyncSessionLocal() as session:
            await session.execute(delete(MessageLog).where(MessageLog.created_at < cutoff))
            await session.commit()
    except Exception as exc:
        await save_error_log(exc, action="cleanup")


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




async def get_course_flex(course: Subject, user_id: str) -> FlexMessage:
    _cfx = _course_flex_cache.get(course.id)
    if _cfx and time.monotonic() - _cfx[1] < _COURSE_FLEX_TTL:
        return _cfx[0]

    all_instrs, all_stats = await asyncio.gather(
        _get_all_instructors_cached(),
        _get_all_review_stats_cached(),
    )
    instructors = all_instrs.get(course.id, [])
    review_count, top_ease_flex = all_stats.get(course.name, (0, None))

    instructor_str = "・".join(i.name for i in instructors) or "未設定"
    liff_url = f"{APP_URL}/liff/course?course_id={course.id}"

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
    msg = FlexMessage(alt_text=f"📖 {course.name}", contents=bubble)
    _course_flex_cache[course.id] = (msg, time.monotonic())
    return msg


def make_no_review_flex(course: Subject, user_id: str = "") -> FlexMessage:
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
    def section_label(text: str) -> FlexText:
        return FlexText(text=text, size="xxs", weight="bold", color="#6366f1",
                        margin="lg")

    def card(icon: str, title: str, desc: str, bg: str = "#f5f3ff", icon_color: str = "#6366f1") -> FlexBox:
        return FlexBox(
            layout="horizontal",
            background_color=bg,
            corner_radius="10px",
            padding_all="md",
            margin="sm",
            contents=[
                FlexBox(
                    layout="vertical",
                    contents=[FlexText(text=icon, size="lg", align="center", gravity="center")],
                    width="36px",
                    height="36px",
                    background_color="#ffffff",
                    corner_radius="8px",
                    flex=0,
                    justify_content="center",
                    align_items="center",
                ),
                FlexBox(
                    layout="vertical",
                    flex=1,
                    margin="md",
                    contents=[
                        FlexText(text=title, weight="bold", size="sm", color="#1e1b4b"),
                        FlexText(text=desc, size="xs", color="#6b7280", wrap=True, margin="xs"),
                    ],
                ),
            ],
        )

    return FlexMessage(
        alt_text="神大ライフハック 使い方ガイド",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexBox(
                        layout="horizontal",
                        contents=[
                            FlexText(text="🎓", size="xxl", flex=0),
                            FlexBox(
                                layout="vertical",
                                flex=1,
                                margin="md",
                                contents=[
                                    FlexText(text="神大ライフハック", weight="bold",
                                             color="#ffffff", size="lg"),
                                    FlexText(text="使い方ガイド", color="#c7d2fe", size="xs"),
                                ],
                            ),
                        ],
                    ),
                ],
                background_color="#4f46e5",
                padding_all="xl",
            ),
            body=FlexBox(
                layout="vertical",
                contents=[
                    section_label("📱  リッチメニュー"),
                    card("📚", "教養", "教養科目を分類別に一覧表示", bg="#f5f3ff"),
                    card("✏️", "レビュー投稿", "レビュー投稿フォームを開く", bg="#f5f3ff"),
                    section_label("💬  チャット"),
                    card("🔍", "科目名を送る",
                         "授業情報・レビューを表示\n例：「英語」「データサイエンス」",
                         bg="#eff6ff"),
                    card("🏆", "人気 / 楽単",
                         "「人気」→ 高評価 TOP5\n「楽単」→ 楽単 TOP5",
                         bg="#eff6ff"),
                ],
                padding_all="lg",
                background_color="#fafafa",
            ),
            footer=FlexBox(
                layout="vertical",
                contents=[
                    FlexButton(
                        action=URIAction(label="📬 お問い合わせ", uri=f"mailto:{CONTACT_EMAIL}"),
                        style="primary",
                        color="#6366f1",
                        height="sm",
                    ),
                    FlexButton(
                        action=URIAction(label="プライバシーポリシー", uri=PRIVACY_URL),
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
    if full.startswith(base) and len(full) > len(base):
        return full[len(base):]
    for b_ch, f_ch in zip(base, full):
        if b_ch != f_ch:
            return f_ch
    return full[-1] if full else ""


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


def make_category_select_flex() -> FlexMessage:
    categories = [
        ("📚 教養科目", "教養科目の系統を選んで表示します", "教養", "#6366f1", "#eef2ff", "#4f46e5"),
        ("🎓 専門科目", "経営学部の専門科目を表示します",   "専門", "#0ea5e9", "#e0f2fe", "#0284c7"),
    ]
    btns = [
        FlexBox(
            layout="vertical",
            action=MessageAction(label=label[:40], text=text),
            contents=[
                FlexText(text=label, size="lg", color=fg, weight="bold", align="center"),
                FlexText(text=desc,  size="xs", color="#64748b", align="center", wrap=True),
            ],
            background_color=bg,
            border_width="2px",
            border_color=border,
            corner_radius="20px",
            padding_all="md",
        )
        for label, desc, text, fg, bg, border in categories
    ]
    return FlexMessage(
        alt_text="📚 科目一覧 — カテゴリを選んでください",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text="📚 科目一覧", weight="bold", color="#ffffff", size="lg"),
                    FlexText(text="カテゴリを選んでください", color="#c7d2fe", size="sm"),
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


def make_classification_select_flex(
    classifications: list[str],
    reviewed_cls: set | None = None,
    title: str = "📚 教養科目",
    subtitle: str = "系統を選んでください",
    header_color: str = "#6366f1",
    data_prefix: str = "",
) -> FlexMessage:
    if reviewed_cls is None:
        reviewed_cls = set()
    btns = [
        FlexBox(
            layout="vertical",
            action=PostbackAction(label=cls[:40], data=f"{data_prefix}{cls}"),
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
        alt_text=f"{title} — {subtitle}",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text=title, weight="bold", color="#ffffff", size="lg"),
                    FlexText(text=subtitle, color="#c7d2fe", size="sm"),
                ],
                background_color=header_color,
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
    _cl_key = f"{category}:{classification}"
    _cl = _course_list_cache.get(_cl_key)
    if _cl and time.monotonic() - _cl[1] < _COURSE_LIST_TTL:
        return _cl[0]

    from collections import defaultdict
    cls_map, (_, _all_c), reviewed_names = await asyncio.gather(
        _get_cls_order_map(), _get_courses_cached(), _get_reviewed_cached()
    )
    _cls_sort = _make_cls_sort(cls_map)
    rows = [c for c in _all_c if
            (not category or c.category == category) and
            (not classification or c.classification == classification)]
    rows = sorted(rows, key=lambda c: (_cls_sort(c.classification or ""), c.sort_order, c.name or ""))

    if not rows:
        if classification:
            label = f"{classification}の"
        elif category:
            label = f"{category}の"
        else:
            label = ""
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

    # syllabus_url は全件キャッシュから取得（DBアクセスなし）
    _sv_by_id = await _get_syllabus_urls_cached()
    course_syllabus_urls: dict[str, str] = {c.name: _sv_by_id[c.id] for c in rows if c.id in _sv_by_id}
    course_liff_urls: dict[str, str] = {c.name: f"{APP_URL}/liff/course?course_id={c.id}" for c in rows}
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    cls_category: dict[str, str] = {}
    cls_faculty: dict[str, str] = {}
    for course in rows:
        name = course.name
        cls = course.classification or "その他"
        cls_category[cls] = course.category or ""
        if course.faculty:
            cls_faculty[cls] = course.faculty
        if name in _sem_variant_names:
            base = _sem_base_for[name]
            if base not in seen_sem_base:
                seen_sem_base.add(base)
                items_sorted = sorted(_sem_bases[base], key=lambda x: x[1])
                suffix = "/".join(sk for _, sk in items_sorted)
                groups[cls].append((base, f"variant:{suffix}"))
            continue
        if name and name[-1] in ('A', 'B', 'C', 'D') and len(name) > 1:
            base = name[:-1]
            variants = [s for s in 'ABCD' if base + s in course_name_set]
            if len(variants) >= 2:
                if base not in seen_base:
                    seen_base.add(base)
                    suffix = "/".join(variants)
                    groups[cls].append((base, f"variant:{suffix}"))
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
                groups[cls].append((base, f"numvariant:{suffix}"))
            continue
        groups[cls].append((name, "single"))

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
            syl_url = course_syllabus_urls.get(name, "")
            if kind == "single":
                liff_url = course_liff_urls.get(name, "")
            elif kind.startswith("variant:"):
                first_suffix = kind.split(":", 1)[1].split("/")[0]
                liff_url = course_liff_urls.get(name + first_suffix, "")
            elif kind.startswith("numvariant:") and name in _num_bases:
                first_name = min(_num_bases[name], key=lambda x: x[1])[0]
                liff_url = course_liff_urls.get(first_name, "")
            else:
                liff_url = ""
            name_box = FlexBox(
                layout="horizontal",
                action=PostbackAction(label=display[:40], data=name),
                contents=[FlexText(text=display, wrap=True, size="sm", color=text_color, flex=1)],
            )
            if liff_url or syl_url:
                link_items = []
                if liff_url:
                    link_items.append(FlexText(text="レビュー", size="xxs", color="#4f46e5", flex=0,
                                               action=URIAction(label="レビュー", uri=liff_url)))
                if syl_url:
                    if link_items:
                        link_items.append(FlexText(text="  ", size="xxs", color="#cbd5e1", flex=0))
                    link_items.append(FlexText(text="シラバス", size="xxs", color="#64748b", flex=0,
                                               action=URIAction(label="シラバス", uri=syl_url)))
                btn_contents.append(FlexBox(
                    layout="vertical",
                    contents=[
                        name_box,
                        FlexBox(layout="horizontal", contents=link_items, margin="xs"),
                    ],
                    padding_top="sm",
                    padding_bottom="sm",
                ))
            else:
                btn_contents.append(
                    FlexBox(
                        layout="vertical",
                        action=PostbackAction(label=display[:40], data=name),
                        contents=[FlexText(text=display, wrap=True, size="sm", color=text_color)],
                        padding_top="sm",
                        padding_bottom="sm",
                    )
                )
        base_cls = classification.rstrip("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮")
        faculty_str = cls_faculty.get(base_cls, "")
        header_contents = [FlexText(text=classification, weight="bold", color="#ffffff", size="sm")]
        if faculty_str:
            header_contents.append(FlexText(text=faculty_str, size="xs", color="#c7d2fe", margin="xs"))
        return FlexBubble(
            size="kilo",
            header=FlexBox(
                layout="vertical",
                contents=header_contents,
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

    MAX_PER_BUBBLE = 6
    _ROMAN = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"
    bubbles: list = []

    def _split_to_bubbles(cls: str, ents: list) -> None:
        if not ents:
            return
        if len(ents) <= MAX_PER_BUBBLE:
            bubbles.append(_make_bubble(cls, ents))
            return
        chunks = [ents[i:i+MAX_PER_BUBBLE] for i in range(0, len(ents), MAX_PER_BUBBLE)]
        for i, chunk in enumerate(chunks):
            suffix = _ROMAN[i] if i < len(_ROMAN) else f"({i+1})"
            bubbles.append(_make_bubble(cls + suffix, chunk))

    for cls, ents in all_groups:
        if cls == "教養(総合)":
            others = [(n, k) for n, k in ents if "GCP" not in n]
            gcps   = [(n, k) for n, k in ents if "GCP" in n]
            _split_to_bubbles("教養(総合)", others)
            _split_to_bubbles("教養(総合) GCP", gcps)
        else:
            _split_to_bubbles(cls, ents)

    alt = f"📚 {category}一覧" if category else "📚 科目一覧"
    if not bubbles:
        return [TextMessage(text="科目が登録されていません。")]

    # 8バブルずつ複数カルーセルに分割（シラバスURL追加後に50KB超え防止）、最大5メッセージ
    result = []
    for chunk in [bubbles[i:i+8] for i in range(0, min(len(bubbles), 40), 8)]:
        if len(chunk) == 1:
            result.append(FlexMessage(alt_text=alt, contents=chunk[0]))
        else:
            result.append(FlexMessage(alt_text=alt, contents=FlexCarousel(contents=chunk)))
    _course_list_cache[_cl_key] = (result, time.monotonic())
    return result


# ── Message handler ─────────────────────────────────────────────

async def handle_message(text: str, user_id: str = "") -> list:
    t = text.strip()

    if t in ["科目一覧", "科目", "授業一覧", "一覧"]:
        return [make_category_select_flex()]

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

    if t.startswith("専門:"):
        cls = t[len("専門:"):]
        return await handle_course_list(category="専門", classification=cls)

    # 分類名の直接タップ（例：「教養(社会)」）
    if t in await _get_cls_set():
        return await handle_course_list(classification=t)

    if t == "専門comingsoon":
        return [TextMessage(text="🚧 専門科目一覧は現在準備中です。\nもうしばらくお待ちください！")]

    if t in ["専門科目", "専門", "専門一覧"]:
        cls_map = await _get_cls_order_map()
        parent_map = await _get_cls_parent_map()
        _cls_sort = _make_cls_sort(cls_map)
        reviewed_names_sen, (_, _all_courses) = await asyncio.gather(
            _get_reviewed_cached(),
            _get_courses_cached(),
        )
        sen_courses = [c for c in _all_courses if c.category == "専門" and c.classification]
        all_cls = {c.classification for c in sen_courses}
        reviewed_cls_sen = {c.classification for c in sen_courses if c.name in reviewed_names_sen}

        # DB駆動のグループ化: parent_groupが設定されている分類 + 親名自体を除外
        child_cls_set = {cls for cls in parent_map if cls in all_cls}
        parent_names = set(parent_map.values())
        all_excluded = child_cls_set | (parent_names & all_cls)

        other_clss = sorted(all_cls - all_excluded, key=_cls_sort)

        # 親グループボタンを生成
        parent_buttons = []
        display_reviewed = set(other_clss) & reviewed_cls_sen
        for parent in sorted(parent_names):
            children = {cls for cls in child_cls_set if parent_map[cls] == parent}
            if children & all_cls or parent in all_cls:
                parent_buttons.append(f"{parent} ▶")
                if reviewed_cls_sen & (children | {parent}):
                    display_reviewed.add(f"{parent} ▶")

        display_clss = parent_buttons + other_clss

        if display_clss:
            return [make_classification_select_flex(
                display_clss, display_reviewed,
                title="🎓 専門科目",
                subtitle="分類を選んでください",
                header_color="#0ea5e9",
            )]
        return await handle_course_list(category="専門")

    # 親グループ ▶ タップ（例: "経営学部 ▶"）
    if t.endswith(" ▶"):
        parent = t[:-2].strip()
        cls_map = await _get_cls_order_map()
        parent_map = await _get_cls_parent_map()
        _cls_sort = _make_cls_sort(cls_map)
        reviewed_names_sen, (_, _all_courses) = await asyncio.gather(
            _get_reviewed_cached(),
            _get_courses_cached(),
        )
        child_clss = sorted([cls for cls, pg in parent_map.items() if pg == parent], key=_cls_sort)
        if child_clss:
            child_set = set(child_clss)
            child_courses = [c for c in _all_courses if c.category == "専門" and c.classification in child_set]
            reviewed_cls = {c.classification for c in child_courses if c.name in reviewed_names_sen}
            return [make_classification_select_flex(
                child_clss, reviewed_cls,
                title=f"🎓 {parent} 専門科目",
                subtitle="分類を選んでください",
                header_color="#0ea5e9",
                data_prefix="専門:",
            )]

    if t in ["レビュー投稿", "レビュー", "投稿"] or "レビュー投稿" in t:
        url = f"{REVIEW_FORM_URL}?uid={user_id}" if user_id else REVIEW_FORM_URL
        return [TextMessage(text=f"📝 以下のフォームからレビューを投稿できます！\n\n{url}")]

    if t in ["時間割", "my時間割", "マイ時間割", "時間割テスト"]:
        if IS_DEV:
            url = f"{APP_URL}/liff/timetable?dev_uid={user_id}" if user_id else f"{APP_URL}/liff/timetable"
        elif TIMETABLE_LIFF_ID:
            url = f"https://liff.line.me/{TIMETABLE_LIFF_ID}"
        else:
            return [TextMessage(text="時間割機能は現在ご利用いただけません。")]
        return [FlexMessage(alt_text="📅 My時間割", contents=FlexBubble(
            body=FlexBox(layout="vertical", spacing="md", contents=[
                FlexText(text="📅 My時間割", weight="bold", size="lg"),
                FlexText(text="タップして時間割を開く", size="sm", color="#64748b"),
                FlexButton(action=URIAction(label="時間割を開く", uri=url),
                           style="primary", color="#6366f1", margin="md"),
            ])
        ))]

    if t in ["ヘルプ", "help", "使い方", "？", "?"]:
        return [make_help_flex()]

    if t in ["問い合わせ", "連絡", "contact", "お問い合わせ"]:
        return [make_help_flex()]

    if t in ["人気の授業", "人気授業", "人気", "おすすめ"]:
        _rk = _ranking_cache.get("popular")
        if _rk and time.monotonic() - _rk[1] < _RANKING_TTL:
            return _rk[0]
        async with AsyncSessionLocal() as _s:
            rows = (await _s.execute(
                select(Subject.name, func.avg(Review.rating).label("avg"))
                .join(CourseSection, CourseSection.subject_id == Subject.id)
                .join(Review, Review.course_section_id == CourseSection.id)
                .where(Review.is_approved == True)
                .group_by(Subject.name)
                .order_by(func.avg(Review.rating).desc())
                .limit(5)
            )).all()
        if not rows:
            return [TextMessage(text=f"まだ承認済みレビューがありません。\nレビューを投稿してください！\n\n{REVIEW_FORM_URL}")]
        items = [
            {"rank": i, "name": name, "stars": stars(math.floor(float(avg) + 0.5))}
            for i, (name, avg) in enumerate(rows, 1)
        ]
        _res = [FlexMessage(alt_text="🏆 人気の授業 TOP5", contents=make_ranking_bubble("🏆 人気の授業 TOP5", items))]
        _ranking_cache["popular"] = (_res, time.monotonic())
        return _res

    if t in ["楽単ランキング", "楽単", "楽"]:
        _rk = _ranking_cache.get("rakutan")
        if _rk and time.monotonic() - _rk[1] < _RANKING_TTL:
            return _rk[0]
        async with AsyncSessionLocal() as _s:
            rows = (await _s.execute(
                select(Subject.name, Review.ease_rating, func.count(Review.id))
                .join(CourseSection, CourseSection.subject_id == Subject.id)
                .join(Review, Review.course_section_id == CourseSection.id)
                .where(Review.is_approved == True)
                .group_by(Subject.name, Review.ease_rating)
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
        _res = [FlexMessage(alt_text="😴 楽単ランキング TOP5", contents=make_ranking_bubble("😴 楽単ランキング TOP5", items))]
        _ranking_cache["rakutan"] = (_res, time.monotonic())
        return _res

    # 全操作をキャッシュから（DBアクセスなし）
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

    # インメモリキーワード検索（DBアクセスなし）
    _PUNCT = '・･、。「」『』【】（）()／/〜~'
    def _normalize_q(s: str) -> str:
        for ch in _PUNCT:
            s = s.replace(ch, '')
        return s

    tokens = [tok for tok in _re.split(r'[\s　]+', t.strip()) if tok]
    _toks_lower = [tok.lower() for tok in tokens]
    courses = [c for c in call if all(
        tok in (c.name or '').lower() or tok in (c.reading or '').lower()
        for tok in _toks_lower
    )][:6]

    # 句読点を除去した正規化検索（フォールバック）
    if not courses:
        _norm_t = _normalize_q(t).lower()
        courses = [c for c in call if _norm_t in _normalize_q(c.name or '').lower()][:6]

    if courses:
        # Letter variants (A/B/C/D) - インメモリ
        _all_names = {c.name for c in call}
        potential_bases = {
            c.name[:-1] for c in courses
            if c.name and c.name[-1] in ('A', 'B', 'C', 'D') and len(c.name) > 1
        }
        base_variants: dict[str, list[str]] = defaultdict(list)
        for _b in potential_bases:
            for _s in ('A', 'B', 'C', 'D'):
                if _b + _s in _all_names:
                    base_variants[_b].append(_b + _s)

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
    if random.random() < 0.02:
        asyncio.create_task(_cleanup_old_logs())

    try:
        for event in events:
            if isinstance(event, FollowEvent):
                try:
                    await _line_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[make_welcome_flex()],
                        )
                    )
                    asyncio.create_task(_save_log_bg(event.source.user_id, "in", "[follow]"))
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
                    await _line_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=messages[:5],
                        )
                    )
                    asyncio.create_task(_save_log_bg(user_id, "out", f"[{len(messages)} msg(s)]"))
                except asyncio.TimeoutError:
                    await save_error_log(Exception("handle_message timeout"), user_id=user_id, action=data)
                    try:
                        await _line_api.reply_message(ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="処理に時間がかかりすぎました。もう一度お試しください。")],
                        ))
                    except Exception:
                        pass
                except Exception as exc:
                    await save_error_log(exc, user_id=user_id, action=f"postback:{data}")
                    try:
                        await _line_api.reply_message(ReplyMessageRequest(
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
                await _line_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=messages[:5],
                    )
                )
                asyncio.create_task(_save_log_bg(user_id, "out", f"[{len(messages)} msg(s)]"))
            except asyncio.TimeoutError:
                await save_error_log(Exception("handle_message timeout"), user_id=user_id, action=user_text)
                try:
                    await _line_api.reply_message(
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
                    await _line_api.reply_message(
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

    def _on_process_done(t: asyncio.Task):
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            asyncio.create_task(save_error_log(exc, action="process_events_bg"))

    task.add_done_callback(_on_process_done)
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
            stmt = select(Subject)
            for tok in tokens:
                t = _escape(tok)
                stmt = stmt.where(or_(
                    Subject.name.ilike(f"%{t}%", escape="\\"),
                    Subject.reading.ilike(f"%{t}%", escape="\\"),
                ))
            stmt = stmt.order_by(Subject.name)
            courses = (await session.execute(stmt)).scalars().all()
            if not courses:
                norm_col = Subject.name
                for ch in ('・', '･', '（', '）', '(', ')'):
                    norm_col = func.replace(norm_col, ch, '')
                norm_tokens = [_normalize_form_q(tok) for tok in tokens]
                stmt2 = select(Subject)
                for tok in norm_tokens:
                    t = _escape(tok)
                    stmt2 = stmt2.where(norm_col.ilike(f"%{t}%", escape="\\"))
                courses = (await session.execute(stmt2.order_by(Subject.name))).scalars().all()
        else:
            stmt = select(Subject).order_by(Subject.name).limit(30)
            courses = (await session.execute(stmt)).scalars().all()
        course_ids = [c.id for c in courses]
        cs_rows = []
        if course_ids:
            cs_rows = (await session.execute(
                select(CourseSection, Instructor)
                .join(Instructor, Instructor.id == CourseSection.instructor_id)
                .where(CourseSection.subject_id.in_(course_ids))
                .order_by(Instructor.name)
            )).all()
        insts_by_course: dict = {}
        for cs, inst in cs_rows:
            insts_by_course.setdefault(cs.subject_id, []).append({"name": inst.name, "url": cs.syllabus_url or ""})
    return {"courses": [
        {"id": c.id, "name": c.name, "instructors": insts_by_course.get(c.id, [])}
        for c in courses
    ]}


@app.get("/api/preload")
async def api_preload():
    async with AsyncSessionLocal() as session:
        courses = (await session.execute(select(Subject).order_by(Subject.name))).scalars().all()
        cs_rows = (await session.execute(
            select(CourseSection, Instructor)
            .join(Instructor, Instructor.id == CourseSection.instructor_id)
            .order_by(Instructor.name)
        )).all()
    insts_by_course: dict = {}
    inst_courses: dict = {}
    course_by_id = {c.id: c.name for c in courses}
    for cs, inst in cs_rows:
        insts_by_course.setdefault(cs.subject_id, []).append({"name": inst.name})
        cname = course_by_id.get(cs.subject_id)
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
            select(Instructor.name)
            .where(Instructor.name.ilike(f"%{escaped}%", escape="\\"))
            .distinct()
        )).scalars().all()
        insts = sorted(insts_raw, key=lambda n: (0 if n.lower().startswith(q_clean.lower()) else 1, n))
        if not insts:
            norm_col = Instructor.name
            for ch in ('・', '･', '（', '）', '(', ')'):
                norm_col = func.replace(norm_col, ch, '')
            escaped_norm = _esc(_normalize_form_q(q_clean))
            insts_raw = (await session.execute(
                select(Instructor.name)
                .where(norm_col.ilike(f"%{escaped_norm}%", escape="\\"))
                .distinct()
            )).scalars().all()
            insts = sorted(insts_raw, key=lambda n: (0 if n.lower().startswith(q_clean.lower()) else 1, n))

        result = []
        if insts:
            all_rows = (await session.execute(
                select(Instructor.name, Subject.id, Subject.name)
                .join(CourseSection, CourseSection.instructor_id == Instructor.id)
                .join(Subject, Subject.id == CourseSection.subject_id)
                .where(Instructor.name.in_(insts))
                .order_by(Instructor.name, Subject.name)
            )).all()
            courses_by_inst: dict[str, list] = {name: [] for name in insts}
            for inst_name, c_id, c_name in all_rows:
                if not any(x["id"] == c_id for x in courses_by_inst[inst_name]):
                    courses_by_inst[inst_name].append({"id": c_id, "name": c_name})
            for name in insts:
                result.append({"name": name, "courses": courses_by_inst[name]})

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
            select(Review.submitter_name)
            .where(Review.student_id == sid)
            .order_by(Review.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        if not row:
            return {"found": False}
        taken = (await session.execute(
            select(UserProfile.line_user_id).where(UserProfile.student_id == sid)
        )).scalars().first()
        if taken is not None and taken != uid:
            return {"found": False}
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
        subject = (await session.execute(
            select(Subject).where(Subject.name == course_name.strip())
        )).scalar_one_or_none()
        if not subject:
            return _form_error("指定された科目が見つかりません")

        uid = line_user_id.strip()
        if uid:
            if not _LINE_USER_ID_RE.match(uid):
                return _form_error("LINE ユーザー ID の形式が不正です")
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
                    return _form_error("プロフィールの保存に失敗しました")
            else:
                if existing.student_id != sid:
                    return _form_error("学籍番号が登録情報と一致しません")

        # 担当教員に対応する course_section を探す
        instr_name = selected_instructor.strip()[:100] or None
        cs_obj = None
        if instr_name:
            instr_obj = (await session.execute(
                select(Instructor).where(Instructor.name == instr_name)
            )).scalar_one_or_none()
            if instr_obj:
                cs_obj = (await session.execute(
                    select(CourseSection).where(
                        CourseSection.subject_id == subject.id,
                        CourseSection.instructor_id == instr_obj.id,
                    )
                )).scalar_one_or_none()
        if cs_obj is None:
            cs_obj = (await session.execute(
                select(CourseSection).where(CourseSection.subject_id == subject.id)
            )).scalars().first()
        if cs_obj is None:
            return _form_error("この科目の担当教員情報が見つかりません")

        review = Review(
            course_section_id=cs_obj.id,
            submitter_name=submitter_name.strip()[:100],
            content=comment.strip()[:500],
            rating=rating,
            ease_rating=ease_rating,
            grading_method=grading_method.strip()[:500] or None,
            selected_instructor=instr_name,
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
    try:
        endpoint = data["endpoint"]
        p256dh = data["keys"]["p256dh"]
        auth = data["keys"]["auth"]
    except (KeyError, TypeError):
        raise HTTPException(status_code=400, detail="invalid subscription payload")
    async with AsyncSessionLocal() as session:
        stmt = pg_insert(PushSubscription).values(
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
        ).on_conflict_do_update(
            index_elements=["endpoint"],
            set_={"p256dh": p256dh, "auth": auth},
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
            Subject.name.ilike(f"%{q_safe}%", escape="\\"),
            Subject.reading.ilike(f"%{q_safe}%", escape="\\"),
            Subject.faculty.ilike(f"%{q_safe}%", escape="\\"),
        )

    async with AsyncSessionLocal() as session:
        base_stmt = select(Subject)
        if category:
            base_stmt = base_stmt.where(Subject.category == category)
        if q:
            base_stmt = base_stmt.where(_search_filter(q))

        courses = (await session.execute(
            base_stmt.order_by(Subject.sort_order, Subject.name)
        )).scalars().all()
        cls_map = await _get_cls_order_map()
        _cls_sort = _make_cls_sort(cls_map)
        courses = sorted(courses, key=lambda c: (_cls_sort(c.classification or ""), c.sort_order, c.name or ""))
        total = len(courses)
        classifications = (await session.execute(
            select(Subject.classification).distinct().order_by(Subject.classification)
        )).scalars().all()
        class_counts_raw = dict((await session.execute(
            select(Subject.classification, func.count(Subject.id))
            .where(Subject.classification.isnot(None), Subject.classification != "")
            .group_by(Subject.classification)
            .order_by(Subject.classification)
        )).all())
        class_counts = {k: class_counts_raw[k] for k in sorted(class_counts_raw, key=_cls_sort)}

        course_ids = [c.id for c in courses]

        cs_instr_rows = []
        if course_ids:
            cs_instr_rows = (await session.execute(
                select(CourseSection, Instructor)
                .join(Instructor, Instructor.id == CourseSection.instructor_id)
                .where(CourseSection.subject_id.in_(course_ids))
            )).all()

        reviews_data = []
        if course_ids:
            reviews_data = (await session.execute(
                select(Review, Subject.name.label("subj_name"))
                .join(CourseSection, CourseSection.id == Review.course_section_id)
                .join(Subject, Subject.id == CourseSection.subject_id)
                .where(CourseSection.subject_id.in_(course_ids))
                .order_by(Review.is_approved, Review.created_at.desc())
            )).all()

    existing = sorted([c for c in classifications if c], key=_cls_sort)
    courses_data = (
        json.dumps({
            c.id: {
                "name": c.name,
                "instructor": "",
                "classification": c.classification or "",
                "category": c.category or "",
                "syllabus_url": "",
                "faculty": c.faculty or "",
                "term": c.term or "",
                "credits": float(c.credits) if c.credits is not None else 0,
            }
            for c in courses
        }, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )

    instructors_by_course: dict = defaultdict(list)
    for cs, inst in sorted(cs_instr_rows, key=lambda x: x[1].name):
        instructors_by_course[cs.subject_id].append(
            SimpleNamespace(id=inst.id, name=inst.name, url=cs.syllabus_url or "")
        )

    reviews_by_course: dict = defaultdict(list)
    for rev, subj_name in reviews_data:
        reviews_by_course[subj_name].append(SimpleNamespace(
            id=rev.id,
            course_name=subj_name,
            comment=rev.content,
            content=rev.content,
            rating=rev.rating,
            ease_rating=rev.ease_rating,
            grading_method=rev.grading_method,
            is_approved=rev.is_approved,
            selected_instructor=rev.selected_instructor,
            created_at=rev.created_at,
            submitter_name=rev.submitter_name,
            nickname=rev.nickname,
            academic_year=rev.academic_year,
            student_id=rev.student_id,
        ))

    # groupby順を保持するため事前グループ化
    cls_parent_map = await _get_cls_parent_map()
    child_cls_set = set(cls_parent_map.keys())
    parent_names_set = set(cls_parent_map.values())

    parent_subgroups: dict = defaultdict(lambda: defaultdict(list))
    regular_grouped: dict = defaultdict(list)
    for c in courses:
        cls = c.classification or "（未分類）"
        if cls in child_cls_set:
            parent_subgroups[cls_parent_map[cls]][cls].append(c)
        elif cls in parent_names_set:
            parent_subgroups[cls]["（未分類）"].append(c)
        else:
            regular_grouped[cls].append(c)

    # parent_subgroups を並び順に整形
    cls_order_map = await _get_cls_order_map()
    _cls_sort = _make_cls_sort(cls_order_map)
    parent_subgroups_sorted = {
        pg: sorted(sub.items(), key=lambda x: _cls_sort(x[0]))
        for pg, sub in sorted(parent_subgroups.items())
    }

    return templates.TemplateResponse("admin/courses.html", {
        "request": request,
        "courses": courses,
        "grouped_courses": list(regular_grouped.items()),
        "parent_subgroups": parent_subgroups_sorted,
        "cls_parent_map": cls_parent_map,
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
            # Instructor upsert
            instr = (await session.execute(
                select(Instructor).where(Instructor.name == name_s)
            )).scalar_one_or_none()
            if not instr:
                instr = Instructor(name=name_s)
                session.add(instr)
                await session.flush()
            # CourseSection 重複チェック
            existing_cs = (await session.execute(
                select(CourseSection).where(
                    CourseSection.subject_id == course_id,
                    CourseSection.instructor_id == instr.id,
                )
            )).scalar_one_or_none()
            if existing_cs:
                if is_ajax:
                    return JSONResponse({"ok": False, "error": "duplicate"})
                referer = request.headers.get("Referer", "/admin/courses")
                sep = "&" if "?" in referer else "?"
                return RedirectResponse(f"{referer}{sep}inst_err={course_id}", status_code=303)
            cs = CourseSection(subject_id=course_id, instructor_id=instr.id, syllabus_url=url_s)
            session.add(cs)
            await session.commit()
            _invalidate_courses_cache()
            if is_ajax:
                return JSONResponse({"ok": True, "id": instr.id, "name": instr.name, "url": url_s or ""})
    if is_ajax:
        return JSONResponse({"ok": False, "error": "empty"})
    return RedirectResponse(request.headers.get("Referer", "/admin/courses"), status_code=303)


@app.post("/admin/courses/{course_id}/instructors/delete/{instructor_id}")
async def delete_instructor(course_id: int, instructor_id: int, request: Request, _: str = Depends(check_admin)):
    from fastapi.responses import JSONResponse
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    async with AsyncSessionLocal() as session:
        cs = (await session.execute(
            select(CourseSection).where(
                CourseSection.subject_id == course_id,
                CourseSection.instructor_id == instructor_id,
            )
        )).scalar_one_or_none()
        if cs:
            has_approved = (await session.execute(
                select(func.count(Review.id)).where(
                    Review.course_section_id == cs.id,
                    Review.is_approved == True,
                )
            )).scalar()
            if has_approved:
                if is_ajax:
                    return JSONResponse({"ok": False, "error": "承認済みレビューがあるため削除できません"})
                return RedirectResponse(request.headers.get("Referer", "/admin/courses"), status_code=303)
            await session.delete(cs)
            await session.commit()
    _invalidate_courses_cache()
    if is_ajax:
        return JSONResponse({"ok": True})
    return RedirectResponse(request.headers.get("Referer", "/admin/courses"), status_code=303)


@app.post("/admin/reviews/cleanup")
async def admin_reviews_cleanup(_: str = Depends(check_admin)):
    # 未承認の孤立レビュー（subject が削除済み）を削除。承認済みは削除しない。
    async with AsyncSessionLocal() as session:
        orphan_cs_ids = (await session.execute(
            select(CourseSection.id).where(
                ~CourseSection.subject_id.in_(select(Subject.id))
            )
        )).scalars().all()
        if orphan_cs_ids:
            await session.execute(
                delete(Review).where(
                    Review.course_section_id.in_(orphan_cs_ids),
                    Review.is_approved == False,
                )
            )
            await session.commit()
    return RedirectResponse("/admin/courses", status_code=303)


@app.post("/admin/courses/migrate-third-language")
async def migrate_third_language(_: str = Depends(check_admin)):
    LANGS = ["ドイツ語", "フランス語"]
    NUMS = [1, 2, 3, 4]
    async with AsyncSessionLocal() as session:
        to_delete = (await session.execute(
            select(Subject).where(Subject.name.contains("第三外国語"))
        )).scalars().all()
        for c in to_delete:
            await session.delete(c)
        for lang in LANGS:
            for n in NUMS:
                name = f"第三外国語({lang})T{n}"
                existing = (await session.execute(
                    select(Subject).where(Subject.name == name)
                )).scalar_one_or_none()
                if not existing:
                    session.add(Subject(
                        name=name,
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
    async with AsyncSessionLocal() as session:
        stmt = select(Subject)
        if prefix.strip():
            stmt = stmt.where(Subject.name.contains(prefix.strip()))
        courses = (await session.execute(stmt)).scalars().all()

        groups: dict[str, list] = defaultdict(list)
        for course in courses:
            base = _re.sub(r'[\s　]*\d+$', '', course.name).strip()
            if base != course.name:
                groups[base].append(course)

        for base_name, dups in groups.items():
            existing = (await session.execute(
                select(Subject).where(Subject.name == base_name)
            )).scalar_one_or_none()

            if existing:
                pass
            else:
                survivor = dups[0]
                survivor.name = base_name
                survivor.reading = _reading(base_name)
                dups = dups[1:]

            for dup in dups:
                await session.delete(dup)

        await session.commit()
    return RedirectResponse("/admin/courses", status_code=303)


def _make_review_ns(rev: Review, course_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=rev.id,
        course_name=course_name,
        comment=rev.content,
        content=rev.content,
        rating=rev.rating,
        ease_rating=rev.ease_rating,
        grading_method=rev.grading_method,
        is_approved=rev.is_approved,
        selected_instructor=rev.selected_instructor,
        created_at=rev.created_at,
        submitter_name=rev.submitter_name,
        nickname=rev.nickname,
        academic_year=rev.academic_year,
        student_id=rev.student_id,
    )


@app.get("/admin/reviews", response_class=HTMLResponse)
async def admin_reviews(request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        pending_rows = (await session.execute(
            select(Review, Subject.name.label("subj_name"))
            .join(CourseSection, CourseSection.id == Review.course_section_id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .where(Review.is_approved == False)
            .order_by(Review.created_at.desc())
        )).all()
        approved_rows = (await session.execute(
            select(Review, Subject.name.label("subj_name"))
            .join(CourseSection, CourseSection.id == Review.course_section_id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .where(Review.is_approved == True)
            .order_by(Review.created_at.desc())
            .limit(50)
        )).all()
    pending = [_make_review_ns(r, n) for r, n in pending_rows]
    approved = [_make_review_ns(r, n) for r, n in approved_rows]
    return templates.TemplateResponse("admin/reviews.html", {
        "request": request,
        "pending": pending,
        "approved": approved,
    })


@app.post("/admin/reviews/approve/{review_id}")
async def admin_review_approve(review_id: int, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        review = await session.get(Review, review_id)
        if review:
            review.is_approved = True
            await session.commit()
    _invalidate_review_cache()
    return RedirectResponse("/admin/reviews", status_code=303)


@app.post("/admin/reviews/reject/{review_id}")
async def admin_review_reject(review_id: int, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        review = await session.get(Review, review_id)
        if review:
            await session.delete(review)
            await session.commit()
    _invalidate_review_cache()
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
    faculty: str = Form(""),
):
    name_s = name.strip()
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(Subject).where(Subject.name == name_s)
        )).scalar_one_or_none()
        if existing:
            return RedirectResponse(
                url=f"/admin/courses?error={py_secrets.token_urlsafe(4)}&msg=duplicate",
                status_code=303,
            )
        session.add(Subject(
            name=name_s,
            classification=classification.strip() or None,
            category=category,
            reading=_reading(name_s),
            term=term.strip() or None,
            credits=credits if credits else None,
            faculty=faculty.strip() or None,
        ))
        await session.commit()
    _invalidate_courses_cache()
    _invalidate_cls_caches()
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
            select(Subject).where(Subject.classification == old_name)
        )).scalars().all()
        for course in courses:
            course.classification = new_name
        cls_row = (await session.execute(
            select(ClassificationOrder).where(ClassificationOrder.name == old_name)
        )).scalar_one_or_none()
        if cls_row:
            cls_row.name = new_name
        await session.commit()
    _invalidate_cls_caches()
    _invalidate_courses_cache()
    return RedirectResponse(url="/admin/courses", status_code=303)


@app.post("/admin/courses/classification/delete")
async def delete_classification(
    _: str = Depends(check_admin),
    classification: str = Form(...),
):
    async with AsyncSessionLocal() as session:
        courses_in_class = (await session.execute(
            select(Subject).where(Subject.classification == classification)
        )).scalars().all()
        for course in courses_in_class:
            course.classification = None
        cls_row = (await session.execute(
            select(ClassificationOrder).where(ClassificationOrder.name == classification)
        )).scalar_one_or_none()
        if cls_row:
            await session.delete(cls_row)
        await session.commit()
    _invalidate_cls_caches()
    _invalidate_courses_cache()
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
                select(Subject.classification).distinct()
            )).scalars().all() if c],
        )
        cls_map = await _get_cls_order_map()
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
    _invalidate_cls_caches()
    _invalidate_courses_cache()
    return JSONResponse({"ok": True})


@app.post("/admin/courses/classification/set_parent")
async def admin_cls_set_parent(
    _: str = Depends(check_admin),
    classification: str = Form(...),
    parent_group: str = Form(default=""),
):
    parent_group = parent_group.strip()
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            select(ClassificationOrder).where(ClassificationOrder.name == classification)
        )).scalar_one_or_none()
        if row:
            row.parent_group = parent_group or None
        else:
            session.add(ClassificationOrder(name=classification, sort_order=0, parent_group=parent_group or None))
        await session.commit()
    _invalidate_cls_caches()
    _invalidate_courses_cache()
    return RedirectResponse(url="/admin/courses", status_code=303)


@app.post("/admin/courses/{course_id}/move")
async def admin_course_move(course_id: int, request: Request, _=Depends(check_admin)):
    from fastapi.responses import JSONResponse
    data = await request.json()
    direction = data.get("direction", "")
    if direction not in ("up", "down"):
        return JSONResponse({"ok": False})

    async with AsyncSessionLocal() as session:
        course = await session.get(Subject, course_id)
        if not course:
            return JSONResponse({"ok": False})

        all_in_cls = list((await session.execute(
            select(Subject)
            .where(Subject.classification == (course.classification or ""))
            .order_by(Subject.sort_order, Subject.name)
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
    _invalidate_courses_cache()
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
    faculty: str = Form(""),
):
    async with AsyncSessionLocal() as session:
        course = (await session.execute(select(Subject).where(Subject.id == course_id))).scalar_one_or_none()
        if course:
            new_name = name.strip()
            course.name = new_name
            course.classification = classification.strip() or None
            course.category = category
            course.reading = _reading(new_name)
            course.term = term.strip() or None
            course.credits = credits if credits else None
            course.faculty = faculty.strip() or None
            await session.commit()
    _invalidate_courses_cache()
    _invalidate_cls_caches()
    return RedirectResponse(url="/admin/courses", status_code=303)


@app.post("/admin/courses/delete/{course_id}")
async def admin_courses_delete(course_id: int, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        course = (await session.execute(select(Subject).where(Subject.id == course_id))).scalar_one_or_none()
        if course:
            cs_ids = (await session.execute(
                select(CourseSection.id).where(CourseSection.subject_id == course_id)
            )).scalars().all()
            if cs_ids:
                has_approved = (await session.execute(
                    select(func.count(Review.id)).where(
                        Review.course_section_id.in_(cs_ids),
                        Review.is_approved == True,
                    )
                )).scalar()
                if has_approved:
                    return RedirectResponse(url=f"/admin/courses?msg=has_reviews", status_code=303)
            await session.delete(course)
            await session.commit()
    _invalidate_courses_cache()
    _invalidate_cls_caches()
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
            subject = await session.get(Subject, course_id)
            if not subject:
                raise HTTPException(status_code=404, detail="course not found")

        async def _cs_instr():
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(CourseSection, Instructor)
                    .join(Instructor, Instructor.id == CourseSection.instructor_id)
                    .where(CourseSection.subject_id == course_id)
                )).all()

        async def _agg(cs_ids: list):
            if not cs_ids:
                return None
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(func.avg(Review.rating), func.count(Review.id))
                    .where(Review.course_section_id.in_(cs_ids), Review.is_approved == True)
                )).first()

        async def _ease(cs_ids: list):
            if not cs_ids:
                return []
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(Review.ease_rating, func.count(Review.id))
                    .where(Review.course_section_id.in_(cs_ids), Review.is_approved == True)
                    .group_by(Review.ease_rating)
                )).all()

        async def _reviews(cs_ids: list):
            if not cs_ids:
                return []
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(Review)
                    .where(Review.course_section_id.in_(cs_ids), Review.is_approved == True)
                    .order_by(Review.selected_instructor.nulls_last(), Review.academic_year.desc())
                    .limit(20)
                )).scalars().all()

        async def _syllabus_code():
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(Syllabus.timetable_code)
                    .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
                    .where(CourseSection.subject_id == course_id)
                    .limit(1)
                )).scalar_one_or_none()

        cs_instr_rows = await _cs_instr()
        cs_ids = [cs.id for cs, _ in cs_instr_rows]

        agg, ease_rows, reviews_raw, sc_code = await asyncio.gather(
            _agg(cs_ids), _ease(cs_ids), _reviews(cs_ids), _syllabus_code()
        )

        # ビューカウント記録
        if cs_ids:
            main_cs_id = cs_ids[0]
            async with AsyncSessionLocal() as s:
                _now = datetime.now(timezone.utc)
                _ins = pg_insert(CourseSectionView).values(
                    course_section_id=main_cs_id,
                    view_count=1,
                    last_viewed_at=_now,
                )
                await s.execute(
                    _ins.on_conflict_do_update(
                        index_elements=["course_section_id"],
                        set_={
                            "view_count": CourseSectionView.view_count + 1,
                            "last_viewed_at": _now,
                        },
                    )
                )
                await s.commit()

        # 最初の非NULL syllabus_url を CourseSection から取得
        syllabus_url = next((cs.syllabus_url for cs, _ in cs_instr_rows if cs.syllabus_url), None)
        if not syllabus_url:
            syllabus_url = _make_syllabus_url(sc_code or "")
        instructor_str = "・".join(instr.name for _, instr in cs_instr_rows)
        avg_rating = float(agg[0]) if agg and agg[0] else None
        top_ease = None
        if ease_rows:
            top_ease = sorted(ease_rows, key=lambda r: (-r[1], EASE_ORDER.get(r[0], 99)))[0][0]

        return {
            "id": subject.id,
            "name": subject.name,
            "instructor": instructor_str,
            "classification": subject.classification or "",
            "category": subject.category or "",
            "term": subject.term or "",
            "credits": float(subject.credits) if subject.credits else 0,
            "syllabus_url": syllabus_url or "",
            "avg_rating": avg_rating,
            "top_ease": top_ease,
            "reviews": [
                {
                    "rating": r.rating,
                    "ease_rating": r.ease_rating,
                    "grading_method": r.grading_method or "",
                    "comment": r.content or "",
                    "instructor": r.selected_instructor or "",
                    "nickname": r.nickname or "",
                    "academic_year": r.academic_year or 0,
                }
                for r in reviews_raw
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
        uri_rows = (await session.execute(
            select(RichMenuTap.button, func.count(RichMenuTap.id).label("cnt"))
            .group_by(RichMenuTap.button)
            .order_by(func.count(RichMenuTap.id).desc())
        )).all()
        msg_btn_rows = (await session.execute(
            select(UserActivity.action, func.sum(UserActivity.count).label("cnt"))
            .where(UserActivity.action.in_(list(MSG_BTN_LABELS.keys())))
            .group_by(UserActivity.action)
            .order_by(func.sum(UserActivity.count).desc())
        )).all()
        activity_joined = (await session.execute(
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
        csv_rows = (await session.execute(
            select(CourseSectionView, Subject.name.label("subj_name"))
            .join(CourseSection, CourseSection.id == CourseSectionView.course_section_id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .order_by(CourseSectionView.view_count.desc())
        )).all()
        course_view_rows = [
            SimpleNamespace(
                course_name=subj_name,
                view_count=csv_row.view_count,
                last_viewed_at=csv_row.last_viewed_at,
            )
            for csv_row, subj_name in csv_rows
        ]

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


# ── 時間割 LIFF ──────────────────────────────────────────────────

@app.get("/liff/timetable", response_class=HTMLResponse)
async def liff_timetable(request: Request):
    return templates.TemplateResponse("liff/timetable.html", {
        "request": request,
        "liff_id": TIMETABLE_LIFF_ID,
        "base_url": APP_URL,
        "IS_DEV": IS_DEV,
        "kyoyo_required_credits": KYOYO_REQUIRED_CREDITS,
    })


@app.get("/api/timetable/profile")
async def api_timetable_profile_get(user_id: str = Query("")):
    if not user_id:
        return {"faculty": None, "grade": None}
    async with AsyncSessionLocal() as session:
        p = await session.get(TimetableProfile, user_id)
        if not p:
            return {"faculty": None, "grade": None}
        return {"faculty": p.faculty, "grade": p.grade}


@app.post("/api/timetable/profile")
async def api_timetable_profile_set(request: Request):
    data = await request.json()
    user_id = data.get("user_id", "")
    if not user_id or not _LINE_USER_ID_RE.match(user_id):
        raise HTTPException(status_code=400, detail="user_id required")
    faculty = data.get("faculty") or None
    grade = data.get("grade")
    if grade is not None:
        grade = int(grade)
        if not (1 <= grade <= 6):
            raise HTTPException(status_code=400, detail="grade must be between 1 and 6")
    async with AsyncSessionLocal() as session:
        p = await session.get(TimetableProfile, user_id)
        if p:
            p.faculty = faculty
            p.grade = grade
        else:
            session.add(TimetableProfile(line_user_id=user_id, faculty=faculty, grade=grade))
        await session.commit()
    return {"ok": True}


_VALID_DAYS = {"月", "火", "水", "木", "金", "土", "日"}

@app.get("/api/timetable/slots/{day}/{period}")
async def api_timetable_slots(day: str, period: int, user_id: str = Query("")):
    if day not in _VALID_DAYS:
        raise HTTPException(status_code=400, detail="invalid day")
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Syllabus, Subject, Instructor)
            .join(Schedule, Schedule.syllabus_id == Syllabus.id)
            .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .join(Instructor, Instructor.id == CourseSection.instructor_id)
            .where(Schedule.day_of_week == day, Schedule.period == period)
            .order_by(Syllabus.quarter, Subject.name)
        )).all()

        if not rows:
            return {"courses": []}

        syllabus_ids = [s.id for s, _, _ in rows]
        registered_ids: set[int] = set()
        if user_id:
            regs = (await session.execute(
                select(UserSyllabus.syllabus_id).where(
                    UserSyllabus.line_user_id == user_id,
                    UserSyllabus.syllabus_id.in_(syllabus_ids),
                )
            )).scalars().all()
            registered_ids = set(regs)

        return {
            "courses": [
                {
                    "id": syl.id,
                    "name": subj.name,
                    "instructor": instr.name,
                    "term": syl.quarter,
                    "timetable_code": syl.timetable_code or "",
                    "department": syl.department or "",
                    "target_grades": syl.target_grades or "",
                    "subject_category": syl.subject_category or "",
                    "registered": syl.id in registered_ids,
                }
                for syl, subj, instr in rows
            ]
        }


def _credits_from_term(term: str | None) -> int:
    if not term:
        return 2
    if "クォーター" in term:
        return 1
    if term in ("前期", "後期") or "セメスター" in term:
        return 2
    if "通年" in term:
        return 4
    return 2


@app.get("/api/timetable/my")
async def api_timetable_my(user_id: str = Query("")):
    if not user_id:
        return {"courses": []}
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(UserSyllabus, Syllabus, Schedule, Subject, Instructor)
            .join(Syllabus, Syllabus.id == UserSyllabus.syllabus_id)
            .join(Schedule, Schedule.syllabus_id == Syllabus.id)
            .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .join(Instructor, Instructor.id == CourseSection.instructor_id)
            .where(UserSyllabus.line_user_id == user_id)
        )).all()

        result = {}
        for us, syl, sch, subj, instr in rows:
            if syl.id not in result:
                result[syl.id] = {
                    "id": syl.id,
                    "name": subj.name,
                    "instructor": instr.name,
                    "term": syl.quarter,
                    "credits": _credits_from_term(syl.quarter),
                    "slots": [],
                }
            result[syl.id]["slots"].append({"day": sch.day_of_week, "period": sch.period})

        return {"courses": list(result.values())}


@app.post("/api/timetable/register/{syllabus_id}")
async def api_timetable_register(syllabus_id: int, request: Request):
    body = await request.json()
    user_id = body.get("user_id", "")
    if not user_id or not _LINE_USER_ID_RE.match(user_id):
        raise HTTPException(status_code=400, detail="user_id required")
    async with AsyncSessionLocal() as session:
        syl = await session.get(Syllabus, syllabus_id)
        if not syl:
            raise HTTPException(status_code=404, detail="course not found")
        await session.execute(
            pg_insert(UserSyllabus)
            .values(line_user_id=user_id, syllabus_id=syllabus_id)
            .on_conflict_do_nothing(index_elements=["line_user_id", "syllabus_id"])
        )
        await session.commit()
    return {"ok": True}


@app.delete("/api/timetable/register/{syllabus_id}")
async def api_timetable_unregister(syllabus_id: int, user_id: str = Query("")):
    if not user_id or not _LINE_USER_ID_RE.match(user_id):
        raise HTTPException(status_code=400, detail="user_id required")
    async with AsyncSessionLocal() as session:
        us = (await session.execute(
            select(UserSyllabus).where(
                UserSyllabus.line_user_id == user_id,
                UserSyllabus.syllabus_id == syllabus_id,
            )
        )).scalar_one_or_none()
        if us:
            await session.delete(us)
            await session.commit()
    return {"ok": True}


# ── 経営学部 成績表 PDF パース ──────────────────────────────────────────────

_SENMON1 = {'経営学基礎論', '会計学基礎論', '市場システム基礎論'}

# 完全一致で判定する第2群科目
# 「会計学特殊講義」「経営学入門演習」等の3群科目と区別するため完全一致のみ使用
_SENMON2_EXACT = {
    # 旧カリキュラム第2群科目（経営.pdf 掲載11科目）
    '経営管理', '経営戦略', '経営史', '経営数学', '経営統計',
    'コーポレートファイナンス', '財務会計', '管理会計',
    'マーケティング', '金融システム', '交通論',
    # 2026年度カリキュラム追加（ナンバリングコード B1BB202 確認済み）
    '経済学', '統計学', '民法', '数学Ⅰ', '数学Ⅱ',
    '経営学入門', '会計学', '国際経営', '経営組織', '財務管理',
    '生産管理', '経営情報', 'ビジネス法',
}
# 前方一致が必要な科目（旧カリ「簿記」・新カリ「簿記Ⅰ」等のバリアントを同一視）
_SENMON2_PREFIX = ('簿記', '数学（')

_TERM_PAT = _re.compile(r'前期|[12]Q|第[12]クオーター|第[12]Q')

_GLOBAL_PREFIXES = (
    'Academic Reading and Writing',
    'International Business',
    'International Management',
    'Introduction to Finance',
    'Introduction to Marketing',
    'Introduction to Management',
    'Introduction to Accounting',
    'Business Presentation',
    'Business Strategy',
    'Business Leadership',
    'Advanced Financial',
    'Advanced Study',
    'Portfolio Management',
    'Portfolio Theory',
    'Entrepreneurial',
    'Capstone',
    'Overview of Corporate',
    'Foundations of Securities',
    'Managerial Accounting',
    'Organization Theory',
    'Marketing Management',
    'Corporate Finance',
    'Operations Management',
    'Statistics for Business',
    'Sustainability Management',
    'Innovation and',
    'Supply Chain',
    'Brand Management',
    'Mergers and',
    'Human Resource Management',
    'Global ',
    '外国文献講義',
    '外国書講読',
)
_GAIGO_FOREIGN = ('ロシア語', 'ドイツ語', 'フランス語', '中国語', '韓国語', 'スペイン語',
                   'アラビア語', 'イタリア語', 'ポルトガル語', '朝鮮語')


def _is_senmon2(name: str) -> bool:
    return name in _SENMON2_EXACT or any(name.startswith(p) for p in _SENMON2_PREFIX)


def _classify_senmon(name: str) -> str:
    """専門科目を群に分類する。DBに登録があればそちらを優先。"""
    db = _senmon_name_to_group.get(name)
    if db:
        return db
    if '初年次セミナー' in name:
        return '初年次'
    if name in _SENMON1:
        return '第1群'
    if _is_senmon2(name):
        return '第2群'
    if any(name.startswith(p) for p in _GLOBAL_PREFIXES):
        return 'グローバル'
    return '第3群'


def _extract_seiseki_raw(text: str) -> dict:
    """PDFテキストから生の科目リストとサマリー値を抽出する（分類はしない）。"""
    gaigo_courses: list[dict] = []
    senmon_courses: list[dict] = []
    current_sec = ''
    course_re = _re.compile(r'^(?:＊\s+)?(.+?)\s+(\d+(?:\.\d+)?)\s+(秀|優|良|可|不可|合格|認定)')
    for line in text.splitlines():
        sec_m = _re.search(r'【(.*?)】', line)
        if sec_m:
            current_sec = sec_m.group(1)
            continue
        m = course_re.match(line.strip())
        if not m:
            continue
        name = m.group(1).strip()
        cr = float(m.group(2))
        if '外国語' in current_sec:
            gaigo_courses.append({
                "name": name, "credits": cr,
                "is_english": 'Academic English' in name,
                "is_foreign": any(name.startswith(p) for p in _GAIGO_FOREIGN),
            })
        elif '専門科目' in current_sec:
            senmon_courses.append({"name": name, "credits": cr})

    def _summary(label: str) -> float:
        mt = _re.search(_re.escape(label) + r'\s+([\d.]+)', text)
        return float(mt.group(1)) if mt else 0.0

    return {
        "gaigo_courses": gaigo_courses,
        "senmon_courses": senmon_courses,
        "summaries": {
            "総合教養科目":   _summary('総合教養科目'),
            "基礎教養科目":   _summary('基礎教養科目'),
            "情報科目":       _summary('情報科目'),
            "共通専門基礎科目": _summary('共通専門基礎科目'),
            "専門科目":       _summary('専門科目'),
            "外国語科目":     _summary('外国語科目'),
        },
    }


def _classify_seiseki_raw(raw: dict) -> dict:
    """生データから単位区分の合計を計算する（DB分類を参照）。"""
    s = raw.get("summaries", {})
    gaigo1 = gaigo2 = 0.0
    for c in raw.get("gaigo_courses", []):
        if c.get("is_english"):
            gaigo1 += c["credits"]
        elif c.get("is_foreign"):
            gaigo2 += c["credits"]
    gaiko_total = s.get("外国語科目", 0.0)
    if gaigo1 + gaigo2 == 0 and gaiko_total > 0:
        gaigo1 = gaigo2 = round(gaiko_total / 2, 1)

    shonen = senmon1 = senmon2 = global_c = 0.0
    for c in raw.get("senmon_courses", []):
        grp = _classify_senmon(c["name"])
        cr = c["credits"]
        if grp == '初年次':   shonen   += cr
        elif grp == '第1群':  senmon1  += cr
        elif grp == '第2群':  senmon2  += cr
        elif grp == 'グローバル': global_c += cr

    senmon_total = s.get("専門科目", 0.0)
    senmon3 = max(0.0, round(senmon_total - shonen - senmon1 - senmon2 - global_c, 1))
    return {
        "kyoyo_kei":   round(s.get("総合教養科目", 0.0), 1),
        "kyoyo_kiban": round(s.get("基礎教養科目", 0.0) + s.get("情報科目", 0.0), 1),
        "gaigo1":  round(gaigo1, 1), "gaigo2":  round(gaigo2, 1),
        "kyotsu":  round(s.get("共通専門基礎科目", 0.0), 1),
        "shonen":  round(shonen, 1),  "senmon1": round(senmon1, 1),
        "senmon2": round(senmon2, 1), "global":  round(global_c, 1),
        "senmon3": round(senmon3, 1),
    }


def _parse_seiseki_pdf(data: bytes) -> dict:
    if not _PDFPLUMBER_OK:
        raise RuntimeError("pdfplumber not available")
    with _pdfplumber.open(io.BytesIO(data)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    gpa = None
    gpa_m = _re.search(r'G\s*P\s*A\s+[\d.]+\s+\d+\s+([\d.]+)', text)
    if gpa_m:
        gpa = float(gpa_m.group(1))
    raw = _extract_seiseki_raw(text)
    return {"gpa": gpa, "credits": _classify_seiseki_raw(raw), "raw": raw}


@app.post("/api/parse_seiseki")
async def api_parse_seiseki(request: Request, file: UploadFile = File(...)):
    if not _PDFPLUMBER_OK:
        raise HTTPException(status_code=503, detail="PDF parsing not available")
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF ファイルを送ってください")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="ファイルサイズが大きすぎます（10MB 以下）")
    try:
        result = _parse_seiseki_pdf(data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"PDF の解析に失敗しました: {e}")
    uid = request.headers.get("X-Line-User-Id", "").strip()
    if uid and _LINE_USER_ID_RE.match(uid):
        async with AsyncSessionLocal() as session:
            existing = await session.get(UserSeisekiRaw, uid)
            raw_data = result["raw"]
            if existing:
                existing.raw_json = raw_data
            else:
                session.add(UserSeisekiRaw(line_user_id=uid, raw_json=raw_data))
            await session.commit()
    return result


@app.post("/api/reclassify_seiseki")
async def api_reclassify_seiseki(request: Request):
    body = await request.json()
    raw = body.get("raw")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="raw data required")
    return {"credits": _classify_seiseki_raw(raw)}


@app.get("/api/seiseki/credits")
async def api_seiseki_credits(uid: str):
    if not uid or not _LINE_USER_ID_RE.match(uid):
        return {}
    async with AsyncSessionLocal() as session:
        row = await session.get(UserSeisekiRaw, uid)
    if not row:
        return {}
    raw = row.raw_json
    return {"credits": _classify_seiseki_raw(raw)}


@app.post("/api/seiseki/save_raw")
async def api_seiseki_save_raw(request: Request):
    body = await request.json()
    uid = body.get("uid", "").strip()
    raw = body.get("raw")
    if not uid or not _LINE_USER_ID_RE.match(uid) or not raw:
        raise HTTPException(status_code=400, detail="uid and raw required")
    async with AsyncSessionLocal() as session:
        existing = await session.get(UserSeisekiRaw, uid)
        if existing:
            existing.raw_json = raw
        else:
            session.add(UserSeisekiRaw(line_user_id=uid, raw_json=raw))
        await session.commit()
    return {"ok": True}


@app.get("/api/credit_requirements")
async def api_credit_requirements(faculty: str = Query("経営学部")):
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(CreditRequirement)
            .where(CreditRequirement.faculty == faculty)
            .order_by(CreditRequirement.sort_order)
        )).scalars().all()
        cc_rows = (await session.execute(
            select(SubjectCreditCategory.category_id, Subject.name)
            .join(Subject, Subject.id == SubjectCreditCategory.subject_id)
        )).all()
    courses_by_cat: dict[str, list[str]] = {}
    for cat_id, course_name in cc_rows:
        courses_by_cat.setdefault(cat_id, []).append(course_name)
    return [
        {
            "category_id":      r.category_id,
            "label":            r.label,
            "group_name":       r.group_name,
            "sort_order":       r.sort_order,
            "required_credits": r.required_credits,
            "note":             r.note or "",
            "approved_courses": courses_by_cat.get(r.category_id, []),
        }
        for r in rows
    ]



# ── 時間割照合ページ ──────────────────────────────────────────────────────────

@app.get("/admin/timetable/check", response_class=HTMLResponse)
async def admin_timetable_check(request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        course_rows = (await session.execute(select(Subject.id, Subject.name, Subject.faculty))).all()
        syllabus_rows = (await session.execute(
            select(Subject.name, Syllabus.department).distinct()
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Syllabus, Syllabus.course_section_id == CourseSection.id)
        )).all()

    course_name_set   = {name for _, name, _ in course_rows}
    syllabus_name_set = {name for name, _ in syllabus_rows}

    # courses にあるが syllabus_courses にない（科目名に学期語句を含む科目は除外）
    only_in_courses = [
        {"id": cid, "name": name, "faculty": faculty or ""}
        for cid, name, faculty in course_rows
        if name not in syllabus_name_set and not _TERM_PAT.search(name)
    ]
    # syllabus_courses にあるが courses にない（重複除去済み名前一覧）
    only_in_syllabus = sorted(
        {(name, dept) for name, dept in syllabus_rows if name not in course_name_set},
        key=lambda x: x[0]
    )
    # 両方に存在
    matched = [
        {"id": cid, "name": name, "faculty": faculty or ""}
        for cid, name, faculty in course_rows
        if name in syllabus_name_set
    ]

    return templates.TemplateResponse("admin/timetable_check.html", {
        "request": request,
        "matched":          matched,
        "only_in_courses":  only_in_courses,
        "only_in_syllabus": only_in_syllabus,
        "total_courses":    len(course_rows),
        "total_syllabus":   len(syllabus_name_set),
    })


# ── 経営学部 管理ページ ────────────────────────────────────────────────────────

_SENMON_GROUPS = ["第1群", "第2群", "第3群", "グローバル", "初年次"]

@app.get("/admin/keiei", response_class=HTMLResponse)
async def admin_keiei(request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        courses = (await session.execute(
            select(Subject)
            .where(Subject.faculty.like("%経営学部%"))
            .order_by(Subject.classification, Subject.sort_order, Subject.name)
        )).scalars().all()
        reqs = (await session.execute(
            select(CreditRequirement).order_by(CreditRequirement.sort_order)
        )).scalars().all()
        kyotsu_candidates = (await session.execute(
            select(Subject.name, Subject.credits)
            .where(Subject.classification.like("%共通専門基礎%"))
            .order_by(Subject.sort_order, Subject.name)
        )).all()
        approved_rows = (await session.execute(
            select(SubjectCreditCategory.category_id, Subject.name)
            .join(Subject, Subject.id == SubjectCreditCategory.subject_id)
        )).all()
    approved_by_cat: dict[str, set[str]] = {}
    for cat_id, course_name in approved_rows:
        approved_by_cat.setdefault(cat_id, set()).add(course_name)
    auto_groups = {c.id: _classify_senmon(c.name) for c in courses}
    return templates.TemplateResponse("admin/keiei.html", {
        "request": request,
        "courses": courses,
        "senmon_groups": _SENMON_GROUPS,
        "reqs": reqs,
        "auto_groups": auto_groups,
        "kyotsu_candidates": kyotsu_candidates,
        "approved_by_cat": approved_by_cat,
    })


@app.post("/admin/keiei/category_courses/{cat_id}")
async def admin_save_category_courses(cat_id: str, request: Request, _: str = Depends(check_admin)):
    from fastapi.responses import JSONResponse as _JSONResponse
    form = await request.form()
    checked = form.getlist("courses")
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(SubjectCreditCategory).where(SubjectCreditCategory.category_id == cat_id)
        )
        for name in checked:
            subj = (await session.execute(
                select(Subject.id, Subject.credits).where(Subject.name == name)
            )).first()
            if subj:
                session.add(SubjectCreditCategory(
                    subject_id=subj.id,
                    category_id=cat_id,
                    credits=float(subj.credits) if subj.credits else 2.0,
                ))
        await session.commit()
    return _JSONResponse({"ok": True})


@app.post("/admin/keiei/courses/{course_id}/senmon_group")
async def admin_keiei_set_group(course_id: int, request: Request, _: str = Depends(check_admin)):
    from fastapi.responses import JSONResponse
    body = await request.json()
    group = body.get("group") or None
    if group and group not in _SENMON_GROUPS:
        raise HTTPException(status_code=400, detail="invalid group")
    async with AsyncSessionLocal() as session:
        course = await session.get(Subject, course_id)
        if not course:
            raise HTTPException(status_code=404)
        course.senmon_group = group
        await session.commit()
    _invalidate_senmon_cache()
    asyncio.create_task(_reload_senmon_cache())
    _invalidate_courses_cache()
    return JSONResponse({"ok": True})


@app.post("/admin/keiei/credit_requirements/update")
async def admin_keiei_update_requirements(request: Request, _: str = Depends(check_admin)):
    form = await request.form()
    async with AsyncSessionLocal() as session:
        existing_ids = {
            r for (r,) in (await session.execute(select(CreditRequirement.category_id))).all()
        }
        for cat_id in existing_ids:
            values: dict = {}
            if f"req_{cat_id}" in form:
                try:
                    values["required_credits"] = max(0, int(form[f"req_{cat_id}"]))
                except ValueError:
                    pass
            if f"note_{cat_id}" in form:
                note_val = form[f"note_{cat_id}"].strip()
                values["note"] = note_val or None
            if f"label_{cat_id}" in form:
                values["label"] = form[f"label_{cat_id}"].strip()
            if f"group_{cat_id}" in form:
                values["group_name"] = form[f"group_{cat_id}"].strip()
            if f"sort_{cat_id}" in form:
                try:
                    values["sort_order"] = int(form[f"sort_{cat_id}"])
                except ValueError:
                    pass
            if values:
                await session.execute(
                    sa_update(CreditRequirement)
                    .where(CreditRequirement.category_id == cat_id)
                    .values(**values)
                )
        await session.commit()
    return RedirectResponse("/admin/keiei", status_code=303)


@app.post("/admin/keiei/credit_requirements/add")
async def admin_keiei_add_requirement(request: Request, _: str = Depends(check_admin)):
    form = await request.form()
    label  = form.get("new_label", "").strip()
    group  = form.get("new_group", "").strip()
    note   = form.get("new_note", "").strip() or None
    if not label:
        return RedirectResponse("/admin/keiei?error=invalid", status_code=303)
    try:
        req    = max(0, int(form.get("new_req", "0")))
        sort_v = int(form.get("new_sort", "999"))
    except ValueError:
        req, sort_v = 0, 999
    cat_id = f"cat_{int(time.time() * 1000)}"
    async with AsyncSessionLocal() as session:
        session.add(CreditRequirement(
            category_id=cat_id,
            label=label,
            group_name=group,
            sort_order=sort_v,
            required_credits=req,
            note=note,
        ))
        await session.commit()
    return RedirectResponse("/admin/keiei", status_code=303)


@app.post("/admin/keiei/credit_requirements/{cat_id}/delete")
async def admin_keiei_delete_requirement(cat_id: str, request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        row = await session.get(CreditRequirement, cat_id)
        if row:
            await session.delete(row)
            await session.commit()
    return RedirectResponse("/admin/keiei", status_code=303)


# ── システム情報学部 管理ページ ────────────────────────────────────────────────

@app.get("/admin/sysinfo", response_class=HTMLResponse)
async def admin_sysinfo(request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        reqs = (await session.execute(
            select(CreditRequirement)
            .where(CreditRequirement.faculty == "システム情報学部")
            .order_by(CreditRequirement.sort_order)
        )).scalars().all()
        courses = (await session.execute(
            select(Subject)
            .where(Subject.faculty.like("%システム情報学部%"))
            .order_by(Subject.name)
        )).scalars().all()
    return templates.TemplateResponse("admin/sysinfo.html", {
        "request": request,
        "reqs": reqs,
        "courses": courses,
    })


@app.post("/admin/sysinfo/credit_requirements/update")
async def admin_sysinfo_update_requirements(request: Request, _: str = Depends(check_admin)):
    form = await request.form()
    async with AsyncSessionLocal() as session:
        existing_ids = {
            r for (r,) in (await session.execute(
                select(CreditRequirement.category_id)
                .where(CreditRequirement.faculty == "システム情報学部")
            )).all()
        }
        for cat_id in existing_ids:
            values: dict = {}
            if f"req_{cat_id}" in form:
                try:
                    values["required_credits"] = max(0, int(form[f"req_{cat_id}"]))
                except ValueError:
                    pass
            if f"note_{cat_id}" in form:
                note_val = form[f"note_{cat_id}"].strip()
                values["note"] = note_val or None
            if f"label_{cat_id}" in form:
                values["label"] = form[f"label_{cat_id}"].strip()
            if f"group_{cat_id}" in form:
                values["group_name"] = form[f"group_{cat_id}"].strip()
            if f"sort_{cat_id}" in form:
                try:
                    values["sort_order"] = int(form[f"sort_{cat_id}"])
                except ValueError:
                    pass
            if values:
                await session.execute(
                    sa_update(CreditRequirement)
                    .where(CreditRequirement.category_id == cat_id)
                    .values(**values)
                )
        await session.commit()
    return RedirectResponse("/admin/sysinfo", status_code=303)


@app.post("/admin/sysinfo/credit_requirements/add")
async def admin_sysinfo_add_requirement(request: Request, _: str = Depends(check_admin)):
    form = await request.form()
    label  = form.get("new_label", "").strip()
    group  = form.get("new_group", "").strip()
    note   = form.get("new_note", "").strip() or None
    if not label:
        return RedirectResponse("/admin/sysinfo?error=invalid", status_code=303)
    try:
        req    = max(0, int(form.get("new_req", "0")))
        sort_v = int(form.get("new_sort", "999"))
    except ValueError:
        req, sort_v = 0, 999
    cat_id = f"si_{int(time.time() * 1000)}"
    async with AsyncSessionLocal() as session:
        session.add(CreditRequirement(
            category_id=cat_id,
            label=label,
            group_name=group,
            sort_order=sort_v,
            required_credits=req,
            note=note,
            faculty="システム情報学部",
        ))
        await session.commit()
    return RedirectResponse("/admin/sysinfo", status_code=303)


@app.post("/admin/sysinfo/credit_requirements/{cat_id}/delete")
async def admin_sysinfo_delete_requirement(cat_id: str, request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        row = await session.get(CreditRequirement, cat_id)
        if row:
            await session.delete(row)
            await session.commit()
    return RedirectResponse("/admin/sysinfo", status_code=303)
