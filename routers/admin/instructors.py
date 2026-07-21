from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select

from core import cache
from core.config import normalize_instructor_name
from core.security import check_admin
from database import AsyncSessionLocal
from models import CourseSection, DisplayOrder, Instructor, Review
from routers.admin._common import reorder_sort_order

router = APIRouter()


@router.post("/admin/courses/{course_id}/instructors/add")
async def add_instructor(course_id: int, request: Request, name: str = Form(...), _: str = Depends(check_admin)):
    name_s = normalize_instructor_name(name)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if name_s:
        async with AsyncSessionLocal() as session:
            # Instructor upsert
            instr = (await session.execute(
                select(Instructor).where(Instructor.name == name_s)
            )).scalar_one_or_none()
            if not instr:
                instr = Instructor(name=name_s)
                session.add(instr)
                await session.flush()
            # CourseSection 重複チェック
            existing_cs = (await session.execute(
                select(CourseSection).where(
                    CourseSection.subject_id == course_id,
                    CourseSection.instructor_id == instr.id,
                )
            )).scalar_one_or_none()
            if existing_cs:
                if is_ajax:
                    return JSONResponse({"ok": False, "error": "duplicate"})
                referer = request.headers.get("Referer", "/admin/courses")
                sep = "&" if "?" in referer else "?"
                return RedirectResponse(f"{referer}{sep}inst_err={course_id}", status_code=303)
            cs = CourseSection(subject_id=course_id, instructor_id=instr.id)
            session.add(cs)
            await session.commit()
            cache.invalidate_courses_cache()
            # シラバスURLはtimetable_code/departmentから動的生成するため、時間割インポート前の
            # このタイミングでは持たない（時間割インポート後は管理画面表示時に自動で付与される）
            if is_ajax:
                return JSONResponse({"ok": True, "id": instr.id, "name": instr.name, "url": ""})
    if is_ajax:
        return JSONResponse({"ok": False, "error": "empty"})
    return RedirectResponse(request.headers.get("Referer", "/admin/courses"), status_code=303)


@router.post("/admin/courses/{course_id}/instructors/delete/{instructor_id}")
async def delete_instructor(course_id: int, instructor_id: int, request: Request, _: str = Depends(check_admin)):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    async with AsyncSessionLocal() as session:
        cs = (await session.execute(
            select(CourseSection).where(
                CourseSection.subject_id == course_id,
                CourseSection.instructor_id == instructor_id,
            )
        )).scalar_one_or_none()
        if cs:
            has_reviews = (await session.execute(
                select(func.count(Review.id)).where(
                    Review.course_section_id == cs.id,
                )
            )).scalar()
            if has_reviews:
                if is_ajax:
                    return JSONResponse({"ok": False, "error": "レビュー（承認済み・未承認とも）が紐づいているため削除できません"})
                return RedirectResponse(request.headers.get("Referer", "/admin/courses"), status_code=303)
            await session.delete(cs)
            await session.commit()
    cache.invalidate_courses_cache()
    if is_ajax:
        return JSONResponse({"ok": True})
    return RedirectResponse(request.headers.get("Referer", "/admin/courses"), status_code=303)


@router.post("/admin/courses/instructor/move")
async def admin_instructor_move(request: Request, _=Depends(check_admin)):
    data = await request.json()
    instructor_id = data.get("id")
    direction = data.get("direction", "")
    if not instructor_id or direction not in ("up", "down"):
        return JSONResponse({"ok": False})

    async with AsyncSessionLocal() as session:
        all_instr = list((await session.execute(
            select(Instructor).order_by(Instructor.sort_order, Instructor.name)
        )).scalars().all())
        if not reorder_sort_order(all_instr, instructor_id, direction):
            return JSONResponse({"ok": False})
        await session.commit()
    cache.invalidate_courses_cache()
    return JSONResponse({"ok": True})


@router.post("/admin/courses/faculty/move")
async def admin_faculty_move(request: Request, _=Depends(check_admin)):
    data = await request.json()
    faculty_id = data.get("id")
    direction = data.get("direction", "")
    if not faculty_id or direction not in ("up", "down"):
        return JSONResponse({"ok": False})

    async with AsyncSessionLocal() as session:
        all_fac = list((await session.execute(
            select(DisplayOrder).where(DisplayOrder.kind == "faculty").order_by(DisplayOrder.sort_order)
        )).scalars().all())
        if not reorder_sort_order(all_fac, faculty_id, direction):
            return JSONResponse({"ok": False})
        await session.commit()
    cache.invalidate_faculty_order_cache()
    return JSONResponse({"ok": True})
