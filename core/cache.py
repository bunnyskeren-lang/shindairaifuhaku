import time

from sqlalchemy import func, select

from core.config import EASE_ORDER, make_syllabus_url
from database import AsyncSessionLocal
from models import CourseSection, DisplayOrder, Instructor, Review, Subject, Syllabus

_CLS_CACHE_TTL = 3600
_COURSE_CACHE_TTL = 3600
_COURSE_FLEX_TTL = 3600
_COURSE_LIST_TTL = 3600
_RANKING_TTL = 3600

# ── classification caches ───────────────────────────────────────
_cls_order_map_cache: dict = {}
_cls_order_map_at: float = 0.0
_cls_parent_map_cache: dict = {}
_cls_parent_map_at: float = 0.0
_cls_cache: set[str] = set()
_cls_cache_at: float = 0.0


async def get_cls_order_map() -> dict:
    global _cls_order_map_cache, _cls_order_map_at
    if _cls_order_map_cache and time.monotonic() - _cls_order_map_at < _CLS_CACHE_TTL:
        return _cls_order_map_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(DisplayOrder).where(DisplayOrder.kind == "classification").order_by(DisplayOrder.sort_order)
        )).scalars().all()
    _cls_order_map_cache = {r.name: r.sort_order for r in rows}
    _cls_order_map_at = time.monotonic()
    return _cls_order_map_cache


async def get_cls_parent_map() -> dict[str, str]:
    global _cls_parent_map_cache, _cls_parent_map_at
    if _cls_parent_map_cache and time.monotonic() - _cls_parent_map_at < _CLS_CACHE_TTL:
        return _cls_parent_map_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(DisplayOrder.name, DisplayOrder.parent_group)
            .where(DisplayOrder.kind == "classification")
            .where(DisplayOrder.parent_group.isnot(None))
            .where(DisplayOrder.parent_group != "")
        )).all()
    _cls_parent_map_cache = {r.name: r.parent_group for r in rows}
    _cls_parent_map_at = time.monotonic()
    return _cls_parent_map_cache


_faculty_order_cache: list = []
_faculty_order_at: float = 0.0


async def get_faculty_order() -> list[str]:
    global _faculty_order_cache, _faculty_order_at
    if _faculty_order_cache and time.monotonic() - _faculty_order_at < _CLS_CACHE_TTL:
        return _faculty_order_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(DisplayOrder.name).where(DisplayOrder.kind == "faculty").order_by(DisplayOrder.sort_order)
        )).scalars().all()
    _faculty_order_cache = list(rows)
    _faculty_order_at = time.monotonic()
    return _faculty_order_cache


def invalidate_faculty_order_cache():
    global _faculty_order_cache, _faculty_order_at
    _faculty_order_cache = []
    _faculty_order_at = 0.0


CREDIT_GROUP_ORDER_FALLBACK = 999999

_credit_group_order_cache: dict = {}
_credit_group_order_at: float = 0.0


async def get_credit_group_order() -> dict[tuple[str, str], int]:
    """(faculty, group_name) -> sort_order。存在しない組み合わせは呼び出し側で _CREDIT_GROUP_ORDER_FALLBACK を使うこと。"""
    global _credit_group_order_cache, _credit_group_order_at
    if _credit_group_order_cache and time.monotonic() - _credit_group_order_at < _CLS_CACHE_TTL:
        return _credit_group_order_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(DisplayOrder).where(DisplayOrder.kind == "credit_requirement_group")
        )).scalars().all()
    _credit_group_order_cache = {(r.faculty, r.name): r.sort_order for r in rows}
    _credit_group_order_at = time.monotonic()
    return _credit_group_order_cache


def invalidate_credit_group_order_cache():
    global _credit_group_order_cache, _credit_group_order_at
    _credit_group_order_cache = {}
    _credit_group_order_at = 0.0


async def get_cls_set() -> set[str]:
    global _cls_cache, _cls_cache_at
    if _cls_cache and time.monotonic() - _cls_cache_at < _CLS_CACHE_TTL:
        return _cls_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(Subject.classification).distinct())).scalars().all()
    _cls_cache = {r for r in rows if r}
    _cls_cache_at = time.monotonic()
    return _cls_cache


def invalidate_cls_caches():
    global _cls_order_map_cache, _cls_order_map_at, _cls_parent_map_cache, _cls_parent_map_at
    global _cls_cache, _cls_cache_at
    _cls_order_map_cache = {}
    _cls_order_map_at = 0.0
    _cls_parent_map_cache = {}
    _cls_parent_map_at = 0.0
    _cls_cache = set()
    _cls_cache_at = 0.0


# ── course / review caches ──────────────────────────────────────
_course_by_name: dict = {}
_course_list_all: list = []
_course_cache_at: float = 0.0

_reviewed_cache: set[str] = set()
_reviewed_cache_at: float = 0.0
_reviewed_cache_init: bool = False

_course_flex_cache: dict[int, tuple] = {}
_course_list_cache: dict[str, tuple] = {}
_ranking_cache: dict[str, tuple] = {}

_syllabus_url_cache: dict[int, str] = {}
_syllabus_url_cache_at: float = 0.0

_all_instructors_cache: dict[int, list] = {}
_all_instructors_cache_at: float = 0.0
_all_review_stats_cache: dict[str, tuple] = {}
_all_review_stats_cache_at: float = 0.0

# senmon_group キャッシュ（PDFパーサーが同期的に参照する）。経営学部の「群」（第1群等）と
# 工学部機械工学科の「必修」フラグの両方をこの1列で共有するため、(科目名, 学部) の複合キーにする
# （科目名だけをキーにすると、学部をまたいだ同名科目（例:「卒業研究」）で値が衝突するため）
_senmon_name_to_group: dict[tuple[str, str], str] = {}

# 経営学部専門科目の classification ラベル→群名。senmon_group（管理画面での手動上書き）が
# 未設定の科目は、シラバスの科目ナンバリングコード由来で既に正しく入っている classification から
# 群を自動導出する（ハードコードされた科目名リストより優先）
_KEIEI_CLASSIFICATION_TO_GROUP = {
    "第1群科目":     "第1群",
    "第2群科目":     "第2群",
    "第3群科目":     "第3群",
    "グローバル科目群": "グローバル",
}


async def get_courses_cached():
    global _course_by_name, _course_list_all, _course_cache_at
    if _course_by_name and time.monotonic() - _course_cache_at < _COURSE_CACHE_TTL:
        return _course_by_name, _course_list_all
    async with AsyncSessionLocal() as s:
        courses = (await s.execute(
            select(Subject).order_by(Subject.sort_order, Subject.name)
        )).scalars().all()
    _course_list_all = courses
    _course_by_name = {c.name: c for c in courses}
    _course_cache_at = time.monotonic()
    return _course_by_name, _course_list_all


async def get_reviewed_cached() -> set[str]:
    global _reviewed_cache, _reviewed_cache_at, _reviewed_cache_init
    if _reviewed_cache_init and time.monotonic() - _reviewed_cache_at < _COURSE_CACHE_TTL:
        return _reviewed_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(Subject.name).distinct()
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.is_approved.is_(True))
        )).scalars().all()
    _reviewed_cache = set(rows)
    _reviewed_cache_at = time.monotonic()
    _reviewed_cache_init = True
    return _reviewed_cache


async def get_all_instructors_cached() -> dict[int, list]:
    global _all_instructors_cache, _all_instructors_cache_at
    if _all_instructors_cache and time.monotonic() - _all_instructors_cache_at < _COURSE_CACHE_TTL:
        return _all_instructors_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(CourseSection, Instructor)
            .join(Instructor, Instructor.id == CourseSection.instructor_id)
            .order_by(Instructor.sort_order, Instructor.name)
        )).all()
    d: dict[int, list] = {}
    for cs, instr in rows:
        d.setdefault(cs.subject_id, []).append(instr)
    _all_instructors_cache = d
    _all_instructors_cache_at = time.monotonic()
    return _all_instructors_cache


async def get_all_review_stats_cached() -> dict[str, tuple]:
    global _all_review_stats_cache, _all_review_stats_cache_at
    if _all_review_stats_cache and time.monotonic() - _all_review_stats_cache_at < _COURSE_CACHE_TTL:
        return _all_review_stats_cache
    async with AsyncSessionLocal() as s:
        count_rows = (await s.execute(
            select(Subject.name, func.count(Review.id).label("cnt"))
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.is_approved.is_(True))
            .group_by(Subject.name)
        )).all()
        ease_rows = (await s.execute(
            select(Subject.name, Review.ease_rating, func.count(Review.id).label("cnt"))
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.is_approved.is_(True), Review.ease_rating.isnot(None))
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
    _all_review_stats_cache = result
    _all_review_stats_cache_at = time.monotonic()
    return _all_review_stats_cache


async def get_syllabus_urls_cached() -> dict[int, str]:
    global _syllabus_url_cache, _syllabus_url_cache_at
    if _syllabus_url_cache and time.monotonic() - _syllabus_url_cache_at < _COURSE_CACHE_TTL:
        return _syllabus_url_cache
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(CourseSection.subject_id, Syllabus.timetable_code, Syllabus.year, Subject.faculty, Subject.department)
            .join(Syllabus, Syllabus.course_section_id == CourseSection.id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .where(Syllabus.timetable_code.isnot(None))
        )).all()
    # 科目につき複数年度のsyllabiがありうるため、最新年度のURLを採用する
    _latest_year: dict[int, int] = {}
    result: dict[int, str] = {}
    for subject_id, code, year, faculty, department in rows:
        if subject_id in _latest_year and year <= _latest_year[subject_id]:
            continue
        url = make_syllabus_url(code, f"{faculty or ''}{department or ''}")
        if not url:
            continue
        _latest_year[subject_id] = year
        result[subject_id] = url
    _syllabus_url_cache = result
    _syllabus_url_cache_at = time.monotonic()
    return _syllabus_url_cache


async def reload_senmon_cache():
    global _senmon_name_to_group
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Subject.name, Subject.faculty, Subject.department, Subject.senmon_group, Subject.classification)
        )).all()
    mapping: dict[tuple[str, str, str], str] = {}
    for name, faculty, department, senmon_group, classification in rows:
        key = (name, faculty, department or "")
        if senmon_group:
            mapping[key] = senmon_group
        elif classification in _KEIEI_CLASSIFICATION_TO_GROUP:
            mapping[key] = _KEIEI_CLASSIFICATION_TO_GROUP[classification]
    _senmon_name_to_group = mapping


def get_senmon_group(name: str, faculty: str, department: str = "") -> str | None:
    return _senmon_name_to_group.get((name, faculty, department))


def invalidate_senmon_cache():
    global _senmon_name_to_group
    _senmon_name_to_group = {}


def invalidate_courses_cache():
    global _course_by_name, _course_list_all, _course_cache_at
    global _all_instructors_cache, _all_instructors_cache_at
    global _course_flex_cache, _course_list_cache, _ranking_cache
    global _syllabus_url_cache, _syllabus_url_cache_at
    global _preload_cache, _preload_cache_at
    _course_by_name = {}
    _course_list_all = []
    _course_cache_at = 0.0
    _all_instructors_cache = {}
    _all_instructors_cache_at = 0.0
    _course_flex_cache = {}
    _course_list_cache = {}
    _ranking_cache = {}
    # 修正理由: シラバスURL(course_sections.syllabus_url)もcourses関連の派生データのため、
    # ここで一緒に無効化しないと管理画面での追加・変更が最大TTL(1時間)反映されなかった。
    _syllabus_url_cache = {}
    _syllabus_url_cache_at = 0.0
    _preload_cache = None
    _preload_cache_at = 0.0


def invalidate_review_cache():
    global _reviewed_cache, _reviewed_cache_at, _reviewed_cache_init
    global _all_review_stats_cache, _all_review_stats_cache_at
    global _course_flex_cache, _ranking_cache, _course_list_cache
    _reviewed_cache = set()
    _reviewed_cache_at = 0.0
    _reviewed_cache_init = False
    _all_review_stats_cache = {}
    _all_review_stats_cache_at = 0.0
    _course_flex_cache = {}
    _ranking_cache = {}
    _course_list_cache = {}


# ── flex / list / ranking caches (アクセスは必ずこれらの関数経由で行う) ──

def get_flex_cache(course_id: int):
    entry = _course_flex_cache.get(course_id)
    if entry and time.monotonic() - entry[1] < _COURSE_FLEX_TTL:
        return entry[0]
    return None


def set_flex_cache(course_id: int, msg) -> None:
    _course_flex_cache[course_id] = (msg, time.monotonic())


def get_course_list_cache(key: str):
    entry = _course_list_cache.get(key)
    if entry and time.monotonic() - entry[1] < _COURSE_LIST_TTL:
        return entry[0]
    return None


def set_course_list_cache(key: str, value) -> None:
    _course_list_cache[key] = (value, time.monotonic())


def get_ranking_cache(key: str):
    entry = _ranking_cache.get(key)
    if entry and time.monotonic() - entry[1] < _RANKING_TTL:
        return entry[0]
    return None


def set_ranking_cache(key: str, value) -> None:
    _ranking_cache[key] = (value, time.monotonic())


# ── registration completeness cache（LINE bot応答パスの毎メッセージDB往復を回避） ──
# 一度登録完了したユーザーが未完了に戻ることは無い（管理画面にリセット機能も無い）ため、
# True確定分はTTL内であればDBを一切見ずに返す。False/未登録は毎回DBを見て最新状態を反映する。
_REGISTRATION_COMPLETE_TTL = 3600
_registration_complete_at: dict[str, float] = {}


def get_registration_complete_cached(user_id: str) -> bool:
    ts = _registration_complete_at.get(user_id)
    return ts is not None and time.monotonic() - ts < _REGISTRATION_COMPLETE_TTL


def set_registration_complete(user_id: str) -> None:
    _registration_complete_at[user_id] = time.monotonic()


# ── /api/preload レスポンスキャッシュ ──
# get_courses_cached/get_all_instructors_cachedからの構築自体は軽いが、
# 全科目・全教員（数千件規模）をループするため、リクエストの都度組み立てず結果をキャッシュする
_PRELOAD_TTL = 3600
_preload_cache: dict | None = None
_preload_cache_at: float = 0.0


def get_preload_cache() -> dict | None:
    if _preload_cache is not None and time.monotonic() - _preload_cache_at < _PRELOAD_TTL:
        return _preload_cache
    return None


def set_preload_cache(data: dict) -> None:
    global _preload_cache, _preload_cache_at
    _preload_cache = data
    _preload_cache_at = time.monotonic()


async def warm_query_caches() -> None:
    import asyncio
    await asyncio.gather(
        get_cls_order_map(),
        get_cls_parent_map(),
        get_cls_set(),
        get_faculty_order(),
        get_credit_group_order(),
        get_courses_cached(),
        get_reviewed_cached(),
        get_all_instructors_cached(),
        get_all_review_stats_cached(),
        get_syllabus_urls_cached(),
    )
