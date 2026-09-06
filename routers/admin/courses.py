import json
from collections import defaultdict
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import case, func, or_, select

from core import cache, undo
from core.config import (
    escape_like,
    make_cls_sort,
    make_syllabus_url,
    normalize_subject_name,
    reading,
    subject_sort_reading_key,
    syllabus_department_key,
)
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
    _excluded_names = frozenset(c.name for c in all_courses_for_variant if c.variant_merge_excluded)
    label_by_name = compute_variant_display_groups(
        [(c.name, c.classification or "") for c in all_courses_for_variant
         if (c.classification or "") not in CLASSIFICATION_MERGE_EXCLUDED],
        extra_excluded_names=_excluded_names,
    )
    # 修正理由: labelはベース名+接尾辞の文字列（例:"水理学 (Ⅰ/Ⅱ)"）でしかなく、別のclassification
    # （別学部）に同名バリアントが偶然存在すると同じ文字列になりうる。label_by_name自体は
    # (科目名, classification)ペアで正しく別グループに分けているのに、ここをlabel文字列だけで
    # キーイングすると別classificationの科目同士が1グループに混ざってしまう
    # （2026-09-06発覚：工学部市民工学科「水理学Ⅰ/Ⅱ」と農学部食料環境システム学科「水理学Ⅰ/Ⅱ」が
    # 同じラベルに統合され、分類ソート順が早い農学部側の見出しの下に工学部側まで表示されるバグ）。
    # キーを(label, classification)のペアにして分類ごとに独立したグループにする。
    members_by_label: dict[tuple[str, str], list] = defaultdict(list)
    for c in all_courses_for_variant:
        label = label_by_name.get((c.name, c.classification or ""))
        if label:
            members_by_label[(label, c.classification or "")].append(c)

    # 「統合解除」ボタン（subjects.variant_merge_excluded、2026-09-06）で個別除外された科目に
    # 「元に戻す」ボタンを出すため、除外を一切適用しなかった場合に本来どのグループへ統合される
    # はずだったかを別途計算しておく（NUM_MERGE_EXCLUDED_NAMES等のコード側の恒常除外は
    # 対象外のまま＝そちらは「元に戻す」ボタンを出さない）
    potential_label_by_name = compute_variant_display_groups(
        [(c.name, c.classification or "") for c in all_courses_for_variant
         if (c.classification or "") not in CLASSIFICATION_MERGE_EXCLUDED],
    )
    potential_members_by_label: dict[tuple[str, str], list] = defaultdict(list)
    for c in all_courses_for_variant:
        label = potential_label_by_name.get((c.name, c.classification or ""))
        if label:
            potential_members_by_label[(label, c.classification or "")].append(c)

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
            _cls_sort(c.classification or ""), c.sort_order, subject_sort_reading_key(c)
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
        seen_labels_for_query: set[tuple[str, str]] = set()
        extra_ids: set[int] = set()
        for c in courses:
            label = label_by_name.get((c.name, c.classification or ""))
            if label:
                label_key = (label, c.classification or "")
                if label_key not in seen_labels_for_query:
                    seen_labels_for_query.add(label_key)
                    extra_ids.update(m.id for m in members_by_label.get(label_key, []))
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
        if not label:
            continue
        label_key = (label, c.classification or "")
        if label_key in group_rows_by_label:
            continue
        members = members_by_label.get(label_key, [c])
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
        group_rows_by_label[label_key] = SimpleNamespace(
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
    seen_labels_rendered: set[tuple[str, str]] = set()
    for c in courses:
        cls = c.classification or "（未分類）"
        label = label_by_name.get((c.name, c.classification or ""))
        if label:
            label_key = (label, c.classification or "")
            if label_key in seen_labels_rendered:
                continue
            seen_labels_rendered.add(label_key)
            row = group_rows_by_label[label_key]
        else:
            can_remerge = False
            remerge_ids: list[int] = []
            if c.variant_merge_excluded:
                potential_label = potential_label_by_name.get((c.name, c.classification or ""))
                if potential_label:
                    potential_key = (potential_label, c.classification or "")
                    remerge_ids = [m.id for m in potential_members_by_label.get(potential_key, [])]
                    can_remerge = bool(remerge_ids)
            row = SimpleNamespace(type="single", course=c, can_remerge=can_remerge, remerge_ids=remerge_ids)
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


async def _determine_new_subject_sort_order(session, classification: str | None) -> int:
    """新規科目のsort_orderを、その分類の既存の並び順の流儀に合わせて決める。
    科目一覧は分類内で(sort_order, よみがな)順に並ぶため、ほとんどの分類（sort_order未着手＝
    全科目0のまま）では新規科目もsort_order=0にすればよみがな順の適切な位置に自動的に
    収まる。一方、上へ/下へボタンで全科目を個別に並び替え済みの分類（例：
    工学部電気電子工学科専門科目、0〜46の完全な連番）では、0で追加すると先頭付近に
    割り込んでしまうため末尾に追加する。判定は「sort_order=0の科目が2件以上あるか」で行う
    （2件以上なら「まだ手を付けていない科目の集団」とみなしよみがな順に委ねる。
    0件・1件なら個別に並び替え済みとみなし末尾に追加する。2026-09-06）。"""
    cls_filter = Subject.classification.is_(None) if classification is None else Subject.classification == classification
    sort_orders = (await session.execute(
        select(Subject.sort_order).where(cls_filter)
    )).scalars().all()
    if not sort_orders:
        return 0
    if sum(1 for so in sort_orders if so == 0) >= 2:
        return 0
    return max(sort_orders) + 1


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


@router.post("/admin/courses/create")
async def admin_courses_create(
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
    """管理画面から科目を新規作成する（2026-09-06追加。従来は編集・削除のみでシラバス
    インポート経由でしか科目を追加できなかった）。重複チェック・レビューコピーの挙動は
    admin_courses_update()の別分類への変更時と同じロジックを流用する。"""
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    new_name = normalize_subject_name(name.strip())
    new_faculty = faculty.strip()
    new_department = department.strip()
    new_classification = classification.strip() or None

    async with AsyncSessionLocal() as session:
        duplicate = await _find_duplicate_subject(session, 0, new_name, new_faculty, new_department)
        if duplicate is not None and not force_duplicate:
            message = (
                f"同じ科目名が既に「{duplicate.classification or '未分類'}」にあります"
                f"（学部：{duplicate.faculty or '未設定'}、学科：{duplicate.department or '未設定'}）。"
                "このまま別の分類として登録しますか？"
                "（登録すると、既存科目の承認済みレビューがこの科目にもコピーされます）"
            )
            if is_ajax:
                return JSONResponse({"ok": False, "error": "duplicate_name", "message": message})
            return RedirectResponse(url="/admin/courses?msg=duplicate_name", status_code=303)

        course = Subject(
            name=new_name,
            classification=new_classification,
            category=category,
            reading=reading(new_name),
            term_type=term_type.strip() or None,
            credits=credits if credits else None,
            faculty=new_faculty,
            department=new_department,
            sort_order=await _determine_new_subject_sort_order(session, new_classification),
        )
        session.add(course)
        await session.commit()

        if duplicate is not None and force_duplicate:
            await _copy_approved_reviews(session, source=duplicate, target=course)

    cache.invalidate_courses_cache()
    cache.invalidate_cls_caches()
    if is_ajax:
        return JSONResponse({"ok": True, "id": course.id})
    return RedirectResponse(url="/admin/courses", status_code=303)


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
            new_classification = classification.strip() or None

            # 2026-09-06: 名前・学部・学科・分類のいずれも変わらない編集（単位数だけの修正等）では
            # 重複チェック自体を行わない。分類またぎの同名科目は一度確認すれば恒久的に共存する設計
            # なので、これが無いと既に確認済みの組み合わせについて無関係な編集のたびに
            # 「同じ科目名が既にあります」の確認ダイアログが毎回再発火してしまう
            identity_changed = (
                new_name != course.name
                or new_faculty != (course.faculty or "")
                or new_department != (course.department or "")
                or new_classification != course.classification
            )

            duplicate = None
            if identity_changed:
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
            course.classification = new_classification
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


@router.post("/admin/courses/group/unmerge")
async def admin_courses_group_unmerge(request: Request, _: str = Depends(check_admin), ids: str = Form(...)):
    """統合表示（生物学各論A1/A2/C1/C2等）の「統合解除」ボタン。指定した科目群を
    subjects.variant_merge_excluded=trueにし、末尾バリアント統合（LINE bot科目一覧・
    管理画面科目一覧の表示のみ）の対象から外す。DB上のSubject行自体は変更しない。
    従来はcore/subject_variants.pyのNUM_MERGE_EXCLUDED_NAMESにコードでハードコードして
    いたが、都度コード変更・デプロイが要るため管理者がボタンで切り替えられるようにした
    （2026-09-06）。"""
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    id_list = _parse_group_ids(ids)
    async with AsyncSessionLocal() as session:
        member_courses = (await session.execute(
            select(Subject).where(Subject.id.in_(id_list))
        )).scalars().all()
        for course in member_courses:
            course.variant_merge_excluded = True
        await session.commit()
    cache.invalidate_courses_cache()
    if is_ajax:
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/admin/courses", status_code=303)


@router.post("/admin/courses/group/remerge")
async def admin_courses_group_remerge(request: Request, _: str = Depends(check_admin), ids: str = Form(...)):
    """admin_courses_group_unmerge()で解除した統合を元に戻す「元に戻す」ボタン。
    指定した科目群のsubjects.variant_merge_excludedをfalseに戻す。"""
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    id_list = _parse_group_ids(ids)
    async with AsyncSessionLocal() as session:
        member_courses = (await session.execute(
            select(Subject).where(Subject.id.in_(id_list))
        )).scalars().all()
        for course in member_courses:
            course.variant_merge_excluded = False
        await session.commit()
    cache.invalidate_courses_cache()
    if is_ajax:
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/admin/courses", status_code=303)


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
    force_duplicate: str = Form(""),
):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    # 統合表示（生物学各論A1/A2/C1/C2等）の編集モーダルはグループ内の全科目に同じ内容を
    # 一括適用する（科目名はバリアントごとに異なるためここでは変更しない）
    id_list = _parse_group_ids(ids)
    new_faculty = faculty.strip()
    new_classification = classification.strip() or None
    async with AsyncSessionLocal() as session:
        member_courses = (await session.execute(
            select(Subject).where(Subject.id.in_(id_list))
        )).scalars().all()

        # 2026-09-06: 単独科目編集(admin_courses_update)と同じ理由で、学部または分類が変わる
        # メンバーについては分類またぎの同名科目衝突をUNIQUE制約違反(通信エラー)にせず確認を挟む。
        # 名前・学科はこのエンドポイントでは変更しないため、それらが不変なメンバーは対象外
        duplicates: list[tuple[Subject, Subject]] = []
        for course in member_courses:
            identity_changed = (
                new_faculty != (course.faculty or "") or new_classification != course.classification
            )
            if not identity_changed:
                continue
            dup = await _find_duplicate_subject(session, course.id, course.name, new_faculty, course.department or "")
            if dup is not None:
                duplicates.append((course, dup))

        if duplicates and not force_duplicate:
            names = "、".join(f"「{c.name}」" for c, _ in duplicates[:5])
            message = (
                f"{names} は既に同じ科目名が別の分類にあります。"
                "このまま別の分類として保存しますか？"
                "（保存すると、既存科目の承認済みレビューがこの科目にもコピーされます）"
            )
            if is_ajax:
                return JSONResponse({"ok": False, "error": "duplicate_name", "message": message})
            return RedirectResponse(url="/admin/courses?msg=duplicate_name", status_code=303)

        for course in member_courses:
            course.classification = new_classification
            course.category = category
            course.term_type = term_type.strip() or None
            course.credits = credits if credits else None
            course.faculty = new_faculty
        await session.commit()

        if duplicates and force_duplicate:
            for course, dup in duplicates:
                await _copy_approved_reviews(session, source=dup, target=course)
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
