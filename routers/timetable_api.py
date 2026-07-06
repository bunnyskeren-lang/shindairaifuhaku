from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import FACULTY_DEPARTMENTS, LINE_USER_ID_RE
from database import AsyncSessionLocal
from models import CourseSection, Instructor, Schedule, Subject, Syllabus, UserProfile, UserSyllabus

router = APIRouter()

_VALID_DAYS = {"月", "火", "水", "木", "金", "土", "日"}

# academic_term は自由文字列のため、時系列順に並べるための明示的な優先度
_TERM_ORDER = case(
    (Syllabus.academic_term == "第1クォーター", 1),
    (Syllabus.academic_term == "第2クォーター", 2),
    (Syllabus.academic_term == "第3クォーター", 3),
    (Syllabus.academic_term == "第4クォーター", 4),
    (Syllabus.academic_term == "後期", 5),
    (Syllabus.academic_term == "集中", 6),
    else_=99,
)


@router.get("/api/timetable/profile")
async def api_timetable_profile_get(user_id: str = Query("")):
    if not user_id:
        return {"faculty": None, "grade": None, "department": None}
    async with AsyncSessionLocal() as session:
        p = await session.get(UserProfile, user_id)
        if not p:
            return {"faculty": None, "grade": None, "department": None}
        return {"faculty": p.faculty, "grade": p.grade, "department": p.department}


@router.post("/api/timetable/profile")
async def api_timetable_profile_set(request: Request):
    data = await request.json()
    user_id = data.get("user_id", "")
    if not user_id or not LINE_USER_ID_RE.match(user_id):
        raise HTTPException(status_code=400, detail="user_id required")
    faculty = data.get("faculty") or None
    grade = data.get("grade")
    if grade is not None:
        grade = int(grade)
        if not (1 <= grade <= 6):
            raise HTTPException(status_code=400, detail="grade must be between 1 and 6")
    department = data.get("department") or None
    if department is not None and department not in FACULTY_DEPARTMENTS.get(faculty, []):
        raise HTTPException(status_code=400, detail="department does not match faculty")
    async with AsyncSessionLocal() as session:
        p = await session.get(UserProfile, user_id)
        if p:
            p.faculty = faculty
            p.grade = grade
            p.department = department
            await session.commit()
    return {"ok": True}


@router.get("/api/timetable/slots/{day}/{period}")
async def api_timetable_slots(day: str, period: int, user_id: str = Query(""), year: int | None = Query(None)):
    if day not in _VALID_DAYS:
        raise HTTPException(status_code=400, detail="invalid day")
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Syllabus, Subject, Instructor)
            .join(Schedule, Schedule.syllabus_id == Syllabus.id)
            .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .join(Instructor, Instructor.id == CourseSection.instructor_id)
            .where(
                Schedule.day_of_week == day,
                Schedule.period == period,
                Subject.hide_from_timetable.is_(False),
            )
        )
        if year is not None:
            stmt = stmt.where(Syllabus.year == year)
        rows = (await session.execute(
            stmt.order_by(Syllabus.year, _TERM_ORDER, Subject.name)
        )).all()

        if not rows:
            return {"courses": []}

        syllabus_ids = [s.id for s, _, _ in rows]
        registered_ids: set[int] = set()
        if user_id:
            regs = (await session.execute(
                select(UserSyllabus.syllabus_id).where(
                    UserSyllabus.line_user_id == user_id,
                    UserSyllabus.syllabus_id.in_(syllabus_ids),
                )
            )).scalars().all()
            registered_ids = set(regs)

        return {
            "courses": [
                {
                    "id": syl.id,
                    "name": subj.name,
                    "instructor": instr.name,
                    "term": syl.academic_term,
                    "timetable_code": syl.timetable_code or "",
                    "department": syl.department or "",
                    "target_grades": syl.target_grades or "",
                    "subject_category": syl.subject_category or "",
                    "registered": syl.id in registered_ids,
                }
                for syl, subj, instr in rows
            ]
        }


def _credits_from_term(term: str | None) -> int:
    if not term:
        return 2
    if "クォーター" in term:
        return 1
    if term in ("前期", "後期") or "セメスター" in term:
        return 2
    if "通年" in term:
        return 4
    return 2


@router.get("/api/timetable/my")
async def api_timetable_my(user_id: str = Query("")):
    if not user_id:
        return {"courses": []}
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(UserSyllabus, Syllabus, Schedule, Subject, Instructor)
            .join(Syllabus, Syllabus.id == UserSyllabus.syllabus_id)
            .join(Schedule, Schedule.syllabus_id == Syllabus.id)
            .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .join(Instructor, Instructor.id == CourseSection.instructor_id)
            .where(UserSyllabus.line_user_id == user_id)
        )).all()

        result = {}
        for us, syl, sch, subj, instr in rows:
            if syl.id not in result:
                result[syl.id] = {
                    "id": syl.id,
                    "name": subj.name,
                    "instructor": instr.name,
                    "term": syl.academic_term,
                    "credits": _credits_from_term(syl.academic_term),
                    "slots": [],
                }
            result[syl.id]["slots"].append({"day": sch.day_of_week, "period": sch.period})

        return {"courses": list(result.values())}


@router.post("/api/timetable/register/{syllabus_id}")
async def api_timetable_register(syllabus_id: int, request: Request):
    body = await request.json()
    user_id = body.get("user_id", "")
    if not user_id or not LINE_USER_ID_RE.match(user_id):
        raise HTTPException(status_code=400, detail="user_id required")
    async with AsyncSessionLocal() as session:
        syl = await session.get(Syllabus, syllabus_id)
        if not syl:
            raise HTTPException(status_code=404, detail="course not found")
        await session.execute(
            pg_insert(UserSyllabus)
            .values(line_user_id=user_id, syllabus_id=syllabus_id)
            .on_conflict_do_nothing(index_elements=["line_user_id", "syllabus_id"])
        )
        await session.commit()
    return {"ok": True}


@router.delete("/api/timetable/register/{syllabus_id}")
async def api_timetable_unregister(syllabus_id: int, user_id: str = Query("")):
    if not user_id or not LINE_USER_ID_RE.match(user_id):
        raise HTTPException(status_code=400, detail="user_id required")
    async with AsyncSessionLocal() as session:
        us = (await session.execute(
            select(UserSyllabus).where(
                UserSyllabus.line_user_id == user_id,
                UserSyllabus.syllabus_id == syllabus_id,
            )
        )).scalar_one_or_none()
        if us:
            await session.delete(us)
            await session.commit()
    return {"ok": True}
