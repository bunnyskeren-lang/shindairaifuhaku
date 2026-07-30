from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import ErrorLog, MessageLog, UserProfile

router = APIRouter()


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, _: str = Depends(check_admin), page: int = Query(default=1, ge=1)):
    per_page = 50
    async with AsyncSessionLocal() as session:
        # message_logsはユーザー数に比例して増え続けるため、user_id別の
        # 最終受信日時をサブクエリで先に集約してからページングする
        last_seen_subq = (
            select(MessageLog.user_id, func.max(MessageLog.created_at).label("last_seen"))
            .where(MessageLog.direction == "in")
            .group_by(MessageLog.user_id)
            .subquery()
        )
        total = (await session.execute(select(func.count()).select_from(last_seen_subq))).scalar_one()
        users = (await session.execute(
            select(
                last_seen_subq.c.user_id,
                last_seen_subq.c.last_seen,
                UserProfile.name,
                UserProfile.student_id,
            )
            .outerjoin(UserProfile, UserProfile.line_user_id == last_seen_subq.c.user_id)
            .order_by(last_seen_subq.c.last_seen.desc())
            .offset((page - 1) * per_page).limit(per_page)
        )).all()
    total_pages = max(1, (total + per_page - 1) // per_page)

    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "users": users,
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
