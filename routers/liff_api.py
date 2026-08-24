import asyncio
import re as _re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core import cache
from core.activity_log import save_error_log
from core.config import (
    EASE_ORDER,
    escape_like, make_syllabus_url, syllabus_department_key,
)
from core.liff_auth import verify_liff_id_token
from core.rate_limit import rate_limiter
from core.subject_variants import compute_variant_groups
from database import AsyncSessionLocal
from models import (
    CourseSection, CourseSectionView, Instructor, Review, ReviewStatus,
    Subject, SubjectUnlock, Syllabus, UserProfile,
)

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
# レビュー閲覧権の解除（チケット消費）はDB書き込みを伴うため、他の書き込み系と同水準に制限する
_unlock_rate_limit = rate_limiter(max_requests=10, window_seconds=60)


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
        # 語尾の数字・アルファベットのみが異なる科目（例: 生物学各論A1/A2/C1/C2）は
        # レビュー投稿フォームの科目検索でも1件にまとめて選べるようにする（LINE bot科目一覧と同じ統合規則）
        variant_map = compute_variant_groups([(c.name, c.faculty or "", c.department or "") for c in courses])
        course_list = [
            {"id": c.id, "name": c.name, "reading": c.reading or "",
             "variantGroup": variant_map.get(c.name, ""),
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
    # 修正理由: レスポンスに「full」（募集締切、投稿の都度変わりうる）を含めるようになったため、
    # ブラウザキャッシュを許可すると締切直後のページ遷移でも古い（締切前の）結果が
    # 再利用され続けてしまう。同一ページロード内では_preload変数に保持し1回しか呼ばないため、
    # キャッシュを無効化しても呼び出し頻度は増えない。
    res.headers["Cache-Control"] = "no-store"
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


async def _group_subject_ids(subject: Subject) -> tuple[str, list[int], list[str]]:
    """科目がレビュー投稿フォームと同じ語尾バリアントグループ（例: 生物学各論A1/A2/C1/C2）
    に属する場合、グループラベル・グループ内の全subject_id・全科目名を返す。
    属さない場合はラベル""・[subject.id]のみを返す（レビュー閲覧では単独科目として扱う）。"""
    _, all_courses = await cache.get_courses_cached()
    variant_map = compute_variant_groups([(c.name, c.faculty or "", c.department or "") for c in all_courses])
    label = variant_map.get(subject.name, "")
    if not label:
        return "", [subject.id], [subject.name]
    # 修正理由: compute_variant_groups()は「ベース名」というラベル文字列しか返さないため、
    # 別学部の科目が偶然同じベース名で数字バリアントグループを持つ場合（例: 工学部「制御工学Ⅰ/Ⅱ」と
    # システム情報学部「制御工学1/2」、どちらも表示ラベルは「制御工学」）、ラベル文字列だけで
    # membersを再構築すると学部をまたいで誤統合してしまう。compute_variant_groups()自体は
    # faculty+department単位でグループ化しているため、ここでも対象subjectと同じfaculty/department
    # の科目だけに絞り込んで正しいグループを再現する。
    members = [
        c for c in all_courses
        if variant_map.get(c.name) == label
        and (c.faculty or "") == (subject.faculty or "")
        and (c.department or "") == (subject.department or "")
    ]
    return label, [c.id for c in members], [c.name for c in members]


@router.get("/api/course/{course_id}")
async def api_course(course_id: int, request: Request, id_token: str = ""):
    try:
        uid = await verify_liff_id_token(id_token, request) if id_token else None
        # 修正理由: subject取得とcs_instr取得は元々別々のAsyncSessionLocal()を開いており、
        # どちらもcourse_id確定後に順番に実行するだけの依存関係なので、DB接続の往復を
        # 1回分減らすため同じセッションにまとめる。
        async with AsyncSessionLocal() as session:
            subject = await session.get(Subject, course_id)
            if not subject:
                raise HTTPException(status_code=404, detail="course not found")
            # 語尾バリアントグループに属する科目は、レビュー閲覧も1つの科目として扱い、
            # グループ内の全科目のレビュー・評価をまとめて表示する（レビュー投稿フォームの
            # 科目検索での統合表示と対にするため）
            group_label, group_subject_ids, group_names = await _group_subject_ids(subject)
            # 修正理由: ORDER BY未指定だとPostgreSQLは行順を保証せず、これに依存する
            # 閲覧数記録先(main_cs_id)・表示するシラバスURL・教員名の表示順がリクエスト
            # ごとに変わりうる非決定的な挙動になっていた。id順で固定する。
            cs_instr_rows = (await session.execute(
                select(CourseSection, Instructor)
                .join(Instructor, Instructor.id == CourseSection.instructor_id)
                .where(CourseSection.subject_id.in_(group_subject_ids))
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
        # 修正理由: バリアントグループでcs_idsはグループ全体にまたがるため、閲覧数は
        # 実際にリクエストされた科目自身のcourse_sectionに記録する（無ければグループ内の
        # 代表にフォールバック）。
        own_cs_ids = [cs.id for cs, _ in cs_instr_rows if cs.subject_id == course_id]
        if cs_ids:
            main_cs_id = own_cs_ids[0] if own_cs_ids else cs_ids[0]
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
        # バリアントグループでは同じ教員が複数の変種を担当している場合があるため重複除去する
        _seen_instr: set[str] = set()
        instr_names: list[str] = []
        for _, instr in cs_instr_rows:
            if instr.name not in _seen_instr:
                _seen_instr.add(instr.name)
                instr_names.append(instr.name)
        instructor_str = "・".join(instr_names)
        top_ease = None
        if ease_rows:
            top_ease = sorted(ease_rows, key=lambda r: (-r[1], EASE_ORDER.get(r[0], 99)))[0][0]

        # レビュー閲覧権（デフォルトでは他人のレビューは見られず、承認されたレビュー1件につき
        # 任意の科目3件分の閲覧権が付与される。閲覧権はsubject単位・バリアントグループ内で共有）
        review_count = sum(cnt for _, cnt in ease_rows)
        unlock_credits = None
        unlocked = review_count == 0
        if not unlocked and uid:
            async with AsyncSessionLocal() as s:
                profile = await s.get(UserProfile, uid)
                unlock_credits = profile.unlock_credits if profile else 0
                unlocked = (await s.execute(
                    select(SubjectUnlock.subject_id).where(
                        SubjectUnlock.line_user_id == uid,
                        SubjectUnlock.subject_id.in_(group_subject_ids),
                    )
                )).scalars().first() is not None
        locked = not unlocked

        return {
            "id": subject.id,
            "name": subject.name,
            "group_label": group_label,
            "group_variant_names": group_names if group_label else [],
            "instructor": instructor_str,
            "classification": subject.classification or "",
            "category": subject.category or "",
            "term_type": subject.term_type or "",
            "credits": float(subject.credits) if subject.credits else 0,
            "syllabus_url": syllabus_url or "",
            "review_count": review_count,
            "locked": locked,
            "unlock_credits": unlock_credits,
            "avg_rating": avg_rating if not locked else None,
            "top_ease": top_ease if not locked else None,
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
            ] if not locked else [],
        }
    except HTTPException:
        raise
    except Exception as exc:
        await save_error_log(exc, action=f"api_course/{course_id}")
        raise


@router.post("/api/course/{course_id}/unlock")
async def unlock_course(course_id: int, request: Request, _rl=Depends(_unlock_rate_limit)):
    """レビュー閲覧権チケットを1枚消費し、指定科目（バリアントグループがあればグループ全体）の
    レビューを閲覧可能にする。"""
    body = await request.json()
    uid = await verify_liff_id_token((body.get("id_token") or "").strip(), request)
    if not uid:
        raise HTTPException(status_code=401, detail="LINEログインの確認に失敗しました")

    async with AsyncSessionLocal() as session:
        subject = await session.get(Subject, course_id)
        if not subject:
            raise HTTPException(status_code=404, detail="course not found")
        profile = await session.get(UserProfile, uid)
        if not profile:
            raise HTTPException(status_code=403, detail="プロフィール未登録です")

        _, group_subject_ids, _ = await _group_subject_ids(subject)

        already = (await session.execute(
            select(SubjectUnlock.subject_id).where(
                SubjectUnlock.line_user_id == uid,
                SubjectUnlock.subject_id.in_(group_subject_ids),
            )
        )).scalars().first() is not None
        if already:
            return {"ok": True, "already": True, "unlock_credits": profile.unlock_credits}

        # UPDATE ... WHERE unlock_credits > 0 の原子性でチケット不足の二重解除を防ぐ
        new_balance = (await session.execute(
            update(UserProfile)
            .where(UserProfile.line_user_id == uid, UserProfile.unlock_credits > 0)
            .values(unlock_credits=UserProfile.unlock_credits - 1)
            .returning(UserProfile.unlock_credits)
        )).scalar_one_or_none()
        if new_balance is None:
            await session.rollback()
            return {"ok": False, "reason": "insufficient_credits", "unlock_credits": profile.unlock_credits}

        for sid_ in group_subject_ids:
            await session.execute(
                pg_insert(SubjectUnlock).values(line_user_id=uid, subject_id=sid_)
                .on_conflict_do_nothing(index_elements=["line_user_id", "subject_id"])
            )
        await session.commit()
        return {"ok": True, "already": False, "unlock_credits": new_balance}
