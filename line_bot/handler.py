import asyncio
import math
import re as _re
import time
from collections import defaultdict

from linebot.v3.messaging import (
    FlexBubble,
    FlexBox,
    FlexButton,
    FlexCarousel,
    FlexMessage,
    FlexSeparator,
    FlexText,
    PostbackAction,
    TextMessage,
    URIAction,
)
from linebot.v3.webhooks import FollowEvent, MessageEvent, PostbackEvent, TextMessageContent
from sqlalchemy import func, select

from core import cache, line_client
from core.activity_log import save_error_log, save_log_bg
from core.config import (
    APP_URL,
    EASE_ORDER,
    EASE_STARS,
    RICHMENU_ID_PREREGISTER,
    REVIEW_FORM_URL,
    is_profile_complete,
    make_cls_sort,
    make_register_url,
    stars,
)
from database import AsyncSessionLocal
from line_bot.flex_builders import (
    get_course_flex,
    make_category_select_flex,
    make_classification_select_flex,
    make_help_flex,
    make_no_review_flex,
    make_ranking_bubble,
    make_registration_flex,
    make_variant_selection_bubble,
)
from models import CourseSection, Review, ReviewStatus, Subject, UserProfile


async def _registration_incomplete(user_id: str) -> bool:
    if cache.get_registration_complete_cached(user_id):
        return False
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, user_id)
        complete = is_profile_complete(profile)
    if complete:
        cache.set_registration_complete(user_id)
    return not complete

# ── 科目名の末尾「文字+数字」バリアント判定 ────────────────────────
# アルファベットや数字のみが異なる科目（ベースの漢字部分が完全一致するもの）は
# 1行にまとめて選択バブル表示する。全角文字（Ｄ等）はASCIIに正規化して判定する。
# 専門科目は末尾が全角ローマ数字（Ⅰ/Ⅱ/Ⅲ...）の命名が主流なため、数字と同様に扱う。
_FULLWIDTH_UPPER = str.maketrans(
    "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
)
_ROMAN_VAL = {chr(0x2160 + i): i + 1 for i in range(12)}  # Ⅰ→1 ... Ⅻ→12
_VNUM = _re.compile(r'^(.*?)[\s　]*([A-ZＡ-Ｚ])?(\d+|[Ⅰ-Ⅻ])$')


def _vnum_match(name: str) -> tuple[str, str, int, str] | None:
    """戻り値: (base, letter, sort_key, disp) の4-tuple。
    disp は表示用の末尾テキスト（ローマ数字はそのまま、数字はASCIIに正規化）。"""
    m = _VNUM.match(name)
    if not m:
        return None
    base = m.group(1).strip()
    letter = (m.group(2) or "").translate(_FULLWIDTH_UPPER)
    raw = m.group(3)
    if raw in _ROMAN_VAL:
        sk = _ROMAN_VAL[raw]
        disp = raw
    else:
        sk = int(raw)
        disp = str(sk)
    return base, letter, sk, disp


# ── Course list carousel ────────────────────────────────────────

# 1回の返信で送れる科目数には上限（40バブル≒240科目、_split_to_bubbles/messages[:5]参照）がある。
# 教養科目・専門科目のimportではclassificationが学部単位で1つにまとまりがちで、学部によっては
# それだけで240件を超え、上限を超えた科目が黙って表示されなくなる問題があった（例:
# 国際人間科学部1005件のうち240件しか表示されない）。この閾値を超える場合は先によみがな順の
# 均等な範囲選択メニューを挟み、どの学部・分類で科目を追加してもこの安全装置が自動で働くように
# する（50音の行（あ行/か行等）で単純に区切ると「科学」「社会」等の頻出語でか行・さ行だけ
# 突出して偏るため、行ではなく件数で均等分割する）。
_ALPHA_SPLIT_THRESHOLD = 48
_ALPHA_CHUNK_SIZE = 50


def _reading_key(subj) -> str:
    return (subj.reading or "").strip() or (subj.name or "")


async def handle_course_list(category: str = "", classification: str = "", faculty: str = "",
                              department: str = "", reading_row: str = "") -> list:
    _cl_key = f"{category}:{classification}:{faculty}:{department}:{reading_row}"
    _cached = cache.get_course_list_cache(_cl_key)
    if _cached is not None:
        return _cached

    cls_map, (_, _all_c), reviewed_names = await asyncio.gather(
        cache.get_cls_order_map(), cache.get_courses_cached(), cache.get_reviewed_cached()
    )
    _cls_sort = make_cls_sort(cls_map)
    rows = [c for c in _all_c if
            (not category or c.category == category) and
            (not classification or c.classification == classification) and
            (not faculty or c.faculty == faculty) and
            (not department or (c.department or "") == department)]

    if not reading_row and len(rows) > _ALPHA_SPLIT_THRESHOLD:
        if faculty and not classification:
            _row_prefix = f"専門F:{faculty}|{department}"
        elif classification and category:
            _row_prefix = f"{category}:{classification}"
        elif classification:
            _row_prefix = classification
        else:
            _row_prefix = None
        if _row_prefix:
            by_reading = sorted(rows, key=_reading_key)
            chunks = [by_reading[i:i + _ALPHA_CHUNK_SIZE] for i in range(0, len(by_reading), _ALPHA_CHUNK_SIZE)]
            items = []
            for i, chunk in enumerate(chunks):
                first_ch = _reading_key(chunk[0])[:1] or "?"
                last_ch = _reading_key(chunk[-1])[:1] or "?"
                label = f"{first_ch}〜{last_ch}" if first_ch != last_ch else first_ch
                items.append((f"{label}（{len(chunk)}件）", f"{_row_prefix}::R:{i}"))
            result = [make_classification_select_flex(
                items, set(),
                title="📚 科目一覧",
                subtitle=f"{len(rows)}件あります。よみがな順で絞り込んでください",
                header_color="#6366f1",
            )]
            cache.set_course_list_cache(_cl_key, result)
            return result

    if reading_row:
        try:
            _idx = int(reading_row)
        except ValueError:
            _idx = -1
        by_reading = sorted(rows, key=_reading_key)
        rows = by_reading[_idx * _ALPHA_CHUNK_SIZE:(_idx + 1) * _ALPHA_CHUNK_SIZE] if _idx >= 0 else []

    rows = sorted(rows, key=lambda c: (_cls_sort(c.classification or ""), c.sort_order, c.name or ""))

    if not rows:
        if faculty:
            label = f"{faculty}の"
        elif classification:
            label = f"{classification}の"
        elif category:
            label = f"{category}の"
        else:
            label = ""
        return [TextMessage(text=f"まだ{label}科目が登録されていません。")]

    course_name_set = {c.name for c in rows}
    seen_base: set[str] = set()

    # Pre-compute numeric variant groups: ベースの漢字部分が同じで、末尾のアルファベット・
    # 数字だけが違う科目（例: 生物学各論A1/A2/C1/C2、微分積分1/2/3/4）を1行にまとめる。
    # classificationが異なれば別学部・別学科の同名ベース科目（例: 工学部「制御工学Ⅰ/Ⅱ」と
    # システム情報学部「制御工学1/2」）である可能性が高いため、base名だけでなく
    # classification単位でグループを分ける
    _num_bases: dict[tuple[str, str], list[tuple[str, str, int, str]]] = defaultdict(list)
    for _c in rows:
        _match = _vnum_match(_c.name)
        if _match:
            _b, _letter, _sk, _disp = _match
            _num_bases[(_b, _c.classification or "その他")].append((_c.name, _letter, _sk, _disp))
    _num_variant_names = {n for _items in _num_bases.values() if len(_items) >= 2 for n, _, _, _ in _items}
    _num_base_for = {n: _key for _key, _items in _num_bases.items() if len(_items) >= 2 for n, _, _, _ in _items}
    seen_num_base: set[tuple[str, str]] = set()

    # Pre-compute seminar variant groups e.g. 外国語セミナーA(英語) → 外国語セミナー(英語) (A/B/C/D)
    _VSEM = _re.compile(r'^(.*?セミナー)([A-Z]|\d+)(\([^)]+\))$')
    _sem_bases: dict[str, list] = defaultdict(list)
    for _c in rows:
        _m = _VSEM.match(_c.name)
        if _m:
            _base_lang = _m.group(1) + _m.group(3)
            _sem_bases[_base_lang].append((_c.name, _m.group(2)))
    _sem_variant_names = {n for _b, _items in _sem_bases.items() if len(_items) >= 2 for n, _ in _items}
    _sem_base_for = {n: _b for _b, _items in _sem_bases.items() if len(_items) >= 2 for n, _ in _items}
    seen_sem_base: set[str] = set()

    # syllabus_url は全件キャッシュから取得（DBアクセスなし）
    _sv_by_id = await cache.get_syllabus_urls_cached()
    course_syllabus_urls: dict[str, str] = {c.name: _sv_by_id[c.id] for c in rows if c.id in _sv_by_id}
    course_liff_urls: dict[str, str] = {c.name: f"{APP_URL}/liff/course?course_id={c.id}" for c in rows}
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    cls_category: dict[str, str] = {}
    cls_faculty: dict[str, str] = {}
    for course in rows:
        name = course.name
        cls = course.classification or "その他"
        cls_category[cls] = course.category or ""
        if course.faculty:
            cls_faculty[cls] = course.faculty
        if name in _sem_variant_names:
            base = _sem_base_for[name]
            if base not in seen_sem_base:
                seen_sem_base.add(base)
                items_sorted = sorted(_sem_bases[base], key=lambda x: x[1])
                suffix = "/".join(sk for _, sk in items_sorted)
                groups[cls].append((base, f"variant:{suffix}"))
            continue
        if name and name[-1] in ('A', 'B', 'C', 'D') and len(name) > 1:
            base = name[:-1]
            variants = [s for s in 'ABCD' if base + s in course_name_set]
            if len(variants) >= 2:
                if base not in seen_base:
                    seen_base.add(base)
                    suffix = "/".join(variants)
                    groups[cls].append((base, f"variant:{suffix}"))
                continue
        if name in _num_variant_names:
            key = _num_base_for[name]
            if key not in seen_num_base:
                seen_num_base.add(key)
                items_sorted = sorted(_num_bases[key], key=lambda x: (x[1], x[2]))
                suffix = "/".join(f"{letter}{disp}" for _, letter, _sk, disp in items_sorted)
                groups[cls].append((key[0], f"numvariant:{suffix}"))
            continue
        groups[cls].append((name, "single"))

    def _cat_order(cls: str) -> int:
        return 0 if cls_category.get(cls, "") == "教養" else 1
    all_groups = sorted(groups.items(), key=lambda x: (_cat_order(x[0]), _cls_sort(x[0])))

    def _entry_has_review(name: str, kind: str, cls: str = "") -> bool:
        if kind == "single":
            return name in reviewed_names
        if kind.startswith("variant:"):
            suffixes = kind.split(":", 1)[1].split("/")
            if any(name + s in reviewed_names for s in suffixes):
                return True
            if name in _sem_bases:
                return any(n in reviewed_names for n, _ in _sem_bases[name])
            return False
        if kind.startswith("numvariant:"):
            key = (name, cls)
            if key in _num_bases:
                return any(n in reviewed_names for n, _, _, _ in _num_bases[key])
            return False
        return False

    def _group_syllabus_url(name: str, kind: str, cls: str = "") -> str:
        # 統合表示（variant/numvariant）はbase名がそのままSubject.nameと一致しないため、
        # グループ内で表示順（最も左側）を優先し、そのシラバスURLを代表として採用する
        # （左側が無ければ右側の変種のURLで代替する）。
        if kind == "single":
            return course_syllabus_urls.get(name, "")
        if kind.startswith("variant:"):
            if name in _sem_bases:
                for n, _ in sorted(_sem_bases[name], key=lambda x: x[1]):
                    url = course_syllabus_urls.get(n, "")
                    if url:
                        return url
                return ""
            for s in kind.split(":", 1)[1].split("/"):
                url = course_syllabus_urls.get(name + s, "")
                if url:
                    return url
            return ""
        if kind.startswith("numvariant:") and (name, cls) in _num_bases:
            for n, _, _, _ in sorted(_num_bases[(name, cls)], key=lambda x: (x[1], x[2])):
                url = course_syllabus_urls.get(n, "")
                if url:
                    return url
        return ""

    def _make_bubble(classification: str, entries: list) -> FlexBubble:
        btn_contents = []
        for idx, (name, kind) in enumerate(entries):
            if kind.startswith("variant:") or kind.startswith("numvariant:"):
                suffix = kind.split(":", 1)[1]
                display = f"{name} ({suffix})"
            else:
                display = name
            has_review = _entry_has_review(name, kind, classification)
            syl_url = _group_syllabus_url(name, kind, classification)
            has_content = has_review or bool(syl_url)
            text_color = "#0f172a" if has_content else "#94a3b8"
            display_text = f"✓{display}" if has_review else display
            if kind == "single":
                liff_url = course_liff_urls.get(name, "")
            elif kind.startswith("variant:"):
                first_suffix = kind.split(":", 1)[1].split("/")[0]
                liff_url = course_liff_urls.get(name + first_suffix, "")
            elif kind.startswith("numvariant:") and (name, classification) in _num_bases:
                first_name = min(_num_bases[(name, classification)], key=lambda x: (x[1], x[2]))[0]
                liff_url = course_liff_urls.get(first_name, "")
            else:
                liff_url = ""

            name_box = FlexBox(
                layout="horizontal",
                action=PostbackAction(label=display[:20], data=name),
                contents=[FlexText(text=display_text, wrap=True, size="sm", color=text_color, flex=1)],
            )

            row_contents = [name_box]
            link_items = []
            if liff_url:
                link_items.append(FlexText(text="📝レビュー", size="xxs", color="#4f46e5", flex=0,
                                           action=URIAction(label="レビュー", uri=liff_url)))
            if syl_url:
                link_items.append(FlexText(text="📄シラバス", size="xxs", color="#2563eb", flex=0,
                                           action=URIAction(label="シラバス", uri=syl_url)))
            if link_items:
                row_contents.append(FlexBox(layout="horizontal", contents=link_items, margin="sm", spacing="md"))

            if idx > 0:
                btn_contents.append(FlexSeparator(margin="md"))
            btn_contents.append(FlexBox(
                layout="vertical",
                contents=row_contents,
                padding_top="sm",
                padding_bottom="sm",
            ))
        base_cls = classification.rstrip("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮")
        faculty_str = cls_faculty.get(base_cls, "")
        header_contents = [FlexText(text=classification, weight="bold", color="#ffffff", size="sm")]
        if faculty_str:
            header_contents.append(FlexText(text=faculty_str, size="xs", color="#c7d2fe", margin="xs"))
        return FlexBubble(
            size="kilo",
            header=FlexBox(
                layout="vertical",
                contents=header_contents,
                background_color="#6366f1",
                padding_all="md",
            ),
            body=FlexBox(
                layout="vertical",
                contents=btn_contents,
                spacing="xs",
                padding_all="md",
            ),
        )

    MAX_PER_BUBBLE = 6
    _ROMAN = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"
    bubbles: list = []

    def _split_to_bubbles(cls: str, ents: list) -> None:
        if not ents:
            return
        if len(ents) <= MAX_PER_BUBBLE:
            bubbles.append(_make_bubble(cls, ents))
            return
        chunks = [ents[i:i+MAX_PER_BUBBLE] for i in range(0, len(ents), MAX_PER_BUBBLE)]
        for i, chunk in enumerate(chunks):
            suffix = _ROMAN[i] if i < len(_ROMAN) else f"({i+1})"
            bubbles.append(_make_bubble(cls + suffix, chunk))

    for cls, ents in all_groups:
        if cls == "教養(総合)":
            others = [(n, k) for n, k in ents if "GCP" not in n]
            gcps   = [(n, k) for n, k in ents if "GCP" in n]
            _split_to_bubbles("教養(総合)", others)
            _split_to_bubbles("教養(総合) GCP", gcps)
        else:
            _split_to_bubbles(cls, ents)

    alt = f"📚 {category}一覧" if category else "📚 科目一覧"
    if not bubbles:
        return [TextMessage(text="科目が登録されていません。")]

    # 8バブルずつ複数カルーセルに分割（シラバスURL追加後に50KB超え防止）、最大5メッセージ
    result = []
    for chunk in [bubbles[i:i+8] for i in range(0, min(len(bubbles), 40), 8)]:
        if len(chunk) == 1:
            result.append(FlexMessage(alt_text=alt, contents=chunk[0]))
        else:
            result.append(FlexMessage(alt_text=alt, contents=FlexCarousel(contents=chunk)))
    cache.set_course_list_cache(_cl_key, result)
    return result


# ── Ranking（起動時prewarm対象。1時間TTLキャッシュのミスを起動直後に埋めておく） ──

async def _get_popular_ranking() -> list:
    _cached = cache.get_ranking_cache("popular")
    if _cached is not None:
        return _cached
    async with AsyncSessionLocal() as _s:
        rows = (await _s.execute(
            select(Subject.name, func.avg(Review.rating).label("avg"))
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.status == ReviewStatus.APPROVED)
            .group_by(Subject.name)
            .order_by(func.avg(Review.rating).desc())
            .limit(5)
        )).all()
    if not rows:
        return [TextMessage(text=f"まだ承認済みレビューがありません。\nレビューを投稿してください！\n\n{REVIEW_FORM_URL}")]
    items = [
        {"rank": i, "name": name, "stars": stars(math.floor(float(avg) + 0.5))}
        for i, (name, avg) in enumerate(rows, 1)
    ]
    _res = [FlexMessage(alt_text="🏆 人気の授業 TOP5", contents=make_ranking_bubble("🏆 人気の授業 TOP5", items))]
    cache.set_ranking_cache("popular", _res)
    return _res


async def _get_rakutan_ranking() -> list:
    _cached = cache.get_ranking_cache("rakutan")
    if _cached is not None:
        return _cached
    async with AsyncSessionLocal() as _s:
        rows = (await _s.execute(
            select(Subject.name, Review.ease_rating, func.count(Review.id))
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.status == ReviewStatus.APPROVED)
            .group_by(Subject.name, Review.ease_rating)
        )).all()
    if not rows:
        return [TextMessage(text=f"まだ承認済みレビューがありません。\nレビューを投稿してください！\n\n{REVIEW_FORM_URL}")]
    course_ease: dict[str, str] = {}
    for name, ease, _ in rows:
        if name not in course_ease or EASE_ORDER.get(ease, 99) < EASE_ORDER.get(course_ease[name], 99):
            course_ease[name] = ease
    top5 = sorted(course_ease.items(), key=lambda x: EASE_ORDER.get(x[1], 99))[:5]
    items = [
        {"rank": i, "name": name, "stars": EASE_STARS.get(ease, "")}
        for i, (name, ease) in enumerate(top5, 1)
    ]
    _res = [FlexMessage(alt_text="😴 楽単ランキング TOP5", contents=make_ranking_bubble("😴 楽単ランキング TOP5", items))]
    cache.set_ranking_cache("rakutan", _res)
    return _res


async def prewarm_rankings() -> None:
    await _get_popular_ranking()
    await _get_rakutan_ranking()


# ── Message handler ─────────────────────────────────────────────

async def _handle_kyoyo_menu() -> list:
    _menu_key = "menu:教養"
    _cached = cache.get_course_list_cache(_menu_key)
    if _cached is not None:
        return _cached
    cls_map = await cache.get_cls_order_map()
    _cls_sort = make_cls_sort(cls_map)
    reviewed_names_edu, (_, _all_courses) = await asyncio.gather(
        cache.get_reviewed_cached(),
        cache.get_courses_cached(),
    )
    edu_courses = [c for c in _all_courses if c.category == "教養" and c.classification]
    clss = sorted({c.classification for c in edu_courses}, key=_cls_sort)
    reviewed_cls = {c.classification for c in edu_courses if c.name in reviewed_names_edu}
    if clss:
        result = [make_classification_select_flex(clss, reviewed_cls)]
    else:
        result = await handle_course_list(category="教養")
    cache.set_course_list_cache(_menu_key, result)
    return result


async def _handle_senmon_menu() -> list:
    _menu_key = "menu:専門"
    _cached = cache.get_course_list_cache(_menu_key)
    if _cached is not None:
        return _cached
    reviewed_names_sen, (_, _all_courses) = await asyncio.gather(
        cache.get_reviewed_cached(),
        cache.get_courses_cached(),
    )
    sen_courses = [c for c in _all_courses if c.category == "専門" and c.classification]
    faculty_order = await cache.get_faculty_order()

    # subjects.faculty で学部ごとにグルーピングする（学科・専攻の区別はsubjects.departmentが
    # 別途持つため、ここではfacultyの一致のみで学部の有無を判定すればよい）。
    faculties_present = [fac for fac in faculty_order
                          if any((c.faculty or "").startswith(fac) for c in sen_courses)]
    grouped_cls = {c.classification for c in sen_courses
                   if any((c.faculty or "").startswith(fac) for fac in faculties_present)}

    cls_map = await cache.get_cls_order_map()
    _cls_sort = make_cls_sort(cls_map)
    # どの学部にも属さない分類（共通専門基礎科目など）はそのまま個別表示
    other_clss = sorted({c.classification for c in sen_courses} - grouped_cls, key=_cls_sort)

    reviewed_fac = {fac for fac in faculties_present
                     if any(c.name in reviewed_names_sen and (c.faculty or "").startswith(fac)
                            for c in sen_courses)}
    reviewed_other = {cls for cls in other_clss
                       if any(c.name in reviewed_names_sen and c.classification == cls
                              for c in sen_courses)}

    display_items = faculties_present + other_clss
    display_reviewed = reviewed_fac | reviewed_other

    if display_items:
        result = [make_classification_select_flex(
            display_items, display_reviewed,
            title="🎓 専門科目",
            subtitle="学部を選んでください",
            header_color="#0ea5e9",
        )]
    else:
        result = await handle_course_list(category="専門")
    cache.set_course_list_cache(_menu_key, result)
    return result


async def _handle_faculty_menu(t: str) -> list:
    _menu_key = f"menu:fac:{t}"
    _cached = cache.get_course_list_cache(_menu_key)
    if _cached is not None:
        return _cached
    reviewed_names_sen, (_, _all_courses) = await asyncio.gather(
        cache.get_reviewed_cached(),
        cache.get_courses_cached(),
    )
    fac_courses = [c for c in _all_courses if c.category == "専門" and c.classification
                    and (c.faculty or "").startswith(t)]
    if not fac_courses:
        result = [TextMessage(text=f"「{t}」の専門科目はまだ登録されていません。")]
        cache.set_course_list_cache(_menu_key, result)
        return result

    dept_values = {c.department or "" for c in fac_courses}
    cls_values = {c.classification for c in fac_courses}
    cls_map = await cache.get_cls_order_map()
    _cls_sort = make_cls_sort(cls_map)

    if len(dept_values) > 1:
        # 学科・専攻ごとに科目が分かれている学部（工学部・医学部・理学部等）→ 学科・専攻一覧
        # department未設定（空文字）の科目は全学科共通科目のため、専用ラベルで表示する
        def _dept_label(d: str) -> str:
            return "全学科共通科目" if d == "" else d
        def _dept_sort_key(d: str) -> int:
            cls_for_d = {c.classification for c in fac_courses if (c.department or "") == d}
            return min((_cls_sort(cls) for cls in cls_for_d), default=0)
        dept_sorted = sorted(dept_values, key=_dept_sort_key)
        reviewed_dept_labels = {
            _dept_label(d) for d in dept_values
            if any(c.name in reviewed_names_sen for c in fac_courses if (c.department or "") == d)
        }
        items = [(_dept_label(d), f"{t}|{d}") for d in dept_sorted]
        result = [make_classification_select_flex(
            items, reviewed_dept_labels,
            title=f"🎓 {t} 専門科目",
            subtitle="学科・専攻を選んでください",
            header_color="#0ea5e9",
            data_prefix="専門F:",
            back_label="◀ 学部選択に戻る",
            back_data="専門",
        )]
    elif len(cls_values) > 1:
        # 学科・専攻の区別はないが、分類（群科目等）が複数ある学部（経営学部等）→ 分類一覧
        cls_sorted = sorted(cls_values, key=_cls_sort)
        reviewed_cls = {cls for cls in cls_sorted
                         if any(c.name in reviewed_names_sen for c in fac_courses if c.classification == cls)}
        result = [make_classification_select_flex(
            cls_sorted, reviewed_cls,
            title=f"🎓 {t} 専門科目",
            subtitle="分類を選んでください",
            header_color="#0ea5e9",
            data_prefix="専門:",
            back_label="◀ 学部選択に戻る",
            back_data="専門",
        )]
    else:
        # 学科・専攻も複数分類も無い学部 → 即座に科目一覧（件数が多ければ内部で50音行選択に切替）
        result = await handle_course_list(category="専門", classification=next(iter(cls_values)))
    cache.set_course_list_cache(_menu_key, result)
    return result


async def _handle_course_search(t: str, user_id: str) -> list:
    # 全操作をキャッシュから（DBアクセスなし）
    _reviewed_names, (cbn, call) = await asyncio.gather(
        cache.get_reviewed_cached(),
        cache.get_courses_cached(),
    )

    # Exact course name match
    exact = cbn.get(t)
    if exact:
        if exact.name not in _reviewed_names:
            return [make_no_review_flex(exact, user_id)]
        return [await get_course_flex(exact, user_id)]

    # Seminar group e.g. 外国語セミナー(英語) → 外国語セミナーA(英語), B(英語)...
    _vsem_m = _re.match(r'^(.*?セミナー)(\([^)]+\))$', t)
    if _vsem_m:
        _sem_prefix, _sem_lang = _vsem_m.group(1), _vsem_m.group(2)
        _sem_pat = _re.compile(
            r'^' + _re.escape(_sem_prefix) + r'.+' + _re.escape(_sem_lang) + r'$', _re.IGNORECASE
        )
        _sem_courses = sorted([c for c in call if _sem_pat.match(c.name)], key=lambda c: c.name)
        if len(_sem_courses) == 1:
            return [await get_course_flex(_sem_courses[0], user_id)]
        if len(_sem_courses) >= 2:
            return [make_variant_selection_bubble(t, [c.name for c in _sem_courses], _reviewed_names)]

    # Variant group (A/B/C/D...)
    _variant_names_set = {t + s for s in ('A', 'B', 'C', 'D')}
    variant_courses = sorted([c for c in call if c.name in _variant_names_set], key=lambda c: c.name)
    if len(variant_courses) >= 2:
        return [make_variant_selection_bubble(t, [c.name for c in variant_courses], _reviewed_names)]

    # Numeric variant group（ベースが同じでアルファベット・数字だけ違う科目をまとめる。例: 生物学各論A1/A2/C1/C2）
    # 学部が異なれば別科目（例: 工学部「制御工学Ⅰ/Ⅱ」とシステム情報学部「制御工学1/2」）なので、
    # ベース名だけでなく学部単位でグループを分ける
    _num_candidates = [c for c in call if (_m := _vnum_match(c.name)) and _m[0] == t]
    if len(_num_candidates) >= 2:
        _num_by_faculty: dict[tuple[str, str], list] = defaultdict(list)
        for _c in _num_candidates:
            _num_by_faculty[(_c.faculty or "", _c.department or "")].append(_c)
        _num_results = []
        for (_fac, _dept), _cs in _num_by_faculty.items():
            _cs_sorted = sorted(_cs, key=lambda c: c.name)
            _fac_label = f"{_fac}{_dept}"
            _label = f"{t}（{_fac_label}）" if len(_num_by_faculty) > 1 and _fac_label else t
            if len(_cs_sorted) >= 2:
                _num_results.append(make_variant_selection_bubble(_label, [c.name for c in _cs_sorted], _reviewed_names))
            else:
                _num_results.append(await get_course_flex(_cs_sorted[0], user_id))
        if _num_results:
            return _num_results

    # インメモリキーワード検索（DBアクセスなし）
    _PUNCT = '・･、。「」『』【】（）()／/〜~'
    def _normalize_q(s: str) -> str:
        for ch in _PUNCT:
            s = s.replace(ch, '')
        return s

    tokens = [tok for tok in _re.split(r'[\s　]+', t.strip()) if tok]
    _toks_lower = [tok.lower() for tok in tokens]
    # 修正理由: all(...) はトークンが空リストだと常にTrueを返すため、空白のみの
    # メッセージ（全角スペース等はstrip()で""になる）だとキャッシュ先頭6件が
    # 無条件でヒットし、意味不明な科目カードが返信されていた。
    courses = [c for c in call if _toks_lower and all(
        tok in (c.name or '').lower() or tok in (c.reading or '').lower()
        for tok in _toks_lower
    )][:6]

    # 句読点を除去した正規化検索（フォールバック）
    if not courses:
        _norm_t = _normalize_q(t).lower()
        courses = [c for c in call if _norm_t in _normalize_q(c.name or '').lower()][:6]

    if courses:
        # Letter variants (A/B/C/D) - インメモリ
        _all_names = {c.name for c in call}
        potential_bases = {
            c.name[:-1] for c in courses
            if c.name and c.name[-1] in ('A', 'B', 'C', 'D') and len(c.name) > 1
        }
        base_variants: dict[str, list[str]] = defaultdict(list)
        for _b in potential_bases:
            for _s in ('A', 'B', 'C', 'D'):
                if _b + _s in _all_names:
                    base_variants[_b].append(_b + _s)

        # Numeric variants（文字違いが2種類以上あるベースだけを選択肢としてまとめる）
        # 学部が異なれば別科目（例: 工学部「制御工学Ⅰ/Ⅱ」とシステム情報学部「制御工学1/2」）なので、
        # ベース名だけでなく学部単位でグループを分ける
        _kw_num_bases: dict[tuple[str, str], list[str]] = defaultdict(list)
        _kw_num_facs: dict[str, set[str]] = defaultdict(set)
        for c in courses:
            _match = _vnum_match(c.name)
            if _match:
                _fac = f"{c.faculty or ''}{c.department or ''}"
                _kw_num_bases[(_match[0], _fac)].append(c.name)
                _kw_num_facs[_match[0]].add(_fac)

        seen_base: set[str] = set()
        seen_num_base: set[tuple[str, str]] = set()
        result = []
        for c in courses:
            name = c.name
            if name and name[-1] in ('A', 'B', 'C', 'D') and len(name) > 1:
                base = name[:-1]
                if base in seen_base:
                    continue
                variants = base_variants.get(base, [])
                if len(variants) >= 2:
                    seen_base.add(base)
                    result.append(make_variant_selection_bubble(base, variants, _reviewed_names))
                    continue
            _m2 = _vnum_match(name)
            if _m2:
                base = _m2[0]
                fac = f"{c.faculty or ''}{c.department or ''}"
                key = (base, fac)
                if key in seen_num_base:
                    continue
                num_vs = _kw_num_bases.get(key, [])
                if len(num_vs) >= 2:
                    seen_num_base.add(key)
                    label = f"{base}（{fac}）" if len(_kw_num_facs.get(base, set())) > 1 and fac else base
                    result.append(make_variant_selection_bubble(label, sorted(num_vs), _reviewed_names))
                    continue
            result.append(await get_course_flex(c, user_id))
        return result[:5]

    return [TextMessage(
        text=f"「{t}」に一致する科目が見つかりませんでした。\n\n「科目一覧」で登録科目を確認するか、「ヘルプ」で使い方をご確認ください。"
    )]


async def handle_message(text: str, user_id: str = "") -> list:
    t = text.strip()
    # 科目数が多い分類・学部で挟まれる50音行選択（例:"専門:国際人間科学部専門科目::R:あ行"）の
    # 行指定部分を切り離す。handle_course_list呼び出し以外の分岐判定には影響しない。
    _reading_row = ""
    if "::R:" in t:
        t, _reading_row = t.split("::R:", 1)

    if t in ["科目一覧", "科目", "授業一覧", "一覧"]:
        return [make_category_select_flex()]

    if t in ["教養", "教養科目", "教養一覧"]:
        return await _handle_kyoyo_menu()

    if t.startswith("教養:"):
        cls = t[len("教養:"):]
        return await handle_course_list(category="教養", classification=cls, reading_row=_reading_row)

    if t.startswith("専門:"):
        cls = t[len("専門:"):]
        return await handle_course_list(category="専門", classification=cls, reading_row=_reading_row)

    if t.startswith("専門F:"):
        fac_dept = t[len("専門F:"):]
        fac, _, dept = fac_dept.partition("|")
        return await handle_course_list(category="専門", faculty=fac, department=dept, reading_row=_reading_row)

    # 分類名の直接タップ（例：「教養(社会)」）
    if t in await cache.get_cls_set():
        return await handle_course_list(classification=t, reading_row=_reading_row)

    if t == "専門comingsoon":
        return [TextMessage(text="🚧 専門科目一覧は現在準備中です。\nもうしばらくお待ちください！")]

    if t in ["専門科目", "専門", "専門一覧"]:
        return await _handle_senmon_menu()

    # 学部名タップ（例："経営学部"）
    if t in await cache.get_faculty_order():
        return await _handle_faculty_menu(t)

    if t in ["レビュー投稿", "レビュー", "投稿"] or "レビュー投稿" in t:
        url = f"{REVIEW_FORM_URL}?uid={user_id}" if user_id else REVIEW_FORM_URL
        return [TextMessage(text=f"📝 以下のフォームからレビューを投稿できます！\n\n{url}")]

    if t in ["生協", "生協アプリ", "coop"]:
        return [FlexMessage(alt_text="🛒 生協アプリ", contents=FlexBubble(
            body=FlexBox(layout="vertical", spacing="md", contents=[
                FlexText(text="🛒 生協アプリ", weight="bold", size="lg"),
                FlexText(text="タップしてApp Store/Google Playの生協アプリページを開く", size="sm", color="#64748b", wrap=True),
                FlexButton(action=URIAction(label="生協アプリのストアページを開く", uri=f"{APP_URL}/coop"),
                           style="primary", color="#6366f1", margin="md"),
            ])
        ))]

    if t in ["バイト", "アルバイト"]:
        return [TextMessage(text="🚧 バイト情報機能は現在準備中です。\nもうしばらくお待ちください！")]

    if t in ["近隣飲食店", "近隣の飲食店"]:
        return [TextMessage(text="🚧 近隣飲食店情報機能は現在準備中です。\nもうしばらくお待ちください！")]

    if t in ["ヘルプ", "help", "使い方", "？", "?"]:
        return [make_help_flex()]

    if t in ["問い合わせ", "連絡", "contact", "お問い合わせ"]:
        return [make_help_flex()]

    if t in ["人気の授業", "人気授業", "人気", "おすすめ"]:
        return await _get_popular_ranking()

    if t in ["楽単ランキング", "楽単", "楽"]:
        return await _get_rakutan_ranking()

    return await _handle_course_search(t, user_id)


# ── Webhook event processing ────────────────────────────────────

_SLOW_REPLY_MS = 2000


def _log_reply_timing(kind: str, start: float, compute_ms: float | None = None) -> None:
    """webhook受信からreply()完了までの実時間を記録する。
    routers/webhook.pyの/callbackは即座に202相当を返すためRenderのアクセスログには
    実際のLINE bot応答速度が出ない。process_events側で計測する。
    compute_ms を渡すと handle_message() の計算時間とLINE API送信(reply())時間を
    切り分けて記録できる（大きなFlexMessageの送信自体が遅いのか、こちら側の計算が
    遅いのかを区別するため）。"""
    duration_ms = (time.perf_counter() - start) * 1000
    marker = " SLOW" if duration_ms >= _SLOW_REPLY_MS else ""
    if compute_ms is not None:
        print(f"[linebot_reply] {kind} {duration_ms:.0f}ms (compute={compute_ms:.0f}ms send={duration_ms - compute_ms:.0f}ms){marker}", flush=True)
    else:
        print(f"[linebot_reply] {kind} {duration_ms:.0f}ms{marker}", flush=True)


async def _handle_reply_event(event, user_id: str, input_text: str, label: str, log_text: str, t0: float) -> None:
    """PostbackEvent/MessageEventで共通の応答処理。未登録なら登録誘導Flexを返し、
    それ以外はhandle_message()を呼んで返信する。タイムアウト・例外時はエラーログを
    保存した上でフォールバックメッセージを返す（従来この一連の流れがpostback/message
    それぞれに別実装され、エラー文言・ログのaction文字列がわずかに食い違っていたため統一）。
    label はログ・action文字列のプレフィックス("postback"/"message")、
    log_text は受信ログに残す生テキスト。"""
    try:
        asyncio.create_task(save_log_bg(user_id, "in", log_text))
        if await _registration_incomplete(user_id):
            register_url = make_register_url(user_id)
            await line_client.reply(event.reply_token, [make_registration_flex(register_url)])
            _log_reply_timing(f"{label}:register", t0)
            return
        messages = await asyncio.wait_for(handle_message(input_text, user_id), timeout=25.0)
        t_compute = time.perf_counter()
        await line_client.reply(event.reply_token, messages[:5])
        asyncio.create_task(save_log_bg(user_id, "out", f"[{len(messages)} msg(s)]"))
        _log_reply_timing(f"{label}:{input_text[:30]}", t0, compute_ms=(t_compute - t0) * 1000)
    except asyncio.TimeoutError:
        await save_error_log(Exception("handle_message timeout"), user_id=user_id, action=f"{label}:{input_text}")
        try:
            await line_client.reply(event.reply_token, [TextMessage(text="処理に時間がかかりすぎました。もう一度お試しください。")])
        except Exception as reply_exc:
            await save_error_log(reply_exc, user_id=user_id, action=f"{label}_reply_failed:{input_text}")
        _log_reply_timing(f"{label}:{input_text[:30]}:timeout", t0)
    except Exception as exc:
        await save_error_log(exc, user_id=user_id, action=f"{label}:{input_text}")
        try:
            await line_client.reply(event.reply_token, [TextMessage(text="エラーが発生しました。しばらくしてからもう一度お試しください。")])
        except Exception as reply_exc:
            await save_error_log(reply_exc, user_id=user_id, action=f"{label}_reply_failed:{input_text}")
        _log_reply_timing(f"{label}:{input_text[:30]}:error", t0)


async def process_events(events) -> None:
    try:
        for event in events:
            _t0 = time.perf_counter()

            if isinstance(event, FollowEvent):
                user_id = event.source.user_id
                try:
                    register_url = make_register_url(user_id)
                    await line_client.reply(event.reply_token, [make_registration_flex(register_url)])
                    asyncio.create_task(save_log_bg(user_id, "in", "[follow]"))
                except Exception as exc:
                    await save_error_log(exc, action="follow")
                try:
                    if await _registration_incomplete(user_id):
                        await line_client.link_rich_menu(user_id, RICHMENU_ID_PREREGISTER)
                    else:
                        await line_client.unlink_rich_menu(user_id)
                except Exception as exc:
                    await save_error_log(exc, user_id=user_id, action="follow_richmenu")
                _log_reply_timing("follow", _t0)
                continue

            if isinstance(event, PostbackEvent):
                user_id = event.source.user_id
                data = event.postback.data
                await _handle_reply_event(event, user_id, data, "postback", f"[postback]{data}", _t0)
                continue

            if not isinstance(event, MessageEvent):
                continue
            if not isinstance(event.message, TextMessageContent):
                continue

            user_id = event.source.user_id
            user_text = event.message.text
            await _handle_reply_event(event, user_id, user_text, "message", user_text, _t0)
    except Exception as exc:
        await save_error_log(exc, action="process_events")
