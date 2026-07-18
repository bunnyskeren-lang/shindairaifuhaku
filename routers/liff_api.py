import asyncio
import re as _re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core import cache, line_client
from core.activity_log import save_error_log
from core.config import (
    APP_URL, DEPARTMENT_UNDECIDED_FACULTIES, DEPARTMENT_UNDECIDED_VALUE, EASE_ORDER, FACULTIES, FACULTY_DEPARTMENTS,
    REGISTER_LIFF_ID, STUDENT_ID_RE, LINE_USER_ID_RE,
    is_profile_complete, make_syllabus_url,
)
from core.liff_auth import verify_liff_id_token
from core.push import send_push_notification
from core.rate_limit import rate_limiter
from core.required_subjects import auto_register_required_subjects
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, CourseSectionView, Instructor, Review, Subject, Syllabus, UserProfile

router = APIRouter()

_FORM_PUNCT = '・･（）()'
# 修正理由: レビュー連投によるスパム・審査キュー圧迫を防ぐため、IPアドレス単位で1分あたり3回までに制限する
_submit_rate_limit = rate_limiter(max_requests=3, window_seconds=60)
# 修正理由: student_idの総当たりによる他人の氏名取得を防ぐため、IPアドレス単位で1分あたり10回までに制限する
_autofill_rate_limit = rate_limiter(max_requests=10, window_seconds=60)


def _normalize_form_q(s: str) -> str:
    for ch in _FORM_PUNCT:
        s = s.replace(ch, '')
    return s


async def _latest_syllabus_urls(session, cs_ids: list) -> dict[int, str]:
    """course_section_idごとに最新年度のsyllabus_urlをtimetable_code/departmentから動的生成する。"""
    if not cs_ids:
        return {}
    rows = (await session.execute(
        select(Syllabus.course_section_id, Syllabus.timetable_code, Syllabus.department, Syllabus.year)
        .where(Syllabus.course_section_id.in_(cs_ids), Syllabus.timetable_code.isnot(None))
    )).all()
    latest_year: dict[int, int] = {}
    result: dict[int, str] = {}
    for cs_id, code, dept, year in rows:
        if cs_id in latest_year and year <= latest_year[cs_id]:
            continue
        url = make_syllabus_url(code, dept or "")
        if not url:
            continue
        latest_year[cs_id] = year
        result[cs_id] = url
    return result


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
        cs_url_map = await _latest_syllabus_urls(session, [cs.id for cs, _ in cs_rows])
        insts_by_course: dict = {}
        for cs, inst in cs_rows:
            insts_by_course.setdefault(cs.subject_id, []).append({"name": inst.name, "url": cs_url_map.get(cs.id, "")})
    return {"courses": [
        {"id": c.id, "name": c.name, "instructors": insts_by_course.get(c.id, [])}
        for c in courses
    ]}


@router.get("/api/preload")
async def api_preload():
    data = cache.get_preload_cache()
    if data is None:
        _, courses = await cache.get_courses_cached()
        insts_by_course = await cache.get_all_instructors_cached()
        inst_courses: dict[str, dict[str, None]] = {}
        for c in courses:
            for inst in insts_by_course.get(c.id, []):
                inst_courses.setdefault(inst.name, {})[c.name] = None
        course_list = [
            {"id": c.id, "name": c.name, "reading": c.reading or "",
             "instructors": [{"name": i.name} for i in insts_by_course.get(c.id, [])]}
            for c in courses
        ]
        instructor_list = [
            {"name": name, "courses": [{"name": cn} for cn in cnames]}
            for name, cnames in sorted(inst_courses.items())
        ]
        data = {"courses": course_list, "instructors": instructor_list}
        cache.set_preload_cache(data)
    res = JSONResponse(data)
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


@router.post("/api/profile/status")
async def profile_status(request: Request):
    body = await request.json()
    uid = await verify_liff_id_token((body.get("id_token") or "").strip(), request)
    if not uid:
        return {"complete": False}
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, uid)
        return {"complete": is_profile_complete(profile)}


@router.post("/api/profile/prefill")
async def profile_prefill(request: Request):
    """LIFF ID token検証済みの本人の既存プロフィールを返す（新規登録フォームのプリフィル用）。

    修正理由: 以前は ?uid= から直接DB照会してテンプレートに埋め込んでいたため、
    任意のuidを指定するだけで他人の氏名・学籍番号等が閲覧できるIDOR/PII漏洩になっていた。
    """
    body = await request.json()
    uid = await verify_liff_id_token((body.get("id_token") or "").strip(), request)
    if not uid:
        return {"found": False}
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, uid)
    if not profile:
        return {"found": False}
    return {
        "found": True,
        "name": profile.name,
        "student_id": profile.student_id,
        "faculty": profile.faculty,
        "grade": profile.grade,
        "department": profile.department,
    }


@router.post("/api/register")
async def register_profile(
    request: Request,
    id_token: str = Form(...),
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

    uid = await verify_liff_id_token(id_token, request)
    if not uid or not LINE_USER_ID_RE.match(uid):
        return _form_error("LINEログインの確認に失敗しました。LINEアプリから開き直してください")
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
    # 2年次からコース分岐する学部（農学部等）は1年次に所属コースが無いため「コース未定」を許容し、
    # departmentはNULLで保存する（単位チェッカーは代表コースへフォールバックする）
    if faculty in DEPARTMENT_UNDECIDED_FACULTIES and department == DEPARTMENT_UNDECIDED_VALUE:
        department = None
    elif department not in FACULTY_DEPARTMENTS.get(faculty, []):
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
            profile = UserProfile(
                line_user_id=uid,
                name=name[:100],
                student_id=sid,
                faculty=faculty,
                grade=grade,
                department=department,
            )
            session.add(profile)
        try:
            await auto_register_required_subjects(session, uid, profile)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await save_error_log(exc, user_id=uid, action="register_profile")
            return _form_error("登録に失敗しました。もう一度お試しください")

    cache.set_registration_complete(uid)

    try:
        await line_client.unlink_rich_menu(uid)
    except Exception as exc:
        await save_error_log(exc, user_id=uid, action="register_richmenu_unlink")

    return templates.TemplateResponse(
        "form_register_success.html", {"request": request, "liff_id": REGISTER_LIFF_ID}
    )


@router.post("/api/autofill")
async def autofill_profile(request: Request, _rl: None = Depends(_autofill_rate_limit)):
    body = await request.json()
    id_token = (body.get("id_token") or "").strip()
    student_id = (body.get("student_id") or "")
    uid = await verify_liff_id_token(id_token, request)
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
            except Exception as exc:
                await session.rollback()
                await save_error_log(exc, user_id=uid, action="autofill_profile_create")
        return {"found": True, "name": row}


@router.post("/submit")
async def submit(
    request: Request,
    course_name: str = Form(...),
    rating: int = Form(...),
    ease_rating: str = Form(...),
    grading_method: str = Form(default=""),
    comment: str = Form(...),
    id_token: str = Form(default=""),
    reg_name: str = Form(default=""),
    student_id: str = Form(default=""),
    selected_instructor: str = Form(default=""),
    nickname: str = Form(default=""),
    academic_year: int = Form(default=0),
    _rl: None = Depends(_submit_rate_limit),
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

    uid = await verify_liff_id_token(id_token, request)
    if not uid or not LINE_USER_ID_RE.match(uid):
        return _form_error("LINEログインの確認に失敗しました。LINEアプリの「レビュー投稿」から開き直してください")

    async with AsyncSessionLocal() as session:
        # 学部をまたいで同名科目が実在しうるため、ここでは存在確認のみ行い
        # .first()で1件だけ取得する（どの学部の科目かは後段の担当教員絞り込みで確定させる）
        subject = (await session.execute(
            select(Subject).where(Subject.name == course_name.strip())
        )).scalars().first()
        if not subject:
            return _form_error("指定された科目が見つかりません")

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
            except Exception as exc:
                await session.rollback()
                await save_error_log(exc, user_id=uid, action="submit_profile_create")
                return _form_error("プロフィールの保存に失敗しました")
        else:
            if existing.student_id != sid:
                return _form_error("学籍番号が登録情報と一致しません")
            submitter_name = existing.name

        # 担当教員に対応する course_section を探す
        instr_name = selected_instructor.strip()[:100] or None
        cs_obj = None
        if instr_name:
            # 科目名＋担当教員名でjoinし直すことで、学部をまたいで同名科目が存在する場合でも
            # 正しいsubject（先頭取得のものとは限らない）とcourse_sectionを一意に特定する
            row = (await session.execute(
                select(Subject, CourseSection)
                .join(CourseSection, CourseSection.subject_id == Subject.id)
                .join(Instructor, Instructor.id == CourseSection.instructor_id)
                .where(Subject.name == course_name.strip(), Instructor.name == instr_name)
            )).first()
            if row is not None:
                subject, cs_obj = row
            # 修正理由: 教員名が指定されたのに一致するcourse_sectionが見つからない場合
            # （教員名変更・統合との競合など）、無条件で「科目の先頭のcourse_section」に
            # フォールバックしていたため、別教員のレビューとして紐づく恐れがあった。
            # 教員未指定（instr_name無し）の場合のみ先頭フォールバックを許可する。
            if cs_obj is None:
                return _form_error("担当教員の情報が更新されています。ページを再読み込みしてもう一度お試しください")
        else:
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

        review_count = (await session.execute(
            select(func.count(Review.id)).where(Review.student_id == sid)
        )).scalar_one()
        course_id = subject.id

    # レビューは既にcommit済みのため、push通知はレスポンスを待たせず
    # バックグラウンドで送る（購読者数が増えても投稿完了レスポンスの速度に影響しないように）。
    async def _notify() -> None:
        try:
            await send_push_notification(
                course_name=course_name.strip(),
                rating=rating,
                ease_rating=ease_rating,
                comment=comment.strip(),
            )
        except Exception as exc:
            await save_error_log(exc, user_id=uid, action="submit_push_notification")

    asyncio.create_task(_notify())

    return templates.TemplateResponse(
        "form_success.html", {
            "request": request,
            "course_name": course_name,
            "course_id": course_id,
            "review_count": review_count,
            "base_url": APP_URL,
        }
    )


@router.get("/api/course/{course_id}")
async def api_course(course_id: int):
    try:
        # 修正理由: subject取得とcs_instr取得は元々別々のAsyncSessionLocal()を開いており、
        # どちらもcourse_id確定後に順番に実行するだけの依存関係なので、DB接続の往復を
        # 1回分減らすため同じセッションにまとめる。
        async with AsyncSessionLocal() as session:
            subject = await session.get(Subject, course_id)
            if not subject:
                raise HTTPException(status_code=404, detail="course not found")
            # 修正理由: ORDER BY未指定だとPostgreSQLは行順を保証せず、これに依存する
            # 閲覧数記録先(main_cs_id)・表示するシラバスURL・教員名の表示順がリクエスト
            # ごとに変わりうる非決定的な挙動になっていた。id順で固定する。
            cs_instr_rows = (await session.execute(
                select(CourseSection, Instructor)
                .join(Instructor, Instructor.id == CourseSection.instructor_id)
                .where(CourseSection.subject_id == course_id)
                .order_by(CourseSection.id)
            )).all()

        async def _agg(cs_ids: list):
            if not cs_ids:
                return None
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(func.avg(Review.rating), func.count(Review.id))
                    .where(Review.course_section_id.in_(cs_ids), Review.is_approved.is_(True))
                )).first()

        async def _ease(cs_ids: list):
            if not cs_ids:
                return []
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(Review.ease_rating, func.count(Review.id))
                    .where(Review.course_section_id.in_(cs_ids), Review.is_approved.is_(True))
                    .group_by(Review.ease_rating)
                )).all()

        async def _reviews(cs_ids: list):
            if not cs_ids:
                return []
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(Review)
                    .where(Review.course_section_id.in_(cs_ids), Review.is_approved.is_(True))
                    .order_by(Review.selected_instructor.nulls_last(), Review.academic_year.desc())
                    .limit(20)
                )).scalars().all()

        async def _syllabus_code():
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(Syllabus.timetable_code, Syllabus.department)
                    .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
                    .where(CourseSection.subject_id == course_id, Syllabus.timetable_code.isnot(None))
                    .order_by(Syllabus.year.desc())
                    .limit(1)
                )).first()

        cs_ids = [cs.id for cs, _ in cs_instr_rows]

        agg, ease_rows, reviews_raw, sc_row = await asyncio.gather(
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

        # 最新年度のsyllabiからtimetable_code/departmentを取得しシラバスURLを動的生成
        syllabus_url = make_syllabus_url(sc_row[0], sc_row[1] or "") if sc_row else ""
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
