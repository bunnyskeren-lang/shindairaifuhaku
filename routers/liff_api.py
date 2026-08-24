import asyncio
import re as _re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core import cache
from core.activity_log import save_error_log
from core.config import (
    EASE_ORDER,
    escape_like, make_syllabus_url, syllabus_department_key,
)
from core.rate_limit import rate_limiter
from database import AsyncSessionLocal
from models import CourseSection, CourseSectionView, Instructor, Review, ReviewStatus, Subject, Syllabus

router = APIRouter()

_FORM_PUNCT = '・･（）()'
# 修正理由: レビュー連投によるスパム・審査キュー圧迫を防ぐため、IPアドレス単位で1分あたり3回までに制限する
_submit_rate_limit = rate_limiter(max_requests=3, window_seconds=60)
# 修正理由: student_idの総当たりによる他人の氏名取得を防ぐため、IPアドレス単位で1分あたり10回までに制限する
_autofill_rate_limit = rate_limiter(max_requests=10, window_seconds=60)
# 修正理由: 未認証・無制限のILIKE全文検索が連打可能だった（/api/preloadの読み込み失敗時のフォールバック用途で
# 通常は高頻度に呼ばれないため、正規利用を妨げない範囲で1分あたり30回までに制限する）
_search_rate_limit = rate_limiter(max_requests=30, window_seconds=60)
# 修正理由: 検索結果に上限が無く、LIMIT無しの全件ILIKEクエリを無制限件数で返しうる状態だった
_SEARCH_RESULT_LIMIT = 50
# 修正理由: /submit等の他の書き込み系エンドポイントにはレート制限があるのに/api/registerだけ
# 無制限だった。id_token検証には120秒のキャッシュ(core/liff_auth.py)があり、有効なトークン1つで
# 検証をバイパスしてDB書き込みを連打できたため、同水準の制限を設ける
_register_rate_limit = rate_limiter(max_requests=5, window_seconds=60)


def _normalize_form_q(s: str) -> str:
    for ch in _FORM_PUNCT:
        s = s.replace(ch, '')
    return s


async def _latest_syllabus_urls(session, cs_ids: list) -> dict[int, str]:
    """course_section_idごとに最新年度のsyllabus_urlをtimetable_code/departmentから動的生成する。"""
    if not cs_ids:
        return {}
    rows = (await session.execute(
        select(Syllabus.course_section_id, Syllabus.timetable_code, Syllabus.year, Subject.faculty, Subject.department)
        .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
        .join(Subject, Subject.id == CourseSection.subject_id)
        .where(Syllabus.course_section_id.in_(cs_ids), Syllabus.timetable_code.isnot(None))
    )).all()
    latest_year: dict[int, int] = {}
    result: dict[int, str] = {}
    for cs_id, code, year, faculty, department in rows:
        if cs_id in latest_year and year <= latest_year[cs_id]:
            continue
        url = make_syllabus_url(code, f"{faculty or ''}{department or ''}")
        if not url:
            continue
        latest_year[cs_id] = year
        result[cs_id] = url
    return result


@router.get("/api/courses")
async def search_courses(q: str = "", _rl=Depends(_search_rate_limit)):
    async with AsyncSessionLocal() as session:
        if q.strip():
            tokens = [tok for tok in _re.split(r'[\s　]+', q.strip()) if tok]
            stmt = select(Subject)
            for tok in tokens:
                t = escape_like(tok)
                stmt = stmt.where(or_(
                    Subject.name.ilike(f"%{t}%", escape="\\"),
                    Subject.reading.ilike(f"%{t}%", escape="\\"),
                ))
            stmt = stmt.order_by(Subject.name).limit(_SEARCH_RESULT_LIMIT)
            courses = (await session.execute(stmt)).scalars().all()
            if not courses:
                norm_col = Subject.name
                for ch in ('・', '･', '（', '）', '(', ')'):
                    norm_col = func.replace(norm_col, ch, '')
                norm_tokens = [_normalize_form_q(tok) for tok in tokens]
                stmt2 = select(Subject)
                for tok in norm_tokens:
                    t = escape_like(tok)
                    stmt2 = stmt2.where(norm_col.ilike(f"%{t}%", escape="\\"))
                stmt2 = stmt2.order_by(Subject.name).limit(_SEARCH_RESULT_LIMIT)
                courses = (await session.execute(stmt2)).scalars().all()
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
        full_pairs = await cache.get_full_course_section_pairs_cached()
        insts_by_course: dict = {}
        for cs, inst in cs_rows:
            insts_by_course.setdefault(cs.subject_id, []).append({
                "name": inst.name,
                "url": cs_url_map.get(cs.id, ""),
                "full": (cs.subject_id, inst.name) in full_pairs,
            })
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
        inst_courses: dict[str, dict[int, str]] = {}
        for c in courses:
            for inst in insts_by_course.get(c.id, []):
                inst_courses.setdefault(inst.name, {})[c.id] = c.name
        course_list = [
            {"id": c.id, "name": c.name, "reading": c.reading or "",
             "instructors": [{"name": i.name} for i in insts_by_course.get(c.id, [])]}
            for c in courses
        ]
        instructor_list = [
            {"name": name, "courses": [{"id": cid, "name": cn} for cid, cn in courses_by_id.items()]}
            for name, courses_by_id in sorted(inst_courses.items())
        ]
        data = {"courses": course_list, "instructors": instructor_list}
        cache.set_preload_cache(data)

    # 「full」（募集締切）はレビュー投稿状況で頻繁に変わりうるため、
    # 構造データ本体（数千件規模でTTL 3600秒キャッシュ）とは切り離し、毎リクエスト時に付与する
    full_pairs = await cache.get_full_course_section_pairs_cached()
    if full_pairs:
        data = {
            "courses": [
                {**c, "instructors": [
                    {**i, "full": (c["id"], i["name"]) in full_pairs} for i in c["instructors"]
                ]}
                for c in data["courses"]
            ],
            "instructors": [
                {**inst, "courses": [
                    {**cn, "full": (cn["id"], inst["name"]) in full_pairs} for cn in inst["courses"]
                ]}
                for inst in data["instructors"]
            ],
        }
    res = JSONResponse(data)
    res.headers["Cache-Control"] = "public, max-age=60"
    return res


@router.get("/api/instructors")
async def search_instructors(q: str = "", _rl=Depends(_search_rate_limit)):
    if not q.strip():
        return {"instructors": []}
    async with AsyncSessionLocal() as session:
        q_clean = q.replace("　", " ").strip()
        escaped = escape_like(q_clean)
        insts_raw = (await session.execute(
            select(Instructor.name)
            .where(Instructor.name.ilike(f"%{escaped}%", escape="\\"))
            .distinct()
            .limit(_SEARCH_RESULT_LIMIT)
        )).scalars().all()
        insts = sorted(insts_raw, key=lambda n: (0 if n.lower().startswith(q_clean.lower()) else 1, n))
        if not insts:
            norm_col = Instructor.name
            for ch in ('・', '･', '（', '）', '(', ')'):
                norm_col = func.replace(norm_col, ch, '')
            escaped_norm = escape_like(_normalize_form_q(q_clean))
            insts_raw = (await session.execute(
                select(Instructor.name)
                .where(norm_col.ilike(f"%{escaped_norm}%", escape="\\"))
                .distinct()
                .limit(_SEARCH_RESULT_LIMIT)
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
            full_pairs = await cache.get_full_course_section_pairs_cached()
            courses_by_inst: dict[str, list] = {name: [] for name in insts}
            for inst_name, c_id, c_name in all_rows:
                if not any(x["id"] == c_id for x in courses_by_inst[inst_name]):
                    courses_by_inst[inst_name].append({"id": c_id, "name": c_name, "full": (c_id, inst_name) in full_pairs})
            for name in insts:
                result.append({"name": name, "courses": courses_by_inst[name]})

    return {"instructors": result}


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

        # 修正理由: agg(平均・件数)とease内訳は同じReviewテーブル・同じ絞り込み条件に対する集計で、
        # 別々のAsyncSessionLocal()×2本（＝DBコネクション2本）に分ける必要が無かった。
        # SUM(rating)/COUNT(rating)はSQLのNULL無視の挙動によりgroup byありでも全体平均に正しく
        # 再合成できるため、ease_rating別の内訳クエリ1本に統合しコネクション使用数を1本減らす
        # （/api/course/{id}は1リクエストあたり最大6本のDBコネクションを個別セッションで並行して
        # 掴んでおり、一斉アクセス時にDB接続プールを圧迫しやすい経路だったため）。
        async def _agg_and_ease(cs_ids: list):
            if not cs_ids:
                return None, []
            async with AsyncSessionLocal() as s:
                rows = (await s.execute(
                    select(
                        Review.ease_rating, func.count(Review.id),
                        func.sum(Review.rating), func.count(Review.rating),
                    )
                    .where(Review.course_section_id.in_(cs_ids), Review.status == ReviewStatus.APPROVED)
                    .group_by(Review.ease_rating)
                )).all()
            ease_rows = [(ease, cnt) for ease, cnt, _, _ in rows]
            rating_sum = sum((rsum or 0) for _, _, rsum, _ in rows)
            rating_count = sum(rcnt for _, _, _, rcnt in rows)
            avg_rating = (rating_sum / rating_count) if rating_count else None
            return avg_rating, ease_rows

        async def _reviews(cs_ids: list):
            if not cs_ids:
                return []
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(Review)
                    .where(Review.course_section_id.in_(cs_ids), Review.status == ReviewStatus.APPROVED)
                    .order_by(Review.selected_instructor.nulls_last(), Review.academic_year.desc())
                    .limit(20)
                )).scalars().all()

        async def _syllabus_code():
            async with AsyncSessionLocal() as s:
                return (await s.execute(
                    select(Syllabus.timetable_code)
                    .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
                    .where(CourseSection.subject_id == course_id, Syllabus.timetable_code.isnot(None))
                    .order_by(Syllabus.year.desc())
                    .limit(1)
                )).first()

        cs_ids = [cs.id for cs, _ in cs_instr_rows]

        (avg_rating, ease_rows), reviews_raw, sc_row = await asyncio.gather(
            _agg_and_ease(cs_ids), _reviews(cs_ids), _syllabus_code()
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

        # 最新年度のsyllabiからtimetable_codeを取得しシラバスURLを動的生成
        syllabus_url = make_syllabus_url(sc_row[0], syllabus_department_key(subject)) if sc_row else ""
        instructor_str = "・".join(instr.name for _, instr in cs_instr_rows)
        top_ease = None
        if ease_rows:
            top_ease = sorted(ease_rows, key=lambda r: (-r[1], EASE_ORDER.get(r[0], 99)))[0][0]

        return {
            "id": subject.id,
            "name": subject.name,
            "instructor": instructor_str,
            "classification": subject.classification or "",
            "category": subject.category or "",
            "term_type": subject.term_type or "",
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
