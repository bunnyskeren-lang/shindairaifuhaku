from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from core import cache
from core.config import IS_DEV, JST, VAPID_PUBLIC_KEY
from core.funnel import FUNNEL_EVENTS_IN_ORDER
from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import (
    CourseSection, CourseSectionView, FunnelEvent, RichMenuTap, Review, Subject, UserActivity, UserProfile,
)

router = APIRouter()

# 漏斗の表示名（core/funnel.py の EVENT_* と対応）
FUNNEL_LABELS = {
    "join_view": "友だち追加ページ",
    "liff_review_view": "投稿フォーム(LIFF中継)",
    "review_form_view": "投稿フォーム",
    "register_view": "登録画面",
    "register_done": "登録完了(新規)",
}
FUNNEL_TOTAL_DAYS = 30
FUNNEL_DAILY_DAYS = 14


def _aware(dt: datetime) -> datetime:
    # SQLiteは日時をtz無しで返すのでUTCとみなす（本番のPostgreSQLはtz付き）
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def _friend_breakdown(session) -> dict:
    """LINE友だち追加者を「登録画面を開いたか」「登録したか」で3グループに分ける。

    - 友だち追加者: user_activity の `[follow]`（ブロック解除の再追加も同じ行）
    - 登録済み: user_profiles
    - 登録画面を開いた: funnel_events の register_view のうち、botの案内リンクの ?uid= が付いていたもの
      （署名未検証のURLパラメータ由来なので人数の目安。core/funnel.py）
    登録画面の記録は計測開始（funnel_events の最初の行）以降のものしか無い。それより前に友だち追加して
    未登録のまま、かつ記録も無い人は「開いたかどうか不明」として別枠にする（開いていない人に数えない）。
    """
    follow_at = {
        uid: last_at for uid, last_at in (await session.execute(
            select(UserActivity.user_id, UserActivity.last_at).where(UserActivity.action == "[follow]")
        )).all()
    }
    registered = set((await session.execute(select(UserProfile.line_user_id))).scalars().all())
    opened = {
        uid: (first_at, cnt) for uid, first_at, cnt in (await session.execute(
            select(FunnelEvent.line_user_id, func.min(FunnelEvent.created_at), func.count(FunnelEvent.id))
            .where(FunnelEvent.event == "register_view", FunnelEvent.line_user_id.is_not(None))
            .group_by(FunnelEvent.line_user_id)
        )).all()
    }
    measure_start = (await session.execute(select(func.min(FunnelEvent.created_at)))).scalar_one_or_none()
    measure_start = _aware(measure_start) if measure_start else None

    def _fmt(dt) -> str:
        return _aware(dt).astimezone(JST).strftime("%m/%d %H:%M")

    def _short(uid: str) -> str:
        return uid[:7] + "…"

    never_opened, opened_unregistered, unknown = [], [], []
    registered_friends = 0
    for uid, followed in follow_at.items():
        if uid in registered:
            registered_friends += 1
        elif uid in opened:
            first_at, cnt = opened[uid]
            opened_unregistered.append({
                "id": _short(uid), "followed_at": _fmt(followed), "opened_at": _fmt(first_at),
                "opens": int(cnt), "_sort": _aware(followed),
            })
        elif measure_start is not None and _aware(followed) >= measure_start:
            never_opened.append({"id": _short(uid), "followed_at": _fmt(followed), "_sort": _aware(followed)})
        else:
            unknown.append(uid)

    newest_first = lambda r: r["_sort"]  # noqa: E731
    never_opened.sort(key=newest_first, reverse=True)
    opened_unregistered.sort(key=newest_first, reverse=True)
    for r in never_opened + opened_unregistered:
        del r["_sort"]

    return {
        "followers": len(follow_at),
        "never_opened": len(never_opened),
        "opened_unregistered": len(opened_unregistered),
        "registered_friends": registered_friends,
        "unknown": len(unknown),
        "registered_total": len(registered),
        "registered_without_follow": len(registered - set(follow_at)),
        "opened_not_friend_unregistered": len(set(opened) - set(follow_at) - registered),
        "measure_start": _fmt(measure_start) if measure_start else None,
        "never_opened_rows": never_opened[:50],
        "opened_unregistered_rows": opened_unregistered[:50],
    }


async def _funnel_stats(session) -> dict:
    """funnel_events（core/funnel.py）と会員登録・レビュー投稿から、段階ごとの到達数を集計する。

    日別はSQL側でJST日付に丸めず、直近14日分の行をPythonで丸める（SQLiteのテストでも動かすため。
    行数は画面表示1回につき高々数万行で、ページ表示ごとの記録なので十分小さい）。
    """
    now = datetime.now(JST)
    since_total = now - timedelta(days=FUNNEL_TOTAL_DAYS)
    since_daily = (now - timedelta(days=FUNNEL_DAILY_DAYS - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    total_rows = (await session.execute(
        select(
            FunnelEvent.event,
            func.count(FunnelEvent.id).label("views"),
            func.count(func.distinct(FunnelEvent.visitor_id)).label("uniques"),
        )
        .where(FunnelEvent.created_at >= since_total)
        .group_by(FunnelEvent.event)
    )).all()
    by_event = {r.event: r for r in total_rows}
    totals = [
        {
            "event": e, "label": FUNNEL_LABELS.get(e, e),
            "views": int(by_event[e].views) if e in by_event else 0,
            "uniques": int(by_event[e].uniques) if e in by_event else 0,
        }
        for e in FUNNEL_EVENTS_IN_ORDER
    ]

    source_rows = (await session.execute(
        select(FunnelEvent.source, FunnelEvent.event, func.count(FunnelEvent.id).label("views"))
        .where(FunnelEvent.created_at >= since_total, FunnelEvent.source != "")
        .group_by(FunnelEvent.source, FunnelEvent.event)
        .order_by(FunnelEvent.source, FunnelEvent.event)
    )).all()
    sources = [
        {"source": r.source, "label": FUNNEL_LABELS.get(r.event, r.event), "views": int(r.views)}
        for r in source_rows
    ]

    event_times = (await session.execute(
        select(FunnelEvent.event, FunnelEvent.created_at).where(FunnelEvent.created_at >= since_daily)
    )).all()
    profile_times = (await session.execute(
        select(UserProfile.created_at).where(UserProfile.created_at >= since_daily)
    )).scalars().all()
    review_times = (await session.execute(
        select(Review.created_at).where(Review.created_at >= since_daily)
    )).scalars().all()

    def _day(dt) -> str:
        # SQLiteは日時をtz無しで返すのでUTCとみなす（本番のPostgreSQLはtz付き）
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(JST).strftime("%m/%d")

    days = [(now - timedelta(days=i)).strftime("%m/%d") for i in range(FUNNEL_DAILY_DAYS)]
    daily = {d: {e: 0 for e in FUNNEL_EVENTS_IN_ORDER} | {"profiles": 0, "reviews": 0} for d in days}
    for event, created in event_times:
        d = _day(created)
        if d in daily and event in daily[d]:
            daily[d][event] += 1
    for created in profile_times:
        d = _day(created)
        if d in daily:
            daily[d]["profiles"] += 1
    for created in review_times:
        d = _day(created)
        if d in daily:
            daily[d]["reviews"] += 1

    return {
        "friends": await _friend_breakdown(session),
        "totals": totals,
        "sources": sources,
        "daily_rows": [{"day": d, **daily[d]} for d in days],
        "events_in_order": [{"event": e, "label": FUNNEL_LABELS.get(e, e)} for e in FUNNEL_EVENTS_IN_ORDER],
        "total_days": FUNNEL_TOTAL_DAYS,
    }


@router.get("/admin/usage-stats")
async def admin_usage_stats(request: Request, _=Depends(check_admin), page: int = Query(default=1, ge=1)):
    per_page = 30  # ユーザー単位でのページング件数
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
        # 全体ランキング（上位20件）はSQL側で集計する（行を全件Pythonに引き上げない）
        ranking_rows = (await session.execute(
            select(UserActivity.action, func.sum(UserActivity.count).label("total"))
            .group_by(UserActivity.action)
            .order_by(func.sum(UserActivity.count).desc())
            .limit(20)
        )).all()

        # ユーザー別利用履歴はユーザー単位でページングする（1ユーザーの行が
        # ページをまたいで分断されないよう、先に対象ユーザーをLIMIT/OFFSETで確定する）
        user_ids_subq = select(UserActivity.user_id).distinct().subquery()
        total_users = (await session.execute(select(func.count()).select_from(user_ids_subq))).scalar_one()
        page_user_ids = (await session.execute(
            select(UserActivity.user_id).distinct()
            .order_by(UserActivity.user_id)
            .offset((page - 1) * per_page).limit(per_page)
        )).scalars().all()
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
            .where(UserActivity.user_id.in_(page_user_ids))
            .order_by(UserActivity.user_id, UserActivity.count.desc())
        )).all() if page_user_ids else []
        funnel = await _funnel_stats(session)
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

    msg_ranking = [(r.action, int(r.total or 0)) for r in ranking_rows]

    all_bar_counts = [s["count"] for s in uri_stats] + [s["count"] for s in msg_btn_stats] + [c for _, c in msg_ranking]
    max_bar = max(all_bar_counts, default=1)
    total_pages = max(1, (total_users + per_page - 1) // per_page)

    return templates.TemplateResponse("admin/usage_stats.html", {
        "request": request,
        "nav_counts": await cache.get_admin_nav_counts_cached(),
        "uri_stats": uri_stats,
        "msg_btn_stats": msg_btn_stats,
        "msg_ranking": msg_ranking,
        "funnel": funnel,
        "activity_rows": activity_joined,
        "course_view_rows": course_view_rows,
        "max_bar": max_bar,
        "IS_DEV": IS_DEV,
        "VAPID_PUBLIC_KEY": VAPID_PUBLIC_KEY,
        "page": page,
        "total_pages": total_pages,
        "total": total_users,
        "url_prefix": "/admin/usage-stats?page=",
    })
