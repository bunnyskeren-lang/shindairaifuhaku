from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import ErrorLog, MessageLog, UserActivity, UserProfile

router = APIRouter()


@router.get("/admin/users", response_class=HTMLResponse)
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


@router.get("/admin/activity", response_class=HTMLResponse)
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
