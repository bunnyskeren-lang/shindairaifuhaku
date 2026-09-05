import json
from collections import defaultdict
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import case, func, or_, select

from core import cache, undo
from core.config import escape_like, make_cls_sort, make_syllabus_url, normalize_subject_name, reading, syllabus_department_key
from core.security import check_admin
from core.subject_variants import CLASSIFICATION_MERGE_EXCLUDED, compute_variant_display_groups
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, DisplayOrder, Instructor, Review, ReviewStatus, Subject, Syllabus
from routers.admin._common import reorder_sort_order

router = APIRouter()


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

    # 生物学各論A1/A2/C1/C2のような語尾バリアント科目を、科目一覧画面・レビュー投稿フォームと
    # 同じ規則で1行に統合表示するための判定（現在の検索・ページングとは独立した全科目データが
    # 対象。一部だけ検索にヒットした場合でもグループ全体を対象に統合する）
    _, all_courses_for_variant = await cache.get_courses_cached()
    label_by_name = compute_variant_display_groups(
        [(c.name, c.classification or "") for c in all_courses_for_variant
         if (c.classification or "") not in CLASSIFICATION_MERGE_EXCLUDED]
    )
    members_by_label: dict[str, list] = defaultdict(list)
    for c in all_courses_for_variant:
        label = label_by_name.get((c.name, c.classification or ""))
        if label:
            members_by_label[label].append(c)

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
        courses = sorted(courses, key=lambda c: (
            _cls_sort(c.classification or ""), c.sort_order, (c.reading or "").strip() or (c.name or "")
        ))
        total = len(courses)
        # 「すべて」タブでもページネーションなしで全件を一度に表示する
        # （以前は4000件超のレンダリング負荷対策で50件単位に分割していたが、
        # 「次へ」を何度も押す必要があり不便なためユーザーの希望で撤廃した）
        total_pages = 1
        page = 1
        class_counts_raw = dict((await session.execute(
            select(Subject.classification, func.count(Subject.id))
            .where(Subject.classification.isnot(None), Subject.classification != "")
            .group_by(Subject.classification)
            .order_by(Subject.classification)
        )).all())
        class_counts = {k: class_counts_raw[k] for k in sorted(class_counts_raw, key=_cls_sort)}

        # バリアントグループの行分け（分類ごとのグループ振り分けに使うcls_parent_map等はこの後の
        # ブロックで取得するため、行のグループ化自体はテンプレート整形部分でまとめて行う。ここでは
        # 「このページに表示される科目のうち、グループの代表としてどのidを問い合わせに含める必要が
        # あるか」だけを先に確定させ、担当教員・レビューのDBクエリ対象idに反映する）
        course_ids = [c.id for c in courses]
        seen_labels_for_query: set[str] = set()
        extra_ids: set[int] = set()
        for c in courses:
            label = label_by_name.get((c.name, c.classification or ""))
            if label and label not in seen_labels_for_query:
                seen_labels_for_query.add(label)
                extra_ids.update(m.id for m in members_by_label.get(label, []))
        query_ids = sorted(set(course_ids) | extra_ids)

        # 担当教員・レビューの中身（教員URL・レビュー本文）は科目管理画面を開いた時点では
        # 描画せず、「担当教員」「レビュー」ボタンを押した時にAjaxで取得する（lazy load）。
        # 全科目分を毎回埋め込むと科目数3000件超で1万行超のDOM生成になり画面が重くなっていたため。
        # ここでは件数バッジ表示に必要な軽量な集計のみ行う
        instr_ids_by_subject: dict[int, set[int]] = defaultdict(set)
        if query_ids:
            cs_pairs = (await session.execute(
                select(CourseSection.subject_id, CourseSection.instructor_id)
                .where(CourseSection.subject_id.in_(query_ids))
            )).all()
            for subj_id, instr_id in cs_pairs:
                instr_ids_by_subject[subj_id].add(instr_id)

        review_agg_rows = []
        if query_ids:
            review_agg_rows = (await session.execute(
                select(CourseSection.subject_id, Review.selected_instructor, Review.status, func.count(Review.id))
                .join(CourseSection, CourseSection.id == Review.course_section_id)
                .where(CourseSection.subject_id.in_(query_ids))
                .group_by(CourseSection.subject_id, Review.selected_instructor, Review.status)
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
                "classification": c.classification or "",
                "category": c.category or "",
                "faculty": c.faculty or "",
                "department": c.department or "",
                "term_type": c.term_type or "",
                "credits": float(c.credits) if c.credits is not None else 0,
            }
            for c in courses
        }, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )

    instructor_count_by_course: dict[int, int] = {
        subj_id: len(ids) for subj_id, ids in instr_ids_by_subject.items()
    }

    # 科目カードのレビューボタンを教員ごとに分割表示するための集計（担当教員未選択は「未選択」にまとめる）。
    # 修正理由: Subject.nameにはUNIQUE制約が無く同名科目が複数存在しうるため、
    # 科目名でキーイングすると別レコードのレビューが同名科目のカードに混在して表示される。
    # Subject.id（subject_id）でキーイングする。
    _summary_tmp: dict = defaultdict(dict)
    for subj_id, selected_instructor, status, cnt in review_agg_rows:
        instructor_name = selected_instructor or "未選択"
        bucket = _summary_tmp[subj_id].setdefault(
            instructor_name,
            {"instructor_name": instructor_name, "total": 0, ReviewStatus.PENDING: 0, ReviewStatus.APPROVED: 0, ReviewStatus.REJECTED: 0},
        )
        bucket["total"] += cnt
        bucket[status] += cnt
    review_summary_by_course: dict = {
        subj_id: sorted(
            (SimpleNamespace(**v) for v in by_instructor.values()),
            key=lambda s: (s.instructor_name == "未選択", s.instructor_name),
        )
        for subj_id, by_instructor in _summary_tmp.items()
    }
    review_total_by_course: dict[int, int] = {
        subj_id: sum(s.total for s in summary) for subj_id, summary in review_summary_by_course.items()
    }
    pending_count_by_course: dict[int, int] = {
        subj_id: sum(getattr(s, ReviewStatus.PENDING) for s in summary) for subj_id, summary in review_summary_by_course.items()
    }

    # groupby順を保持するため事前グループ化
    cls_parent_map = await cache.get_cls_parent_map()
    child_cls_set = set(cls_parent_map.keys())
    parent_names_set = set(cls_parent_map.values())

    # 語尾バリアント科目（生物学各論A1/A2/C1/C2等）を1行に統合するグループ行を構築。
    # 「編集」「削除」はグループ内の全科目に一括適用し、「担当教員」「レビュー」は全科目分を
    # 1つの一覧に集約表示する（ユーザー確認済みの管理画面統合表示の仕様）
    group_rows_by_label: dict = {}
    for c in courses:
        label = label_by_name.get((c.name, c.classification or ""))
        if not label or label in group_rows_by_label:
            continue
        members = members_by_label.get(label, [c])
        ids = [m.id for m in members]

        combined_instr_ids: set = set()
        for mid in ids:
            combined_instr_ids |= instr_ids_by_subject.get(mid, set())

        summary_tmp_grp: dict = {}
        for mid in ids:
            for s in review_summary_by_course.get(mid, []):
                bucket = summary_tmp_grp.setdefault(
                    s.instructor_name,
                    {"instructor_name": s.instructor_name, "total": 0, ReviewStatus.PENDING: 0, ReviewStatus.APPROVED: 0, ReviewStatus.REJECTED: 0},
                )
                bucket["total"] += s.total
                bucket[ReviewStatus.PENDING] += getattr(s, ReviewStatus.PENDING)
                bucket[ReviewStatus.APPROVED] += getattr(s, ReviewStatus.APPROVED)
                bucket[ReviewStatus.REJECTED] += getattr(s, ReviewStatus.REJECTED)
        review_summary = sorted(
            (SimpleNamespace(**v) for v in summary_tmp_grp.values()),
            key=lambda s: (s.instructor_name == "未選択", s.instructor_name),
        )
        pending_count = sum(getattr(s, ReviewStatus.PENDING) for s in review_summary)
        review_total = sum(s.total for s in review_summary)

        primary = members[0]
        group_rows_by_label[label] = SimpleNamespace(
            type="group",
            key=f"g{len(group_rows_by_label) + 1}",
            label=label,
            members=members,
            ids=ids,
            instructor_count=len(combined_instr_ids),
            review_summary=review_summary,
            review_total=review_total,
            pending_count=pending_count,
            category=primary.category or "",
            classification=primary.classification or "",
            faculty=primary.faculty or "",
            term_type=primary.term_type or "",
            credits=float(primary.credits) if primary.credits is not None else 0,
        )

    parent_subgroups: dict = defaultdict(lambda: defaultdict(list))
    regular_grouped: dict = defaultdict(list)
    seen_labels_rendered: set = set()
    for c in courses:
        cls = c.classification or "（未分類）"
        label = label_by_name.get((c.name, c.classification or ""))
        if label:
            if label in seen_labels_rendered:
                continue
            seen_labels_rendered.add(label)
            row = group_rows_by_label[label]
        else:
            row = SimpleNamespace(type="single", course=c)
        if cls in child_cls_set:
            parent_subgroups[cls_parent_map[cls]][cls].append(row)
        elif cls in parent_names_set:
            parent_subgroups[cls]["（未分類）"].append(row)
        else:
            regular_grouped[cls].append(row)

    # parent_subgroups を並び順に整形
    cls_order_map = await cache.get_cls_order_map()
    _cls_sort = make_cls_sort(cls_order_map)
    parent_subgroups_sorted = {
        pg: sorted(sub.items(), key=lambda x: _cls_sort(x[0]))
        for pg, sub in sorted(parent_subgroups.items())
    }

    groups_data = (
        json.dumps({
            row.key: {
                "label": row.label,
                "ids": row.ids,
                "classification": row.classification,
                "category": row.category,
                "faculty": row.faculty,
                "term_type": row.term_type,
                "credits": row.credits,
            }
            for row in group_rows_by_label.values()
        }, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )

    return templates.TemplateResponse("admin/courses.html", {
        "request": request,
        "courses": courses,
        "grouped_courses": list(regular_grouped.items()),
        "parent_subgroups": parent_subgroups_sorted,
        "cls_parent_map": cls_parent_map,
        "active_category": category,
        "class_counts": class_counts,
        "courses_data": courses_data,
        "groups_data": groups_data,
        "review_summary_by_course": review_summary_by_course,
        "review_total_by_course": review_total_by_course,
        "pending_count_by_course": pending_count_by_course,
        "instructor_count_by_course": instructor_count_by_course,
        "all_instructors": all_instructors,
        "all_faculties": all_faculties,
        "error": msg,
        "total": total,
        "q": q,
        "page": page,
        "total_pages": total_pages,
        "url_prefix": "/admin/courses?page=",
        "has_undo": undo.has_last_deleted(),
    })


@router.get("/admin/courses/panel/instructors")
async def admin_courses_panel_instructors(
    ids: str = Query(...), editable: int = Query(0), _: str = Depends(check_admin),
):
    id_list = _parse_group_ids(ids)
    if not id_list:
        return JSONResponse({"ok": True, "html": ""})

    async with AsyncSessionLocal() as session:
        cs_instr_rows = (await session.execute(
            select(CourseSection, Instructor)
            .join(Instructor, Instructor.id == CourseSection.instructor_id)
            .where(CourseSection.subject_id.in_(id_list))
        )).all()
        subjects = (await session.execute(
            select(Subject).where(Subject.id.in_(id_list))
        )).scalars().all()
        subj_by_id = {c.id: c for c in subjects}
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

    instructors = []
    seen_inst_ids: set = set()
    for cs, inst in sorted(cs_instr_rows, key=lambda x: (x[1].sort_order, x[1].name)):
        if inst.id in seen_inst_ids:
            continue
        seen_inst_ids.add(inst.id)
        instructors.append(SimpleNamespace(id=inst.id, name=inst.name, url=cs_url_map.get(cs.id, "")))

    html = templates.env.get_template("admin/_instructor_chips.html").render(
        instructors=instructors,
        editable=bool(editable),
        course_id=id_list[0] if len(id_list) == 1 else None,
    )
    return JSONResponse({"ok": True, "html": html})


@router.get("/admin/courses/panel/reviews")
async def admin_courses_panel_reviews(ids: str = Query(...), _: str = Depends(check_admin)):
    id_list = _parse_group_ids(ids)
    reviews = []
    if id_list:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(Review, CourseSection.subject_id, Subject.name.label("subj_name"))
                .join(CourseSection, CourseSection.id == Review.course_section_id)
                .join(Subject, Subject.id == CourseSection.subject_id)
                .where(CourseSection.subject_id.in_(id_list))
                .order_by(
                    case((Review.status == ReviewStatus.PENDING, 0), (Review.status == ReviewStatus.APPROVED, 1), else_=2),
                    Review.created_at.desc(),
                )
            )).all()
        for rev, subj_id, subj_name in rows:
            reviews.append(SimpleNamespace(
                id=rev.id, course_name=subj_name, comment=rev.content, content=rev.content,
                rating=rev.rating, ease_rating=rev.ease_rating, grading_method=rev.grading_method,
                status=rev.status, selected_instructor=rev.selected_instructor, created_at=rev.created_at,
                submitter_name=rev.submitter_name, nickname=rev.nickname, academic_year=rev.academic_year,
                student_id=rev.student_id,
            ))

    html = templates.env.get_template("admin/_review_table.html").render(
        reviews=reviews, show_course_name=len(id_list) != 1,
    )
    return JSONResponse({"ok": True, "html": html})


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


async def _find_duplicate_subject(session, course_id: int, name: str, faculty: str, department: str) -> Subject | None:
    """(name, faculty, department)が完全に一致する他のSubjectを探す。classification違いのみの
    「全く同じ科目名」を検出するための判定（2026-09-05、UNIQUE制約からclassificationを
    除いた3列で一致する行がこれに当たる）。"""
    return (await session.execute(
        select(Subject).where(
            Subject.id != course_id,
            Subject.name == name,
            Subject.faculty == faculty,
            Subject.department == department,
        )
    )).scalars().first()


async def _copy_approved_reviews(session, source: Subject, target: Subject) -> None:
    """全く同じ科目名を別分類にも登録する際、確認の上で既存科目(source)の承認済みレビューを
    target側にも複製して見せる。買取（支払い）対象からは常に除外するため
    payment_request_id/credit_granted_atは付与せず、copied_from_review_idにコピー元を記録する
    （routers/payment_api.pyの買取対象クエリはこの列で除外している）。"""
    source_sections = (await session.execute(
        select(CourseSection).where(CourseSection.subject_id == source.id)
    )).scalars().all()
    if not source_sections:
        return
    target_sections = (await session.execute(
        select(CourseSection).where(CourseSection.subject_id == target.id)
    )).scalars().all()
    target_section_by_instructor = {cs.instructor_id: cs for cs in target_sections}

    # 同じ操作が繰り返されても同じレビューを何度も複製しないためのガード
    already_copied_ids: set[int] = set()
    if target_sections:
        already_copied_ids = set((await session.execute(
            select(Review.copied_from_review_id).where(
                Review.course_section_id.in_([cs.id for cs in target_sections]),
                Review.copied_from_review_id.isnot(None),
            )
        )).scalars().all())

    for src_section in source_sections:
        target_section = target_section_by_instructor.get(src_section.instructor_id)
        if target_section is None:
            target_section = CourseSection(subject_id=target.id, instructor_id=src_section.instructor_id)
            session.add(target_section)
            await session.flush()
            target_section_by_instructor[src_section.instructor_id] = target_section

        src_reviews = (await session.execute(
            select(Review).where(
                Review.course_section_id == src_section.id,
                Review.status == ReviewStatus.APPROVED,
            )
        )).scalars().all()
        for r in src_reviews:
            if r.id in already_copied_ids:
                continue
            session.add(Review(
                course_section_id=target_section.id,
                content=r.content,
                rating=r.rating,
                ease_rating=r.ease_rating,
                grading_method=r.grading_method,
                submitter_name=r.submitter_name,
                nickname=r.nickname,
                student_id=r.student_id,
                academic_year=r.academic_year,
                selected_instructor=r.selected_instructor,
                status=ReviewStatus.APPROVED,
                copied_from_review_id=r.id,
            ))
    await session.commit()


@router.post("/admin/courses/update/{course_id}")
async def admin_courses_update(
    course_id: int,
    request: Request,
    _: str = Depends(check_admin),
    name: str = Form(...),
    classification: str = Form(""),
    category: str = Form("専門"),
    term_type: str = Form(""),
    credits: float = Form(0),
    faculty: str = Form(""),
    department: str = Form(""),
    force_duplicate: str = Form(""),
):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    async with AsyncSessionLocal() as session:
        course = (await session.execute(select(Subject).where(Subject.id == course_id))).scalar_one_or_none()
        if course:
            # normalize_subject_name()はSubject.nameのvalidatorと同じ正規化（ローマ数字の半角→全角）。
            # 重複判定はDB保存後の正規化済み値どうしで比較する必要があるため事前に揃えておく
            new_name = normalize_subject_name(name.strip())
            # 修正理由: department列はnullable=False+空文字プレースホルダ方式なのに対し、
            # facultyだけ空欄保存でNULLになる非対称な状態だった。UNIQUE制約はNULL同士を
            # 区別しないため空文字に揃えておく(将来faculty列をNOT NULL化する際の前提)
            new_faculty = faculty.strip()
            new_department = department.strip()

            duplicate = await _find_duplicate_subject(session, course_id, new_name, new_faculty, new_department)
            if duplicate is not None and not force_duplicate:
                # 2026-09-05: 全く同じ科目名（学部・学科も同一）を別分類に登録しようとした場合、
                # 誤操作の可能性があるため確認なしでは保存させない。フロントはこのエラーを
                # 検知して確認ダイアログを出し、「はい」ならforce_duplicate付きで再送する
                message = (
                    f"同じ科目名が既に「{duplicate.classification or '未分類'}」にあります"
                    f"（学部：{duplicate.faculty or '未設定'}、学科：{duplicate.department or '未設定'}）。"
                    "このまま別の分類として保存しますか？"
                    "（保存すると、既存科目の承認済みレビューがこの科目にもコピーされます）"
                )
                if is_ajax:
                    return JSONResponse({"ok": False, "error": "duplicate_name", "message": message})
                return RedirectResponse(url="/admin/courses?msg=duplicate_name", status_code=303)

            course.name = new_name
            course.classification = classification.strip() or None
            course.category = category
            course.reading = reading(new_name)
            course.term_type = term_type.strip() or None
            course.credits = credits if credits else None
            course.faculty = new_faculty
            course.department = new_department
            await session.commit()

            if duplicate is not None and force_duplicate:
                await _copy_approved_reviews(session, source=duplicate, target=course)
    cache.invalidate_courses_cache()
    cache.invalidate_cls_caches()
    if is_ajax:
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/admin/courses", status_code=303)


async def _snapshot_subject(session, course: Subject) -> dict:
    """「元に戻す」用に、削除直前の科目1件分の状態（本体+セクション+シラバス）を保存する。
    reviews/subject_unlocksは復元対象外（reviews紐づき科目はそもそも削除がブロックされるため
    喪失しない。subject_unlocksの解除記録が失われるのは許容する）。"""
    sections = (await session.execute(
        select(CourseSection).where(CourseSection.subject_id == course.id)
    )).scalars().all()
    section_snapshots = []
    for sec in sections:
        syllabi = (await session.execute(
            select(Syllabus).where(Syllabus.course_section_id == sec.id)
        )).scalars().all()
        section_snapshots.append({
            "instructor_id": sec.instructor_id,
            "syllabi": [
                {"year": s.year, "academic_term": s.academic_term, "timetable_code": s.timetable_code}
                for s in syllabi
            ],
        })
    return {
        "subject": {
            "name": course.name,
            "reading": course.reading,
            "faculty": course.faculty,
            "department": course.department,
            "classification": course.classification,
            "category": course.category,
            "sort_order": course.sort_order,
            "term_type": course.term_type,
            "credits": course.credits,
        },
        "sections": section_snapshots,
    }


@router.post("/admin/courses/delete/{course_id}")
async def admin_courses_delete(course_id: int, request: Request, _: str = Depends(check_admin)):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
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
                    if is_ajax:
                        return JSONResponse({"ok": False, "error": "has_reviews"})
                    return RedirectResponse(url="/admin/courses?msg=has_reviews", status_code=303)
            snapshot = await _snapshot_subject(session, course)
            await session.delete(course)
            await session.commit()
            undo.set_last_deleted([snapshot])
    cache.invalidate_courses_cache()
    cache.invalidate_cls_caches()
    if is_ajax:
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/admin/courses", status_code=303)


@router.post("/admin/courses/undo")
async def admin_courses_undo(_: str = Depends(check_admin)):
    """直前に削除した科目（単独/統合表示の一括削除どちらも）を1回だけ元に戻す。"""
    snapshots = undo.pop_last_deleted()
    if not snapshots:
        return JSONResponse({"ok": False, "error": "nothing_to_undo"})
    async with AsyncSessionLocal() as session:
        for snap in snapshots:
            new_course = Subject(**snap["subject"])
            session.add(new_course)
            await session.flush()
            for sec_snap in snap["sections"]:
                instructor_exists = (await session.execute(
                    select(Instructor.id).where(Instructor.id == sec_snap["instructor_id"])
                )).scalar_one_or_none()
                if instructor_exists is None:
                    continue
                new_section = CourseSection(subject_id=new_course.id, instructor_id=sec_snap["instructor_id"])
                session.add(new_section)
                await session.flush()
                for syl in sec_snap["syllabi"]:
                    session.add(Syllabus(
                        course_section_id=new_section.id,
                        year=syl["year"],
                        academic_term=syl["academic_term"],
                        timetable_code=syl["timetable_code"],
                    ))
        await session.commit()
    cache.invalidate_courses_cache()
    cache.invalidate_cls_caches()
    return JSONResponse({"ok": True})


def _parse_group_ids(ids: str) -> list[int]:
    return [int(x) for x in ids.split(",") if x.strip().isdigit()]


@router.post("/admin/courses/group/update")
async def admin_courses_group_update(
    request: Request,
    _: str = Depends(check_admin),
    ids: str = Form(...),
    classification: str = Form(""),
    category: str = Form("専門"),
    term_type: str = Form(""),
    credits: float = Form(0),
    faculty: str = Form(""),
):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    # 統合表示（生物学各論A1/A2/C1/C2等）の編集モーダルはグループ内の全科目に同じ内容を
    # 一括適用する（科目名はバリアントごとに異なるためここでは変更しない）
    id_list = _parse_group_ids(ids)
    async with AsyncSessionLocal() as session:
        member_courses = (await session.execute(
            select(Subject).where(Subject.id.in_(id_list))
        )).scalars().all()
        for course in member_courses:
            course.classification = classification.strip() or None
            course.category = category
            course.term_type = term_type.strip() or None
            course.credits = credits if credits else None
            course.faculty = faculty.strip()
        await session.commit()
    cache.invalidate_courses_cache()
    cache.invalidate_cls_caches()
    if is_ajax:
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/admin/courses", status_code=303)


@router.post("/admin/courses/group/delete")
async def admin_courses_group_delete(request: Request, _: str = Depends(check_admin), ids: str = Form(...)):
    # 統合表示行の削除はグループ内の全科目を一括削除する。いずれか1件でもレビューが
    # 紐づいていれば（単独削除と同じ保護ルールで）全体をブロックする
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    id_list = _parse_group_ids(ids)
    async with AsyncSessionLocal() as session:
        cs_ids = (await session.execute(
            select(CourseSection.id).where(CourseSection.subject_id.in_(id_list))
        )).scalars().all()
        if cs_ids:
            has_reviews = (await session.execute(
                select(func.count(Review.id)).where(Review.course_section_id.in_(cs_ids))
            )).scalar()
            if has_reviews:
                if is_ajax:
                    return JSONResponse({"ok": False, "error": "has_reviews"})
                return RedirectResponse(url="/admin/courses?msg=has_reviews", status_code=303)
        member_courses = (await session.execute(
            select(Subject).where(Subject.id.in_(id_list))
        )).scalars().all()
        snapshots = [await _snapshot_subject(session, course) for course in member_courses]
        for course in member_courses:
            await session.delete(course)
        await session.commit()
        undo.set_last_deleted(snapshots)
    cache.invalidate_courses_cache()
    cache.invalidate_cls_caches()
    if is_ajax:
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/admin/courses", status_code=303)
