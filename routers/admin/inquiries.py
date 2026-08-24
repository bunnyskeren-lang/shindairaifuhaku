from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import Inquiry, InquiryStatus

router = APIRouter()

_PAGE_SIZE = 50


@router.get("/admin/inquiries", response_class=HTMLResponse)
async def admin_inquiries(
    request: Request,
    _: str = Depends(check_admin),
    hpage: int = Query(default=1, ge=1),
):
    async with AsyncSessionLocal() as session:
        pending = (await session.execute(
            select(Inquiry)
            .where(Inquiry.status == InquiryStatus.PENDING)
            .order_by(Inquiry.created_at.asc())
        )).scalars().all()

        handled_total = (await session.execute(
            select(func.count(Inquiry.id)).where(Inquiry.status == InquiryStatus.HANDLED)
        )).scalar_one()
        handled = (await session.execute(
            select(Inquiry)
            .where(Inquiry.status == InquiryStatus.HANDLED)
            .order_by(Inquiry.handled_at.desc())
            .offset((hpage - 1) * _PAGE_SIZE).limit(_PAGE_SIZE)
        )).scalars().all()

    return templates.TemplateResponse("admin/inquiries.html", {
        "request": request,
        "pending": pending,
        "handled": handled,
        "hpage": hpage,
        "htotal": handled_total,
        "htotal_pages": max(1, (handled_total + _PAGE_SIZE - 1) // _PAGE_SIZE),
    })


@router.post("/admin/inquiries/handle/{inquiry_id}")
async def admin_inquiry_handle(inquiry_id: int, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        inquiry = await session.get(Inquiry, inquiry_id)
        if inquiry and inquiry.status == InquiryStatus.PENDING:
            inquiry.status = InquiryStatus.HANDLED
            inquiry.handled_at = datetime.now(timezone.utc)
            await session.commit()
    return RedirectResponse("/admin/inquiries", status_code=303)


@router.post("/admin/inquiries/reopen/{inquiry_id}")
async def admin_inquiry_reopen(inquiry_id: int, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        inquiry = await session.get(Inquiry, inquiry_id)
        if inquiry and inquiry.status == InquiryStatus.HANDLED:
            inquiry.status = InquiryStatus.PENDING
            inquiry.handled_at = None
            await session.commit()
    return RedirectResponse("/admin/inquiries", status_code=303)
