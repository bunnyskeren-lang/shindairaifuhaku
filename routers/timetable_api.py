from fastapi import APIRouter, Header, HTTPException, Query, Request
from sqlalchemy import and_, case, or_, select

from core.config import DEFAULT_ACADEMIC_YEAR, FACULTY_DEPARTMENTS
from core.liff_auth import verify_liff_id_token
from core.required_subjects import auto_register_required_subjects, register_syllabus_for_user
from database import AsyncSessionLocal
from models import (
    CourseSection, CreditRequirement, Instructor, RegistrationCap, Schedule, Subject, SubjectCreditCategory, Syllabus,
    UserProfile, UserSyllabus,
)

router = APIRouter()


async def _require_liff_user(id_token: str) -> str:
    uid = await verify_liff_id_token(id_token or "")
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


async def _build_credit_countable_filter(session, faculty: str | None, department: str | None):
    """ユーザーの学部・学科に単位チェッカーの区分が定義されている場合のみ、
    その区分に対応する科目だけを許可する絞り込み条件を返す。
    単位チェッカー未対応の学部・学科（credit_requirementsに行が無い）ではNoneを返し、絞り込みを行わない。

    credit_requirements.category_id / label は学部ごとに書式がバラバラ（例: 経営学部は
    「外国語第1」、工学部各学科は「外国語第Ⅰ」、管理画面の「＋カテゴリを追加」で作成した
    category_idはcat_<timestamp>等）で個別に対応させるのは非現実的なため、
    admin/keiei.html・admin/sysinfo.html・admin/koubu系で一貫している group_name
    （"教養科目"/"専門科目"）の有無だけを見る。新しい学部・学科の単位チェッカーが
    同じ管理画面の仕組みで追加された場合、コード変更なしでこの絞り込みが自動適用される。"""
    if not faculty:
        return None
    reqs = (await session.execute(
        select(CreditRequirement.group_name, CreditRequirement.category_id).where(
            CreditRequirement.faculty == faculty,
            or_(CreditRequirement.department == department, CreditRequirement.department.is_(None)),
        )
    )).all()
    if not reqs:
        return None
    group_names = {g for g, _ in reqs}
    category_ids = [c for _, c in reqs]

    # 学部専門科目は Subject.faculty が「経営学部」のように学部名のみの場合と、
    # 「工学部機械工学科」のように学部名+学科名で連結される場合があるため両方を候補にする
    own_faculties = {faculty, f"{faculty}{department or ''}"}
    conditions = []
    if "専門科目" in group_names:
        # subject_credit_categoriesでコース別に科目が紐付け済み（農学部等）ならそちらを優先し、
        # 他コースの専門科目が混在しないよう絞り込む。未紐付けの学部・学科は従来通り学部全体で判定する。
        tagged_ids = (await session.execute(
            select(SubjectCreditCategory.subject_id)
            .where(SubjectCreditCategory.category_id.in_(category_ids))
            .distinct()
        )).scalars().all()
        if tagged_ids:
            conditions.append(Subject.id.in_(tagged_ids))
        else:
            conditions.append(and_(Subject.faculty.in_(own_faculties), Subject.category == "専門"))
            conditions.append(and_(Subject.faculty == "教養教育院", Subject.classification == "共通専門基礎科目"))
    if "教養科目" in group_names:
        conditions.append(and_(Subject.faculty == "教養教育院", Subject.classification.like("教養(%")))
    if not conditions:
        return None
    return or_(*conditions)


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
        return {"years": years}


@router.get("/api/timetable/profile")
async def api_timetable_profile_get(x_liff_id_token: str = Header("", alias="X-Liff-Id-Token")):
    user_id = await verify_liff_id_token(x_liff_id_token)
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
    user_id = await _require_liff_user(data.get("id_token", ""))
    faculty = data.get("faculty") or None
    grade = data.get("grade")
    if grade is not None:
        try:
            grade = int(grade)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="grade must be an integer")
        if not (1 <= grade <= 6):
            raise HTTPException(status_code=400, detail="grade must be between 1 and 6")
    department = data.get("department") or None
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
    day: str, period: int, year: int | None = Query(None),
    x_liff_id_token: str = Header("", alias="X-Liff-Id-Token"),
):
    if day not in _VALID_DAYS:
        raise HTTPException(status_code=400, detail="invalid day")
    user_id = await verify_liff_id_token(x_liff_id_token)
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, user_id) if user_id else None
        credit_filter = await _build_credit_countable_filter(
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
async def api_timetable_my(year: int = DEFAULT_ACADEMIC_YEAR, x_liff_id_token: str = Header("", alias="X-Liff-Id-Token")):
    user_id = await verify_liff_id_token(x_liff_id_token)
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
            .where(UserSyllabus.line_user_id == user_id, Syllabus.year == year)
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
                    "timetable_code": syl.timetable_code or "",
                    "subject_category": syl.subject_category or "",
                    "department": syl.department or "",
                    "classroom": us.classroom or "",
                    "slots": [],
                }
            result[syl.id]["slots"].append({"day": sch.day_of_week, "period": sch.period})

        return {"courses": list(result.values())}


@router.post("/api/timetable/classroom/{syllabus_id}")
async def api_timetable_classroom_set(syllabus_id: int, request: Request):
    body = await request.json()
    user_id = await _require_liff_user(body.get("id_token", ""))
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
    user_id = await _require_liff_user(body.get("id_token", ""))
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
                total_credits = sum(_credits_from_term(t) for t in terms)
                if total_credits > cap:
                    warning = (
                        f"{syl.year}年度の登録単位数が上限（{cap}単位）を超えています"
                        f"（現在{total_credits}単位登録済み）"
                    )
    return {"ok": True, "warning": warning}


@router.delete("/api/timetable/register/{syllabus_id}")
async def api_timetable_unregister(
    syllabus_id: int, x_liff_id_token: str = Header("", alias="X-Liff-Id-Token"),
):
    user_id = await _require_liff_user(x_liff_id_token)
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
