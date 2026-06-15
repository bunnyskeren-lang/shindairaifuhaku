import os
import re as _re
import json
import asyncio
import random
import traceback as _traceback
import hashlib
import hmac
import base64
import secrets as py_secrets
from collections import defaultdict
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
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent

from sqlalchemy import select, func, delete, or_, update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, AsyncSessionLocal
from models import MessageLog, Course, PendingReview, UserPreference, UserProfile, UserActivity, ErrorLog, PushSubscription, CourseInstructor

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
STUDENT_ID_RE = _re.compile(r'^\d{7}(MM|ME|MH|[LHJEBSTAZ])$')
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

_CLS_ORDER_KEYS = ["基盤", "人文", "社会", "自然", "総合", "健康", "外国語"]

def _cls_order(name: str) -> int:
    for i, kw in enumerate(_CLS_ORDER_KEYS):
        if kw in (name or ""):
            return i
    return len(_CLS_ORDER_KEYS)

EASE_ORDER = {"SS": 0, "S": 1, "A": 2, "B": 3, "C": 4}
EASE_LABEL = {"SS": "超楽 😴😴", "S": "楽 😴", "A": "普通 😊", "B": "きつめ 😤", "C": "激ムズ 😰"}
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
    asyncio.create_task(_self_ping())
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
    review_count: int,
    top_ease: Optional[str],
    grading_summary: dict,
    comments: list[str],
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

    body_contents.append(FlexSeparator(margin="lg"))

    # 学びになった度
    if avg_rating is not None:
        body_contents.append(
            FlexText(text="⭐ 学びになった度", size="xs", color="#64748b", weight="bold", margin="md")
        )
        body_contents.append(
            FlexBox(
                layout="horizontal",
                contents=[
                    FlexText(text=stars(round(avg_rating)), size="lg", color="#f59e0b", flex=0),
                    FlexText(text=f"  {avg_rating:.1f}点", size="sm", color="#475569", margin="sm"),
                    FlexText(text=f"({review_count}件)", size="xs", color="#94a3b8", margin="sm"),
                ],
                margin="xs",
            )
        )
    else:
        body_contents.append(
            FlexText(text="⭐ まだレビューなし", size="sm", color="#94a3b8", margin="md")
        )

    # 楽単度
    if top_ease:
        body_contents.append(
            FlexText(text="😴 楽単度", size="xs", color="#64748b", weight="bold", margin="md")
        )
        body_contents.append(
            FlexBox(
                layout="horizontal",
                contents=[
                    FlexBox(
                        layout="horizontal",
                        contents=[
                            FlexText(
                                text=EASE_LABEL.get(top_ease, top_ease),
                                size="sm",
                                color="#ffffff",
                                weight="bold",
                            ),
                        ],
                        background_color=EASE_COLOR.get(top_ease, "#6366f1"),
                        corner_radius="12px",
                        padding_x="lg",
                        padding_y="sm",
                    ),
                ],
                margin="xs",
            )
        )

    # 成績評価方法
    grading_items = []
    if grading_summary.get("attendance"):
        grading_items.append(("🏫 出席", grading_summary["attendance"]))
    if grading_summary.get("homework"):
        grading_items.append(("📝 課題", grading_summary["homework"]))
    if grading_summary.get("evals"):
        grading_items.append(("🎯 評価", "・".join(grading_summary["evals"])))

    if grading_items:
        body_contents.append(FlexSeparator(margin="lg"))
        body_contents.append(
            FlexText(text="📋 成績評価方法", size="xs", color="#64748b", weight="bold", margin="md")
        )
        for label, val in grading_items:
            body_contents.append(
                FlexBox(
                    layout="horizontal",
                    contents=[
                        FlexText(text=label, size="xs", color="#64748b", flex=2),
                        FlexText(text=val, size="xs", color="#334155", flex=4, wrap=True),
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
    from collections import Counter as _Counter
    agg = (await session.execute(
        select(func.avg(PendingReview.rating), func.count(PendingReview.id))
        .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
    )).first()
    avg_rating = float(agg[0]) if agg and agg[0] else None
    review_count = int(agg[1]) if agg and agg[1] else 0

    ease_rows = (await session.execute(
        select(PendingReview.ease_rating, func.count(PendingReview.id))
        .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
        .group_by(PendingReview.ease_rating)
    )).all()
    top_ease = None
    if ease_rows:
        top_ease = sorted(ease_rows, key=lambda r: EASE_ORDER.get(r[0], 99))[0][0]

    grading_rows = (await session.execute(
        select(PendingReview.grading_method)
        .where(
            PendingReview.course_name == course.name,
            PendingReview.is_approved == True,
            PendingReview.grading_method.isnot(None),
        )
    )).scalars().all()
    att_c, hw_c, eval_c = _Counter(), _Counter(), _Counter()
    for gm in grading_rows:
        if not gm:
            continue
        for part in gm.split(" / "):
            if ":" not in part:
                continue
            key, val = part.split(":", 1)
            key, val = key.strip(), val.strip()
            if key == "出席":
                att_c[val] += 1
            elif key == "課題":
                hw_c[val] += 1
            elif key == "評価":
                for ev in val.split("・"):
                    ev = ev.strip()
                    if ev:
                        eval_c[ev] += 1
    grading_summary = {
        "attendance": att_c.most_common(1)[0][0] if att_c else None,
        "homework": hw_c.most_common(1)[0][0] if hw_c else None,
        "evals": [ev for ev, _ in eval_c.most_common(3)],
    }

    limit = await get_user_max_reviews(session, user_id)
    comments = (await session.execute(
        select(PendingReview.comment)
        .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
        .order_by(PendingReview.created_at.desc())
        .limit(limit)
    )).scalars().all()

    instructors = (await session.execute(
        select(CourseInstructor).where(CourseInstructor.course_id == course.id)
    )).scalars().all()
    instructor_str = "・".join(i.name for i in instructors) or course.instructor or "未設定"

    url = f"{REVIEW_FORM_URL}?uid={user_id}" if user_id else REVIEW_FORM_URL
    bubble = make_course_bubble(
        course.name, instructor_str, course.classification,
        avg_rating, review_count, top_ease, grading_summary, list(comments),
        review_url=url,
        syllabus_url=course.syllabus_url or "",
    )
    return FlexMessage(alt_text=f"📖 {course.name}", contents=bubble)


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


def make_variant_selection_bubble(base_name: str, variant_names: list[str]) -> FlexMessage:
    suffix_str = " / ".join(n[-1] for n in variant_names)
    btns = []
    for i, name in enumerate(variant_names):
        icon = VARIANT_ICONS.get(i, "▶")
        color = VARIANT_COLORS[i % len(VARIANT_COLORS)]
        btns.append(
            FlexButton(
                action=MessageAction(label=f"{icon} {name}", text=name),
                style="primary",
                color=color,
                height="sm",
            )
        )
    return FlexMessage(
        alt_text=f"📚 {base_name} — {suffix_str} どれを見ますか？",
        contents=FlexBubble(
            header=FlexBox(
                layout="vertical",
                contents=[
                    FlexText(text=base_name, weight="bold", color="#ffffff", size="lg", wrap=True),
                    FlexText(text=f"{suffix_str}  どれを見ますか？", color="#c7d2fe", size="sm"),
                ],
                background_color="#6366f1",
                padding_all="lg",
            ),
            body=FlexBox(
                layout="vertical",
                contents=btns,
                spacing="md",
                padding_all="lg",
            ),
        ),
    )


async def handle_course_list(category: str = "") -> list:
    from collections import defaultdict
    async with AsyncSessionLocal() as session:
        stmt = select(Course).order_by(Course.name)
        if category:
            stmt = stmt.where(Course.category == category)
        rows = (await session.execute(stmt)).scalars().all()
        rows = sorted(rows, key=lambda c: (_cls_order(c.classification or ""), c.name or ""))

        if not rows:
            label = f"{category}の" if category else ""
            return [TextMessage(text=f"まだ{label}科目が登録されていません。")]

        course_name_set = {c.name for c in rows}
        seen_base: set[str] = set()

        # Pre-compute numeric variant groups
        _num_bases: dict[str, list] = defaultdict(list)
        for _c in rows:
            _m = _re.match(r'^(.*?)[\s　]*(\d+)$', _c.name)
            if _m:
                _b = _m.group(1).strip()
                _num_bases[_b].append((_c.name, int(_m.group(2))))
        _num_variant_names = {n for _b, _items in _num_bases.items() if len(_items) >= 2 for n, _ in _items}
        _num_base_for = {n: _b for _b, _items in _num_bases.items() if len(_items) >= 2 for n, _ in _items}
        seen_num_base: set[str] = set()

        groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for course in rows:
            name = course.name
            classification = course.classification or "その他"
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
                    suffix = "/".join(str(n) for _, n in nums_sorted)
                    groups[classification].append((base, f"numvariant:{suffix}"))
                continue
            groups[classification].append((name, "single"))

        CAROUSEL_MAX = 12
        all_groups = sorted(groups.items(), key=lambda x: _cls_order(x[0]))
        visible_groups = all_groups[:CAROUSEL_MAX]
        overflow_groups = all_groups[CAROUSEL_MAX:]

        bubbles = []
        for classification, entries in visible_groups:
            btn_contents = []
            for name, kind in entries:
                if kind.startswith("variant:") or kind.startswith("numvariant:"):
                    suffix = kind.split(":", 1)[1]
                    display = f"{name} ({suffix})"
                else:
                    display = name
                btn_contents.append(
                    FlexBox(
                        layout="vertical",
                        action=MessageAction(label=display[:40], text=name),
                        contents=[
                            FlexText(
                                text=display,
                                wrap=True,
                                size="sm",
                                color="#4f46e5",
                            )
                        ],
                        padding_top="sm",
                        padding_bottom="sm",
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
        if not bubbles:
            return [TextMessage(text="科目が登録されていません。")]
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

async def handle_message(text: str, user_id: str = "") -> list:
    t = text.strip()

    if t in ["科目一覧", "科目", "授業一覧", "一覧"]:
        return await handle_course_list()

    if t in ["教養", "教養科目", "教養一覧"]:
        return await handle_course_list(category="教養")

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

        # Exact course name match
        exact = (await session.execute(
            select(Course).where(Course.name == t)
        )).scalar_one_or_none()
        if exact:
            return [await get_course_flex(session, exact, user_id)]

        # Variant group (A/B/C/D...)
        _variant_names = [t + s for s in ('A', 'B', 'C', 'D')]
        variant_courses = (await session.execute(
            select(Course).where(Course.name.in_(_variant_names)).order_by(Course.name)
        )).scalars().all()
        if len(variant_courses) >= 2:
            return [make_variant_selection_bubble(t, [c.name for c in variant_courses])]

        # Numeric variant group (e.g. 「英語1」「英語2」)
        _num_candidates = (await session.execute(
            select(Course).where(Course.name.like(f"{t}%")).order_by(Course.name)
        )).scalars().all()
        _num_pat = _re.compile(r'^' + _re.escape(t) + r'[\s　]*\d+$')
        _num_variants = [c for c in _num_candidates if _num_pat.match(c.name)]
        if len(_num_variants) >= 2:
            return [make_variant_selection_bubble(t, [c.name for c in _num_variants])]

        # Keyword search
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
                        result.append(make_variant_selection_bubble(base, variants))
                        continue
                _m2 = _re.match(r'^(.*?)[\s　]*\d+$', name)
                if _m2:
                    base = _m2.group(1).strip()
                    if base in seen_num_base:
                        continue
                    num_vs = _kw_num_bases.get(base, [])
                    if len(num_vs) >= 2:
                        seen_num_base.add(base)
                        result.append(make_variant_selection_bubble(base, sorted(num_vs)))
                        continue
                result.append(await get_course_flex(session, c, user_id))
            return result[:5]

    return [TextMessage(
        text=f"「{t}」に一致する科目が見つかりませんでした。\n\n「科目一覧」で登録科目を確認するか、「ヘルプ」で使い方をご確認ください。"
    )]


# ── Routes ──────────────────────────────────────────────────────

async def _process_events(events) -> None:
    if random.random() < 0.02:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        async with AsyncSessionLocal() as session:
            await session.execute(delete(MessageLog).where(MessageLog.created_at < cutoff))
            await session.commit()

    async with AsyncSessionLocal() as session:
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

                if not isinstance(event, MessageEvent):
                    continue
                if not isinstance(event.message, TextMessageContent):
                    continue

                user_id = event.source.user_id
                user_text = event.message.text

                try:
                    await save_log(session, user_id, "in", user_text)
                    messages = await asyncio.wait_for(
                        handle_message(user_text, user_id),
                        timeout=25.0,
                    )
                    await save_log(session, user_id, "out", f"[{len(messages)} msg(s)]")
                    await line_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=messages[:5],
                        )
                    )
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

    asyncio.create_task(_process_events(events))
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, uid: str = Query(default="")):
    is_new_user = False
    if uid:
        async with AsyncSessionLocal() as session:
            profile = (await session.execute(
                select(UserProfile).where(UserProfile.line_user_id == uid)
            )).scalar_one_or_none()
            is_new_user = profile is None
    return templates.TemplateResponse(
        "form_index.html",
        {"request": request, "uid": uid, "is_new_user": is_new_user},
    )


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
            stmt = stmt.order_by(Course.name).limit(10)
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
):
    if not (1 <= rating <= 5):
        raise HTTPException(status_code=400, detail="Invalid rating")
    if ease_rating not in ("SS", "S", "A", "B", "C"):
        raise HTTPException(status_code=400, detail="Invalid ease_rating")

    async with AsyncSessionLocal() as session:
        uid = line_user_id.strip()
        if uid:
            existing = (await session.execute(
                select(UserProfile).where(UserProfile.line_user_id == uid)
            )).scalar_one_or_none()
            if existing is None:
                if not reg_name.strip():
                    raise HTTPException(status_code=400, detail="お名前を入力してください")
                if not STUDENT_ID_RE.match(student_id.strip()):
                    raise HTTPException(
                        status_code=400,
                        detail="学籍番号の形式が正しくありません（例：2345678S、医学部は2345678MM）",
                    )
                session.add(UserProfile(
                    line_user_id=uid,
                    name=reg_name.strip()[:100],
                    student_id=student_id.strip(),
                ))

        review = PendingReview(
            submitter_name=submitter_name.strip()[:20],
            course_name=course_name.strip()[:200],
            rating=rating,
            ease_rating=ease_rating,
            grading_method=grading_method.strip()[:500] or None,
            comment=comment.strip()[:500],
            selected_instructor=selected_instructor.strip()[:100] or None,
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
            base_stmt.order_by(Course.name)
        )).scalars().all()
        courses = sorted(courses, key=lambda c: (_cls_order(c.classification or ""), c.name or ""))
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
        class_counts = {k: class_counts_raw[k] for k in sorted(class_counts_raw, key=_cls_order)}

    existing = sorted([c for c in classifications if c], key=_cls_order)
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

    from collections import defaultdict
    course_ids = [c.id for c in courses]
    course_names = [c.name for c in courses]

    if course_ids:
        instructors_raw = (await session.execute(
            select(CourseInstructor).where(CourseInstructor.course_id.in_(course_ids))
        )).scalars().all()
    else:
        instructors_raw = []
    instructors_by_course: dict = defaultdict(list)
    for inst in sorted(instructors_raw, key=lambda i: i.name):
        instructors_by_course[inst.course_id].append(inst)

    if course_names:
        reviews_raw = (await session.execute(
            select(PendingReview)
            .where(PendingReview.course_name.in_(course_names))
            .order_by(PendingReview.is_approved, PendingReview.created_at.desc())
        )).scalars().all()
    else:
        reviews_raw = []
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
    name_s = name.strip()
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
    classification: str = Form(""),
    category: str = Form("専門"),
):
    async with AsyncSessionLocal() as session:
        course = (await session.execute(select(Course).where(Course.id == course_id))).scalar_one_or_none()
        if course:
            course.name = name.strip()
            course.classification = classification.strip()
            course.category = category
            course.reading = _reading(name.strip())
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


@app.get("/health")
@app.head("/health")
async def health():
    return {"status": "healthy"}
