import asyncio
import contextvars
import time
from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, or_, select

from core.config import (
    CHANNEL,
    EASE_ORDER,
    JST,
    MAX_REVIEWS_PER_COURSE_SECTION,
    ON_DEMAND_SAME_CONTENT_SUBJECTS,
    REVIEW_SUBMISSION_SENMON_CATEGORY,
    is_profile_complete,
    latest_syllabus_url_map,
)
from core.subject_variants import (
    CLASSIFICATION_MERGE_EXCLUDED,
    LETTER_ONLY_VIEW_MERGE_CLASSIFICATIONS,
    LETTER_SPLIT_EXCLUDED_CLASSIFICATIONS,
    NUM_MERGE_EXCLUDED_NAMES,
    compute_letter_view_groups,
    compute_variant_display_groups,
    compute_variant_full_labels,
    compute_variant_groups,
    compute_variant_member_suffix_map,
    is_hoken_gakka_senko,
    is_remote_tagged,
)
from database import AsyncSessionLocal
from models import (
    CourseSection,
    DisplayOrder,
    ErrorLog,
    Inquiry,
    InquiryStatus,
    Instructor,
    PaymentRequest,
    PaymentRequestStatus,
    Review,
    ReviewStatus,
    Subject,
    SubjectUnlock,
    Syllabus,
    UserProfile,
)

# 全キャッシュ共通のTTLポリシー(1時間)。用途別に名前を分けているが値は全て同じであるべきなので、
# ここ1箇所を直せば全キャッシュに反映される(個別に変えたい場合のみ該当行だけ上書きする)
_DEFAULT_CACHE_TTL = 3600
_CLS_CACHE_TTL = _DEFAULT_CACHE_TTL
_COURSE_CACHE_TTL = _DEFAULT_CACHE_TTL
_COURSE_FLEX_TTL = _DEFAULT_CACHE_TTL
_COURSE_LIST_TTL = _DEFAULT_CACHE_TTL


class _TTLCache:
    """パラメータなし・単一値のTTLキャッシュ共通ヘルパー(2026-09-22導入)。

    以前はこのファイルの大半の関数が「モジュールグローバルに値とタイムスタンプを持ち、
    TTL内なら返す・切れていたらDBから取得してglobal再代入する」処理を手書きで
    繰り返していた。get_*_cached()側のシグネチャ・戻り値・TTL・キャッシュ無効化条件は
    一切変えず、その定型部分だけをここに集約する。

    fetchは毎回DBから値を取得する非同期callable。validは「キャッシュ値をTTL内で
    使い回してよいか」の判定callableで、呼び出し元ごとに2通りある:
      - bool                    : 値が空collection(falsy)なら毎回再取得しにいく
                                   (「空 = まだ何も取れていない」とみなす旧来の書き方)
      - lambda v: v is not None : Noneセンチネルのみ再取得条件にする
                                   (空collectionでも正当な取得結果として使い回す)
    default_factoryはinvalidate()直後の初期値を作るゼロ引数callable。{}/[]/set()/Noneを
    呼び出しごとに新しく生成するため、ミュータブルな初期値を使い回さないようcallableで受け取る。
    """

    __slots__ = ("_at", "_default_factory", "_fetch", "_gen", "_ttl", "_valid", "_value")

    def __init__(self, ttl, fetch, valid, default_factory):
        self._ttl = ttl
        self._fetch = fetch
        self._valid = valid
        self._default_factory = default_factory
        self._value = default_factory()
        self._at = 0.0
        self._gen = 0

    async def get(self):
        if self._valid(self._value) and time.monotonic() - self._at < self._ttl:
            return self._value
        self._value = await self._fetch()
        self._at = time.monotonic()
        return self._value

    async def refresh(self) -> None:
        """TTL切れを待たずにバックグラウンドで再取得して差し替える（差し替え中も古い値を
        返し続けるため、利用者が再取得待ちにならない）。取得中にinvalidate()された場合は、
        取得した値が古い可能性があるので捨てる（世代番号_genで判定）。"""
        gen = self._gen
        value = await self._fetch()
        if gen == self._gen:
            self._value = value
            self._at = time.monotonic()

    def invalidate(self) -> None:
        self._value = self._default_factory()
        self._at = 0.0
        self._gen += 1


# ── classification caches ───────────────────────────────────────

async def _fetch_cls_order_map() -> dict:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(DisplayOrder).where(DisplayOrder.kind == "classification").order_by(DisplayOrder.sort_order)
        )).scalars().all()
    return {r.name: r.sort_order for r in rows}


_cls_order_map = _TTLCache(_CLS_CACHE_TTL, _fetch_cls_order_map, bool, dict)


async def get_cls_order_map() -> dict:
    return await _cls_order_map.get()


async def _fetch_cls_parent_map() -> dict[str, str]:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(DisplayOrder.name, DisplayOrder.parent_group)
            .where(DisplayOrder.kind == "classification")
            .where(DisplayOrder.parent_group.isnot(None))
            .where(DisplayOrder.parent_group != "")
        )).all()
    return {r.name: r.parent_group for r in rows}


_cls_parent_map = _TTLCache(_CLS_CACHE_TTL, _fetch_cls_parent_map, bool, dict)


async def get_cls_parent_map() -> dict[str, str]:
    return await _cls_parent_map.get()


async def _fetch_faculty_order() -> list[str]:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(DisplayOrder.name).where(DisplayOrder.kind == "faculty").order_by(DisplayOrder.sort_order)
        )).scalars().all()
    return list(rows)


_faculty_order = _TTLCache(_CLS_CACHE_TTL, _fetch_faculty_order, bool, list)


async def get_faculty_order() -> list[str]:
    return await _faculty_order.get()


def invalidate_faculty_order_cache():
    _faculty_order.invalidate()


async def _fetch_cls_set() -> set[str]:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(Subject.classification).distinct())).scalars().all()
    return {r for r in rows if r}


_cls_set = _TTLCache(_CLS_CACHE_TTL, _fetch_cls_set, bool, set)


async def get_cls_set() -> set[str]:
    return await _cls_set.get()


def invalidate_cls_caches():
    _cls_order_map.invalidate()
    _cls_parent_map.invalidate()
    _cls_set.invalidate()


# ── course / review caches ──────────────────────────────────────

_course_flex_cache: dict[int, tuple] = {}
_course_list_cache: dict[str, tuple] = {}


async def _fetch_courses() -> tuple[dict, list]:
    async with AsyncSessionLocal() as s:
        courses = (await s.execute(
            select(Subject).order_by(Subject.sort_order, Subject.name)
        )).scalars().all()
    return {c.name: c for c in courses}, courses


# (courses_by_name, course_list_all) のタプルを1つの値としてキャッシュする。「空なら
# 再取得」のtruthy判定は元コードと同じくcourses_by_name側(v[0])だけを見る（タプル自体は
# 要素数2で常にtruthyなため、そのままboolを渡すと空collectionでも再取得されなくなる）。
_courses = _TTLCache(_COURSE_CACHE_TTL, _fetch_courses, lambda v: bool(v[0]), lambda: ({}, []))


async def get_courses_cached():
    return await _courses.get()


async def get_on_demand_subject_ids_cached() -> frozenset[int]:
    """ON_DEMAND_SAME_CONTENT_SUBJECTS（科目名, 学部）を現在の subjects.id へ解決した集合。
    subjects の再インポートで id が振り直されても追従する（get_courses_cached() に相乗り
    するので専用 TTL は持たない）。"""
    _, all_courses = await get_courses_cached()
    return frozenset(
        c.id for c in all_courses
        if (c.name, c.faculty or "") in ON_DEMAND_SAME_CONTENT_SUBJECTS
    )


async def _fetch_reviewed() -> set[str]:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(Subject.name).distinct()
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.status == ReviewStatus.APPROVED)
        )).scalars().all()
    return set(rows)


# 空集合(=レビュー承認済み科目0件)も正当な取得結果として使い回すため、truthyではなく
# Noneセンチネルで「未取得」を判定する(元コードの_reviewed_cache_initフラグと同義)。
_reviewed = _TTLCache(_COURSE_CACHE_TTL, _fetch_reviewed, lambda v: v is not None, lambda: None)


async def get_reviewed_cached() -> set[str]:
    return await _reviewed.get()


async def _fetch_all_instructors() -> dict[int, list]:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(CourseSection, Instructor)
            .join(Instructor, Instructor.id == CourseSection.instructor_id)
            .order_by(Instructor.sort_order, Instructor.name)
        )).all()
    d: dict[int, list] = {}
    for cs, instr in rows:
        d.setdefault(cs.subject_id, []).append(instr)
    return d


_all_instructors = _TTLCache(_COURSE_CACHE_TTL, _fetch_all_instructors, bool, dict)


async def get_all_instructors_cached() -> dict[int, list]:
    return await _all_instructors.get()


async def _fetch_all_review_stats() -> dict[str, tuple]:
    async with AsyncSessionLocal() as s:
        count_rows = (await s.execute(
            select(Subject.name, func.count(Review.id).label("cnt"))
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.status == ReviewStatus.APPROVED)
            .group_by(Subject.name)
        )).all()
        ease_rows = (await s.execute(
            select(Subject.name, Review.ease_rating, func.count(Review.id).label("cnt"))
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.status == ReviewStatus.APPROVED, Review.ease_rating.isnot(None))
            .group_by(Subject.name, Review.ease_rating)
        )).all()
    ease_map: dict[str, list] = {}
    for name, ease, cnt in ease_rows:
        ease_map.setdefault(name, []).append((ease, cnt))
    result = {}
    for name, cnt in count_rows:
        top_ease = None
        if name in ease_map:
            top_ease = sorted(ease_map[name], key=lambda r: (-r[1], EASE_ORDER.get(r[0], 99)))[0][0]
        result[name] = (cnt, top_ease)
    return result


_all_review_stats = _TTLCache(_COURSE_CACHE_TTL, _fetch_all_review_stats, bool, dict)


async def get_all_review_stats_cached() -> dict[str, tuple]:
    return await _all_review_stats.get()


async def _fetch_ease_extremes() -> dict[int, tuple[str, str, str]]:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(Subject.id, Subject.name, Review.ease_rating)
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.status == ReviewStatus.APPROVED)
            .group_by(Subject.id, Subject.name, Review.ease_rating)
        )).all()
    result: dict[int, tuple[str, str, str]] = {}
    for sid, name, ease in rows:
        if sid not in result:
            result[sid] = (name, ease, ease)
            continue
        _, best, worst = result[sid]
        if EASE_ORDER.get(ease, 99) < EASE_ORDER.get(best, 99):
            best = ease
        if EASE_ORDER.get(ease, -1) > EASE_ORDER.get(worst, -1):
            worst = ease
        result[sid] = (name, best, worst)
    return result


_ease_extremes = _TTLCache(_COURSE_CACHE_TTL, _fetch_ease_extremes, bool, dict)


async def get_ease_extremes_cached() -> dict[int, tuple[str, str, str]]:
    """楽単/鬼単ランキング(line_bot.handler._get_rakutan_ranking/_get_onitan_ranking)向け:
    subject_id → (科目名, 最も高い楽単度, 最も低い楽単度)のマップ。

    修正理由(2026-09-03): 従来はランキング表示のたびにSubject×CourseSection×Reviewを
    全件JOINしてPython側で科目ごとの最良/最悪easeを求めており、10連おみくじ(2026-08-25に
    同種の問題を修正済み)と同じくレビュー件数が増えるほど重くなる設計だった。
    get_all_review_stats_cachedと同じTTL・無効化契機(invalidate_review_cache)でキャッシュする。
    """
    return await _ease_extremes.get()


async def _fetch_review_remaining() -> dict[tuple[int, str], int]:
    async with AsyncSessionLocal() as s:
        cs_rows = (await s.execute(
            select(CourseSection.subject_id, Instructor.name, CourseSection.review_closed)
            .join(Instructor, Instructor.id == CourseSection.instructor_id)
        )).all()
        review_rows = (await s.execute(
            select(CourseSection.subject_id, Instructor.name, func.count(Review.id))
            .join(Instructor, Instructor.id == CourseSection.instructor_id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.status.in_((ReviewStatus.PENDING, ReviewStatus.APPROVED)))
            .group_by(CourseSection.subject_id, Instructor.name)
        )).all()
    counts = {(sid, name): cnt for sid, name, cnt in review_rows}

    _, all_courses = await get_courses_cached()
    variant_map = await get_variant_map_cached()
    # 専門科目は管理画面と同じ compute_variant_display_groups() 単位で募集枠を共有する
    # （2026-09-08、ユーザー指示。教養科目は従来どおり compute_variant_groups() 単位）
    senmon_group = await get_senmon_variant_group_cached()
    group_key_by_sid: dict[int, tuple] = {}
    for c in all_courses:
        if (c.category or "") == REVIEW_SUBMISSION_SENMON_CATEGORY:
            g = senmon_group.get(c.id)
            if g:
                group_key_by_sid[c.id] = ("senmon", tuple(g[2]))
            continue
        label = variant_map.get(c.name)
        if label:
            group_key_by_sid[c.id] = (label, c.faculty or "", c.department or "")

    group_totals: dict[tuple, int] = {}
    for (sid, name), cnt in counts.items():
        gkey = group_key_by_sid.get(sid)
        if gkey:
            key = (gkey, name)
            group_totals[key] = group_totals.get(key, 0) + cnt

    result: dict[tuple[int, str], int] = {}
    for sid, name, closed in cs_rows:
        if closed:
            # 管理画面から手動で募集終了にした科目×教員は残り0（＝募集終了表示）
            result[(sid, name)] = 0
            continue
        gkey = group_key_by_sid.get(sid)
        total = group_totals.get((gkey, name), 0) if gkey else counts.get((sid, name), 0)
        result[(sid, name)] = max(0, MAX_REVIEWS_PER_COURSE_SECTION - total)
    return result


_full_pairs = _TTLCache(_COURSE_CACHE_TTL, _fetch_review_remaining, lambda v: v is not None, lambda: None)


async def get_review_remaining_cached() -> dict[tuple[int, str], int]:
    """(subject_id, 担当教員名)の組ごとに、あと何件レビューを募集できるか
    （MAX_REVIEWS_PER_COURSE_SECTION - 待機中+承認済み件数、0未満にはならない）を返す。
    フォーム側で残り枠バッジ・募集締切表示に使う（実際の受付可否はsubmit時にDBで再確認する）。
    戻り値に含まれない組は投稿0件＝上限まるごと空きとして扱う。

    末尾バリアントグループ（例: 線形代数1/2/3/4）に属する科目は、同じ教員が複数メンバーを
    担当している場合、実質同じ授業のため募集枠をグループ全体で合算する（2026-09-01、
    以前はsubject_id単位でしか見ておらず、同じ教員のバリアント違い科目それぞれに1件ずつ
    投稿できてしまい「1科目1件まで」の上限をすり抜けられていたバグの修正）。
    """
    return await _full_pairs.get()


def invalidate_full_pairs_cache():
    _full_pairs.invalidate()


async def _fetch_syllabus_urls() -> dict[int, str]:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(CourseSection.subject_id, Syllabus.timetable_code, Syllabus.year, Subject.faculty, Subject.department)
            .join(Syllabus, Syllabus.course_section_id == CourseSection.id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .where(Syllabus.timetable_code.isnot(None))
        )).all()
    # 科目につき複数年度のsyllabiがありうるため、最新年度のURLを採用する（共通ヘルパー）
    return latest_syllabus_url_map(rows)


_syllabus_urls = _TTLCache(_COURSE_CACHE_TTL, _fetch_syllabus_urls, bool, dict)


async def get_syllabus_urls_cached() -> dict[int, str]:
    return await _syllabus_urls.get()


async def _fetch_syllabus_urls_by_pair() -> dict[tuple[int, str], str]:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(CourseSection.subject_id, Instructor.name, Syllabus.timetable_code,
                   Syllabus.year, Subject.faculty, Subject.department)
            .join(Instructor, Instructor.id == CourseSection.instructor_id)
            .join(Syllabus, Syllabus.course_section_id == CourseSection.id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .where(Syllabus.timetable_code.isnot(None))
        )).all()
    # (subject_id, 教員名) をキーに整形してから共通ヘルパーで最新年度URLを選ぶ
    return latest_syllabus_url_map(
        ((sid, iname), code, year, faculty, department)
        for sid, iname, code, year, faculty, department in rows
    )


_syllabus_urls_by_pair = _TTLCache(_COURSE_CACHE_TTL, _fetch_syllabus_urls_by_pair, bool, dict)


async def get_syllabus_urls_by_pair_cached() -> dict[tuple[int, str], str]:
    """(subject_id, 教員名) → 最新年度のシラバスURL。
    以前は routers/liff_api.py の /api/preload 内に同種のクエリが直書きされており、
    faculty別キャッシュのミスごとに syllabi 全件スキャンが走り prewarm対象にもなっていなかった
    （2026-09-08にここへ集約）。"""
    return await _syllabus_urls_by_pair.get()


# ── 無効化後のバックグラウンド再ウォームアップ ──────────────────
# 科目・レビューの更新でキャッシュを無効化すると、次にLINE botを開いた1人が全キャッシュの
# 再構築待ち（実測2〜7秒）を負担していた。無効化の直後にバックグラウンドで作り直しておき、
# その1人が遅くならないようにする。core.cacheはline_botをimportできないため、
# 実際の処理は起動時にcore.prewarmがregister_rewarm_hook()で登録する。
_REWARM_DEBOUNCE_SEC = 3.0
_rewarm_hook = None
_rewarm_scheduled = False


def register_rewarm_hook(hook) -> None:
    global _rewarm_hook
    _rewarm_hook = hook


def _schedule_rewarm() -> None:
    global _rewarm_scheduled
    if _rewarm_hook is None or _rewarm_scheduled:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    from core.background_tasks import fire_and_forget
    _rewarm_scheduled = True
    fire_and_forget(_run_rewarm())


async def _run_rewarm() -> None:
    global _rewarm_scheduled
    # 管理画面の一括操作などで連続して無効化されるため、少し待って1回にまとめる
    await asyncio.sleep(_REWARM_DEBOUNCE_SEC)
    _rewarm_scheduled = False
    try:
        await _rewarm_hook()
    except Exception as e:
        print(f"Rewarm after invalidation failed: {e}", flush=True)


def invalidate_courses_cache():
    global _course_flex_cache, _course_list_cache
    global _preload_cache
    _courses.invalidate()
    _all_instructors.invalidate()
    _course_flex_cache = {}
    _course_list_cache = {}
    # 修正理由: シラバスURL(course_sections.syllabus_url)もcourses関連の派生データのため、
    # ここで一緒に無効化しないと管理画面での追加・変更が最大TTL(1時間)反映されなかった。
    _syllabus_urls.invalidate()
    _syllabus_urls_by_pair.invalidate()
    _preload_cache = {}
    # 語尾バリアントグループ(compute_variant_groups)も科目一覧に依存する派生データのため、
    # ここで一緒に無効化する
    _variant_map.invalidate()
    _variant_full_label.invalidate()
    _variant_member_suffix.invalidate()
    # 専門科目の管理画面互換バリアントグループ(compute_variant_display_groups)もcourses依存
    _senmon_variant_group.invalidate()
    # 教養科目A/Bのレビュー閲覧統合グループ(compute_letter_view_groups)もcourses依存の
    # 派生データのため一緒に無効化する
    _letter_view_group.invalidate()
    # 自由入力検索インデックスもcourses/variant_mapの派生データのため一緒に無効化する
    _search_index.invalidate()
    _schedule_rewarm()


def invalidate_review_cache():
    global _course_flex_cache, _course_list_cache
    _reviewed.invalidate()
    _all_review_stats.invalidate()
    _course_flex_cache = {}
    _course_list_cache = {}
    _ease_extremes.invalidate()
    invalidate_full_pairs_cache()
    _schedule_rewarm()


# ── flex / list / ranking caches (アクセスは必ずこれらの関数経由で行う) ──

def get_flex_cache(course_id: int):
    entry = _course_flex_cache.get(course_id)
    if entry and time.monotonic() - entry[1] < _COURSE_FLEX_TTL:
        return entry[0]
    return None


def set_flex_cache(course_id: int, msg) -> None:
    _course_flex_cache[course_id] = (msg, time.monotonic())


# バックグラウンドの再ウォームアップ（core.prewarm.rewarm_caches）だけがTrueにする。
# ContextVarなのでそのタスク内でのみ有効で、同時に動くユーザーリクエストの
# キャッシュ参照には影響しない（TTL切れ前でも作り直して差し替えるために使う）。
force_list_rebuild: contextvars.ContextVar[bool] = contextvars.ContextVar("force_list_rebuild", default=False)


def get_course_list_cache(key: str):
    if force_list_rebuild.get():
        return None
    entry = _course_list_cache.get(key)
    if entry and time.monotonic() - entry[1] < _COURSE_LIST_TTL:
        return entry[0]
    return None


def set_course_list_cache(key: str, value) -> None:
    _course_list_cache[key] = (value, time.monotonic())


# ── registration completeness cache（LINE bot応答パスの毎メッセージDB往復を回避） ──
# True確定分はTTL内であればDBを一切見ずに返す。False/未登録は毎回DBを見て最新状態を反映する。
#
# 「一度完了したら未完了に戻らない」は厳密には成り立たない（生協求人質問の必須化・
# 医学部保健学科の専攻再入力など、必須項目追加で complete→incomplete が起きる）。
# ただしそれらの complete→incomplete 化はすべて database.py init_db() の起動時バックフィルで、
# init_db() はプロセス起動直後＝このプロセス内キャッシュがまだ空のときに走るため実害が無い。
# 将来もし「稼働中のプロセスで」プロフィールを不完全化する経路を足す場合は、その箇所で
# 必ず invalidate_registration_complete() を呼ぶこと。
_REGISTRATION_COMPLETE_TTL = 3600
_registration_complete_at: dict[str, float] = {}


def get_registration_complete_cached(user_id: str) -> bool:
    ts = _registration_complete_at.get(user_id)
    return ts is not None and time.monotonic() - ts < _REGISTRATION_COMPLETE_TTL


def set_registration_complete(user_id: str) -> None:
    _registration_complete_at[user_id] = time.monotonic()


def invalidate_registration_complete(user_id: str) -> None:
    """稼働中プロセスでプロフィールを不完全化した直後に呼ぶ（現状は呼び出し元なし。
    上のコメント参照）。"""
    _registration_complete_at.pop(user_id, None)


# ── /api/preload レスポンスキャッシュ ──
# get_courses_cached/get_all_instructors_cachedからの構築自体は軽いが、
# 全科目・全教員（数千件規模）をループするため、リクエストの都度組み立てず結果をキャッシュする
_PRELOAD_TTL = 3600
# レビュー投稿フォームの科目候補は「教養科目（全員共通）＋ 指定学部の専門科目」を返すため、
# 学部ごとに別のレスポンスになる。faculty="" は教養科目のみ（学部未指定・未ログイン相当）。
_preload_cache: dict[str, tuple[dict, float]] = {}


def get_preload_cache(faculty: str = "") -> dict | None:
    entry = _preload_cache.get(faculty or "")
    if entry is not None and time.monotonic() - entry[1] < _PRELOAD_TTL:
        return entry[0]
    return None


def set_preload_cache(data: dict, faculty: str = "") -> None:
    _preload_cache[faculty or ""] = (data, time.monotonic())


async def _fetch_variant_map() -> dict[str, str]:
    _, all_courses = await get_courses_cached()
    _letter_split_excluded_names = frozenset(
        c.name for c in all_courses if (c.classification or "") in LETTER_SPLIT_EXCLUDED_CLASSIFICATIONS)
    _num_excluded_names = NUM_MERGE_EXCLUDED_NAMES | frozenset(
        c.name for c in all_courses if c.variant_merge_excluded)
    return compute_variant_groups(
        [(c.name, c.faculty or "", c.department or "") for c in all_courses
         if (c.classification or "") not in CLASSIFICATION_MERGE_EXCLUDED],
        letter_split_excluded_names=_letter_split_excluded_names,
        num_excluded_names=_num_excluded_names,
    )


_variant_map = _TTLCache(_COURSE_CACHE_TTL, _fetch_variant_map, lambda v: v is not None, lambda: None)


async def get_variant_map_cached() -> dict[str, str]:
    """科目名 → 語尾バリアントグループのベース名ラベルのマップ（compute_variant_groups()の結果）。

    レビュー閲覧権チケットのグループ判定(routers/liff_api.py _group_subject_ids)から
    リクエストの都度呼ばれるが、計算自体は全科目（数千件規模）を走査する正規表現マッチのため、
    科目一覧と同じTTLでキャッシュし毎リクエストの再計算を避ける。
    """
    return await _variant_map.get()


async def _fetch_variant_member_suffix_map() -> dict[str, str]:
    _, all_courses = await get_courses_cached()
    _letter_split_excluded_names = frozenset(
        c.name for c in all_courses if (c.classification or "") in LETTER_SPLIT_EXCLUDED_CLASSIFICATIONS)
    _num_excluded_names = NUM_MERGE_EXCLUDED_NAMES | frozenset(
        c.name for c in all_courses if c.variant_merge_excluded)
    return compute_variant_member_suffix_map(
        [(c.name, c.faculty or "", c.department or "") for c in all_courses
         if (c.classification or "") not in CLASSIFICATION_MERGE_EXCLUDED],
        letter_split_excluded_names=_letter_split_excluded_names,
        num_excluded_names=_num_excluded_names,
    )


_variant_member_suffix = _TTLCache(
    _COURSE_CACHE_TTL, _fetch_variant_member_suffix_map, lambda v: v is not None, lambda: None)


async def get_variant_member_suffix_map_cached() -> dict[str, str]:
    """科目名 → その科目自身の短い表示用バリアント接尾辞のマップ（compute_variant_member_suffix_map()）。

    routers/liff_api.py `_group_subject_ids()`が科目詳細LIFFの「◯◯のレビューをまとめて表示」
    バッジ表示用に、グループ内メンバーそれぞれの接尾辞（ラベル全体ではなく短い接尾辞）を
    得るために使う。get_variant_map_cached()と対象・除外条件は同一。
    """
    return await _variant_member_suffix.get()


async def get_variant_group_subject_ids(subject: Subject) -> list[int]:
    """subjectが末尾バリアントグループ（例: 生物学各論A1/A2/C1/C2）に属する場合、
    グループ内の全subject_idを返す。属さない場合は[subject.id]のみを返す。
    レビュー閲覧統合(routers/liff_api.py _group_subject_ids)とレビュー投稿の重複防止・
    募集枠共有(get_review_remaining_cached()、routers/review_submit_api.py)の両方が
    同じグループ判定を使うための共通実装（2026-09-01、両者が別々にロジックを持つと
    line_bot/handler.py同様の同期漏れが起きうるため一本化）。

    専門科目（REVIEW_SUBMISSION_SENMON_CATEGORY、共通専門基礎含む）は2026-09-08の
    ユーザー指示で、フォーム候補の統合表示・投稿の重複防止/募集枠共有を管理画面の科目一覧
    （compute_variant_display_groups()）と完全一致させることにしたため、専門科目だけは
    get_senmon_variant_group_cached()（＝compute_variant_display_groups()の結果）を使う。
    教養科目は従来通りcompute_variant_groups()（variant_map）を使う。"""
    _, all_courses = await get_courses_cached()
    if (subject.category or "") == REVIEW_SUBMISSION_SENMON_CATEGORY:
        senmon_group = await get_senmon_variant_group_cached()
        g = senmon_group.get(subject.id)
        return list(g[2]) if g else [subject.id]
    variant_map = await get_variant_map_cached()
    label = variant_map.get(subject.name, "")
    if not label:
        ids = [subject.id]
    else:
        # compute_variant_groups()はラベル文字列（ベース名）しか返さないため、別学部の科目が
        # 偶然同じベース名グループを持つ場合の誤統合を避け、対象subjectと同じfaculty/departmentの
        # 科目だけに絞り込む（liff_api.py _group_subject_ids参照）。
        # さらに遠隔クラス（「（遠隔）」タグ付き）と対面クラスは授業形態が異なるため
        # 別グループとして扱う（compute_variant_bases()のnum_basesはタグ完全一致でグループ化
        # しているが、compute_variant_groups()が返すベース名ラベルはタグ抜きで遠隔/対面が
        # 同じ文字列になるため、ここでラベル一致に加えてタグの有無も揃える。2026-09-09、
        # 教養(外国語第1)のAcademic English等で遠隔・対面のレビューが1つのLIFFページに
        # 混在していた不具合の修正。[[project_remote_variant_group_split_20260831]]）
        ids = [
            c.id for c in all_courses
            if variant_map.get(c.name) == label
            and (c.faculty or "") == (subject.faculty or "")
            and (c.department or "") == (subject.department or "")
            and is_remote_tagged(c.name) == is_remote_tagged(subject.name)
        ]

    # 医学部保健学科の4専攻（看護学/理学療法学/作業療法学/検査技術科学）は、専攻ごとに
    # departmentが異なる別Subjectとして登録されているため、上記の同一department絞り込みでは
    # 統合されない。科目名が完全一致する専攻横断科目はレビューを共有する恒常ルール
    # （2026-09-06、ユーザー指示）のため、ここだけ同一department縛りを外して合流させる。
    if is_hoken_gakka_senko(subject.faculty or "", subject.department or ""):
        cross_ids = {
            c.id for c in all_courses
            if c.name == subject.name and is_hoken_gakka_senko(c.faculty or "", c.department or "")
        }
        if len(cross_ids) > 1:
            ids = sorted(set(ids) | cross_ids)
    return ids


def _longest_common_prefix(strings: list[str]) -> str:
    if not strings:
        return ""
    lo, hi = min(strings), max(strings)
    for i, ch in enumerate(lo):
        if i >= len(hi) or hi[i] != ch:
            return lo[:i]
    return lo


async def _fetch_senmon_variant_group() -> dict[int, tuple[str, str, list[int]]]:
    _, all_courses = await get_courses_cached()
    _excluded_names = frozenset(c.name for c in all_courses if c.variant_merge_excluded)
    label_by_name = compute_variant_display_groups(
        [(c.name, c.classification or "") for c in all_courses
         if (c.classification or "") not in CLASSIFICATION_MERGE_EXCLUDED],
        extra_excluded_names=_excluded_names,
    )
    members_by_label: dict[tuple[str, str], list] = defaultdict(list)
    for c in all_courses:
        label = label_by_name.get((c.name, c.classification or ""))
        if label:
            members_by_label[(label, c.classification or "")].append(c)

    result: dict[int, tuple[str, str, list[int]]] = {}
    for (label, _cls), members in members_by_label.items():
        if len(members) < 2:
            continue
        if not any((m.category or "") == REVIEW_SUBMISSION_SENMON_CATEGORY for m in members):
            continue
        ids = sorted(m.id for m in members)
        base = _longest_common_prefix([m.name for m in members]) or label
        for m in members:
            result[m.id] = (base, label, ids)
    return result


_senmon_variant_group = _TTLCache(
    _COURSE_CACHE_TTL, _fetch_senmon_variant_group, lambda v: v is not None, lambda: None)


async def get_senmon_variant_group_cached() -> dict[int, tuple[str, str, list[int]]]:
    """専門科目（REVIEW_SUBMISSION_SENMON_CATEGORY、共通専門基礎含む）向けの
    subject_id → (共通プレフィックス, 管理画面と同一のグループ表示ラベル, グループ内全subject_id)。
    グループ（メンバー2件以上）に属さない専門科目はマップに含めない。

    レビュー投稿フォームの専門科目候補の統合表示、および投稿の重複防止・募集枠共有を、
    管理画面の科目一覧（routers/admin/courses.py が compute_variant_display_groups() で
    生成する統合グループ）と完全に一致させるための派生キャッシュ（2026-09-08、ユーザー指示）。
    従来フォームが使っていた compute_variant_groups() は、
    (1) 末尾アルファベットのみのバリアント（例: 国際人間科学部 Academic Writing（英）A/B）を
        統合せず、A/B を別々に投稿できてしまっていた、
    (2) グループラベルがタグ抜きのベース名だったため「線形代数(1/2)」と
        「線形代数(1/2)(再履修)」がフロント側で同一ラベル文字列になり1セットに混在していた、
    という2つの不具合があった。管理画面と同じ compute_variant_display_groups() は
    (科目名, classification) 単位・タグ完全一致でグループ化し、MANUAL_VARIANT_GROUPS や
    LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONS も反映するため、両方が解消する。
    教養科目は従来どおり compute_variant_groups()（get_variant_map_cached()）を使う。

    管理画面と完全一致させるため、compute_variant_display_groups() には
    routers/admin/courses.py と同一の入力（全科目・CLASSIFICATION_MERGE_EXCLUDED 除外・
    variant_merge_excluded の動的除外）を渡し、結果を (label, classification) 単位で束ねてから
    専門科目メンバーを含むグループだけを残す。"""
    return await _senmon_variant_group.get()


async def _fetch_letter_view_group() -> dict[str, tuple[str, list[str], dict[str, str]]]:
    _, all_courses = await get_courses_cached()
    by_cls: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for c in all_courses:
        if (c.classification or "") in LETTER_ONLY_VIEW_MERGE_CLASSIFICATIONS:
            by_cls[c.classification].append((c.name, c.faculty or "", c.department or ""))
    result: dict[str, tuple[str, list[str], dict[str, str]]] = {}
    for names_fd in by_cls.values():
        result.update(compute_letter_view_groups(names_fd))
    return result


_letter_view_group = _TTLCache(
    _COURSE_CACHE_TTL, _fetch_letter_view_group, lambda v: v is not None, lambda: None)


async def get_letter_view_group_cached() -> dict[str, tuple[str, list[str], dict[str, str]]]:
    """LETTER_ONLY_VIEW_MERGE_CLASSIFICATIONS（教養科目の人文/社会/自然/総合）向け。
    科目名 → (グループベースラベル, グループ内科目名リスト(A→B→C順), 科目名→letterの辞書)
    のマップ（compute_letter_view_groups()の結果）。

    get_variant_map_cached()（レビュー投稿・募集枠共有）とは意図的に別系統のキャッシュ。
    routers/liff_api.py `_group_subject_ids()`がレビュー"閲覧"のみをA/B全体でまとめるために使う
    （core.subject_variants.LETTER_ONLY_VIEW_MERGE_CLASSIFICATIONS docstring参照）。
    """
    return await _letter_view_group.get()


async def _fetch_variant_full_label_map() -> dict[str, str]:
    _, all_courses = await get_courses_cached()
    _letter_split_excluded_names = frozenset(
        c.name for c in all_courses if (c.classification or "") in LETTER_SPLIT_EXCLUDED_CLASSIFICATIONS)
    _num_excluded_names = NUM_MERGE_EXCLUDED_NAMES | frozenset(
        c.name for c in all_courses if c.variant_merge_excluded)
    return compute_variant_full_labels(
        [(c.name, c.faculty or "", c.department or "") for c in all_courses
         if (c.classification or "") not in CLASSIFICATION_MERGE_EXCLUDED],
        letter_split_excluded_names=_letter_split_excluded_names,
        num_excluded_names=_num_excluded_names,
    )


_variant_full_label = _TTLCache(
    _COURSE_CACHE_TTL, _fetch_variant_full_label_map, lambda v: v is not None, lambda: None)


async def get_variant_full_label_map_cached() -> dict[str, str]:
    """科目名 → 括弧付き接尾辞込みの完全なグループ表示名のマップ（compute_variant_full_labels()）。

    管理画面のレビュー科目別集計（routers/admin/reviews.py）が、末尾バリアント違いの科目
    （力学基礎1/力学基礎2等）を「力学基礎(1/2)」のようにまとめて表示するために使う。
    get_variant_map_cached()と同様、全科目走査のコストを避けるためTTLキャッシュする。
    """
    return await _variant_full_label.get()


async def _fetch_search_index() -> list[dict]:
    _, all_courses = await get_courses_cached()
    variant_map = await get_variant_map_cached()

    _label_buckets: dict[str, list] = defaultdict(list)
    for c in all_courses:
        label = variant_map.get(c.name, "")
        if label:
            _label_buckets[label].append(c)

    seen_group_keys: set[tuple[str, str, str]] = set()
    search_rows: list[dict] = []
    for c in all_courses:
        label = variant_map.get(c.name, "")
        if label:
            key = (label, c.faculty or "", c.department or "")
            if key in seen_group_keys:
                continue
            seen_group_keys.add(key)
            members = [
                m for m in _label_buckets[label]
                if (m.faculty or "") == (c.faculty or "") and (m.department or "") == (c.department or "")
            ]
            rep = min(members, key=lambda m: m.name)
            display = label
        else:
            members = [c]
            rep = c
            display = c.name
        # 区切り文字なしで連結すると、境界をまたぐ検索語が意図せずマッチしうるため、
        # 科目名には出現しない制御文字を区切りに挟む
        _parts = [display] if not label else [display] + [m.name for m in members]
        text = "\x1f".join(_parts)
        reading = "\x1f".join(m.reading or "" for m in members)
        search_rows.append({
            "display": display, "rep": rep, "members": members,
            "faculty": c.faculty or "", "department": c.department or "",
            "text": text.lower(), "reading": reading.lower(),
        })

    # 同名ベースのグループが学部違いで複数存在する場合、表示名だけでは区別できないため学部名を補う
    _display_counts: dict[str, int] = defaultdict(int)
    for r in search_rows:
        _display_counts[r["display"]] += 1
    for r in search_rows:
        if _display_counts[r["display"]] > 1:
            fac_label = f"{r['faculty']}{r['department']}"
            if fac_label:
                r["display"] = f"{r['display']}（{fac_label}）"

    return search_rows


_search_index = _TTLCache(_COURSE_CACHE_TTL, _fetch_search_index, lambda v: v is not None, lambda: None)


async def get_search_index_cached() -> list[dict]:
    """LINE bot自由入力検索(line_bot.handler._handle_course_search)向けの検索行インデックス。

    バリアントグループ化（同一グループの代表科目への集約）・同名科目の学部名による表示名の
    曖昧さ解消は検索語に依存しない前処理だが、従来は自由入力メッセージが来るたびに
    course_sections全件（5000件超）に対しこの前処理を毎回再計算しており、1メッセージあたり
    数十msの同期CPU処理としてイベントループを塞いでいた（2026-09-03、レイテンシ改善で導入）。
    get_variant_map_cached()と同じ理由で、科目一覧と同じTTLでキャッシュする。
    """
    return await _search_index.get()


# ── admin session revocation（core/security.pyのcheck_admin用） ──────────────
# TTLを他キャッシュ(1時間)より大幅に短くしているのは、ログアウト操作を他ワーカー
# プロセスへ迅速に反映させるため（WEB_CONCURRENCY>1構成時、単一ログアウトが
# 全ワーカーの管理者トークンを失効させるまでの遅延を許容範囲に抑える）
_ADMIN_REVOKE_CACHE_TTL = 10
_admin_revoke_epoch: float = 0.0
_admin_revoke_epoch_at: float | None = None


async def get_admin_revoke_epoch_cached() -> float:
    global _admin_revoke_epoch, _admin_revoke_epoch_at
    if _admin_revoke_epoch_at is not None and time.monotonic() - _admin_revoke_epoch_at < _ADMIN_REVOKE_CACHE_TTL:
        return _admin_revoke_epoch
    from models import AdminSession
    try:
        async with AsyncSessionLocal() as s:
            row = (await s.execute(
                select(AdminSession.revoked_before).where(AdminSession.id == 1)
            )).scalar_one_or_none()
    except Exception:
        # 修正理由: 一括ログアウト機構はあくまで追加の安全策であり、DB一時障害時に
        # 管理画面全体を巻き添えでログイン不能にしてはならない(フェイルオープン)。
        # 直近の成功値を維持しつつ、次のTTL経過後に再試行する
        _admin_revoke_epoch_at = time.monotonic()
        return _admin_revoke_epoch
    _admin_revoke_epoch = row.timestamp() if row else 0.0
    _admin_revoke_epoch_at = time.monotonic()
    return _admin_revoke_epoch


def invalidate_admin_revoke_cache() -> None:
    global _admin_revoke_epoch_at
    _admin_revoke_epoch_at = None


# ── BAN状態キャッシュ（core/moderation.py用） ──────────────────────────
# registration_completeとは異なりBAN→解除の双方向遷移があるため、片方向の
# 「一度Trueなら覚え続ける」パターンは使えない。TTLを短くし、かつ管理画面での
# BAN/解除操作の直後にinvalidate_ban_cache()を呼んで反映遅延を抑える設計にする
_BAN_STATUS_CACHE_TTL = 60
_ban_status_cache: dict[str, tuple[bool, float]] = {}


async def get_ban_status_cached(line_user_id: str) -> bool:
    cached = _ban_status_cache.get(line_user_id)
    if cached is not None:
        banned, at = cached
        if time.monotonic() - at < _BAN_STATUS_CACHE_TTL:
            return banned
    from models import UserProfile
    async with AsyncSessionLocal() as s:
        profile = await s.get(UserProfile, line_user_id)
    banned = bool(profile and profile.banned_at is not None)
    _ban_status_cache[line_user_id] = (banned, time.monotonic())
    return banned


def invalidate_ban_cache(line_user_id: str) -> None:
    """管理画面のBAN/解除操作の直後に呼ぶ。呼び忘れると最大_BAN_STATUS_CACHE_TTL秒古い状態が使われる。"""
    _ban_status_cache.pop(line_user_id, None)
    _linebot_user_state_cache.pop(line_user_id, None)


# ── LINE bot 受信イベント処理用 ユーザー状態スナップショット ────────────────
# レビュー閲覧メニュー等の操作のたびに、line_bot/handler.py の _user_banned /
# _registration_incomplete / _get_unlocked_subject_ids がそれぞれ別々の
# AsyncSessionLocal() で UserProfile / subject_unlocks へ往復していた。
# Render(シンガポール)⇄Supabase(日本)間はDB1往復のコストが高く、科目一覧等の
# 共有キャッシュがヒットしていても、その手前のこれらの往復ぶんの待ちが体感遅延に
# 直結していた（2026-09-08、レイテンシ改善）。3つの判定材料を1セッションでまとめて
# 取得し、短いTTLでプロセス内キャッシュする。1回の操作バーストで往復は最大1本、
# TTL内の連続タップでは0本になる。
# - banned はBAN→解除の双方向遷移があるため長いTTLは不可。管理画面のBAN/解除操作は
#   invalidate_ban_cache() が本キャッシュも落とす
# - unlocked_subject_ids はレビュー閲覧権の解除操作
#   （routers/liff_api.py /api/course/{id}/unlock）の直後に
#   invalidate_linebot_user_state() でキャッシュを落とすため取りこぼさない
_LINEBOT_USER_STATE_TTL = 60
_linebot_user_state_cache: dict[str, tuple[bool, bool, frozenset[int], float]] = {}


async def get_linebot_user_state_cached(line_user_id: str) -> tuple[bool, bool, frozenset[int]]:
    """(banned, registration_complete, unlocked_subject_ids) を1回のDBセッションで取得し
    _LINEBOT_USER_STATE_TTL 秒キャッシュする。line_user_id が空なら即デフォルトを返す。"""
    if not line_user_id:
        return False, False, frozenset()
    cached = _linebot_user_state_cache.get(line_user_id)
    if cached is not None and time.monotonic() - cached[3] < _LINEBOT_USER_STATE_TTL:
        return cached[0], cached[1], cached[2]
    async with AsyncSessionLocal() as s:
        profile = await s.get(UserProfile, line_user_id)
        unlocked = frozenset((await s.execute(
            select(SubjectUnlock.subject_id).where(SubjectUnlock.line_user_id == line_user_id)
        )).scalars().all())
    banned = bool(profile and profile.banned_at is not None)
    complete = is_profile_complete(profile)
    _linebot_user_state_cache[line_user_id] = (banned, complete, unlocked, time.monotonic())
    # 登録完了は一方向遷移。sticky キャッシュも温めておくと、本スナップショットのTTLが
    # 切れた後も _registration_incomplete がDBを見ずに False を返せる
    if complete:
        set_registration_complete(line_user_id)
    return banned, complete, unlocked


def invalidate_linebot_user_state(line_user_id: str) -> None:
    """レビュー閲覧権の解除直後など、次のLINE bot操作へ即時反映したいときに呼ぶ。"""
    _linebot_user_state_cache.pop(line_user_id, None)


# ── 管理画面ナビの件数バッジ（概要ダッシュボード・サイドバー共通） ──────────────
# 単独運営者が巡回すべきキュー（レビュー承認・お問い合わせ・支払い申請・エラー）の
# 待機件数。ログアウト状態確認(_ADMIN_REVOKE_CACHE_TTL=10)と同程度、操作直後の
# 反映遅延を抑えたいためTTLは短め。
_NAV_COUNTS_TTL = 30
# routers/admin/users_errors.py の _SUBMIT_DUPLICATE_ACTION_PREFIX と同じ値（循環import回避のため
# ここでも定義する）。レビュー二重送信の「既に投稿済み」拒否はテレメトリであり本物の障害ではないため、
# 「本日のエラー」バッジからは除外する。
_SUBMIT_DUPLICATE_ACTION_PREFIX = "submit_duplicate:"


async def _fetch_admin_nav_counts() -> dict:
    async with AsyncSessionLocal() as s:
        pending_reviews = (await s.execute(
            select(func.count(Review.id)).where(Review.status == ReviewStatus.PENDING)
        )).scalar_one()
        unhandled_inquiries = (await s.execute(
            select(func.count(Inquiry.id)).where(Inquiry.status == InquiryStatus.PENDING)
        )).scalar_one()
        unpaid_payments = (await s.execute(
            select(func.count(PaymentRequest.id)).where(PaymentRequest.status == PaymentRequestStatus.PENDING)
        )).scalar_one()
        today_start = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
        dup_like = ErrorLog.action.like(_SUBMIT_DUPLICATE_ACTION_PREFIX + "%")
        errors_today = (await s.execute(
            select(func.count(ErrorLog.id)).where(
                ErrorLog.created_at >= today_start,
                or_(ErrorLog.action.is_(None), ~dup_like),
                # このサービス自身のチャンネルのエラーだけ数える（ゲスト用botのエラーで本番の警告バッジが
                # 点かないようにするため。全チャンネル分は各画面のチャンネル切替で見る）
                ErrorLog.source == CHANNEL,
            )
        )).scalar_one()
    return {
        "pending_reviews": pending_reviews,
        "unhandled_inquiries": unhandled_inquiries,
        "unpaid_payments": unpaid_payments,
        "errors_today": errors_today,
    }


_nav_counts = _TTLCache(_NAV_COUNTS_TTL, _fetch_admin_nav_counts, bool, dict)


async def get_admin_nav_counts_cached() -> dict:
    """サイドバーの件数バッジ・概要ダッシュボードのKPIタイルが共通で使う、
    レビュー承認待ち・お問い合わせ未対応・支払い申請未処理・本日のエラー件数。
    管理画面のGETハンドラはこれを呼び、テンプレートへ `nav_counts` として渡す。"""
    return await _nav_counts.get()


def invalidate_admin_nav_counts_cache() -> None:
    """承認・却下・支払い処理・お問い合わせ対応などバッジに影響する操作の直後に呼ぶ
    （呼び忘れても最大_NAV_COUNTS_TTL秒で自然に解消する）。"""
    _nav_counts.invalidate()


async def warm_query_caches() -> None:
    # 修正理由: 全部を一度にasyncio.gatherすると起動時にDBセッションが同数同時に開き、
    # Supabase poolerの「セッションモード」（DATABASE_URLのポート5432、1クライアント接続＝
    # 1バックエンド固定）が持つ同時セッション数上限に達しEMAXCONNSESSIONで失敗する
    # （2026-08-31に発生確認済み）。3件ずつのバッチに分けて直列に実行することで
    # 同時に開くセッション数を抑える。
    # get_senmon_variant_group_cached は get_courses_cached の結果に相乗りする派生キャッシュ
    # （compute_variant_display_groups の全科目実行が重い）ため、courses より後ろに置く。
    _tasks = [
        get_cls_order_map(),
        get_cls_parent_map(),
        get_cls_set(),
        get_faculty_order(),
        get_courses_cached(),
        get_reviewed_cached(),
        get_all_instructors_cached(),
        get_all_review_stats_cached(),
        get_syllabus_urls_cached(),
        get_syllabus_urls_by_pair_cached(),
        get_variant_map_cached(),
        get_senmon_variant_group_cached(),
        get_ease_extremes_cached(),
        get_admin_nav_counts_cached(),
        # 自由入力検索の初回だけ約2秒かかっていた（courses/variant_mapに依存するため後ろに置く）
        get_search_index_cached(),
    ]
    _BATCH = 3
    for i in range(0, len(_tasks), _BATCH):
        await asyncio.gather(*_tasks[i:i + _BATCH])


async def refresh_query_caches() -> None:
    """TTL切れ（1時間）を待たず、warm_query_caches()と同じ対象を古い値を返し続けたまま
    再取得して差し替える。1時間ごとに最初に開いた1人が再取得待ちになるのを防ぐ。
    派生キャッシュ（senmon_variant_group・search_index）は元のcourses/variant_mapの
    再取得後に更新されるよう、依存元を先のバッチに置く。warm_query_caches同様、
    同時セッション数を抑えるため3件ずつ直列に実行する。"""
    batches = [
        (_cls_order_map, _cls_parent_map, _cls_set),
        (_faculty_order, _courses, _reviewed),
        (_all_instructors, _all_review_stats, _syllabus_urls),
        (_syllabus_urls_by_pair, _variant_map, _ease_extremes),
        (_senmon_variant_group, _search_index),
    ]
    for batch in batches:
        await asyncio.gather(*(c.refresh() for c in batch))
