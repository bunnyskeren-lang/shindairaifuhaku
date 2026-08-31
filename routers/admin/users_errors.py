from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from core import cache
from core.config import REVIEW_APPROVAL_UNLOCK_CREDITS
from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, ErrorLog, MessageLog, Review, Subject, SubjectUnlock, UserProfile

router = APIRouter()


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, _: str = Depends(check_admin), page: int = Query(default=1, ge=1)):
    per_page = 50
    async with AsyncSessionLocal() as session:
        # 修正理由: 以前はmessage_logs（LINEからの受信ログ）を主語にしてuser_profilesを
        # 後から紐付けていたため、message_logsが30日で自動削除される
        # （core.activity_log.cleanup_old_logs）と、しばらくLINEを開いていないだけの
        # 登録済みユーザーが一覧から丸ごと消えてしまっていた（レビュー閲覧権チケットの
        # 残高は消えないため、管理画面から見えなくなるのは実害があった）。
        # user_profilesを主語にし、最終受信日時は分かる範囲で付随情報として出す。
        last_seen_subq = (
            select(MessageLog.user_id, func.max(MessageLog.created_at).label("last_seen"))
            .where(MessageLog.direction == "in")
            .group_by(MessageLog.user_id)
            .subquery()
        )
        total = (await session.execute(select(func.count(UserProfile.line_user_id)))).scalar_one()
        users = (await session.execute(
            select(
                UserProfile.line_user_id.label("user_id"),
                last_seen_subq.c.last_seen,
                UserProfile.created_at.label("registered_at"),
                UserProfile.name,
                UserProfile.student_id,
                UserProfile.unlock_credits,
                UserProfile.banned_at,
                UserProfile.ban_reason,
            )
            .outerjoin(last_seen_subq, last_seen_subq.c.user_id == UserProfile.line_user_id)
            .order_by(last_seen_subq.c.last_seen.desc().nulls_last())
            .offset((page - 1) * per_page).limit(per_page)
        )).all()

        # このページに表示するユーザーの学籍番号ぶんだけ、投稿レビューを
        # 科目×担当教員で集計する（reviews.student_id はフォーム手入力の
        # テキストのため、user_profiles.student_id との完全一致でのみ紐づく）
        student_ids = [u.student_id for u in users if u.student_id]
        review_map: dict[str, dict] = {}
        if student_ids:
            review_rows = (await session.execute(
                select(
                    Review.student_id,
                    Subject.name,
                    Review.selected_instructor,
                    Review.status,
                    func.count(Review.id),
                )
                .join(CourseSection, CourseSection.id == Review.course_section_id)
                .join(Subject, Subject.id == CourseSection.subject_id)
                .where(Review.student_id.in_(student_ids))
                .group_by(Review.student_id, Subject.name, Review.selected_instructor, Review.status)
                .order_by(Subject.name)
            )).all()
            for sid, course_name, instructor, status, cnt in review_rows:
                entry = review_map.setdefault(sid, {"total": 0, "breakdown": []})
                entry["total"] += cnt
                entry["breakdown"].append((course_name, instructor, status, cnt))

        # レビュー閲覧権チケットの付与数（credit_granted_atが立っている承認済みレビュー件数×付与枚数）・
        # 使用数（付与総数 - 現在残数）・解除済み科目一覧を、このページに表示する分だけ集計する
        granted_count_map: dict[str, int] = {}
        if student_ids:
            granted_rows = (await session.execute(
                select(Review.student_id, func.count(Review.id))
                .where(Review.student_id.in_(student_ids), Review.credit_granted_at.isnot(None))
                .group_by(Review.student_id)
            )).all()
            granted_count_map = {sid: cnt for sid, cnt in granted_rows}

        line_user_ids = [u.user_id for u in users]
        unlocked_subjects_map: dict[str, list] = {}
        if line_user_ids:
            unlock_rows = (await session.execute(
                select(SubjectUnlock.line_user_id, Subject.name)
                .join(Subject, Subject.id == SubjectUnlock.subject_id)
                .where(SubjectUnlock.line_user_id.in_(line_user_ids))
                .order_by(Subject.name)
            )).all()
            for uid, name in unlock_rows:
                unlocked_subjects_map.setdefault(uid, []).append(name)

        ticket_map: dict[str, dict] = {}
        for u in users:
            granted = granted_count_map.get(u.student_id, 0) * REVIEW_APPROVAL_UNLOCK_CREDITS if u.student_id else 0
            balance = u.unlock_credits or 0
            ticket_map[u.user_id] = {
                "balance": balance,
                "granted": granted,
                # 付与総数-現在残数=使用数。マイナスにはならない想定だが、表示上の破綻を避けるためガードする
                "used": max(granted - balance, 0),
                "subjects": unlocked_subjects_map.get(u.user_id, []),
            }

    total_pages = max(1, (total + per_page - 1) // per_page)

    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "users": users,
        "review_map": review_map,
        "ticket_map": ticket_map,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "url_prefix": "/admin/users?page=",
    })


@router.get("/admin/errors", response_class=HTMLResponse)
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


def _safe_admin_redirect(next_path: str) -> str:
    # オープンリダイレクト防止。管理画面配下のパスのみ許可する
    return next_path if next_path.startswith("/admin/") else "/admin/users"


@router.post("/admin/users/ban/{line_user_id}")
async def admin_user_ban(
    line_user_id: str,
    reason: str = Form(default=""),
    next: str = Form(default="/admin/users"),
    _: str = Depends(check_admin),
):
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, line_user_id)
        if profile and profile.banned_at is None:
            profile.banned_at = datetime.now(timezone.utc)
            profile.ban_reason = reason.strip()[:500] or None
            await session.commit()
    cache.invalidate_ban_cache(line_user_id)
    return RedirectResponse(_safe_admin_redirect(next), status_code=303)


@router.post("/admin/users/unban/{line_user_id}")
async def admin_user_unban(
    line_user_id: str,
    next: str = Form(default="/admin/users"),
    _: str = Depends(check_admin),
):
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, line_user_id)
        if profile and profile.banned_at is not None:
            profile.banned_at = None
            profile.ban_reason = None
            await session.commit()
    cache.invalidate_ban_cache(line_user_id)
    return RedirectResponse(_safe_admin_redirect(next), status_code=303)
