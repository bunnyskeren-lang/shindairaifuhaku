import re as _re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core import cache, moderation
from core.activity_log import save_error_log
from core.config import (
    BAN_MESSAGE_TEXT, EASE_ORDER, FACULTIES, KYOTSU_SENMON_KISO_FACULTY,
    MAX_REVIEWS_PER_COURSE_SECTION,
    ON_DEMAND_SAME_CONTENT_NOTE,
    REVIEW_SUBMISSION_CATEGORY, REVIEW_SUBMISSION_SENMON_CATEGORY, REVIEW_VIEW_CATEGORY,
    escape_like, latest_syllabus_url_map, make_syllabus_url, syllabus_department_key,
)
from core.grading_method import parse_grading_method
from core.liff_auth import verify_liff_id_token
from core.rate_limit import rate_limiter
from core.subject_variants import hoken_gakka_senko_label, is_hoken_gakka_senko, is_remote_tagged
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


def _clean_faculty(faculty: str) -> str:
    """クエリで渡された学部名を既知の11学部に限定する（不明な値はキャッシュ汚染防止のため空扱い）。"""
    faculty = (faculty or "").strip()
    return faculty if faculty in FACULTIES else ""


def _submission_category_clause(faculty: str):
    """レビュー投稿フォームの科目候補に含める条件。
    - 教養科目: 全員
    - 共通専門基礎科目（faculty="教養教育院" の専門科目）: 全員
    - その他の専門科目: 指定学部のぶんのみ（faculty が空なら含めない）
    学科の絞り込みは core.config.subject_submittable_for_profile() でクライアント側／/submit側が行う。"""
    clauses = [
        Subject.category == REVIEW_SUBMISSION_CATEGORY,
        and_(
            Subject.category == REVIEW_SUBMISSION_SENMON_CATEGORY,
            Subject.faculty == KYOTSU_SENMON_KISO_FACULTY,
        ),
    ]
    if faculty:
        clauses.append(and_(
            Subject.category == REVIEW_SUBMISSION_SENMON_CATEGORY,
            Subject.faculty == faculty,
        ))
    return or_(*clauses)


def _variant_group_fields(category, subject_id, name, variant_map, senmon_group):
    """レビュー投稿フォーム候補1件分の統合表示用フィールドを返す。
    - variantGroup: サフィックス抽出用の共通プレフィックス（ベース名）
    - variantGroupKey: フロントエンドがグループ化に使う一意キー
    - variantGroupLabel: そのまま表示する完全なグループラベル（専門科目のみ。空なら
      フロントがベース名＋サフィックスを組み立てる）
    - isRemote: 遠隔/対面クラスを別グループにするための補助フラグ

    専門科目（共通専門基礎含む）は管理画面の科目一覧と全く同じ統合
    （compute_variant_display_groups()）に揃える（2026-09-08、ユーザー指示）。
    教養科目は従来どおり compute_variant_groups()（variant_map）単位。"""
    if (category or "") == REVIEW_SUBMISSION_SENMON_CATEGORY:
        g = senmon_group.get(subject_id)
        if g:
            base, label, _ids = g
            return {"variantGroup": base, "variantGroupKey": f"L:{label}",
                    "variantGroupLabel": label, "isRemote": False}
        return {"variantGroup": "", "variantGroupKey": "",
                "variantGroupLabel": "", "isRemote": False}
    vg = variant_map.get(name, "")
    remote = is_remote_tagged(name)
    return {"variantGroup": vg,
            "variantGroupKey": (vg + (" remote" if remote else "")) if vg else "",
            "variantGroupLabel": "", "isRemote": remote}


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
    # 行は既に (course_section_id, code, year, faculty, department) の形なので共通ヘルパーへ直接渡す
    return latest_syllabus_url_map(rows)


@router.get("/api/courses")
async def search_courses(q: str = "", faculty: str = "", _rl=Depends(_search_rate_limit)):
    faculty = _clean_faculty(faculty)
    cat_clause = _submission_category_clause(faculty)
    async with AsyncSessionLocal() as session:
        if q.strip():
            q_stripped = q.strip()
            tokens = [tok for tok in _re.split(r'[\s　]+', q_stripped) if tok]
            q_full = escape_like(q_stripped)
            relevance = case(
                (Subject.name.ilike(f"{q_full}%", escape="\\"), 0),
                else_=1,
            )
            stmt = select(Subject).where(cat_clause)
            for tok in tokens:
                t = escape_like(tok)
                stmt = stmt.where(or_(
                    Subject.name.ilike(f"%{t}%", escape="\\"),
                    Subject.reading.ilike(f"%{t}%", escape="\\"),
                ))
            stmt = stmt.order_by(relevance, Subject.name).limit(_SEARCH_RESULT_LIMIT)
            courses = (await session.execute(stmt)).scalars().all()
            if not courses:
                norm_col = Subject.name
                for ch in ('・', '･', '（', '）', '(', ')'):
                    norm_col = func.replace(norm_col, ch, '')
                norm_tokens = [_normalize_form_q(tok) for tok in tokens]
                norm_q_full = escape_like(_normalize_form_q(q_stripped))
                norm_relevance = case(
                    (norm_col.ilike(f"{norm_q_full}%", escape="\\"), 0),
                    else_=1,
                )
                stmt2 = select(Subject).where(cat_clause)
                for tok in norm_tokens:
                    t = escape_like(tok)
                    stmt2 = stmt2.where(norm_col.ilike(f"%{t}%", escape="\\"))
                stmt2 = stmt2.order_by(norm_relevance, Subject.name).limit(_SEARCH_RESULT_LIMIT)
                courses = (await session.execute(stmt2)).scalars().all()
        else:
            stmt = (
                select(Subject)
                .where(cat_clause)
                .order_by(Subject.name).limit(30)
            )
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
        remaining_map = await cache.get_review_remaining_cached()
        on_demand_ids = await cache.get_on_demand_subject_ids_cached()
        insts_by_course: dict = {}
        for cs, inst in cs_rows:
            # オンデマンド配信科目は「担当教員によらず内容が同一」だが、2026-09-08以降は
            # 募集締切扱いにはしない（投稿は受け付け、フォーム側で「どちらの先生でも可」と案内する）
            remaining = remaining_map.get((cs.subject_id, inst.name), MAX_REVIEWS_PER_COURSE_SECTION)
            insts_by_course.setdefault(cs.subject_id, []).append({
                "name": inst.name,
                "url": cs_url_map.get(cs.id, ""),
                "full": remaining <= 0,
                "remaining": remaining,
            })
    return {"courses": [
        {"id": c.id, "name": c.name,
         "category": c.category or "",
         "faculty": c.faculty or "", "department": c.department or "",
         "on_demand": c.id in on_demand_ids,
         "instructors": insts_by_course.get(c.id, [])}
        for c in courses
    ]}


@router.get("/api/preload")
async def api_preload(faculty: str = ""):
    # レビュー投稿フォームの科目候補は「教養科目（全員共通）＋ 指定学部の専門科目」。
    # faculty はプロフィール（会員登録情報）の学部で、クライアントがプリフィル解決後に付与する。
    # 学科の絞り込みはクライアント側（core.config.subject_submittable_for_profile 相当）で行う。
    faculty = _clean_faculty(faculty)
    data = cache.get_preload_cache(faculty)
    if data is None:
        _, all_courses_ = await cache.get_courses_cached()
        courses = [
            c for c in all_courses_
            if c.category == REVIEW_SUBMISSION_CATEGORY
            or (c.category == REVIEW_SUBMISSION_SENMON_CATEGORY and (c.faculty or "") == KYOTSU_SENMON_KISO_FACULTY)
            or (faculty and c.category == REVIEW_SUBMISSION_SENMON_CATEGORY and (c.faculty or "") == faculty)
        ]
        # 前提: 専門科目のバリアント統合グループ（get_senmon_variant_group_cached）は
        # (科目名, classification) 単位で、classification に学部名が埋まっているため1グループが
        # 複数の faculty 値をまたぐことはない。この前提が崩れると、faculty で絞ったこの courses に
        # グループの一部メンバーしか入らず、フロントの統合表示が不完全になる／submit 側は
        # 完全な group_subject_ids で重複判定するため「フォーム上は別項目なのに投稿済みで弾かれる」
        # 不整合が起きうる。跨ぐ classification を新設するときはここも見直すこと。
        # （共通専門基礎 KYOTSU_SENMON_KISO_FACULTY と医学部保健学科4専攻は faculty 一致で常に全員含まれる）
        insts_by_course = await cache.get_all_instructors_cached()
        inst_courses: dict[str, dict[int, object]] = {}
        for c in courses:
            for inst in insts_by_course.get(c.id, []):
                inst_courses.setdefault(inst.name, {})[c.id] = c
        # 語尾の数字・アルファベットのみが異なる科目（例: 生物学各論A1/A2/C1/C2）は
        # レビュー投稿フォームの科目検索でも1件にまとめて選べるようにする（LINE bot科目一覧と同じ統合規則）
        variant_map = await cache.get_variant_map_cached()
        senmon_group = await cache.get_senmon_variant_group_cached()
        # 科目×担当教員ごとの最新シラバスURL（レビュー投稿フォームの「この科目×教員の
        # シラバスはこちら」ボタンの表示可否・遷移先に使う）。学部を問わず全件共通なので
        # faculty別のこのキャッシュとは別に、全体で1つのTTLキャッシュ＋prewarm対象にしている。
        syllabus_by_pair = await cache.get_syllabus_urls_by_pair_cached()
        # variantGroupは遠隔/対面で同じベース名文字列になる（ラベル自体は共通の接頭辞を保つ
        # 必要があるため）。フロントエンド側の統合表示（_groupCourseItems）が誤って
        # 遠隔クラスと対面クラスを1グループに混在させないよう、isRemoteを別途渡す
        # （core.subject_variants.is_remote_tagged()参照）。専門科目は管理画面と同じ統合に
        # 揃えるためvariantGroupKey/variantGroupLabelを別途渡す（_variant_group_fields参照）。
        course_list = [
            {"id": c.id, "name": c.name, "reading": c.reading or "",
             "category": c.category or "",
             "faculty": c.faculty or "", "department": c.department or "",
             **_variant_group_fields(c.category, c.id, c.name, variant_map, senmon_group),
             "instructors": [
                 {"name": i.name, "syllabus_url": syllabus_by_pair.get((c.id, i.name), "")}
                 for i in insts_by_course.get(c.id, [])
             ]}
            for c in courses
        ]
        instructor_list = [
            {"name": name, "courses": [
                {"id": ic.id, "name": ic.name,
                 "category": ic.category or "",
                 "faculty": ic.faculty or "", "department": ic.department or "",
                 "syllabus_url": syllabus_by_pair.get((ic.id, name), ""),
                 **_variant_group_fields(ic.category, ic.id, ic.name, variant_map, senmon_group)}
                for ic in courses_by_id.values()
            ]}
            for name, courses_by_id in sorted(inst_courses.items())
        ]
        data = {"courses": course_list, "instructors": instructor_list}
        cache.set_preload_cache(data, faculty)

    # 「full」/「remaining」（募集締切・残り枠）はレビュー投稿状況で頻繁に変わりうるため、
    # 構造データ本体（数千件規模でTTL 3600秒キャッシュ）とは切り離し、毎リクエスト時に付与する。
    # オンデマンド配信科目は 2026-09-08 以降は募集締切扱いにせず、フロントで「担当教員を
    # 問わず内容は同一（どちらの先生を選んでも可）」と案内するための on_demand フラグのみ渡す。
    remaining_map = await cache.get_review_remaining_cached()
    on_demand_ids = await cache.get_on_demand_subject_ids_cached()
    if remaining_map or on_demand_ids:
        def _full(sid, name):
            return remaining_map.get((sid, name), MAX_REVIEWS_PER_COURSE_SECTION) <= 0
        def _remaining(sid, name):
            return remaining_map.get((sid, name), MAX_REVIEWS_PER_COURSE_SECTION)
        data = {
            "courses": [
                {**c, "on_demand": c["id"] in on_demand_ids, "instructors": [
                    {**i, "full": _full(c["id"], i["name"]), "remaining": _remaining(c["id"], i["name"])}
                    for i in c["instructors"]
                ]}
                for c in data["courses"]
            ],
            "instructors": [
                {**inst, "courses": [
                    {**cn, "on_demand": cn["id"] in on_demand_ids,
                     "full": _full(cn["id"], inst["name"]), "remaining": _remaining(cn["id"], inst["name"])}
                    for cn in inst["courses"]
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
async def search_instructors(q: str = "", faculty: str = "", _rl=Depends(_search_rate_limit)):
    if not q.strip():
        return {"instructors": []}
    faculty = _clean_faculty(faculty)
    cat_clause = _submission_category_clause(faculty)
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
                select(Instructor.name, Subject.id, Subject.name,
                       Subject.category, Subject.faculty, Subject.department)
                .join(CourseSection, CourseSection.instructor_id == Instructor.id)
                .join(Subject, Subject.id == CourseSection.subject_id)
                .where(Instructor.name.in_(insts), cat_clause)
                .order_by(Instructor.name, Subject.name)
            )).all()
            remaining_map = await cache.get_review_remaining_cached()
            on_demand_ids = await cache.get_on_demand_subject_ids_cached()
            variant_map = await cache.get_variant_map_cached()
            senmon_group = await cache.get_senmon_variant_group_cached()
            courses_by_inst: dict[str, list] = {name: [] for name in insts}
            for inst_name, c_id, c_name, c_cat, c_fac, c_dept in all_rows:
                if not any(x["id"] == c_id for x in courses_by_inst[inst_name]):
                    # オンデマンド配信科目は募集締切扱いにしない（on_demand フラグのみ渡す）
                    remaining = remaining_map.get((c_id, inst_name), MAX_REVIEWS_PER_COURSE_SECTION)
                    courses_by_inst[inst_name].append({
                        "id": c_id, "name": c_name, "full": remaining <= 0, "remaining": remaining,
                        "on_demand": c_id in on_demand_ids,
                        "category": c_cat or "", "faculty": c_fac or "", "department": c_dept or "",
                        **_variant_group_fields(c_cat, c_id, c_name, variant_map, senmon_group),
                    })
            # 教養科目を担当していない教員（専門科目のみ担当）はレビュー投稿フォームの
            # 検索結果から除外する
            for name in insts:
                if courses_by_inst[name]:
                    result.append({"name": name, "courses": courses_by_inst[name]})

    return {"instructors": result}


async def _group_subject_ids(subject: Subject) -> tuple[str, list[int], list[str]]:
    """科目がレビュー投稿フォームと同じ語尾バリアントグループ（例: 生物学各論A1/A2/C1/C2）
    に属する場合、グループラベル・グループ内の全subject_id・全科目名を返す。
    属さない場合はラベル""・[subject.id]のみを返す（レビュー閲覧では単独科目として扱う）。
    グループ判定の実体はcache.get_variant_group_subject_ids()（レビュー投稿の重複防止・
    募集枠共有と共通化済み、2026-09-01）。

    上記のグループに属さない場合でも、LETTER_ONLY_VIEW_MERGE_CLASSIFICATIONS（教養科目の
    人文/社会/自然/総合、2026-09-05）対象の科目は、cache.get_letter_view_group_cached()
    （投稿枠共有には一切影響しない別系統のグループ判定）でA/B文字バリアントをまとめる。
    レビュー閲覧・チケット解除は1つのLIFFページに合算するが、レビュー投稿の募集枠・
    重複防止はA/B別科目のまま（この関数の結果を使わない）。

    医学部保健学科の4専攻をまたいだ完全同名科目（2026-09-06、ユーザー指示）はcache.
    get_variant_group_subject_ids()側で合流済みのidsが返るが、科目名自体は全メンバー
    共通でラベル文字列を持たないため、その場合はグループラベルに元の科目名をそのまま使い、
    科目名の代わりに専攻名（department から「保健学科」を除いた部分）を返す。"""
    variant_map = await cache.get_variant_map_cached()
    label = variant_map.get(subject.name, "")
    ids = await cache.get_variant_group_subject_ids(subject)
    if label and len(ids) >= 2:
        _, all_courses = await cache.get_courses_cached()
        names_by_id = {c.id: c.name for c in all_courses}
        # バッジ表示（「◯◯のレビューをまとめて表示」）には科目名そのものではなく短い
        # 接尾辞を使う。labelはグループのベース名（接尾辞を含まない）のため、括弧付き
        # 別名パターン等では科目名から単純にlabelを取り除いても接尾辞にならない
        # （2026-09-07、バッジに科目名がそのまま重複表示されるバグの修正）。
        suffix_map = await cache.get_variant_member_suffix_map_cached()
        display_names = [suffix_map.get(names_by_id[i], names_by_id[i]) for i in ids]
        return label, ids, display_names

    if len(ids) >= 2 and is_hoken_gakka_senko(subject.faculty or "", subject.department or ""):
        _, all_courses = await cache.get_courses_cached()
        courses_by_id = {c.id: c for c in all_courses}
        senko_labels = [hoken_gakka_senko_label(courses_by_id[i].department or "") for i in ids]
        return subject.name, ids, senko_labels

    letter_view = await cache.get_letter_view_group_cached()
    entry = letter_view.get(subject.name)
    if entry:
        letter_label, letter_names, _letters = entry
        _, all_courses = await cache.get_courses_cached()
        name_to_id = {c.name: c.id for c in all_courses}
        letter_ids = [name_to_id[n] for n in letter_names if n in name_to_id]
        if len(letter_ids) >= 2:
            return letter_label, letter_ids, letter_names

    return "", [subject.id], [subject.name]


@router.get("/api/course/{course_id}")
async def api_course(course_id: int, request: Request, id_token: str = ""):
    try:
        uid = await verify_liff_id_token(id_token, request) if id_token else None
        # BANされたユーザーは書き込み系(unlock/submit)だけでなく、リッチメニュー経由の
        # レビュー閲覧そのものも封じる(2026-08-29、閲覧だけは素通りしていた不備の修正)
        if uid and await moderation.is_banned(uid):
            raise HTTPException(status_code=403, detail=BAN_MESSAGE_TEXT)
        # 修正理由(2026-09-04): 従来はsubject取得・agg/ease集計・レビュー本体・シラバスコード・
        # 教員別シラバスURL・閲覧数記録の6クエリをそれぞれ別セッション（一部はasyncio.gatherで
        # 並行）に分けており、Render(Singapore)⇄Supabase(東京/大阪)間のDB往復コストが高い構成
        # では1リクエストで最大6往復分のレイテンシが積み重なっていた（LINEの「詳細・レビューを
        # 見る」タップから表示まで4秒台かかる実害の主因と判明）。asyncio.gatherによる並行クエリは
        # 同一セッションでは使えない（InterfaceError、非同期クエリのルール参照）ため、並行実行を
        # 諦めて全クエリを1本のセッションで順次実行し、DB往復回数を減らす。
        async with AsyncSessionLocal() as session:
            subject = await session.get(Subject, course_id)
            if not subject:
                raise HTTPException(status_code=404, detail="course not found")
            # 語尾バリアントグループに属する科目は、レビュー閲覧も1つの科目として扱い、
            # グループ内の全科目のレビュー・評価をまとめて表示する（レビュー投稿フォームの
            # 科目検索での統合表示と対にするため）
            group_label, group_subject_ids, group_names = await _group_subject_ids(subject)
            on_demand_ids = await cache.get_on_demand_subject_ids_cached()
            # 修正理由: ORDER BY未指定だとPostgreSQLは行順を保証せず、これに依存する
            # 閲覧数記録先(main_cs_id)・表示するシラバスURL・教員名の表示順がリクエスト
            # ごとに変わりうる非決定的な挙動になっていた。id順で固定する。
            cs_instr_rows = (await session.execute(
                select(CourseSection, Instructor)
                .join(Instructor, Instructor.id == CourseSection.instructor_id)
                .where(CourseSection.subject_id.in_(group_subject_ids))
                .order_by(CourseSection.id)
            )).all()
            cs_ids = [cs.id for cs, _ in cs_instr_rows]
            cs_subject_by_id = {cs.id: cs.subject_id for cs, _ in cs_instr_rows}

            # 教養科目のA/B文字バリアント統合（LETTER_ONLY_VIEW_MERGE_CLASSIFICATIONS）では、
            # レビューカードごとに担当教員がA/Bどちらのクラスのものかを明記する（2026-09-05、
            # ユーザー指示。並行クラスで担当教員が異なることが多いため、統合表示だけだと
            # レビューがどちらのクラスのものか分からなくなる不備の対策）。
            letter_view = await cache.get_letter_view_group_cached()
            _letter_entry = letter_view.get(subject.name)
            cs_id_to_letter: dict[int, str] = {}
            if _letter_entry:
                _, _letter_names, name_to_letter = _letter_entry
                id_to_name = dict(zip(group_subject_ids, group_names))
                cs_id_to_letter = {
                    cs.id: name_to_letter.get(id_to_name.get(cs.subject_id, ""), "")
                    for cs, _instr in cs_instr_rows
                }

            # 承認済みレビューの集計を1本のGROUP BYにまとめる。以下すべてこの結果から再合成する:
            #   - 充実度★1〜5・楽単度SS〜Cの件数分布（ease_rating × rating の2軸）
            #   - 平均充実度・総件数
            #   - 教員別の承認済みレビュー件数（チケット解除前でも「どの教員に何件あるか」を
            #     出すため locked 状態に関わらず集計。selected_instructor 優先、無ければ
            #     course_section 由来の教員名。2026-09-08 ユーザー指示）
            # そのため group by キーに selected_instructor・course_section_id も足す（行数は
            # 増えるが集計値は同じ。以前ここで別クエリを1本投げていたのを統合した）。
            _cs_id_to_instr_nm = {cs.id: instr.name for cs, instr in cs_instr_rows}
            if cs_ids:
                ease_rows = (await session.execute(
                    select(Review.ease_rating, Review.rating, Review.selected_instructor,
                           Review.course_section_id, func.count(Review.id))
                    .where(Review.course_section_id.in_(cs_ids), Review.status == ReviewStatus.APPROVED)
                    .group_by(Review.ease_rating, Review.rating, Review.selected_instructor,
                              Review.course_section_id)
                )).all()
            else:
                ease_rows = []
            ease_counts: dict = {}
            rating_counts: dict = {}
            rating_sum = 0
            rating_total = 0
            review_count = 0
            instr_review_counts: dict[str, int] = {}
            for ease, rating, sel_instr, csid, cnt in ease_rows:
                review_count += cnt
                if ease:
                    ease_counts[ease] = ease_counts.get(ease, 0) + cnt
                if rating is not None:
                    rating_counts[rating] = rating_counts.get(rating, 0) + cnt
                    rating_sum += rating * cnt
                    rating_total += cnt
                _nm = (sel_instr or "").strip() or _cs_id_to_instr_nm.get(csid, "")
                if _nm:
                    instr_review_counts[_nm] = instr_review_counts.get(_nm, 0) + cnt
            avg_rating = (rating_sum / rating_total) if rating_total else None

            if cs_ids:
                reviews_raw = (await session.execute(
                    select(Review)
                    .where(Review.course_section_id.in_(cs_ids), Review.status == ReviewStatus.APPROVED)
                    .order_by(Review.selected_instructor.nulls_last(), Review.academic_year.desc())
                    .limit(20)
                )).scalars().all()
            else:
                reviews_raw = []

            sc_row = (await session.execute(
                select(Syllabus.timetable_code)
                .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
                .where(CourseSection.subject_id == course_id, Syllabus.timetable_code.isnot(None))
                .order_by(Syllabus.year.desc())
                .limit(1)
            )).first()

            cs_syllabus_urls = await _latest_syllabus_urls(session, cs_ids)
            # 教員別のシラバスURL（絞り込みチップで教員を選んだ際、その教員のシラバスに切り替えるため）。
            # 同じ教員が複数course_section（バリアント違い等）を持つ場合は最初に見つかった方を採用する
            instructor_syllabus_urls: dict[str, str] = {}
            for cs, instr in cs_instr_rows:
                url = cs_syllabus_urls.get(cs.id)
                if url and instr.name not in instructor_syllabus_urls:
                    instructor_syllabus_urls[instr.name] = url

            # 開講科目（バリアント）別メタ情報。
            # 「1ページに統合表示するが、レビュー募集は開講科目ごとに別管理」という教養科目で、
            # 詳細ページ（templates/liff/course.html）に「すべて / 科目名A / 科目名B …」の
            # 絞り込みチップ行を出すために使う。グループに属さない単独科目では空リストを返し、
            # フロント側はチップ行自体を描画しない。
            variants_payload: list[dict] = []
            if group_label and len(group_subject_ids) >= 2:
                _, _all_courses_v = await cache.get_courses_cached()
                _name_by_id = {c.id: c.name for c in _all_courses_v}
                _remaining_map_v = await cache.get_review_remaining_cached()
                _instr_by_sid: dict[int, list[str]] = {}
                _syl_by_sid: dict[int, dict[str, str]] = {}
                for cs, instr in cs_instr_rows:
                    lst = _instr_by_sid.setdefault(cs.subject_id, [])
                    if instr.name not in lst:
                        lst.append(instr.name)
                    _u = cs_syllabus_urls.get(cs.id)
                    if _u:
                        _syl_by_sid.setdefault(cs.subject_id, {}).setdefault(instr.name, _u)
                for _sid in group_subject_ids:
                    _v_instrs = _instr_by_sid.get(_sid, [])
                    # オンデマンド配信科目も 2026-09-08 以降は締切扱いにしない
                    if _v_instrs:
                        _is_open = any(
                            _remaining_map_v.get((_sid, nm), MAX_REVIEWS_PER_COURSE_SECTION) > 0
                            for nm in _v_instrs
                        )
                    else:
                        _is_open = True
                    variants_payload.append({
                        "id": _sid,
                        "name": _name_by_id.get(_sid, ""),
                        "instructors": _v_instrs,
                        "open": _is_open,
                        "syllabus_urls": _syl_by_sid.get(_sid, {}),
                    })

            # ビューカウント記録
            # 修正理由: バリアントグループでcs_idsはグループ全体にまたがるため、閲覧数は
            # 実際にリクエストされた科目自身のcourse_sectionに記録する（無ければグループ内の
            # 代表にフォールバック）。
            own_cs_ids = [cs.id for cs, _ in cs_instr_rows if cs.subject_id == course_id]
            if cs_ids:
                main_cs_id = own_cs_ids[0] if own_cs_ids else cs_ids[0]
                _now = datetime.now(timezone.utc)
                _ins = pg_insert(CourseSectionView).values(
                    course_section_id=main_cs_id,
                    view_count=1,
                    last_viewed_at=_now,
                )
                await session.execute(
                    _ins.on_conflict_do_update(
                        index_elements=["course_section_id"],
                        set_={
                            "view_count": CourseSectionView.view_count + 1,
                            "last_viewed_at": _now,
                        },
                    )
                )

            # レビュー閲覧権（デフォルトでは他人のレビューは見られず、承認されたレビュー1件につき
            # core.config.review_approval_unlock_credits(科目category) 枚（教養5枚・専門3枚）の
            # 閲覧権チケットが付与される。閲覧権はsubject単位・バリアントグループ内で共有）
            # 専門科目は投稿解禁後もチケット解除・件数/評価集計表示を含め一切閲覧不可にする
            # （2026-09-06、ユーザー指示。閲覧解禁は別途指示があるまで行わない）
            view_restricted = subject.category != REVIEW_VIEW_CATEGORY

            unlock_credits = None
            # 閲覧中の本人が投稿したレビューをハイライト表示するため、自分のstudent_idを控えておく
            # （reviewsテーブルにline_user_idは無いため、user_profiles.student_idとの一致で判定する）
            my_student_id = None
            unlocked = review_count == 0 and not view_restricted
            if not unlocked and not view_restricted and uid:
                profile = await session.get(UserProfile, uid)
                unlock_credits = profile.unlock_credits if profile else 0
                my_student_id = profile.student_id if profile else None
                unlocked = (await session.execute(
                    select(SubjectUnlock.subject_id).where(
                        SubjectUnlock.line_user_id == uid,
                        SubjectUnlock.subject_id.in_(group_subject_ids),
                    )
                )).scalars().first() is not None
            locked = not unlocked
            if view_restricted:
                review_count = 0

            await session.commit()

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
        if ease_counts:
            top_ease = sorted(ease_counts.items(), key=lambda r: (-r[1], EASE_ORDER.get(r[0], 99)))[0][0]

        return {
            "id": subject.id,
            "name": subject.name,
            "group_label": group_label,
            "group_variant_names": group_names if group_label else [],
            "variants": variants_payload,
            "instructor": instructor_str,
            "classification": subject.classification or "",
            "category": subject.category or "",
            "term_type": subject.term_type or "",
            "credits": float(subject.credits) if subject.credits else 0,
            "note": ON_DEMAND_SAME_CONTENT_NOTE if (not locked and subject.id in on_demand_ids) else "",
            "syllabus_url": syllabus_url or "",
            "instructor_syllabus_urls": instructor_syllabus_urls,
            # チケット解除前でも担当教員を選んでシラバスだけ見られるようにするため、
            # レビュー由来ではなくcourse_sections由来の教員名一覧をlocked状態に関わらず返す
            "instructor_names": instr_names,
            # 教員別の承認済みレビュー件数（name -> 件数）。チケット解除前でも返す。
            "instructor_review_counts": instr_review_counts if not view_restricted else {},
            "review_count": review_count,
            "locked": locked,
            "view_restricted": view_restricted,
            "unlock_credits": unlock_credits,
            # 平均評価・最頻の楽単度はロック中でも実値をそのまま返す（2026-09-08、ユーザー指示）。
            # フロントは解除前カードで CSS blur をかけて見せるが、これは解除の動機づけの演出で
            # あってアクセス制御ではない（数値はJSON応答に含まれ DevTools で読める）。
            # 集計値のみの開示なので許容範囲。個々のレビュー本文・件数分布はロック中は返さない。
            "avg_rating": avg_rating,
            "top_ease": top_ease,
            "rating_distribution": rating_counts if not locked else {},
            "ease_distribution": ease_counts if not locked else {},
            "reviews": [
                {
                    "rating": r.rating,
                    "ease_rating": r.ease_rating,
                    # 修正理由: JS側での独自パース(旧/新形式判定・区切り文字分解)を無くすため、
                    # core.grading_method.parse_grading_method()（管理画面等と共通のパーサー）で
                    # サーバー側であらかじめ[{"label","text"}, ...]へ変換して返す
                    "grading_method": parse_grading_method(r.grading_method),
                    "comment": r.content or "",
                    "instructor": r.selected_instructor or "",
                    "variant_letter": cs_id_to_letter.get(r.course_section_id, ""),
                    "variant_id": cs_subject_by_id.get(r.course_section_id, subject.id),
                    "nickname": r.nickname or "",
                    "academic_year": r.academic_year or 0,
                    "created_at": r.created_at.isoformat(),
                    "is_mine": bool(my_student_id and r.student_id and r.student_id == my_student_id),
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
    await moderation.raise_if_banned(uid)

    async with AsyncSessionLocal() as session:
        subject = await session.get(Subject, course_id)
        if not subject:
            raise HTTPException(status_code=404, detail="course not found")
        if subject.category != REVIEW_VIEW_CATEGORY:
            raise HTTPException(status_code=403, detail="専門科目のレビューは現在閲覧できません")
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
        # LINE bot 側の科目一覧が「解除済み」バッジを即時反映できるよう、
        # ユーザー状態スナップショット（banned/登録状態/解除済み科目をまとめてキャッシュ）を落とす
        cache.invalidate_linebot_user_state(uid)
        return {"ok": True, "already": False, "unlock_credits": new_balance}
