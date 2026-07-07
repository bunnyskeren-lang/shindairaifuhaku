import json
import re as _re
import secrets as py_secrets
from collections import defaultdict
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_, select

from core import cache
from core.config import make_cls_sort, normalize_instructor_name, reading
from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, DisplayOrder, Instructor, Review, Subject

router = APIRouter()


@router.get("/admin/courses", response_class=HTMLResponse)
async def admin_courses(request: Request, _: str = Depends(check_admin), msg: str = "", q: str = Query(default=""), category: str = Query(default="")):
    q = q.strip()

    def _search_filter(q: str):
        q_safe = q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        return or_(
            Subject.name.ilike(f"%{q_safe}%", escape="\\"),
            Subject.reading.ilike(f"%{q_safe}%", escape="\\"),
            Subject.faculty.ilike(f"%{q_safe}%", escape="\\"),
        )

    async with AsyncSessionLocal() as session:
        base_stmt = select(Subject)
        if category:
            base_stmt = base_stmt.where(Subject.category == category)
        if q:
            base_stmt = base_stmt.where(_search_filter(q))

        courses = (await session.execute(
            base_stmt.order_by(Subject.sort_order, Subject.name)
        )).scalars().all()
        cls_map = await cache.get_cls_order_map()
        _cls_sort = make_cls_sort(cls_map)
        courses = sorted(courses, key=lambda c: (_cls_sort(c.classification or ""), c.sort_order, c.name or ""))
        total = len(courses)
        classifications = (await session.execute(
            select(Subject.classification).distinct().order_by(Subject.classification)
        )).scalars().all()
        class_counts_raw = dict((await session.execute(
            select(Subject.classification, func.count(Subject.id))
            .where(Subject.classification.isnot(None), Subject.classification != "")
            .group_by(Subject.classification)
            .order_by(Subject.classification)
        )).all())
        class_counts = {k: class_counts_raw[k] for k in sorted(class_counts_raw, key=_cls_sort)}

        course_ids = [c.id for c in courses]

        cs_instr_rows = []
        if course_ids:
            cs_instr_rows = (await session.execute(
                select(CourseSection, Instructor)
                .join(Instructor, Instructor.id == CourseSection.instructor_id)
                .where(CourseSection.subject_id.in_(course_ids))
            )).all()

        reviews_data = []
        if course_ids:
            reviews_data = (await session.execute(
                select(Review, CourseSection.subject_id, Subject.name.label("subj_name"))
                .join(CourseSection, CourseSection.id == Review.course_section_id)
                .join(Subject, Subject.id == CourseSection.subject_id)
                .where(CourseSection.subject_id.in_(course_ids))
                .order_by(Review.is_approved, Review.created_at.desc())
            )).all()

        all_instructors = [] if q or category else (await session.execute(
            select(Instructor).order_by(Instructor.sort_order, Instructor.name)
        )).scalars().all()

        all_faculties = [] if q or category else (await session.execute(
            select(DisplayOrder).where(DisplayOrder.kind == "faculty").order_by(DisplayOrder.sort_order)
        )).scalars().all()

    existing = sorted([c for c in classifications if c], key=_cls_sort)
    courses_data = (
        json.dumps({
            c.id: {
                "name": c.name,
                "instructor": "",
                "classification": c.classification or "",
                "category": c.category or "",
                "syllabus_url": "",
                "faculty": c.faculty or "",
                "term_type": c.term_type or "",
                "credits": float(c.credits) if c.credits is not None else 0,
                "hide_from_timetable": c.hide_from_timetable,
            }
            for c in courses
        }, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )

    instructors_by_course: dict = defaultdict(list)
    for cs, inst in sorted(cs_instr_rows, key=lambda x: (x[1].sort_order, x[1].name)):
        instructors_by_course[cs.subject_id].append(
            SimpleNamespace(id=inst.id, name=inst.name, url=cs.syllabus_url or "")
        )

    # 修正理由: Subject.nameにはUNIQUE制約が無く同名科目が複数存在しうるため、
    # 科目名でキーイングすると別レコードのレビューが同名科目のカードに混在して表示される。
    # instructors_by_courseと同様にSubject.id（subject_id）でキーイングする。
    reviews_by_course: dict = defaultdict(list)
    for rev, subj_id, subj_name in reviews_data:
        reviews_by_course[subj_id].append(SimpleNamespace(
            id=rev.id,
            course_name=subj_name,
            comment=rev.content,
            content=rev.content,
            rating=rev.rating,
            ease_rating=rev.ease_rating,
            grading_method=rev.grading_method,
            is_approved=rev.is_approved,
            selected_instructor=rev.selected_instructor,
            created_at=rev.created_at,
            submitter_name=rev.submitter_name,
            nickname=rev.nickname,
            academic_year=rev.academic_year,
            student_id=rev.student_id,
        ))

    # groupby順を保持するため事前グループ化
    cls_parent_map = await cache.get_cls_parent_map()
    child_cls_set = set(cls_parent_map.keys())
    parent_names_set = set(cls_parent_map.values())

    parent_subgroups: dict = defaultdict(lambda: defaultdict(list))
    regular_grouped: dict = defaultdict(list)
    for c in courses:
        cls = c.classification or "（未分類）"
        if cls in child_cls_set:
            parent_subgroups[cls_parent_map[cls]][cls].append(c)
        elif cls in parent_names_set:
            parent_subgroups[cls]["（未分類）"].append(c)
        else:
            regular_grouped[cls].append(c)

    # parent_subgroups を並び順に整形
    cls_order_map = await cache.get_cls_order_map()
    _cls_sort = make_cls_sort(cls_order_map)
    parent_subgroups_sorted = {
        pg: sorted(sub.items(), key=lambda x: _cls_sort(x[0]))
        for pg, sub in sorted(parent_subgroups.items())
    }

    return templates.TemplateResponse("admin/courses.html", {
        "request": request,
        "courses": courses,
        "grouped_courses": list(regular_grouped.items()),
        "parent_subgroups": parent_subgroups_sorted,
        "cls_parent_map": cls_parent_map,
        "active_category": category,
        "classifications": existing,
        "class_counts": class_counts,
        "courses_data": courses_data,
        "reviews_by_course": reviews_by_course,
        "instructors_by_course": instructors_by_course,
        "all_instructors": all_instructors,
        "all_faculties": all_faculties,
        "error": msg,
        "total": total,
        "q": q,
    })


@router.post("/admin/courses/{course_id}/instructors/add")
async def add_instructor(course_id: int, request: Request, name: str = Form(...), url: str = Form(""), _: str = Depends(check_admin)):
    name_s = normalize_instructor_name(name.strip())
    url_s = url.strip() or None
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
            cs = CourseSection(subject_id=course_id, instructor_id=instr.id, syllabus_url=url_s)
            session.add(cs)
            await session.commit()
            cache.invalidate_courses_cache()
            if is_ajax:
                return JSONResponse({"ok": True, "id": instr.id, "name": instr.name, "url": url_s or ""})
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

        try:
            idx = next(i for i, inst in enumerate(all_instr) if inst.id == instructor_id)
        except StopIteration:
            return JSONResponse({"ok": False})

        delta = -1 if direction == "up" else 1
        swap_idx = idx + delta
        if swap_idx < 0 or swap_idx >= len(all_instr):
            return JSONResponse({"ok": True})

        all_instr[idx], all_instr[swap_idx] = all_instr[swap_idx], all_instr[idx]
        for i, inst in enumerate(all_instr):
            inst.sort_order = i
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

        try:
            idx = next(i for i, f in enumerate(all_fac) if f.id == faculty_id)
        except StopIteration:
            return JSONResponse({"ok": False})

        delta = -1 if direction == "up" else 1
        swap_idx = idx + delta
        if swap_idx < 0 or swap_idx >= len(all_fac):
            return JSONResponse({"ok": True})

        all_fac[idx], all_fac[swap_idx] = all_fac[swap_idx], all_fac[idx]
        for i, f in enumerate(all_fac):
            f.sort_order = i
        await session.commit()
    cache.invalidate_faculty_order_cache()
    return JSONResponse({"ok": True})


@router.post("/admin/courses/migrate-third-language")
async def migrate_third_language(_: str = Depends(check_admin)):
    LANGS = ["ドイツ語", "フランス語"]
    NUMS = [1, 2, 3, 4]
    has_skipped = False
    async with AsyncSessionLocal() as session:
        to_delete = (await session.execute(
            select(Subject).where(Subject.name.contains("第三外国語"))
        )).scalars().all()
        for c in to_delete:
            # 修正理由: レビューが紐づくcourse_sectionはRESTRICT制約で削除できず、
            # チェックなしだと例外発生時にこの一括処理全体がロールバックされていた。
            # レビューが付いている科目は削除せずスキップする（他ルートのhas_reviewsチェックと同じ方針）。
            has_reviews = (await session.execute(
                select(func.count(Review.id))
                .join(CourseSection, CourseSection.id == Review.course_section_id)
                .where(CourseSection.subject_id == c.id)
            )).scalar()
            if has_reviews:
                has_skipped = True
                continue
            await session.delete(c)
        for lang in LANGS:
            for n in NUMS:
                name = f"第三外国語({lang})T{n}"
                existing = (await session.execute(
                    select(Subject).where(Subject.name == name)
                )).scalar_one_or_none()
                if not existing:
                    session.add(Subject(
                        name=name,
                        classification="外国語", category="教養",
                        reading=reading(name),
                    ))
        await session.commit()
    cache.invalidate_courses_cache()
    if has_skipped:
        return RedirectResponse("/admin/courses?msg=has_reviews", status_code=303)
    return RedirectResponse("/admin/courses", status_code=303)


@router.post("/admin/courses/strip-trailing-numbers")
async def strip_trailing_numbers(
    request: Request,
    _: str = Depends(check_admin),
    prefix: str = Form(default=""),
):
    async with AsyncSessionLocal() as session:
        stmt = select(Subject)
        if prefix.strip():
            stmt = stmt.where(Subject.name.contains(prefix.strip()))
        courses = (await session.execute(stmt)).scalars().all()

        groups: dict[str, list] = defaultdict(list)
        for course in courses:
            base = _re.sub(r'[\s　]*\d+$', '', course.name).strip()
            if base != course.name:
                groups[base].append(course)

        has_skipped = False
        for base_name, dups in groups.items():
            existing = (await session.execute(
                select(Subject).where(Subject.name == base_name)
            )).scalar_one_or_none()

            if existing:
                to_remove = dups
            else:
                survivor = dups[0]
                survivor.name = base_name
                survivor.reading = reading(base_name)
                to_remove = dups[1:]

            # 修正理由: レビューが紐づくcourse_sectionはRESTRICT制約で削除できない。
            # チェックなしだと1グループの例外でリクエスト全体（他の無関係なグループの
            # マージも含む）がロールバックされていた。レビュー付き科目はスキップして
            # 他のグループのマージは継続させる。
            for dup in to_remove:
                has_reviews = (await session.execute(
                    select(func.count(Review.id))
                    .join(CourseSection, CourseSection.id == Review.course_section_id)
                    .where(CourseSection.subject_id == dup.id)
                )).scalar()
                if has_reviews:
                    has_skipped = True
                    continue
                await session.delete(dup)

        await session.commit()
    cache.invalidate_courses_cache()
    if has_skipped:
        return RedirectResponse("/admin/courses?msg=has_reviews", status_code=303)
    return RedirectResponse("/admin/courses", status_code=303)


@router.post("/admin/courses/add")
async def admin_courses_add(
    _: str = Depends(check_admin),
    name: str = Form(...),
    classification: str = Form(""),
    category: str = Form("専門"),
    term_type: str = Form(""),
    credits: float = Form(0),
    syllabus_url: str = Form(""),
    faculty: str = Form(""),
):
    name_s = name.strip()
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(Subject).where(Subject.name == name_s)
        )).scalar_one_or_none()
        if existing:
            return RedirectResponse(
                url=f"/admin/courses?error={py_secrets.token_urlsafe(4)}&msg=duplicate",
                status_code=303,
            )
        session.add(Subject(
            name=name_s,
            classification=classification.strip() or None,
            category=category,
            reading=reading(name_s),
            term_type=term_type.strip() or None,
            credits=credits if credits else None,
            faculty=faculty.strip() or None,
        ))
        await session.commit()
    cache.invalidate_courses_cache()
    cache.invalidate_cls_caches()
    return RedirectResponse(url="/admin/courses", status_code=303)


@router.post("/admin/courses/classification/rename")
async def rename_classification(
    _: str = Depends(check_admin),
    old_name: str = Form(...),
    new_name: str = Form(...),
):
    new_name = new_name.strip()
    if not new_name or new_name == old_name:
        return RedirectResponse(url="/admin/courses", status_code=303)
    async with AsyncSessionLocal() as session:
        courses = (await session.execute(
            select(Subject).where(Subject.classification == old_name)
        )).scalars().all()
        for course in courses:
            course.classification = new_name
        cls_row = (await session.execute(
            select(DisplayOrder).where(DisplayOrder.kind == "classification", DisplayOrder.name == old_name)
        )).scalar_one_or_none()
        if cls_row:
            # 修正理由: new_nameが既存の分類名（2つの分類を1つに統合するケース）だと
            # (kind, name, faculty)のUNIQUE制約に違反しIntegrityErrorで科目側の
            # classification更新まで全てロールバックしていた。既存分類への統合の場合は
            # 旧DisplayOrder行を削除する（新分類側の並び順をそのまま使う）。
            new_row_exists = (await session.execute(
                select(DisplayOrder).where(DisplayOrder.kind == "classification", DisplayOrder.name == new_name)
            )).scalar_one_or_none()
            if new_row_exists:
                await session.delete(cls_row)
            else:
                cls_row.name = new_name
        await session.commit()
    cache.invalidate_cls_caches()
    cache.invalidate_courses_cache()
    return RedirectResponse(url="/admin/courses", status_code=303)


@router.post("/admin/courses/classification/delete")
async def delete_classification(
    _: str = Depends(check_admin),
    classification: str = Form(...),
):
    async with AsyncSessionLocal() as session:
        courses_in_class = (await session.execute(
            select(Subject).where(Subject.classification == classification)
        )).scalars().all()
        for course in courses_in_class:
            course.classification = None
        cls_row = (await session.execute(
            select(DisplayOrder).where(DisplayOrder.kind == "classification", DisplayOrder.name == classification)
        )).scalar_one_or_none()
        if cls_row:
            await session.delete(cls_row)
        await session.commit()
    cache.invalidate_cls_caches()
    cache.invalidate_courses_cache()
    return RedirectResponse(url="/admin/courses", status_code=303)


@router.post("/admin/courses/classification/move")
async def admin_cls_move(request: Request, _=Depends(check_admin)):
    data = await request.json()
    name = data.get("name", "")
    direction = data.get("direction", "")
    if not name or direction not in ("up", "down"):
        return JSONResponse({"ok": False})

    async with AsyncSessionLocal() as session:
        all_cls = sorted(
            [c for c in (await session.execute(
                select(Subject.classification).distinct()
            )).scalars().all() if c],
        )
        cls_map = await cache.get_cls_order_map()
        _cls_sort = make_cls_sort(cls_map)
        sorted_cls = sorted(all_cls, key=_cls_sort)

        try:
            idx = sorted_cls.index(name)
        except ValueError:
            return JSONResponse({"ok": False})

        delta = -1 if direction == "up" else 1
        swap_idx = idx + delta
        if swap_idx < 0 or swap_idx >= len(sorted_cls):
            return JSONResponse({"ok": True})

        sorted_cls[idx], sorted_cls[swap_idx] = sorted_cls[swap_idx], sorted_cls[idx]

        for i, cls_name in enumerate(sorted_cls):
            existing = (await session.execute(
                select(DisplayOrder).where(DisplayOrder.kind == "classification", DisplayOrder.name == cls_name)
            )).scalar_one_or_none()
            if existing:
                existing.sort_order = i
            else:
                session.add(DisplayOrder(kind="classification", name=cls_name, sort_order=i))
        await session.commit()
    cache.invalidate_cls_caches()
    cache.invalidate_courses_cache()
    return JSONResponse({"ok": True})


@router.post("/admin/courses/classification/set_parent")
async def admin_cls_set_parent(
    _: str = Depends(check_admin),
    classification: str = Form(...),
    parent_group: str = Form(default=""),
):
    parent_group = parent_group.strip()
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            select(DisplayOrder).where(DisplayOrder.kind == "classification", DisplayOrder.name == classification)
        )).scalar_one_or_none()
        if row:
            row.parent_group = parent_group or None
        else:
            session.add(DisplayOrder(kind="classification", name=classification, sort_order=0, parent_group=parent_group or None))
        await session.commit()
    cache.invalidate_cls_caches()
    cache.invalidate_courses_cache()
    return RedirectResponse(url="/admin/courses", status_code=303)


@router.post("/admin/courses/{course_id}/toggle-timetable-visibility")
async def admin_course_toggle_timetable_visibility(course_id: int, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        course = await session.get(Subject, course_id)
        if not course:
            return JSONResponse({"ok": False})
        course.hide_from_timetable = not course.hide_from_timetable
        await session.commit()
        hidden = course.hide_from_timetable
    cache.invalidate_courses_cache()
    return JSONResponse({"ok": True, "hide_from_timetable": hidden})


@router.post("/admin/courses/{course_id}/move")
async def admin_course_move(course_id: int, request: Request, _=Depends(check_admin)):
    data = await request.json()
    direction = data.get("direction", "")
    if direction not in ("up", "down"):
        return JSONResponse({"ok": False})

    async with AsyncSessionLocal() as session:
        course = await session.get(Subject, course_id)
        if not course:
            return JSONResponse({"ok": False})

        all_in_cls = list((await session.execute(
            select(Subject)
            .where(Subject.classification == (course.classification or ""))
            .order_by(Subject.sort_order, Subject.name)
        )).scalars().all())

        try:
            idx = next(i for i, c in enumerate(all_in_cls) if c.id == course_id)
        except StopIteration:
            return JSONResponse({"ok": False})

        delta = -1 if direction == "up" else 1
        swap_idx = idx + delta
        if swap_idx < 0 or swap_idx >= len(all_in_cls):
            return JSONResponse({"ok": True})

        all_in_cls[idx], all_in_cls[swap_idx] = all_in_cls[swap_idx], all_in_cls[idx]
        for i, c in enumerate(all_in_cls):
            c.sort_order = i
        await session.commit()
    cache.invalidate_courses_cache()
    return JSONResponse({"ok": True})


@router.post("/admin/courses/update/{course_id}")
async def admin_courses_update(
    course_id: int,
    _: str = Depends(check_admin),
    name: str = Form(...),
    classification: str = Form(""),
    category: str = Form("専門"),
    term_type: str = Form(""),
    credits: float = Form(0),
    syllabus_url: str = Form(""),
    faculty: str = Form(""),
):
    async with AsyncSessionLocal() as session:
        course = (await session.execute(select(Subject).where(Subject.id == course_id))).scalar_one_or_none()
        if course:
            new_name = name.strip()
            course.name = new_name
            course.classification = classification.strip() or None
            course.category = category
            course.reading = reading(new_name)
            course.term_type = term_type.strip() or None
            course.credits = credits if credits else None
            course.faculty = faculty.strip() or None
            await session.commit()
    cache.invalidate_courses_cache()
    cache.invalidate_cls_caches()
    return RedirectResponse(url="/admin/courses", status_code=303)


@router.post("/admin/courses/delete/{course_id}")
async def admin_courses_delete(course_id: int, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        course = (await session.execute(select(Subject).where(Subject.id == course_id))).scalar_one_or_none()
        if course:
            cs_ids = (await session.execute(
                select(CourseSection.id).where(CourseSection.subject_id == course_id)
            )).scalars().all()
            if cs_ids:
                has_reviews = (await session.execute(
                    select(func.count(Review.id)).where(
                        Review.course_section_id.in_(cs_ids),
                    )
                )).scalar()
                if has_reviews:
                    return RedirectResponse(url=f"/admin/courses?msg=has_reviews", status_code=303)
            await session.delete(course)
            await session.commit()
    cache.invalidate_courses_cache()
    cache.invalidate_cls_caches()
    return RedirectResponse(url="/admin/courses", status_code=303)
