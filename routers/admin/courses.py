import json
from collections import defaultdict
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import case, func, or_, select

from core import cache
from core.config import escape_like, make_cls_sort, make_syllabus_url, reading, syllabus_department_key
from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, DisplayOrder, Instructor, Review, Subject, Syllabus
from routers.admin._common import reorder_sort_order

router = APIRouter()


_PAGE_SIZE = 50


@router.get("/admin/courses", response_class=HTMLResponse)
async def admin_courses(
    request: Request, _: str = Depends(check_admin), msg: str = "",
    q: str = Query(default=""), category: str = Query(default=""), page: int = Query(default=1, ge=1),
):
    q = q.strip()

    def _search_filter(q: str):
        q_safe = escape_like(q)
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
        # 修正理由: 「すべて」タブ(q・category未指定)は4000件超を毎回HTML/JSに全展開しており
        # レスポンスサイズ・レンダリング時間が肥大化していた。分類の並び順（DisplayOrder由来の
        # カスタム順）はDBのORDER BYだけでは再現できずPython側で全件ソートする必要があるため、
        # DBクエリ自体の絞り込みではなく、ソート後にページ単位へスライスする形で表示量のみ削減する。
        # 検索結果・大分類フィルタ時は件数が絞られ実害が小さいため従来通り全件表示のままにする。
        total_pages = 1
        if not q and not category:
            total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
            page = min(page, total_pages)
            start = (page - 1) * _PAGE_SIZE
            courses = courses[start:start + _PAGE_SIZE]
        else:
            page = 1
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

        # course_sectionごとに最新年度のsyllabus_urlをtimetable_code/departmentから動的生成
        # （departmentはSyllabusではなくSubject.faculty/departmentから再構成。coursesは既に
        # ロード済みなので追加JOIN不要）
        subj_by_id = {c.id: c for c in courses}
        cs_subject_map = {cs.id: cs.subject_id for cs, _ in cs_instr_rows}
        cs_url_map: dict[int, str] = {}
        cs_ids_all = [cs.id for cs, _ in cs_instr_rows]
        if cs_ids_all:
            syl_rows = (await session.execute(
                select(Syllabus.course_section_id, Syllabus.timetable_code, Syllabus.year)
                .where(Syllabus.course_section_id.in_(cs_ids_all), Syllabus.timetable_code.isnot(None))
            )).all()
            _latest_year: dict[int, int] = {}
            for cs_id, code, year in syl_rows:
                if cs_id in _latest_year and year <= _latest_year[cs_id]:
                    continue
                subj = subj_by_id.get(cs_subject_map.get(cs_id))
                dept = syllabus_department_key(subj) if subj else ""
                url = make_syllabus_url(code, dept)
                if not url:
                    continue
                _latest_year[cs_id] = year
                cs_url_map[cs_id] = url

        reviews_data = []
        if course_ids:
            reviews_data = (await session.execute(
                select(Review, CourseSection.subject_id, Subject.name.label("subj_name"))
                .join(CourseSection, CourseSection.id == Review.course_section_id)
                .join(Subject, Subject.id == CourseSection.subject_id)
                .where(CourseSection.subject_id.in_(course_ids))
                .order_by(
                    case((Review.status == "pending", 0), (Review.status == "approved", 1), else_=2),
                    Review.created_at.desc(),
                )
            )).all()

        all_instructors = [] if q or category else (await session.execute(
            select(Instructor).order_by(Instructor.sort_order, Instructor.name)
        )).scalars().all()

        all_faculties = [] if q or category else (await session.execute(
            select(DisplayOrder).where(DisplayOrder.kind == "faculty").order_by(DisplayOrder.sort_order)
        )).scalars().all()

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
            SimpleNamespace(id=inst.id, name=inst.name, url=cs_url_map.get(cs.id, ""))
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
            status=rev.status,
            selected_instructor=rev.selected_instructor,
            created_at=rev.created_at,
            submitter_name=rev.submitter_name,
            nickname=rev.nickname,
            academic_year=rev.academic_year,
            student_id=rev.student_id,
        ))

    # 科目カードのレビューボタンを教員ごとに分割表示するための集計（担当教員未選択は「未選択」にまとめる）
    _summary_tmp: dict = defaultdict(dict)
    for rev, subj_id, _subj_name in reviews_data:
        instructor_name = rev.selected_instructor or "未選択"
        bucket = _summary_tmp[subj_id].setdefault(
            instructor_name,
            {"instructor_name": instructor_name, "total": 0, "pending": 0, "approved": 0, "rejected": 0},
        )
        bucket["total"] += 1
        bucket[rev.status] += 1
    review_summary_by_course: dict = {
        subj_id: sorted(
            (SimpleNamespace(**v) for v in by_instructor.values()),
            key=lambda s: (s.instructor_name == "未選択", s.instructor_name),
        )
        for subj_id, by_instructor in _summary_tmp.items()
    }

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
        "class_counts": class_counts,
        "courses_data": courses_data,
        "reviews_by_course": reviews_by_course,
        "review_summary_by_course": review_summary_by_course,
        "instructors_by_course": instructors_by_course,
        "all_instructors": all_instructors,
        "all_faculties": all_faculties,
        "error": msg,
        "total": total,
        "q": q,
        "page": page,
        "total_pages": total_pages,
        "url_prefix": "/admin/courses?page=",
    })


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
        if not reorder_sort_order(all_in_cls, course_id, direction):
            return JSONResponse({"ok": False})
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
            # 修正理由: department列はnullable=False+空文字プレースホルダ方式なのに対し、
            # facultyだけ空欄保存でNULLになる非対称な状態だった。UNIQUE制約はNULL同士を
            # 区別しないため空文字に揃えておく(将来faculty列をNOT NULL化する際の前提)
            course.faculty = faculty.strip()
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
                    return RedirectResponse(url="/admin/courses?msg=has_reviews", status_code=303)
            await session.delete(course)
            await session.commit()
    cache.invalidate_courses_cache()
    cache.invalidate_cls_caches()
    return RedirectResponse(url="/admin/courses", status_code=303)
