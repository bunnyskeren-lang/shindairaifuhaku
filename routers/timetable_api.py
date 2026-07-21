from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, func, or_, select

from core import cache
from core.config import DEFAULT_ACADEMIC_YEAR, FACULTY_DEPARTMENTS, syllabus_department_key
from core.liff_auth import verify_liff_id_token
from core.required_subjects import auto_register_required_subjects, register_syllabus_for_user
from database import AsyncSessionLocal
from models import (
    CourseSection, Instructor, RegistrationCap, Schedule, Subject, Syllabus,
    UserCustomCourse, UserProfile, UserSyllabus,
)

router = APIRouter()


async def _require_liff_user(id_token: str, request: Request | None = None) -> str:
    uid = await verify_liff_id_token(id_token or "", request)
    if not uid:
        raise HTTPException(status_code=401, detail="LINEログインの確認に失敗しました")
    return uid

_VALID_DAYS = {"月", "火", "水", "木", "金", "土", "日", "集"}

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


@router.get("/api/timetable/years")
async def api_timetable_years():
    async with AsyncSessionLocal() as session:
        years = (await session.execute(
            select(Syllabus.year).distinct().order_by(Syllabus.year)
        )).scalars().all()
        # シラバスがまだ無い次年度も先行して選べるように、最大年度の翌年を常に候補へ加える
        next_year = (years[-1] if years else DEFAULT_ACADEMIC_YEAR) + 1
        if next_year not in years:
            years.append(next_year)
    res = JSONResponse({"years": years})
    # 修正理由: 開講年度は実質不変なのに毎回DB問い合わせしており、マイ時間割を開くたびの
    # 待ち時間に寄与していた（実測314ms）。他の変化しにくい一覧系API(/api/preload等)と同様、
    # ブラウザ側キャッシュに任せる。
    res.headers["Cache-Control"] = "public, max-age=300"
    return res


@router.get("/api/timetable/profile")
async def api_timetable_profile_get(
    request: Request, x_liff_id_token: str = Header("", alias="X-Liff-Id-Token"),
):
    user_id = await verify_liff_id_token(x_liff_id_token, request)
    if not user_id:
        return {"faculty": None, "grade": None, "department": None}
    async with AsyncSessionLocal() as session:
        p = await session.get(UserProfile, user_id)
        if not p:
            return {"faculty": None, "grade": None, "department": None}
        return {"faculty": p.faculty, "grade": p.grade, "department": p.department}


class TimetableProfileRequest(BaseModel):
    id_token: str = ""
    faculty: str | None = None
    grade: int | None = Field(None, ge=1, le=6)
    department: str | None = None

    # 修正理由: フロントはfaculty/departmentが未選択のとき空文字列を送ってくるため、
    # 従来の`data.get("faculty") or None`と同じ意味になるようbefore validatorで変換する。
    @field_validator("faculty", "department", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        return v or None


@router.post("/api/timetable/profile")
async def api_timetable_profile_set(data: TimetableProfileRequest, request: Request):
    user_id = await _require_liff_user(data.id_token, request)
    faculty = data.faculty
    grade = data.grade
    department = data.department
    if department is not None and department not in FACULTY_DEPARTMENTS.get(faculty, []):
        raise HTTPException(status_code=400, detail="department does not match faculty")
    async with AsyncSessionLocal() as session:
        p = await session.get(UserProfile, user_id)
        # 修正理由: UserProfileが存在しない場合は何も更新していないのに
        # 常に{"ok": True}を返しており、保存失敗が呼び出し元から検知できなかった。
        if not p:
            raise HTTPException(status_code=404, detail="user profile not found")
        p.faculty = faculty
        p.grade = grade
        p.department = department
        await auto_register_required_subjects(session, user_id, p)
        await session.commit()
    return {"ok": True}


@router.get("/api/timetable/slots/{day}/{period}")
async def api_timetable_slots(
    request: Request, day: str, period: int, year: int | None = Query(None),
    x_liff_id_token: str = Header("", alias="X-Liff-Id-Token"),
):
    if day not in _VALID_DAYS:
        raise HTTPException(status_code=400, detail="invalid day")
    user_id = await verify_liff_id_token(x_liff_id_token, request)
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, user_id) if user_id else None
        credit_filter = await cache.get_credit_countable_filter(
            session, profile.faculty if profile else None, profile.department if profile else None,
        )

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
        if credit_filter is not None:
            stmt = stmt.where(credit_filter)
        if year is not None:
            stmt = stmt.where(Syllabus.year == year)
        rows = (await session.execute(
            stmt.order_by(Syllabus.year, _TERM_ORDER, Subject.name)
        )).all()

        syllabus_ids = [s.id for s, _, _ in rows]
        registered_ids: set[int] = set()
        if user_id and syllabus_ids:
            regs = (await session.execute(
                select(UserSyllabus.syllabus_id).where(
                    UserSyllabus.line_user_id == user_id,
                    UserSyllabus.syllabus_id.in_(syllabus_ids),
                )
            )).scalars().all()
            registered_ids = set(regs)

        courses = [
            {
                "id": syl.id,
                "name": subj.name,
                "instructor": instr.name,
                "academic_term": syl.academic_term,
                "timetable_code": syl.timetable_code or "",
                "department": syllabus_department_key(subj),
                "target_grades": syl.target_grades or "",
                "subject_category": syl.subject_category or "",
                "registered": syl.id in registered_ids,
                "is_custom": False,
            }
            for syl, subj, instr in rows
        ]

        # ユーザーが手動追加した個人用の科目（他ユーザーには表示されない）
        if user_id:
            custom_stmt = select(UserCustomCourse).where(
                UserCustomCourse.line_user_id == user_id,
                UserCustomCourse.day_of_week == day,
                UserCustomCourse.period == period,
            )
            if year is not None:
                custom_stmt = custom_stmt.where(UserCustomCourse.year == year)
            custom_rows = (await session.execute(custom_stmt)).scalars().all()
            courses += [
                {
                    "id": c.id,
                    "name": c.name,
                    "instructor": c.instructor or "",
                    "academic_term": None,
                    "timetable_code": "",
                    "department": "",
                    "target_grades": "",
                    "subject_category": "",
                    "classification": c.classification or "",
                    "credits": c.credits,
                    "registered": True,
                    "is_custom": True,
                }
                for c in custom_rows
            ]

        return {"courses": courses}


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


async def _load_timetable_courses(session, user_id: str, year: int) -> list[dict]:
    rows = (await session.execute(
        select(UserSyllabus, Syllabus, Schedule, Subject, Instructor)
        .join(Syllabus, Syllabus.id == UserSyllabus.syllabus_id)
        .join(Schedule, Schedule.syllabus_id == Syllabus.id)
        .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
        .join(Subject, Subject.id == CourseSection.subject_id)
        .join(Instructor, Instructor.id == CourseSection.instructor_id)
        .where(UserSyllabus.line_user_id == user_id, Syllabus.year == year)
    )).all()

    result = {}
    for us, syl, sch, subj, instr in rows:
        if syl.id not in result:
            result[syl.id] = {
                "id": syl.id,
                "name": subj.name,
                "instructor": instr.name,
                "academic_term": syl.academic_term,
                "credits": _credits_from_term(syl.academic_term),
                "timetable_code": syl.timetable_code or "",
                "subject_category": syl.subject_category or "",
                "department": syllabus_department_key(subj),
                "classroom": us.classroom or "",
                "is_custom": False,
                "slots": [],
            }
        result[syl.id]["slots"].append({"day": sch.day_of_week, "period": sch.period})

    # ユーザーが手動追加した個人用の科目
    custom_rows = (await session.execute(
        select(UserCustomCourse).where(
            UserCustomCourse.line_user_id == user_id,
            UserCustomCourse.year == year,
        )
    )).scalars().all()
    for c in custom_rows:
        # syllabus_idとIDの数値空間が別のため、辞書キーが衝突しないよう文字列キーにする
        result[f"custom_{c.id}"] = {
            "id": c.id,
            "name": c.name,
            "instructor": c.instructor or "",
            "academic_term": None,
            "credits": c.credits,
            "timetable_code": "",
            "subject_category": "",
            "department": "",
            "classroom": "",
            "classification": c.classification or "",
            "is_custom": True,
            "slots": [{"day": c.day_of_week, "period": c.period}],
        }

    return list(result.values())


@router.get("/api/timetable/my")
async def api_timetable_my(
    request: Request, year: int = DEFAULT_ACADEMIC_YEAR,
    x_liff_id_token: str = Header("", alias="X-Liff-Id-Token"),
):
    user_id = await verify_liff_id_token(x_liff_id_token, request)
    if not user_id:
        return {"courses": [], "cap": None}
    async with AsyncSessionLocal() as session:
        courses = await _load_timetable_courses(session, user_id, year)
        # CAPバーの分母用。学部・学科・年度のCAP行が無い場合はNone（フロント側でバー自体を隠す）
        profile = await session.get(UserProfile, user_id)
        cap = None
        if profile and profile.faculty:
            cap = await _get_registration_cap(session, profile.faculty, profile.department, year)
        return {"courses": courses, "cap": cap}


@router.post("/api/timetable/classroom/{syllabus_id}")
async def api_timetable_classroom_set(syllabus_id: int, request: Request):
    body = await request.json()
    user_id = await _require_liff_user(body.get("id_token", ""), request)
    classroom = (body.get("classroom") or "").strip()[:20]
    async with AsyncSessionLocal() as session:
        us = (await session.execute(
            select(UserSyllabus).where(
                UserSyllabus.line_user_id == user_id,
                UserSyllabus.syllabus_id == syllabus_id,
            )
        )).scalar_one_or_none()
        if not us:
            raise HTTPException(status_code=404, detail="course not registered")
        us.classroom = classroom or None
        await session.commit()
    return {"ok": True, "classroom": classroom}


async def _get_registration_cap(session, faculty: str | None, department: str | None, year: int) -> int | None:
    """学部・学科・年度に対応するCAP値を返す。学科一致の行を優先し、無ければ学部共通(department NULL)の行を使う。"""
    if not faculty:
        return None
    return (await session.execute(
        select(RegistrationCap.max_credits)
        .where(
            RegistrationCap.faculty == faculty,
            RegistrationCap.year == year,
            or_(RegistrationCap.department == department, RegistrationCap.department.is_(None)),
        )
        .order_by(RegistrationCap.department.is_(None))
    )).scalars().first()


@router.post("/api/timetable/register/{syllabus_id}")
async def api_timetable_register(syllabus_id: int, request: Request):
    body = await request.json()
    user_id = await _require_liff_user(body.get("id_token", ""), request)
    async with AsyncSessionLocal() as session:
        syl = await session.get(Syllabus, syllabus_id)
        if not syl:
            raise HTTPException(status_code=404, detail="course not found")
        await register_syllabus_for_user(session, user_id, syllabus_id)
        await session.commit()

        warning = None
        profile = await session.get(UserProfile, user_id)
        if profile and profile.faculty:
            cap = await _get_registration_cap(session, profile.faculty, profile.department, syl.year)
            if cap is not None:
                terms = (await session.execute(
                    select(Syllabus.academic_term)
                    .join(UserSyllabus, UserSyllabus.syllabus_id == Syllabus.id)
                    .where(UserSyllabus.line_user_id == user_id, Syllabus.year == syl.year)
                )).scalars().all()
                # 手動追加科目（user_custom_courses）も履修単位としてCAPに算入する
                custom_credits = (await session.execute(
                    select(func.coalesce(func.sum(UserCustomCourse.credits), 0)).where(
                        UserCustomCourse.line_user_id == user_id,
                        UserCustomCourse.year == syl.year,
                    )
                )).scalar_one()
                total_credits = sum(_credits_from_term(t) for t in terms) + custom_credits
                if total_credits > cap:
                    warning = (
                        f"{syl.year}年度の登録単位数が上限（{cap}単位）を超えています"
                        f"（現在{total_credits}単位登録済み）"
                    )
    return {"ok": True, "warning": warning}


@router.delete("/api/timetable/register/{syllabus_id}")
async def api_timetable_unregister(
    request: Request, syllabus_id: int, x_liff_id_token: str = Header("", alias="X-Liff-Id-Token"),
):
    user_id = await _require_liff_user(x_liff_id_token, request)
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


