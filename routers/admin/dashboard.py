from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core import cache
from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import (
    ErrorLog,
    Inquiry,
    InquiryStatus,
    MessageLog,
    PaymentRequest,
    PaymentRequestStatus,
    PushSubscription,
    Review,
    ReviewStatus,
    UserProfile,
)

router = APIRouter()

# routers/admin/users_errors.py の _SUBMIT_DUPLICATE_ACTION_PREFIX と同じ値
# （循環importを避けるためここでも定義する。core/cache.pyの同名定数も参照）。
_SUBMIT_DUPLICATE_ACTION_PREFIX = "submit_duplicate:"


@router.get("/admin", response_class=HTMLResponse)
async def admin_overview(request: Request, _: str = Depends(check_admin)):
    nav_counts = await cache.get_admin_nav_counts_cached()
    async with AsyncSessionLocal() as session:
        # 「対応が必要な項目」の各行に添える補足情報（最も古い未対応の発生日時・直近のエラー内容）
        oldest_pending_review = (await session.execute(
            select(Review.created_at)
            .where(Review.status == ReviewStatus.PENDING)
            .order_by(Review.created_at.asc())
            .limit(1)
        )).scalar_one_or_none()
        oldest_inquiry = (await session.execute(
            select(Inquiry.created_at)
            .where(Inquiry.status == InquiryStatus.PENDING)
            .order_by(Inquiry.created_at.asc())
            .limit(1)
        )).scalar_one_or_none()
        unpaid_amount = (await session.execute(
            select(func.coalesce(func.sum(PaymentRequest.amount), 0))
            .where(PaymentRequest.status == PaymentRequestStatus.PENDING)
        )).scalar_one()
        dup_like = ErrorLog.action.like(_SUBMIT_DUPLICATE_ACTION_PREFIX + "%")
        latest_error = (await session.execute(
            select(ErrorLog.action, ErrorLog.error_type, ErrorLog.created_at)
            .where(or_(ErrorLog.action.is_(None), ~dup_like))
            .order_by(ErrorLog.created_at.desc())
            .limit(1)
        )).first()

        # 最近のアクティビティ: 直近のメッセージログ（ユーザー自身の操作＝direction="in"のみ。
        # 送信者は会員登録済みなら氏名で表示）
        recent_logs = (await session.execute(
            select(
                MessageLog.message,
                MessageLog.created_at,
                UserProfile.name.label("name"),
            )
            .outerjoin(UserProfile, UserProfile.line_user_id == MessageLog.user_id)
            .where(MessageLog.direction == "in")
            .order_by(MessageLog.created_at.desc())
            .limit(8)
        )).all()

    return templates.TemplateResponse("admin/overview.html", {
        "request": request,
        "nav_counts": nav_counts,
        "oldest_pending_review": oldest_pending_review,
        "oldest_inquiry": oldest_inquiry,
        "unpaid_amount": unpaid_amount,
        "latest_error": latest_error,
        "recent_logs": recent_logs,
    })


@router.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs_page(request: Request, _: str = Depends(check_admin), page: int = Query(default=1, ge=1)):
    per_page = 50
    nav_counts = await cache.get_admin_nav_counts_cached()
    async with AsyncSessionLocal() as session:
        # 「ユーザーが何に関心を示したか」が分かるログとして、ユーザー自身の操作
        # （direction="in"）のみを対象にする。bot側の応答（旧"out"の"[N msg(s)]"だけの行）は
        # 情報量がなく無駄なノイズだったため、そもそも記録しない運用に変更した
        # （line_bot/handler.py _handle_reply_event参照）。応答の成否・所要時間は
        # 別画面「デバッグログ」（/admin/debug-logs、DebugLog）で追える。
        total = (await session.execute(
            select(func.count(MessageLog.id)).where(MessageLog.direction == "in")
        )).scalar_one()
        # 送信者を LINE user_id ではなく会員登録時の氏名・学籍番号で表示するため
        # user_profiles を left join する（未登録ユーザーの行も残すので outerjoin）。
        # 生のLINE user_idは関心の把握には不要なノイズのため表示しない
        # （デバッグ目的でIDが必要な場合はデバッグログ側を参照）。
        logs = (await session.execute(
            select(
                MessageLog.message,
                MessageLog.created_at,
                UserProfile.name.label("name"),
                UserProfile.student_id.label("student_id"),
            )
            .outerjoin(UserProfile, UserProfile.line_user_id == MessageLog.user_id)
            .where(MessageLog.direction == "in")
            .order_by(MessageLog.created_at.desc())
            .offset((page - 1) * per_page).limit(per_page)
        )).all()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse("admin/logs.html", {
        "request": request,
        "nav_counts": nav_counts,
        "logs": logs,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "url_prefix": "/admin/logs?page=",
    })


@router.post("/admin/push/subscribe")
async def admin_push_subscribe(request: Request, _: str = Depends(check_admin)):
    data = await request.json()
    try:
        endpoint = data["endpoint"]
        p256dh = data["keys"]["p256dh"]
        auth = data["keys"]["auth"]
    except (KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid subscription payload") from exc
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
