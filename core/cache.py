import time

from sqlalchemy import func, select

from core.config import EASE_ORDER
from database import AsyncSessionLocal
from models import CourseSection, DisplayOrder, Instructor, Review, Subject

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

# senmon_group キャッシュ（PDFパーサーが同期的に参照する）
_senmon_name_to_group: dict[str, str] = {}


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
            .where(Review.is_approved == True)
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
            .where(Review.is_approved == True)
            .group_by(Subject.name)
        )).all()
        ease_rows = (await s.execute(
            select(Subject.name, Review.ease_rating, func.count(Review.id).label("cnt"))
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.is_approved == True, Review.ease_rating.isnot(None))
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
            select(CourseSection.subject_id, CourseSection.syllabus_url)
            .where(CourseSection.syllabus_url.isnot(None))
        )).all()
    _syllabus_url_cache = {sid: url for sid, url in rows}
    _syllabus_url_cache_at = time.monotonic()
    return _syllabus_url_cache


async def reload_senmon_cache():
    global _senmon_name_to_group
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Subject.name, Subject.senmon_group).where(Subject.senmon_group.isnot(None))
        )).all()
    _senmon_name_to_group = {r[0]: r[1] for r in rows}


def get_senmon_group(name: str) -> str | None:
    return _senmon_name_to_group.get(name)


def invalidate_senmon_cache():
    global _senmon_name_to_group
    _senmon_name_to_group = {}


def invalidate_courses_cache():
    global _course_by_name, _course_list_all, _course_cache_at
    global _all_instructors_cache, _all_instructors_cache_at
    global _course_flex_cache, _course_list_cache, _ranking_cache
    _course_by_name = {}
    _course_list_all = []
    _course_cache_at = 0.0
    _all_instructors_cache = {}
    _all_instructors_cache_at = 0.0
    _course_flex_cache = {}
    _course_list_cache = {}
    _ranking_cache = {}


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
