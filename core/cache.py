import time

from sqlalchemy import func, select

from core.config import EASE_ORDER, MAX_REVIEWS_PER_COURSE_SECTION, make_syllabus_url
from core.subject_variants import (
    CLASSIFICATION_MERGE_EXCLUDED,
    compute_variant_full_labels,
    compute_variant_groups,
)
from database import AsyncSessionLocal
from models import CourseSection, DisplayOrder, Instructor, Review, ReviewStatus, Subject, Syllabus

# 全キャッシュ共通のTTLポリシー(1時間)。用途別に名前を分けているが値は全て同じであるべきなので、
# ここ1箇所を直せば全キャッシュに反映される(個別に変えたい場合のみ該当行だけ上書きする)
_DEFAULT_CACHE_TTL = 3600
_CLS_CACHE_TTL = _DEFAULT_CACHE_TTL
_COURSE_CACHE_TTL = _DEFAULT_CACHE_TTL
_COURSE_FLEX_TTL = _DEFAULT_CACHE_TTL
_COURSE_LIST_TTL = _DEFAULT_CACHE_TTL

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

_syllabus_url_cache: dict[int, str] = {}
_syllabus_url_cache_at: float = 0.0

_all_instructors_cache: dict[int, list] = {}
_all_instructors_cache_at: float = 0.0
_all_review_stats_cache: dict[str, tuple] = {}
_all_review_stats_cache_at: float = 0.0


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
            .where(Review.status == ReviewStatus.APPROVED)
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
    _all_review_stats_cache = result
    _all_review_stats_cache_at = time.monotonic()
    return _all_review_stats_cache


_full_pairs_cache: dict[tuple[int, str], int] | None = None
_full_pairs_cache_at: float = 0.0


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
    global _full_pairs_cache, _full_pairs_cache_at
    if _full_pairs_cache is not None and time.monotonic() - _full_pairs_cache_at < _COURSE_CACHE_TTL:
        return _full_pairs_cache
    async with AsyncSessionLocal() as s:
        cs_rows = (await s.execute(
            select(CourseSection.subject_id, Instructor.name)
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
    group_key_by_sid: dict[int, tuple] = {}
    for c in all_courses:
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
    for sid, name in cs_rows:
        gkey = group_key_by_sid.get(sid)
        total = group_totals.get((gkey, name), 0) if gkey else counts.get((sid, name), 0)
        result[(sid, name)] = max(0, MAX_REVIEWS_PER_COURSE_SECTION - total)
    _full_pairs_cache = result
    _full_pairs_cache_at = time.monotonic()
    return _full_pairs_cache


def invalidate_full_pairs_cache():
    global _full_pairs_cache, _full_pairs_cache_at
    _full_pairs_cache = None
    _full_pairs_cache_at = 0.0


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


def invalidate_courses_cache():
    global _course_by_name, _course_list_all, _course_cache_at
    global _all_instructors_cache, _all_instructors_cache_at
    global _course_flex_cache, _course_list_cache
    global _syllabus_url_cache, _syllabus_url_cache_at
    global _preload_cache, _preload_cache_at
    global _variant_map_cache, _variant_map_cache_at
    global _variant_full_label_cache, _variant_full_label_cache_at
    _course_by_name = {}
    _course_list_all = []
    _course_cache_at = 0.0
    _all_instructors_cache = {}
    _all_instructors_cache_at = 0.0
    _course_flex_cache = {}
    _course_list_cache = {}
    # 修正理由: シラバスURL(course_sections.syllabus_url)もcourses関連の派生データのため、
    # ここで一緒に無効化しないと管理画面での追加・変更が最大TTL(1時間)反映されなかった。
    _syllabus_url_cache = {}
    _syllabus_url_cache_at = 0.0
    _preload_cache = None
    _preload_cache_at = 0.0
    # 語尾バリアントグループ(compute_variant_groups)も科目一覧に依存する派生データのため、
    # ここで一緒に無効化する
    _variant_map_cache = None
    _variant_map_cache_at = 0.0
    _variant_full_label_cache = None
    _variant_full_label_cache_at = 0.0


def invalidate_review_cache():
    global _reviewed_cache, _reviewed_cache_at, _reviewed_cache_init
    global _all_review_stats_cache, _all_review_stats_cache_at
    global _course_flex_cache, _course_list_cache
    _reviewed_cache = set()
    _reviewed_cache_at = 0.0
    _reviewed_cache_init = False
    _all_review_stats_cache = {}
    _all_review_stats_cache_at = 0.0
    _course_flex_cache = {}
    _course_list_cache = {}
    invalidate_full_pairs_cache()


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


_variant_map_cache: dict[str, str] | None = None
_variant_map_cache_at: float = 0.0


async def get_variant_map_cached() -> dict[str, str]:
    """科目名 → 語尾バリアントグループのベース名ラベルのマップ（compute_variant_groups()の結果）。

    レビュー閲覧権チケットのグループ判定(routers/liff_api.py _group_subject_ids)から
    リクエストの都度呼ばれるが、計算自体は全科目（数千件規模）を走査する正規表現マッチのため、
    科目一覧と同じTTLでキャッシュし毎リクエストの再計算を避ける。
    """
    global _variant_map_cache, _variant_map_cache_at
    if _variant_map_cache is not None and time.monotonic() - _variant_map_cache_at < _COURSE_CACHE_TTL:
        return _variant_map_cache
    _, all_courses = await get_courses_cached()
    _variant_map_cache = compute_variant_groups(
        [(c.name, c.faculty or "", c.department or "") for c in all_courses
         if (c.classification or "") not in CLASSIFICATION_MERGE_EXCLUDED],
    )
    _variant_map_cache_at = time.monotonic()
    return _variant_map_cache


async def get_variant_group_subject_ids(subject: Subject) -> list[int]:
    """subjectが末尾バリアントグループ（例: 生物学各論A1/A2/C1/C2）に属する場合、
    グループ内の全subject_idを返す。属さない場合は[subject.id]のみを返す。
    レビュー閲覧統合(routers/liff_api.py _group_subject_ids)とレビュー投稿の重複防止・
    募集枠共有(get_review_remaining_cached()、routers/review_submit_api.py)の両方が
    同じグループ判定を使うための共通実装（2026-09-01、両者が別々にロジックを持つと
    line_bot/handler.py同様の同期漏れが起きうるため一本化）。"""
    _, all_courses = await get_courses_cached()
    variant_map = await get_variant_map_cached()
    label = variant_map.get(subject.name, "")
    if not label:
        return [subject.id]
    # compute_variant_groups()はラベル文字列（ベース名）しか返さないため、別学部の科目が
    # 偶然同じベース名グループを持つ場合の誤統合を避け、対象subjectと同じfaculty/departmentの
    # 科目だけに絞り込む（liff_api.py _group_subject_ids参照）
    return [
        c.id for c in all_courses
        if variant_map.get(c.name) == label
        and (c.faculty or "") == (subject.faculty or "")
        and (c.department or "") == (subject.department or "")
    ]


_variant_full_label_cache: dict[str, str] | None = None
_variant_full_label_cache_at: float = 0.0


async def get_variant_full_label_map_cached() -> dict[str, str]:
    """科目名 → 括弧付き接尾辞込みの完全なグループ表示名のマップ（compute_variant_full_labels()）。

    管理画面のレビュー科目別集計（routers/admin/reviews.py）が、末尾バリアント違いの科目
    （力学基礎1/力学基礎2等）を「力学基礎(1/2)」のようにまとめて表示するために使う。
    get_variant_map_cached()と同様、全科目走査のコストを避けるためTTLキャッシュする。
    """
    global _variant_full_label_cache, _variant_full_label_cache_at
    if _variant_full_label_cache is not None and time.monotonic() - _variant_full_label_cache_at < _COURSE_CACHE_TTL:
        return _variant_full_label_cache
    _, all_courses = await get_courses_cached()
    _variant_full_label_cache = compute_variant_full_labels(
        [(c.name, c.faculty or "", c.department or "") for c in all_courses
         if (c.classification or "") not in CLASSIFICATION_MERGE_EXCLUDED],
    )
    _variant_full_label_cache_at = time.monotonic()
    return _variant_full_label_cache


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


async def warm_query_caches() -> None:
    import asyncio
    await asyncio.gather(
        get_cls_order_map(),
        get_cls_parent_map(),
        get_cls_set(),
        get_faculty_order(),
        get_courses_cached(),
        get_reviewed_cached(),
        get_all_instructors_cached(),
        get_all_review_stats_cached(),
        get_syllabus_urls_cached(),
        get_variant_map_cached(),
    )
