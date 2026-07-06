import asyncio
import math
import random
import re as _re
from collections import defaultdict

from linebot.v3.messaging import (
    FlexBubble,
    FlexBox,
    FlexButton,
    FlexCarousel,
    FlexMessage,
    FlexText,
    PostbackAction,
    TextMessage,
    URIAction,
)
from linebot.v3.webhooks import FollowEvent, MessageEvent, PostbackEvent, TextMessageContent
from sqlalchemy import func, select

from core import cache, line_client
from core.activity_log import cleanup_old_logs, save_error_log, save_log_bg
from core.config import (
    APP_URL,
    EASE_ORDER,
    EASE_STARS,
    IS_DEV,
    REVIEW_FORM_URL,
    TIMETABLE_LIFF_ID,
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
from models import CourseSection, Review, Subject, UserProfile


async def _registration_incomplete(user_id: str) -> bool:
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, user_id)
        return not is_profile_complete(profile)

# ── Course list carousel ────────────────────────────────────────


async def handle_course_list(category: str = "", classification: str = "") -> list:
    _cl_key = f"{category}:{classification}"
    _cached = cache.get_course_list_cache(_cl_key)
    if _cached is not None:
        return _cached

    cls_map, (_, _all_c), reviewed_names = await asyncio.gather(
        cache.get_cls_order_map(), cache.get_courses_cached(), cache.get_reviewed_cached()
    )
    _cls_sort = make_cls_sort(cls_map)
    rows = [c for c in _all_c if
            (not category or c.category == category) and
            (not classification or c.classification == classification)]
    rows = sorted(rows, key=lambda c: (_cls_sort(c.classification or ""), c.sort_order, c.name or ""))

    if not rows:
        if classification:
            label = f"{classification}の"
        elif category:
            label = f"{category}の"
        else:
            label = ""
        return [TextMessage(text=f"まだ{label}科目が登録されていません。")]

    course_name_set = {c.name for c in rows}
    seen_base: set[str] = set()

    # Pre-compute numeric variant groups (plain digits OR letter+digits e.g. T1)
    _VNUM = _re.compile(r'^(.*?)[\s　]*([A-Z]?\d+)$')
    _num_bases: dict[str, list] = defaultdict(list)
    for _c in rows:
        _m = _VNUM.match(_c.name)
        if _m:
            _b = _m.group(1).strip()
            _sk = int(_re.search(r'\d+', _m.group(2)).group())
            _num_bases[_b].append((_c.name, _sk))
    _num_variant_names = {n for _b, _items in _num_bases.items() if len(_items) >= 2 for n, _ in _items}
    _num_base_for = {n: _b for _b, _items in _num_bases.items() if len(_items) >= 2 for n, _ in _items}
    seen_num_base: set[str] = set()

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
            base = _num_base_for[name]
            if base not in seen_num_base:
                seen_num_base.add(base)
                nums_sorted = sorted(_num_bases[base], key=lambda x: x[1])
                suffix = "/".join(
                    _m2.group(2) if (_m2 := _VNUM.match(n)) else str(sk)
                    for n, sk in nums_sorted
                )
                groups[cls].append((base, f"numvariant:{suffix}"))
            continue
        groups[cls].append((name, "single"))

    def _cat_order(cls: str) -> int:
        return 0 if cls_category.get(cls, "") == "教養" else 1
    all_groups = sorted(groups.items(), key=lambda x: (_cat_order(x[0]), _cls_sort(x[0])))

    def _entry_has_review(name: str, kind: str) -> bool:
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
            if name in _num_bases:
                return any(n in reviewed_names for n, _ in _num_bases[name])
            return False
        return False

    def _make_bubble(classification: str, entries: list) -> FlexBubble:
        btn_contents = []
        for name, kind in entries:
            if kind.startswith("variant:") or kind.startswith("numvariant:"):
                suffix = kind.split(":", 1)[1]
                display = f"{name} ({suffix})"
            else:
                display = name
            has_review = _entry_has_review(name, kind)
            text_color = "#4f46e5" if has_review else "#94a3b8"
            syl_url = course_syllabus_urls.get(name, "")
            if kind == "single":
                liff_url = course_liff_urls.get(name, "")
            elif kind.startswith("variant:"):
                first_suffix = kind.split(":", 1)[1].split("/")[0]
                liff_url = course_liff_urls.get(name + first_suffix, "")
            elif kind.startswith("numvariant:") and name in _num_bases:
                first_name = min(_num_bases[name], key=lambda x: x[1])[0]
                liff_url = course_liff_urls.get(first_name, "")
            else:
                liff_url = ""
            name_box = FlexBox(
                layout="horizontal",
                action=PostbackAction(label=display[:40], data=name),
                contents=[FlexText(text=display, wrap=True, size="sm", color=text_color, flex=1)],
            )
            if liff_url or syl_url:
                link_items = []
                if liff_url:
                    link_items.append(FlexText(text="レビュー", size="xxs", color="#4f46e5", flex=0,
                                               action=URIAction(label="レビュー", uri=liff_url)))
                if syl_url:
                    if link_items:
                        link_items.append(FlexText(text="  ", size="xxs", color="#cbd5e1", flex=0))
                    link_items.append(FlexText(text="シラバス", size="xxs", color="#64748b", flex=0,
                                               action=URIAction(label="シラバス", uri=syl_url)))
                btn_contents.append(FlexBox(
                    layout="vertical",
                    contents=[
                        name_box,
                        FlexBox(layout="horizontal", contents=link_items, margin="xs"),
                    ],
                    padding_top="sm",
                    padding_bottom="sm",
                ))
            else:
                btn_contents.append(
                    FlexBox(
                        layout="vertical",
                        action=PostbackAction(label=display[:40], data=name),
                        contents=[FlexText(text=display, wrap=True, size="sm", color=text_color)],
                        padding_top="sm",
                        padding_bottom="sm",
                    )
                )
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


# ── Message handler ─────────────────────────────────────────────

async def handle_message(text: str, user_id: str = "") -> list:
    t = text.strip()

    if t in ["科目一覧", "科目", "授業一覧", "一覧"]:
        return [make_category_select_flex()]

    if t in ["教養", "教養科目", "教養一覧"]:
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
            return [make_classification_select_flex(clss, reviewed_cls)]
        return await handle_course_list(category="教養")

    if t.startswith("教養:"):
        cls = t[len("教養:"):]
        return await handle_course_list(category="教養", classification=cls)

    if t.startswith("専門:"):
        cls = t[len("専門:"):]
        return await handle_course_list(category="専門", classification=cls)

    # 分類名の直接タップ（例：「教養(社会)」）
    if t in await cache.get_cls_set():
        return await handle_course_list(classification=t)

    if t == "専門comingsoon":
        return [TextMessage(text="🚧 専門科目一覧は現在準備中です。\nもうしばらくお待ちください！")]

    if t in ["専門科目", "専門", "専門一覧"]:
        cls_map = await cache.get_cls_order_map()
        parent_map = await cache.get_cls_parent_map()
        _cls_sort = make_cls_sort(cls_map)
        reviewed_names_sen, (_, _all_courses) = await asyncio.gather(
            cache.get_reviewed_cached(),
            cache.get_courses_cached(),
        )
        sen_courses = [c for c in _all_courses if c.category == "専門" and c.classification]
        all_cls = {c.classification for c in sen_courses}
        reviewed_cls_sen = {c.classification for c in sen_courses if c.name in reviewed_names_sen}

        # DB駆動のグループ化: parent_groupが設定されている分類 + 親名自体を除外
        child_cls_set = {cls for cls in parent_map if cls in all_cls}
        parent_names = set(parent_map.values())
        all_excluded = child_cls_set | (parent_names & all_cls)

        other_clss = sorted(all_cls - all_excluded, key=_cls_sort)

        # 親グループボタンを生成
        parent_buttons = []
        display_reviewed = set(other_clss) & reviewed_cls_sen
        for parent in sorted(parent_names):
            children = {cls for cls in child_cls_set if parent_map[cls] == parent}
            if children & all_cls or parent in all_cls:
                parent_buttons.append(f"{parent} ▶")
                if reviewed_cls_sen & (children | {parent}):
                    display_reviewed.add(f"{parent} ▶")

        display_clss = parent_buttons + other_clss

        if display_clss:
            return [make_classification_select_flex(
                display_clss, display_reviewed,
                title="🎓 専門科目",
                subtitle="分類を選んでください",
                header_color="#0ea5e9",
            )]
        return await handle_course_list(category="専門")

    # 親グループ ▶ タップ（例: "経営学部 ▶"）
    if t.endswith(" ▶"):
        parent = t[:-2].strip()
        cls_map = await cache.get_cls_order_map()
        parent_map = await cache.get_cls_parent_map()
        _cls_sort = make_cls_sort(cls_map)
        reviewed_names_sen, (_, _all_courses) = await asyncio.gather(
            cache.get_reviewed_cached(),
            cache.get_courses_cached(),
        )
        child_clss = sorted([cls for cls, pg in parent_map.items() if pg == parent], key=_cls_sort)
        if child_clss:
            child_set = set(child_clss)
            child_courses = [c for c in _all_courses if c.category == "専門" and c.classification in child_set]
            reviewed_cls = {c.classification for c in child_courses if c.name in reviewed_names_sen}
            return [make_classification_select_flex(
                child_clss, reviewed_cls,
                title=f"🎓 {parent} 専門科目",
                subtitle="分類を選んでください",
                header_color="#0ea5e9",
                data_prefix="専門:",
            )]

    if t in ["レビュー投稿", "レビュー", "投稿"] or "レビュー投稿" in t:
        url = f"{REVIEW_FORM_URL}?uid={user_id}" if user_id else REVIEW_FORM_URL
        return [TextMessage(text=f"📝 以下のフォームからレビューを投稿できます！\n\n{url}")]

    if t in ["時間割", "my時間割", "マイ時間割", "時間割テスト"]:
        if IS_DEV:
            url = f"{APP_URL}/liff/timetable?dev_uid={user_id}" if user_id else f"{APP_URL}/liff/timetable"
        elif TIMETABLE_LIFF_ID:
            url = f"https://liff.line.me/{TIMETABLE_LIFF_ID}"
        else:
            return [TextMessage(text="時間割機能は現在ご利用いただけません。")]
        return [FlexMessage(alt_text="📅 My時間割", contents=FlexBubble(
            body=FlexBox(layout="vertical", spacing="md", contents=[
                FlexText(text="📅 My時間割", weight="bold", size="lg"),
                FlexText(text="タップして時間割を開く", size="sm", color="#64748b"),
                FlexButton(action=URIAction(label="時間割を開く", uri=url),
                           style="primary", color="#6366f1", margin="md"),
            ])
        ))]

    if t in ["ヘルプ", "help", "使い方", "？", "?"]:
        return [make_help_flex()]

    if t in ["問い合わせ", "連絡", "contact", "お問い合わせ"]:
        return [make_help_flex()]

    if t in ["人気の授業", "人気授業", "人気", "おすすめ"]:
        _cached = cache.get_ranking_cache("popular")
        if _cached is not None:
            return _cached
        async with AsyncSessionLocal() as _s:
            rows = (await _s.execute(
                select(Subject.name, func.avg(Review.rating).label("avg"))
                .join(CourseSection, CourseSection.subject_id == Subject.id)
                .join(Review, Review.course_section_id == CourseSection.id)
                .where(Review.is_approved == True)
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

    if t in ["楽単ランキング", "楽単", "楽"]:
        _cached = cache.get_ranking_cache("rakutan")
        if _cached is not None:
            return _cached
        async with AsyncSessionLocal() as _s:
            rows = (await _s.execute(
                select(Subject.name, Review.ease_rating, func.count(Review.id))
                .join(CourseSection, CourseSection.subject_id == Subject.id)
                .join(Review, Review.course_section_id == CourseSection.id)
                .where(Review.is_approved == True)
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

    # Numeric variant group (e.g. 「英語1」「英語2」or「第三外国語(ドイツ語)T1」)
    _num_pat = _re.compile(r'^' + _re.escape(t) + r'(?<!\d)[\s　]*[A-Z]?\d+$')
    _num_variants = sorted(
        [c for c in call if c.name.startswith(t) and _num_pat.match(c.name)],
        key=lambda c: int(_re.search(r'\d+', c.name[len(t):]).group()),
    )
    if len(_num_variants) >= 2:
        return [make_variant_selection_bubble(t, [c.name for c in _num_variants], _reviewed_names)]

    # インメモリキーワード検索（DBアクセスなし）
    _PUNCT = '・･、。「」『』【】（）()／/〜~'
    def _normalize_q(s: str) -> str:
        for ch in _PUNCT:
            s = s.replace(ch, '')
        return s

    tokens = [tok for tok in _re.split(r'[\s　]+', t.strip()) if tok]
    _toks_lower = [tok.lower() for tok in tokens]
    courses = [c for c in call if all(
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

        # Numeric variants
        _kw_num_bases: dict[str, list[str]] = defaultdict(list)
        for c in courses:
            _m = _re.match(r'^(.*?)[\s　]*(\d+)$', c.name)
            if _m:
                _kw_num_bases[_m.group(1).strip()].append(c.name)

        seen_base: set[str] = set()
        seen_num_base: set[str] = set()
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
            _m2 = _re.match(r'^(.*?)[\s　]*[A-Z]?\d+$', name)
            if _m2:
                base = _m2.group(1).strip()
                if base in seen_num_base:
                    continue
                num_vs = _kw_num_bases.get(base, [])
                if len(num_vs) >= 2:
                    seen_num_base.add(base)
                    result.append(make_variant_selection_bubble(base, sorted(num_vs), _reviewed_names))
                    continue
            result.append(await get_course_flex(c, user_id))
        return result[:5]

    return [TextMessage(
        text=f"「{t}」に一致する科目が見つかりませんでした。\n\n「科目一覧」で登録科目を確認するか、「ヘルプ」で使い方をご確認ください。"
    )]


# ── Webhook event processing ────────────────────────────────────

async def process_events(events) -> None:
    if random.random() < 0.02:
        asyncio.create_task(cleanup_old_logs())

    try:
        for event in events:
            if isinstance(event, FollowEvent):
                user_id = event.source.user_id
                try:
                    register_url = make_register_url(user_id)
                    await line_client.reply(event.reply_token, [make_registration_flex(register_url)])
                    asyncio.create_task(save_log_bg(user_id, "in", "[follow]"))
                except Exception as exc:
                    await save_error_log(exc, action="follow")
                continue

            if isinstance(event, PostbackEvent):
                user_id = event.source.user_id
                data = event.postback.data
                try:
                    asyncio.create_task(save_log_bg(user_id, "in", f"[postback]{data}"))
                    if await _registration_incomplete(user_id):
                        register_url = make_register_url(user_id)
                        await line_client.reply(event.reply_token, [make_registration_flex(register_url)])
                        continue
                    messages = await asyncio.wait_for(
                        handle_message(data, user_id),
                        timeout=25.0,
                    )
                    await line_client.reply(event.reply_token, messages[:5])
                    asyncio.create_task(save_log_bg(user_id, "out", f"[{len(messages)} msg(s)]"))
                except asyncio.TimeoutError:
                    await save_error_log(Exception("handle_message timeout"), user_id=user_id, action=data)
                    try:
                        await line_client.reply(event.reply_token, [TextMessage(text="処理に時間がかかりすぎました。もう一度お試しください。")])
                    except Exception:
                        pass
                except Exception as exc:
                    await save_error_log(exc, user_id=user_id, action=f"postback:{data}")
                    try:
                        await line_client.reply(event.reply_token, [TextMessage(text="エラーが発生しました。もう一度お試しください。")])
                    except Exception:
                        pass
                continue

            if not isinstance(event, MessageEvent):
                continue
            if not isinstance(event.message, TextMessageContent):
                continue

            user_id = event.source.user_id
            user_text = event.message.text

            try:
                asyncio.create_task(save_log_bg(user_id, "in", user_text))
                if await _registration_incomplete(user_id):
                    register_url = make_register_url(user_id)
                    await line_client.reply(event.reply_token, [make_registration_flex(register_url)])
                    continue
                messages = await asyncio.wait_for(
                    handle_message(user_text, user_id),
                    timeout=25.0,
                )
                await line_client.reply(event.reply_token, messages[:5])
                asyncio.create_task(save_log_bg(user_id, "out", f"[{len(messages)} msg(s)]"))
            except asyncio.TimeoutError:
                await save_error_log(Exception("handle_message timeout"), user_id=user_id, action=user_text)
                try:
                    await line_client.reply(event.reply_token, [TextMessage(text="処理に時間がかかりすぎました。もう一度お試しください。")])
                except Exception:
                    pass
            except Exception as exc:
                await save_error_log(exc, user_id=user_id, action=user_text)
                try:
                    await line_client.reply(event.reply_token, [TextMessage(text="エラーが発生しました。しばらくしてからもう一度お試しください。")])
                except Exception:
                    pass
    except Exception as exc:
        await save_error_log(exc, action="process_events")
