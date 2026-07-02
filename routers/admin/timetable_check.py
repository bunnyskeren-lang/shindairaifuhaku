from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from core.security import check_admin
from core.seiseki import TERM_PAT
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, Subject, Syllabus

router = APIRouter()


@router.get("/admin/timetable/check", response_class=HTMLResponse)
async def admin_timetable_check(request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        course_rows = (await session.execute(select(Subject.id, Subject.name, Subject.faculty))).all()
        syllabus_rows = (await session.execute(
            select(Subject.name, Syllabus.department).distinct()
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Syllabus, Syllabus.course_section_id == CourseSection.id)
        )).all()

    course_name_set   = {name for _, name, _ in course_rows}
    syllabus_name_set = {name for name, _ in syllabus_rows}

    # courses にあるが syllabus_courses にない（科目名に学期語句を含む科目は除外）
    only_in_courses = [
        {"id": cid, "name": name, "faculty": faculty or ""}
        for cid, name, faculty in course_rows
        if name not in syllabus_name_set and not TERM_PAT.search(name)
    ]
    # syllabus_courses にあるが courses にない（重複除去済み名前一覧）
    only_in_syllabus = sorted(
        {(name, dept) for name, dept in syllabus_rows if name not in course_name_set},
        key=lambda x: x[0]
    )
    # 両方に存在
    matched = [
        {"id": cid, "name": name, "faculty": faculty or ""}
        for cid, name, faculty in course_rows
        if name in syllabus_name_set
    ]

    return templates.TemplateResponse("admin/timetable_check.html", {
        "request": request,
        "matched":          matched,
        "only_in_courses":  only_in_courses,
        "only_in_syllabus": only_in_syllabus,
        "total_courses":    len(course_rows),
        "total_syllabus":   len(syllabus_name_set),
    })
