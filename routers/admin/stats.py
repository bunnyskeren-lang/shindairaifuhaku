from collections import defaultdict
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from core.config import IS_DEV, VAPID_PUBLIC_KEY
from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, CourseSectionView, RichMenuTap, Subject, UserActivity, UserProfile

router = APIRouter()


@router.get("/admin/richmenu-stats")
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


@router.get("/admin/usage-stats")
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
