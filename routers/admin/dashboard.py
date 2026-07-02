from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import MessageLog, PushSubscription

router = APIRouter()


@router.get("/admin", response_class=HTMLResponse)
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


@router.post("/admin/push/subscribe")
async def admin_push_subscribe(request: Request, _: str = Depends(check_admin)):
    data = await request.json()
    try:
        endpoint = data["endpoint"]
        p256dh = data["keys"]["p256dh"]
        auth = data["keys"]["auth"]
    except (KeyError, TypeError):
        raise HTTPException(status_code=400, detail="invalid subscription payload")
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
