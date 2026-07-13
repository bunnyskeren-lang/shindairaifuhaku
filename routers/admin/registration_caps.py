from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from core.config import FACULTY_DEPARTMENTS
from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import RegistrationCap

router = APIRouter()


@router.get("/admin/registration_caps", response_class=HTMLResponse)
async def admin_registration_caps(request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        caps = (await session.execute(
            select(RegistrationCap).order_by(
                RegistrationCap.faculty, RegistrationCap.department, RegistrationCap.year,
            )
        )).scalars().all()
    return templates.TemplateResponse("admin/registration_caps.html", {
        "request": request,
        "caps": caps,
        "faculty_departments": FACULTY_DEPARTMENTS,
    })


@router.post("/admin/registration_caps/update")
async def admin_registration_caps_update(request: Request, _: str = Depends(check_admin)):
    form = await request.form()
    async with AsyncSessionLocal() as session:
        ids = {
            r for (r,) in (await session.execute(select(RegistrationCap.id))).all()
        }
        for cap_id in ids:
            key = f"max_{cap_id}"
            if key not in form:
                continue
            try:
                max_credits = int(form[key])
            except ValueError:
                continue
            await session.execute(
                sa_update(RegistrationCap).where(RegistrationCap.id == cap_id).values(max_credits=max_credits)
            )
        await session.commit()
    return RedirectResponse("/admin/registration_caps", status_code=303)


@router.post("/admin/registration_caps/add")
async def admin_registration_caps_add(request: Request, _: str = Depends(check_admin)):
    form = await request.form()
    faculty = form.get("new_faculty", "").strip()
    department = form.get("new_department", "").strip() or None
    if not faculty:
        return RedirectResponse("/admin/registration_caps?error=invalid", status_code=303)
    try:
        year = int(form.get("new_year", ""))
        max_credits = int(form.get("new_max_credits", ""))
    except ValueError:
        return RedirectResponse("/admin/registration_caps?error=invalid", status_code=303)
    async with AsyncSessionLocal() as session:
        session.add(RegistrationCap(faculty=faculty, department=department, year=year, max_credits=max_credits))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return RedirectResponse("/admin/registration_caps?error=duplicate", status_code=303)
    return RedirectResponse("/admin/registration_caps", status_code=303)


@router.post("/admin/registration_caps/{cap_id}/delete")
async def admin_registration_caps_delete(cap_id: int, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        row = await session.get(RegistrationCap, cap_id)
        if row:
            await session.delete(row)
            await session.commit()
    return JSONResponse({"ok": True})
