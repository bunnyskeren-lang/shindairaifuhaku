from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from core import cache
from core.config import credit_tickets_granted_clause, review_approval_unlock_credits
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
                UserProfile.faculty,
                UserProfile.department,
                UserProfile.coop_jobsite_known,
                UserProfile.unlock_credits,
                UserProfile.payment_limit,
                UserProfile.banned_at,
                UserProfile.ban_reason,
            )
            .outerjoin(last_seen_subq, last_seen_subq.c.user_id == UserProfile.line_user_id)
            .order_by(UserProfile.created_at.desc())
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

        # レビュー閲覧権チケットの「付与数」（実際に付与したレビューぶんの合計枚数）・
        # 使用数（付与総数 - 現在残数）・解除済み科目一覧を、このページに表示する分だけ集計する。
        # 付与枚数は科目カテゴリで異なる（教養2枚・専門1枚）ため、カテゴリ別件数に枚数を掛けて合算する。
        # NULL（未付与）も番兵値（現金換算済み・付与なし）も除外する
        # → credit_tickets_granted_clause() に集約。
        granted_count_map: dict[str, int] = {}
        if student_ids:
            granted_rows = (await session.execute(
                select(Review.student_id, Subject.category, func.count(Review.id))
                .join(CourseSection, CourseSection.id == Review.course_section_id)
                .join(Subject, Subject.id == CourseSection.subject_id)
                .where(
                    Review.student_id.in_(student_ids),
                    credit_tickets_granted_clause(Review.credit_granted_at),
                )
                .group_by(Review.student_id, Subject.category)
            )).all()
            for sid, category, cnt in granted_rows:
                granted_count_map[sid] = (
                    granted_count_map.get(sid, 0) + cnt * review_approval_unlock_credits(category)
                )

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
            granted = granted_count_map.get(u.student_id, 0) if u.student_id else 0
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


# LIFF IDトークン期限切れ→再ログインのテレメトリ（profile_api.py の /api/liff-auth-event が
# action="liff_reauth:<form>:<stage>" で記録）。既定のエラー一覧にもそのまま出す。
# ?view=liff_reauth のときだけ、この種別に絞って抽出表示する。
_LIFF_REAUTH_ACTION_PREFIX = "liff_reauth:"


@router.get("/admin/errors", response_class=HTMLResponse)
async def admin_errors(
    request: Request,
    _: str = Depends(check_admin),
    page: int = Query(default=1, ge=1),
    view: str = Query(default=""),
):
    per_page = 50
    is_reauth_view = view == "liff_reauth"
    reauth_like = ErrorLog.action.like(_LIFF_REAUTH_ACTION_PREFIX + "%")
    async with AsyncSessionLocal() as session:
        count_stmt = select(func.count(ErrorLog.id))
        if is_reauth_view:
            count_stmt = count_stmt.where(reauth_like)
        total = (await session.execute(count_stmt)).scalar_one()
        reauth_total = (await session.execute(
            select(func.count(ErrorLog.id)).where(reauth_like)
        )).scalar_one()
        rows_stmt = (
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
        )
        if is_reauth_view:
            rows_stmt = rows_stmt.where(reauth_like)
        errors = (await session.execute(
            rows_stmt.offset((page - 1) * per_page).limit(per_page)
        )).all()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse("admin/errors.html", {
        "request": request,
        "errors": errors,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "url_prefix": f"/admin/errors?view={view}&page=" if view else "/admin/errors?page=",
        "is_reauth_view": is_reauth_view,
        "reauth_total": reauth_total,
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


@router.post("/admin/users/payment-limit/{line_user_id}")
async def admin_user_payment_limit(
    line_user_id: str,
    amount: str = Form(default="0"),
    next: str = Form(default="/admin/users"),
    _: str = Depends(check_admin),
):
    # レビュー報酬の支払い上限額（円）。100円単位・0以上のみ受け付ける。
    # 不正値は無視して現状維持する（現状は記録・表示専用の値）。
    try:
        value = int(amount)
    except (TypeError, ValueError):
        value = None
    if value is not None and value >= 0 and value % 100 == 0:
        async with AsyncSessionLocal() as session:
            profile = await session.get(UserProfile, line_user_id)
            if profile:
                profile.payment_limit = value
                await session.commit()
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
