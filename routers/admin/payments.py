from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import PaymentRequest, PaymentRequestStatus, Review, UserProfile

router = APIRouter()

_PAGE_SIZE = 50


@router.get("/admin/payments", response_class=HTMLResponse)
async def admin_payments(
    request: Request,
    _: str = Depends(check_admin),
    ppage: int = Query(default=1, ge=1),
):
    async with AsyncSessionLocal() as session:
        pending_rows = (await session.execute(
            select(PaymentRequest, UserProfile.name.label("profile_name"))
            .outerjoin(UserProfile, UserProfile.student_id == PaymentRequest.student_id)
            .where(PaymentRequest.status == PaymentRequestStatus.PENDING)
            .order_by(PaymentRequest.created_at.asc())
        )).all()

        paid_total = (await session.execute(
            select(func.count(PaymentRequest.id)).where(PaymentRequest.status == PaymentRequestStatus.PAID)
        )).scalar_one()
        paid_rows = (await session.execute(
            select(PaymentRequest, UserProfile.name.label("profile_name"))
            .outerjoin(UserProfile, UserProfile.student_id == PaymentRequest.student_id)
            .where(PaymentRequest.status == PaymentRequestStatus.PAID)
            .order_by(PaymentRequest.paid_at.desc())
            .offset((ppage - 1) * _PAGE_SIZE).limit(_PAGE_SIZE)
        )).all()

    return templates.TemplateResponse("admin/payments.html", {
        "request": request,
        "pending": pending_rows,
        "paid": paid_rows,
        "ppage": ppage,
        "ptotal": paid_total,
        "ptotal_pages": max(1, (paid_total + _PAGE_SIZE - 1) // _PAGE_SIZE),
    })


@router.post("/admin/payments/pay/{request_id}")
async def admin_payment_pay(request_id: int, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        payment_request = await session.get(PaymentRequest, request_id)
        if payment_request and payment_request.status == PaymentRequestStatus.PENDING:
            payment_request.status = PaymentRequestStatus.PAID
            payment_request.paid_at = datetime.now(timezone.utc)
            await session.commit()
    return RedirectResponse("/admin/payments", status_code=303)


@router.post("/admin/payments/reject/{request_id}")
async def admin_payment_reject(request_id: int, _: str = Depends(check_admin)):
    # 却下時は予約していたreviewsの紐付けを解除し、未払いプールに戻す
    async with AsyncSessionLocal() as session:
        payment_request = await session.get(PaymentRequest, request_id)
        if payment_request and payment_request.status == PaymentRequestStatus.PENDING:
            await session.execute(
                Review.__table__.update()
                .where(Review.payment_request_id == payment_request.id)
                .values(payment_request_id=None)
            )
            payment_request.status = PaymentRequestStatus.REJECTED
            await session.commit()
    return RedirectResponse("/admin/payments", status_code=303)
