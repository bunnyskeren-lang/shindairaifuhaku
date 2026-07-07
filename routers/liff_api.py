import asyncio
import re as _re
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core import cache, line_client
from core.activity_log import save_error_log
from core.config import (
    EASE_ORDER, FACULTIES, FACULTY_DEPARTMENTS, REGISTER_LIFF_ID, STUDENT_ID_RE, LINE_USER_ID_RE,
    is_profile_complete, make_syllabus_url,
)
from core.push import send_push_notification
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, CourseSectionView, Instructor, Review, Subject, Syllabus, UserProfile

router = APIRouter()

_FORM_PUNCT = '・･（）()'


def _normalize_form_q(s: str) -> str:
    for ch in _FORM_PUNCT:
        s = s.replace(ch, '')
    return s


@router.get("/api/courses")
async def search_courses(q: str = ""):
    async with AsyncSessionLocal() as session:
        if q.strip():
            tokens = [tok for tok in _re.split(r'[\s　]+', q.strip()) if tok]
            def _escape(tok: str) -> str:
                return tok.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            stmt = select(Subject)
            for tok in tokens:
                t = _escape(tok)
                stmt = stmt.where(or_(
                    Subject.name.ilike(f"%{t}%", escape="\\"),
                    Subject.reading.ilike(f"%{t}%", escape="\\"),
                ))
            stmt = stmt.order_by(Subject.name)
            courses = (await session.execute(stmt)).scalars().all()
            if not courses:
                norm_col = Subject.name
                for ch in ('・', '･', '（', '）', '(', ')'):
                    norm_col = func.replace(norm_col, ch, '')
                norm_tokens = [_normalize_form_q(tok) for tok in tokens]
                stmt2 = select(Subject)
                for tok in norm_tokens:
                    t = _escape(tok)
                    stmt2 = stmt2.where(norm_col.ilike(f"%{t}%", escape="\\"))
                courses = (await session.execute(stmt2.order_by(Subject.name))).scalars().all()
        else:
            stmt = select(Subject).order_by(Subject.name).limit(30)
            courses = (await session.execute(stmt)).scalars().all()
        course_ids = [c.id for c in courses]
        cs_rows = []
        if course_ids:
            cs_rows = (await session.execute(
                select(CourseSection, Instructor)
                .join(Instructor, Instructor.id == CourseSection.instructor_id)
                .where(CourseSection.subject_id.in_(course_ids))
                .order_by(Instructor.sort_order, Instructor.name)
            )).all()
        insts_by_course: dict = {}
        for cs, inst in cs_rows:
            insts_by_course.setdefault(cs.subject_id, []).append({"name": inst.name, "url": cs.syllabus_url or ""})
    return {"courses": [
        {"id": c.id, "name": c.name, "instructors": insts_by_course.get(c.id, [])}
        for c in courses
    ]}


@router.get("/api/preload")
async def api_preload():
    async with AsyncSessionLocal() as session:
        courses = (await session.execute(select(Subject).order_by(Subject.name))).scalars().all()
        cs_rows = (await session.execute(
            select(CourseSection, Instructor)
            .join(Instructor, Instructor.id == CourseSection.instructor_id)
            .order_by(Instructor.sort_order, Instructor.name)
        )).all()
    insts_by_course: dict = {}
    inst_courses: dict = {}
    course_by_id = {c.id: c.name for c in courses}
    for cs, inst in cs_rows:
        insts_by_course.setdefault(cs.subject_id, []).append({"name": inst.name})
        cname = course_by_id.get(cs.subject_id)
        if cname:
            bucket = inst_courses.setdefault(inst.name, [])
            if not any(x["name"] == cname for x in bucket):
                bucket.append({"name": cname})
    course_list = [
        {"id": c.id, "name": c.name, "reading": c.reading or "", "instructors": insts_by_course.get(c.id, [])}
        for c in courses
    ]
    instructor_list = [
        {"name": name, "courses": clist}
        for name, clist in sorted(inst_courses.items())
    ]
    res = JSONResponse({"courses": course_list, "instructors": instructor_list})
    res.headers["Cache-Control"] = "public, max-age=300"
    return res


@router.get("/api/faculties")
async def api_faculties():
    res = JSONResponse({"faculties": await cache.get_faculty_order(), "departments": FACULTY_DEPARTMENTS})
    res.headers["Cache-Control"] = "public, max-age=300"
    return res


@router.get("/api/instructors")
async def search_instructors(q: str = ""):
    if not q.strip():
        return {"instructors": []}
    async with AsyncSessionLocal() as session:
        def _esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        q_clean = q.replace("　", " ").strip()
        escaped = _esc(q_clean)
        insts_raw = (await session.execute(
            select(Instructor.name)
            .where(Instructor.name.ilike(f"%{escaped}%", escape="\\"))
            .distinct()
        )).scalars().all()
        insts = sorted(insts_raw, key=lambda n: (0 if n.lower().startswith(q_clean.lower()) else 1, n))
        if not insts:
            norm_col = Instructor.name
            for ch in ('・', '･', '（', '）', '(', ')'):
                norm_col = func.replace(norm_col, ch, '')
            escaped_norm = _esc(_normalize_form_q(q_clean))
            insts_raw = (await session.execute(
                select(Instructor.name)
                .where(norm_col.ilike(f"%{escaped_norm}%", escape="\\"))
                .distinct()
            )).scalars().all()
            insts = sorted(insts_raw, key=lambda n: (0 if n.lower().startswith(q_clean.lower()) else 1, n))

        result = []
        if insts:
            all_rows = (await session.execute(
                select(Instructor.name, Subject.id, Subject.name)
                .join(CourseSection, CourseSection.instructor_id == Instructor.id)
                .join(Subject, Subject.id == CourseSection.subject_id)
                .where(Instructor.name.in_(insts))
                .order_by(Instructor.name, Subject.name)
            )).all()
            courses_by_inst: dict[str, list] = {name: [] for name in insts}
            for inst_name, c_id, c_name in all_rows:
                if not any(x["id"] == c_id for x in courses_by_inst[inst_name]):
                    courses_by_inst[inst_name].append({"id": c_id, "name": c_name})
            for name in insts:
                result.append({"name": name, "courses": courses_by_inst[name]})

    return {"instructors": result}


@router.get("/api/profile/status")
async def profile_status(uid: str = ""):
    uid = uid.strip()
    if not uid:
        return {"complete": False}
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, uid)
        return {"complete": is_profile_complete(profile)}


@router.post("/api/register")
async def register_profile(
    request: Request,
    uid: str = Form(...),
    name: str = Form(...),
    student_id: str = Form(...),
    faculty: str = Form(...),
    grade: int = Form(...),
    department: str = Form(...),
):
    def _form_error(msg: str):
        return templates.TemplateResponse(
            "form_error.html", {"request": request, "message": msg}, status_code=400
        )

    uid = uid.strip()
    if not uid or not LINE_USER_ID_RE.match(uid):
        return _form_error("LINE ユーザー ID の形式が不正です")
    name = _re.sub(r'[\s　]+', '', name)
    if not name:
        return _form_error("お名前を入力してください")
    sid = _re.sub(r'[\s　]+', '', student_id).upper()
    if not STUDENT_ID_RE.match(sid):
        return _form_error("学籍番号の形式が正しくありません（例：2345678S、医学部は2345678MM）")
    if faculty not in FACULTIES:
        return _form_error("学部を選択してください")
    if not (1 <= grade <= 6):
        return _form_error("学年を選択してください")
    if department not in FACULTY_DEPARTMENTS.get(faculty, []):
        return _form_error("学科を選択してください")

    async with AsyncSessionLocal() as session:
        taken = (await session.execute(
            select(UserProfile.line_user_id).where(UserProfile.student_id == sid)
        )).scalars().first()
        if taken is not None and taken != uid:
            return _form_error("この学籍番号はすでに別のアカウントで登録されています")

        profile = await session.get(UserProfile, uid)
        if profile:
            profile.name = name[:100]
            profile.student_id = sid
            profile.faculty = faculty
            profile.grade = grade
            profile.department = department
        else:
            session.add(UserProfile(
                line_user_id=uid,
                name=name[:100],
                student_id=sid,
                faculty=faculty,
                grade=grade,
                department=department,
            ))
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            return _form_error("登録に失敗しました。もう一度お試しください")

    try:
        await line_client.unlink_rich_menu(uid)
    except Exception as exc:
        await save_error_log(exc, user_id=uid, action="register_richmenu_unlink")

    return templates.TemplateResponse(
        "form_register_success.html", {"request": request, "liff_id": REGISTER_LIFF_ID}
    )


@router.get("/api/autofill")
async def autofill_profile(uid: str = "", student_id: str = ""):
    uid = uid.strip()
    sid = student_id.strip().upper()
    if not uid or not sid or not STUDENT_ID_RE.match(sid):
        return {"found": False}
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(UserProfile).where(UserProfile.line_user_id == uid)
        )).scalar_one_or_none()
        if existing:
            return {"found": True, "name": existing.name}
        row = (await session.execute(
            select(Review.submitter_name)
            .where(Review.student_id == sid)
            .order_by(Review.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        if not row:
            return {"found": False}
        taken = (await session.execute(
            select(UserProfile.line_user_id).where(UserProfile.student_id == sid)
        )).scalars().first()
        if taken is not None and taken != uid:
            return {"found": False}
        if not taken:
            try:
                session.add(UserProfile(line_user_id=uid, name=row, student_id=sid))
                await session.commit()
            except Exception:
                await session.rollback()
        return {"found": True, "name": row}


@router.post("/submit")
async def submit(
    request: Request,
    course_name: str = Form(...),
    rating: int = Form(...),
    ease_rating: str = Form(...),
    grading_method: str = Form(default=""),
    comment: str = Form(...),
    line_user_id: str = Form(default=""),
    reg_name: str = Form(default=""),
    student_id: str = Form(default=""),
    selected_instructor: str = Form(default=""),
    nickname: str = Form(default=""),
    academic_year: int = Form(default=0),
):
    def _form_error(msg: str):
        return templates.TemplateResponse(
            "form_error.html", {"request": request, "message": msg}, status_code=400
        )

    if not (1 <= rating <= 5):
        return _form_error("評価が不正です")
    if ease_rating not in ("SS", "S", "A", "B", "C"):
        return _form_error("楽単度が不正です")
    if not (2000 <= academic_year <= 2100):
        return _form_error("受講年度を選択してください")
    if not comment.strip():
        return _form_error("コメントを入力してください")

    sid = student_id.strip().upper()
    if not STUDENT_ID_RE.match(sid):
        return _form_error("学籍番号の形式が正しくありません（例：2345678S、医学部は2345678MM）")

    async with AsyncSessionLocal() as session:
        subject = (await session.execute(
            select(Subject).where(Subject.name == course_name.strip())
        )).scalar_one_or_none()
        if not subject:
            return _form_error("指定された科目が見つかりません")

        uid = line_user_id.strip()
        if not uid or not LINE_USER_ID_RE.match(uid):
            return _form_error("LINEアプリの「レビュー投稿」からアクセスしてください")

        existing = (await session.execute(
            select(UserProfile).where(UserProfile.line_user_id == uid)
        )).scalar_one_or_none()
        if existing is None:
            if not reg_name.strip():
                return _form_error("お名前を入力してください")
            taken = (await session.execute(
                select(UserProfile.line_user_id).where(UserProfile.student_id == sid)
            )).scalars().first()
            if taken is not None and taken != uid:
                return _form_error("この学籍番号はすでに別のアカウントで登録されています")
            submitter_name = reg_name.strip()[:100]
            try:
                session.add(UserProfile(
                    line_user_id=uid,
                    name=submitter_name,
                    student_id=sid,
                ))
                await session.flush()
            except Exception:
                await session.rollback()
                return _form_error("プロフィールの保存に失敗しました")
        else:
            if existing.student_id != sid:
                return _form_error("学籍番号が登録情報と一致しません")
            submitter_name = existing.name

        # 担当教員に対応する course_section を探す
        instr_name = selected_instructor.strip()[:100] or None
        cs_obj = None
        if instr_name:
            instr_obj = (await session.execute(
                select(Instructor).where(Instructor.name == instr_name)
            )).scalar_one_or_none()
            if instr_obj:
                cs_obj = (await session.execute(
                    select(CourseSection).where(
                        CourseSection.subject_id == subject.id,
                        CourseSection.instructor_id == instr_obj.id,
                    )
                )).scalar_one_or_none()
        if cs_obj is None:
            cs_obj = (await session.execute(
                select(CourseSection).where(CourseSection.subject_id == subject.id)
            )).scalars().first()
        if cs_obj is None:
            return _form_error("この科目の担当教員情報が見つかりません")

        review = Review(
            course_section_id=cs_obj.id,
            submitter_name=submitter_name,
            content=comment.strip()[:500],
            rating=rating,
            ease_rating=ease_rating,
            grading_method=grading_method.strip()[:500] or None,
            selected_instructor=instr_name,
            nickname=nickname.strip()[:30] or None,
            academic_year=academic_year,
            student_id=sid or None,
            is_approved=False,
        )
        session.add(review)
        await session.commit()

    await send_push_notification(
        course_name=course_name.strip(),
        rating=rating,
        ease_rating=ease_rating,
        comment=comment.strip(),
    )

    return templates.TemplateResponse(
        "form_success.html", {"request": request, "course_name": course_name}
    )


@router.get("/api/course/{course_id}")
async def api_course(course_id: int):
    try:
        async with AsyncSessionLocal() as session:
            subject = await session.get(Subject, course_id)
            if not subject:
                raise HTTPException(status_code=404, detail="course not found")

        async def _cs_instr():
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(CourseSection, Instructor)
                    .join(Instructor, Instructor.id == CourseSection.instructor_id)
                    .where(CourseSection.subject_id == course_id)
                )).all()

        async def _agg(cs_ids: list):
            if not cs_ids:
                return None
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(func.avg(Review.rating), func.count(Review.id))
                    .where(Review.course_section_id.in_(cs_ids), Review.is_approved == True)
                )).first()

        async def _ease(cs_ids: list):
            if not cs_ids:
                return []
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(Review.ease_rating, func.count(Review.id))
                    .where(Review.course_section_id.in_(cs_ids), Review.is_approved == True)
                    .group_by(Review.ease_rating)
                )).all()

        async def _reviews(cs_ids: list):
            if not cs_ids:
                return []
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(Review)
                    .where(Review.course_section_id.in_(cs_ids), Review.is_approved == True)
                    .order_by(Review.selected_instructor.nulls_last(), Review.academic_year.desc())
                    .limit(20)
                )).scalars().all()

        async def _syllabus_code():
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(Syllabus.timetable_code)
                    .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
                    .where(CourseSection.subject_id == course_id)
                    .limit(1)
                )).scalar_one_or_none()

        cs_instr_rows = await _cs_instr()
        cs_ids = [cs.id for cs, _ in cs_instr_rows]

        agg, ease_rows, reviews_raw, sc_code = await asyncio.gather(
            _agg(cs_ids), _ease(cs_ids), _reviews(cs_ids), _syllabus_code()
        )

        # ビューカウント記録
        if cs_ids:
            main_cs_id = cs_ids[0]
            async with AsyncSessionLocal() as s:
                _now = datetime.now(timezone.utc)
                _ins = pg_insert(CourseSectionView).values(
                    course_section_id=main_cs_id,
                    view_count=1,
                    last_viewed_at=_now,
                )
                await s.execute(
                    _ins.on_conflict_do_update(
                        index_elements=["course_section_id"],
                        set_={
                            "view_count": CourseSectionView.view_count + 1,
                            "last_viewed_at": _now,
                        },
                    )
                )
                await s.commit()

        # 最初の非NULL syllabus_url を CourseSection から取得
        syllabus_url = next((cs.syllabus_url for cs, _ in cs_instr_rows if cs.syllabus_url), None)
        if not syllabus_url:
            syllabus_url = make_syllabus_url(sc_code or "")
        instructor_str = "・".join(instr.name for _, instr in cs_instr_rows)
        avg_rating = float(agg[0]) if agg and agg[0] else None
        top_ease = None
        if ease_rows:
            top_ease = sorted(ease_rows, key=lambda r: (-r[1], EASE_ORDER.get(r[0], 99)))[0][0]

        return {
            "id": subject.id,
            "name": subject.name,
            "instructor": instructor_str,
            "classification": subject.classification or "",
            "category": subject.category or "",
            "term": subject.term_type or "",
            "credits": float(subject.credits) if subject.credits else 0,
            "syllabus_url": syllabus_url or "",
            "avg_rating": avg_rating,
            "top_ease": top_ease,
            "reviews": [
                {
                    "rating": r.rating,
                    "ease_rating": r.ease_rating,
                    "grading_method": r.grading_method or "",
                    "comment": r.content or "",
                    "instructor": r.selected_instructor or "",
                    "nickname": r.nickname or "",
                    "academic_year": r.academic_year or 0,
                }
                for r in reviews_raw
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:
        await save_error_log(exc, action=f"api_course/{course_id}")
        raise
