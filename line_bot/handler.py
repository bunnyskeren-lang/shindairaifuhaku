import asyncio
import random
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
from sqlalchemy import select

from core import cache, line_client, moderation
from core.activity_log import save_error_log, save_log_bg
from core.config import (
    APP_URL,
    BAN_MESSAGE_TEXT,
    EASE_ORDER,
    EASE_STARS,
    RICHMENU_ID_MAIN,
    RICHMENU_ID_PREREGISTER,
    is_profile_complete,
    make_cls_sort,
    make_course_liff_url,
    make_register_url,
    make_review_liff_url,
)
from core.subject_variants import (
    LETTER_MERGE_EXCLUDED_CLASSIFICATIONS,
    TAG_PRIORITY,
    compute_variant_bases,
    num_variant_suffix,
    variant_tag_in_suffix,
)
from database import AsyncSessionLocal
from line_bot.flex_builders import (
    get_course_flex,
    make_category_select_flex,
    make_classification_select_flex,
    make_help_flex,
    make_no_review_flex,
    make_omikuji_card,
    make_onitan_card,
    make_rakutan_card,
    make_registration_flex,
    make_review_browse_entry_flex,
    make_search_result_card,
)
from models import CourseSection, Review, ReviewStatus, Subject, UserProfile


async def _user_banned(user_id: str) -> bool:
    return await moderation.is_banned(user_id)


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
# バリアント判定・束ね方の手順はcore.subject_variants.compute_variant_bases()に一本化済み
# （2026-08-25に判定規則の正規表現だけ共有したが、束ね方の手順自体は2026-08-30まで
# ここに手動複製されたままで同期漏れが繰り返し起きていた。以後は
# core/subject_variants.py側を編集すればここにも自動的に反映される）。

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


def _build_alpha_split_menu(rows: list, category: str, classification: str,
                             faculty: str, department: str) -> list | None:
    """rowsがよみがな順の均等分割メニューを挟むべき件数のとき、選択メニューのFlexMessageを
    組み立てて返す。分割の絞り込み軸(row_prefix)が定まらない場合はNoneを返し、
    呼び出し側は通常の一覧表示にフォールバックする。"""
    if faculty and not classification:
        row_prefix = f"専門F:{faculty}|{department}"
    elif classification and category:
        row_prefix = f"{category}:{classification}"
    elif classification:
        row_prefix = classification
    else:
        return None
    by_reading = sorted(rows, key=_reading_key)
    chunks = [by_reading[i:i + _ALPHA_CHUNK_SIZE] for i in range(0, len(by_reading), _ALPHA_CHUNK_SIZE)]
    items = []
    for i, chunk in enumerate(chunks):
        first_ch = _reading_key(chunk[0])[:1] or "?"
        last_ch = _reading_key(chunk[-1])[:1] or "?"
        label = f"{first_ch}〜{last_ch}" if first_ch != last_ch else first_ch
        items.append((f"{label}（{len(chunk)}件）", f"{row_prefix}::R:{i}"))
    return [make_classification_select_flex(
        items, set(),
        title="📚 科目一覧",
        subtitle=f"{len(rows)}件あります。よみがな順で絞り込んでください",
        header_color="#6366f1",
    )]


async def _build_course_bubbles(rows: list, reviewed_names: set, cls_sort) -> list:
    """絞り込み・ソート済みのSubject行から、末尾バリアント統合・シラバス/レビューURL付与済みの
    FlexBubble一覧を組み立てる（8バブル単位のカルーセル分割は呼び出し側で行う）。"""
    # バリアント判定・束ね方の実体はcore.subject_variants.compute_variant_bases()に一本化済み
    # （faculty+department単位でグループ化する理由等は同関数のdocstring参照）
    names_with_fd = [(c.name, c.faculty or "", c.department or "") for c in rows]
    _letter_excluded = {c.name for c in rows if c.classification in LETTER_MERGE_EXCLUDED_CLASSIFICATIONS}
    _sem_bases, _letter_bases, _num_bases = compute_variant_bases(names_with_fd, _letter_excluded)

    _num_variant_names = {n for _items in _num_bases.values() for n, _, _, _, _ in _items}
    _num_base_for = {n: _key for _key, _items in _num_bases.items() for n, _, _, _, _ in _items}
    seen_num_base: set[tuple[str, str, str, bool]] = set()

    _letter_base_for: dict[str, tuple[str, str, str]] = {
        _key[0] + s: _key for _key, _variants in _letter_bases.items() for s in _variants
    }
    seen_letter_base: set[tuple[str, str, str]] = set()

    _sem_variant_names = {n for _items in _sem_bases.values() for n, _ in _items}
    _sem_base_for = {n: _key for _key, _items in _sem_bases.items() for n, _ in _items}
    seen_sem_base: set[tuple[str, str, str]] = set()

    # syllabus_url は全件キャッシュから取得（DBアクセスなし）
    _sv_by_id = await cache.get_syllabus_urls_cached()
    course_syllabus_urls: dict[str, str] = {c.name: _sv_by_id[c.id] for c in rows if c.id in _sv_by_id}
    course_liff_urls: dict[str, str] = {c.name: make_course_liff_url(c.id) for c in rows}
    # entries内タプルの末尾2要素は、variant/numvariant種別の科目は(faculty, department)を保持する
    # （_num_bases/_letter_bases/_sem_basesの正しいキーをclassificationからだけでは復元できないため。
    # singleでは""）
    groups: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
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
                groups[cls].append((base[0], f"variant:{suffix}", base[1], base[2]))
            continue
        if name in _letter_base_for:
            key = _letter_base_for[name]
            if key not in seen_letter_base:
                seen_letter_base.add(key)
                suffix = "/".join(_letter_bases[key])
                groups[cls].append((key[0], f"variant:{suffix}", key[1], key[2]))
            continue
        if name in _num_variant_names:
            key = _num_base_for[name]
            if key not in seen_num_base:
                seen_num_base.add(key)
                suffix = num_variant_suffix(_num_bases[key])
                groups[cls].append((key[0], f"numvariant:{suffix}", key[1], key[2]))
            continue
        groups[cls].append((name, "single", "", ""))

    def _cat_order(cls: str) -> int:
        return 0 if cls_category.get(cls, "") == "教養" else 1
    all_groups = sorted(groups.items(), key=lambda x: (_cat_order(x[0]), cls_sort(x[0])))

    def _entry_has_review(name: str, kind: str, fd: tuple[str, str] = ("", "")) -> bool:
        if kind == "single":
            return name in reviewed_names
        if kind.startswith("variant:"):
            suffixes = kind.split(":", 1)[1].split("/")
            if any(name + s in reviewed_names for s in suffixes):
                return True
            key = (name, fd[0], fd[1])
            if key in _sem_bases:
                return any(n in reviewed_names for n, _ in _sem_bases[key])
            return False
        if kind.startswith("numvariant:"):
            key = (name, fd[0], fd[1], variant_tag_in_suffix(kind))
            if key in _num_bases:
                return any(n in reviewed_names for n, _, _, _, _ in _num_bases[key])
            return False
        return False

    def _group_syllabus_url(name: str, kind: str, fd: tuple[str, str] = ("", "")) -> str:
        # 統合表示（variant/numvariant）はbase名がそのままSubject.nameと一致しないため、
        # グループ内で表示順（最も左側）を優先し、そのシラバスURLを代表として採用する
        # （左側が無ければ右側の変種のURLで代替する）。
        if kind == "single":
            return course_syllabus_urls.get(name, "")
        if kind.startswith("variant:"):
            key = (name, fd[0], fd[1])
            if key in _sem_bases:
                for n, _ in sorted(_sem_bases[key], key=lambda x: x[1]):
                    url = course_syllabus_urls.get(n, "")
                    if url:
                        return url
                return ""
            for s in kind.split(":", 1)[1].split("/"):
                url = course_syllabus_urls.get(name + s, "")
                if url:
                    return url
            return ""
        if kind.startswith("numvariant:"):
            key = (name, fd[0], fd[1], variant_tag_in_suffix(kind))
            if key in _num_bases:
                for n, _, _, _, _ in sorted(_num_bases[key], key=lambda x: (x[1], x[2], TAG_PRIORITY.get(x[4], 9))):
                    url = course_syllabus_urls.get(n, "")
                    if url:
                        return url
        return ""

    def _make_bubble(classification: str, entries: list) -> FlexBubble:
        btn_contents = []
        for idx, (name, kind, fac, dept) in enumerate(entries):
            fd = (fac, dept)
            if kind.startswith("variant:") or kind.startswith("numvariant:"):
                suffix = kind.split(":", 1)[1]
                display = f"{name} ({suffix})"
            else:
                display = name
            has_review = _entry_has_review(name, kind, fd)
            syl_url = _group_syllabus_url(name, kind, fd)
            has_content = has_review or bool(syl_url)
            text_color = "#0f172a" if has_content else "#94a3b8"
            display_text = f"✓{display}" if has_review else display
            if kind == "single":
                liff_url = course_liff_urls.get(name, "")
            elif kind.startswith("variant:"):
                first_suffix = kind.split(":", 1)[1].split("/")[0]
                liff_url = course_liff_urls.get(name + first_suffix, "")
            elif kind.startswith("numvariant:") and (key := (name, fac, dept, variant_tag_in_suffix(kind))) in _num_bases:
                first_name = min(_num_bases[key], key=lambda x: (x[1], x[2], TAG_PRIORITY.get(x[4], 9)))[0]
                liff_url = course_liff_urls.get(first_name, "")
            else:
                liff_url = ""

            if liff_url:
                # 科目カード（Postbackでchat内に表示するFlexカード）は、ユーザーが
                # 科目名を直接テキスト入力したときのみ表示する。一覧・ランキング等での
                # 科目名タップはバリアント選択を挟まず常に科目詳細LIFFへ直接遷移させる
                name_action = URIAction(label=display[:20], uri=liff_url)
            else:
                name_action = PostbackAction(label=display[:20], data=name)
            name_box = FlexBox(
                layout="horizontal",
                action=name_action,
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
            others = [e for e in ents if "GCP" not in e[0]]
            gcps   = [e for e in ents if "GCP" in e[0]]
            _split_to_bubbles("教養(総合)", others)
            _split_to_bubbles("教養(総合) GCP", gcps)
        else:
            _split_to_bubbles(cls, ents)

    return bubbles


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
        split_menu = _build_alpha_split_menu(rows, category, classification, faculty, department)
        if split_menu is not None:
            cache.set_course_list_cache(_cl_key, split_menu)
            return split_menu

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

    bubbles = await _build_course_bubbles(rows, reviewed_names, _cls_sort)

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


# ── Ranking ──────────────────────────────────────────────────────

async def _get_rakutan_ranking() -> list:
    # 「楽単度が最も高い」科目群からランダムに5件選ぶ。毎回結果を変えたいのでキャッシュしない
    async with AsyncSessionLocal() as _s:
        rows = (await _s.execute(
            select(Subject.id, Subject.name, Review.ease_rating)
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.status == ReviewStatus.APPROVED)
            .group_by(Subject.id, Subject.name, Review.ease_rating)
        )).all()
    if not rows:
        return [TextMessage(text=f"まだ承認済みレビューがありません。\nレビューを投稿してください！\n\n{make_review_liff_url()}")]
    course_best: dict[int, tuple[str, str]] = {}
    for sid, name, ease in rows:
        if sid not in course_best or EASE_ORDER.get(ease, 99) < EASE_ORDER.get(course_best[sid][1], 99):
            course_best[sid] = (name, ease)
    tiers = sorted({ease for _, ease in course_best.values()}, key=lambda e: EASE_ORDER.get(e, 99))
    selected: list[tuple[int, str, str]] = []
    for tier in tiers:
        if len(selected) >= 5:
            break
        pool = [(sid, name, ease) for sid, (name, ease) in course_best.items() if ease == tier]
        random.shuffle(pool)
        selected.extend(pool[:5 - len(selected)])
    items = [
        {"rank": i, "id": sid, "name": name, "stars": EASE_STARS.get(ease, ""), "ease": ease}
        for i, (sid, name, ease) in enumerate(selected, 1)
    ]
    return [make_rakutan_card(items)]


async def _get_onitan_ranking() -> list:
    # 「鬼単度が最も高い」(=楽単度が最も低い)科目群からランダムに5件選ぶ。毎回結果を変えたいのでキャッシュしない
    async with AsyncSessionLocal() as _s:
        rows = (await _s.execute(
            select(Subject.id, Subject.name, Review.ease_rating)
            .join(CourseSection, CourseSection.subject_id == Subject.id)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.status == ReviewStatus.APPROVED)
            .group_by(Subject.id, Subject.name, Review.ease_rating)
        )).all()
    if not rows:
        return [TextMessage(text=f"まだ承認済みレビューがありません。\nレビューを投稿してください！\n\n{make_review_liff_url()}")]
    course_best: dict[int, tuple[str, str]] = {}
    for sid, name, ease in rows:
        if sid not in course_best or EASE_ORDER.get(ease, -1) > EASE_ORDER.get(course_best[sid][1], -1):
            course_best[sid] = (name, ease)
    tiers = sorted({ease for _, ease in course_best.values()}, key=lambda e: -EASE_ORDER.get(e, -1))
    selected: list[tuple[int, str, str]] = []
    for tier in tiers:
        if len(selected) >= 5:
            break
        pool = [(sid, name, ease) for sid, (name, ease) in course_best.items() if ease == tier]
        random.shuffle(pool)
        selected.extend(pool[:5 - len(selected)])
    items = [
        {"rank": i, "id": sid, "name": name, "stars": EASE_STARS.get(ease, ""), "ease": ease}
        for i, (sid, name, ease) in enumerate(selected, 1)
    ]
    return [make_onitan_card(items)]


async def _get_omikuji() -> list:
    # 承認済みレビューがある科目からランダムに10件選ぶ(楽単度を表示)。抽選結果は毎回変えたいので
    # キャッシュしないが、抽選対象の絞り込みはキャッシュ済みの科目名セットで行い(DBアクセスなし)、
    # 実際にDBへ問い合わせるのは抽選で選ばれた10件の楽単度取得のみにする。
    # 修正理由(2026-08-25): 一時的に、承認済みレビューがある全科目分のSubject×CourseSection×Review
    # を毎回DBから全件取得してからPython側でシャッフルする実装になっていたが、レビュー件数が
    # 増えるほどクエリ・メモリ負荷が線形に増える設計だった。2026-08-25以前の
    # func.random() LIMIT 10方式と同じく、DBに触れる範囲を抽選後の10件だけに絞り戻す。
    reviewed_names, (cbn, _call) = await asyncio.gather(
        cache.get_reviewed_cached(),
        cache.get_courses_cached(),
    )
    if not reviewed_names:
        return [TextMessage(text=f"まだ承認済みレビューがありません。\nレビューを投稿してください！\n\n{make_review_liff_url()}")]
    pool = [cbn[name] for name in reviewed_names if name in cbn]
    random.shuffle(pool)
    selected_subjects = pool[:10]
    ids = [s.id for s in selected_subjects]
    async with AsyncSessionLocal() as _s:
        ease_rows = (await _s.execute(
            select(CourseSection.subject_id, Review.ease_rating)
            .join(Review, Review.course_section_id == CourseSection.id)
            .where(Review.status == ReviewStatus.APPROVED, CourseSection.subject_id.in_(ids))
        )).all()
    best_ease: dict[int, str] = {}
    for sid, ease in ease_rows:
        if sid not in best_ease or EASE_ORDER.get(ease, 99) < EASE_ORDER.get(best_ease[sid], 99):
            best_ease[sid] = ease
    items = [
        {
            "rank": i, "id": s.id, "name": s.name,
            "stars": EASE_STARS.get(best_ease.get(s.id, ""), ""),
            "ease": best_ease.get(s.id, ""),
        }
        for i, s in enumerate(selected_subjects, 1)
    ]
    return [make_omikuji_card(items)]


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


_MSG_SEARCH_LIMIT = 10


async def _handle_course_search(t: str, user_id: str) -> list:
    # 全操作をキャッシュから（DBアクセスなし）
    _reviewed_names, (cbn, call), variant_map = await asyncio.gather(
        cache.get_reviewed_cached(),
        cache.get_courses_cached(),
        cache.get_variant_map_cached(),
    )

    # Exact course name match
    exact = cbn.get(t)
    if exact:
        if exact.name not in _reviewed_names:
            return [make_no_review_flex(exact, user_id)]
        return [await get_course_flex(exact, user_id)]

    # バリアントグループ（末尾の数字/文字/セミナー言語のみが異なる科目、例:
    # 生物学各論A1/A2/C1/C2、外国語セミナーA(英語)/B(英語)）は科目詳細ページ側で
    # レビューを統合表示する([[project_review_view_variant_merge_20260824]])ため、
    # どの変種を選んでも結果は同じになる。選択バブルは廃止し、検索結果ではグループを
    # 1件（グループ内で名前が最も若い科目を代表）に集約して扱う。
    # variant_map(compute_variant_groups)はベース名ラベルのみでfaculty/departmentを
    # 区別しないため、同名ベースが学部違いで複数存在するケース（例: 工学部「制御工学Ⅰ/Ⅱ」と
    # システム情報学部「制御工学1/2」）はここでfaculty+departmentも合わせてグループキーにする
    # （routers/liff_api.py _group_subject_idsと同じ判定基準）。
    _label_buckets: dict[str, list] = defaultdict(list)
    for c in call:
        label = variant_map.get(c.name, "")
        if label:
            _label_buckets[label].append(c)

    seen_group_keys: set[tuple[str, str, str]] = set()
    search_rows = []
    for c in call:
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
        # 修正理由: 区切り文字なしで連結すると、境界をまたぐ検索語が意図せずマッチしうる
        # （例: display末尾+先頭member名の一部が偶然一致する場合）。科目名には出現しない
        # 制御文字を区切りに挟み、単一科目の場合は名前を二重連結しないようdisplay自体を
        # membersに含めない
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

    # グループのベース名が一意に1件だけ一致する場合は、通常の科目名完全一致と同様に詳細カードへ直行
    _unique_exact = [r for r in search_rows if r["display"] == t]
    if len(_unique_exact) == 1:
        rep = _unique_exact[0]["rep"]
        if rep.name not in _reviewed_names:
            return [make_no_review_flex(rep, user_id)]
        return [await get_course_flex(rep, user_id)]

    # インメモリキーワード検索（DBアクセスなし）
    _PUNCT = '・･、。「」『』【】（）()／/〜~'
    def _normalize_q(s: str) -> str:
        for ch in _PUNCT:
            s = s.replace(ch, '')
        return s

    tokens = [tok for tok in _re.split(r'[\s　]+', t.strip()) if tok]
    _toks_lower = [tok.lower() for tok in tokens]
    # 修正理由: all(...) はトークンが空リストだと常にTrueを返すため、空白のみの
    # メッセージ（全角スペース等はstrip()で""になる）だとキャッシュ先頭件が
    # 無条件でヒットし、意味不明な検索結果が返信されていた。
    matched = [r for r in search_rows if _toks_lower and all(
        tok in r["text"] or tok in r["reading"] for tok in _toks_lower
    )]

    # 句読点を除去した正規化検索（フォールバック）
    if not matched:
        _norm_t = _normalize_q(t).lower()
        matched = [r for r in search_rows if _norm_t and _norm_t in _normalize_q(r["text"])]

    if not matched:
        return [TextMessage(
            text=f"「{t}」に一致する科目が見つかりませんでした。\n\n「科目一覧」で登録科目を確認するか、「ヘルプ」で使い方をご確認ください。"
        )]

    # 関連度順：科目名（グループはベース名）が入力語から始まるものを最優先
    t_lower = t.lower()
    matched.sort(key=lambda r: 0 if r["display"].lower().startswith(t_lower) else 1)
    total_matched = len(matched)
    matched = matched[:_MSG_SEARCH_LIMIT]

    all_stats = await cache.get_all_review_stats_cached()
    items = []
    for r in matched:
        best_ease = None
        for m in r["members"]:
            _cnt, top_ease = all_stats.get(m.name, (0, None))
            if top_ease and (best_ease is None or EASE_ORDER.get(top_ease, 99) < EASE_ORDER.get(best_ease, 99)):
                best_ease = top_ease
        items.append({
            "id": r["rep"].id,
            "name": r["display"],
            "faculty": f"{r['faculty']}{r['department']}",
            "stars": EASE_STARS.get(best_ease, ""),
            "category": r["rep"].category or "",
        })

    count_label = f"{total_matched}件" if total_matched <= _MSG_SEARCH_LIMIT else f"{total_matched}件中{_MSG_SEARCH_LIMIT}件"
    return [make_search_result_card(items, title=f"🔍「{t}」の検索結果（{count_label}）")]


async def handle_message(text: str, user_id: str = "") -> list:
    t = text.strip()
    # 科目数が多い分類・学部で挟まれる50音行選択（例:"専門:国際人間科学部専門科目::R:あ行"）の
    # 行指定部分を切り離す。handle_course_list呼び出し以外の分岐判定には影響しない。
    _reading_row = ""
    if "::R:" in t:
        t, _reading_row = t.split("::R:", 1)

    if t in ["レビュー閲覧", "レビューを閲覧"]:
        return [make_review_browse_entry_flex()]

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
        url = make_review_liff_url(user_id=user_id)
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

    if t in ["楽単ランキング", "楽単", "楽"]:
        return await _get_rakutan_ranking()

    if t in ["鬼単ランキング", "鬼単", "鬼"]:
        return await _get_onitan_ranking()

    if t in ["10連おみくじ", "10連みくじ", "おみくじ", "みくじ"]:
        return await _get_omikuji()

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
        if await _user_banned(user_id):
            await line_client.reply(event.reply_token, [TextMessage(text=BAN_MESSAGE_TEXT)])
            _log_reply_timing(f"{label}:banned", t0)
            return
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
                if await _user_banned(user_id):
                    try:
                        await line_client.reply(event.reply_token, [TextMessage(text=BAN_MESSAGE_TEXT)])
                        asyncio.create_task(save_log_bg(user_id, "in", "[follow:banned]"))
                    except Exception as exc:
                        await save_error_log(exc, user_id=user_id, action="follow_banned")
                    _log_reply_timing("follow:banned", _t0)
                    continue
                try:
                    register_url = make_register_url(user_id)
                    await line_client.reply(event.reply_token, [make_registration_flex(register_url)])
                    asyncio.create_task(save_log_bg(user_id, "in", "[follow]"))
                except Exception as exc:
                    await save_error_log(exc, action="follow")
                try:
                    if await _registration_incomplete(user_id):
                        # LINE側のデフォルトリッチメニューを登録前メニューにしてあるため
                        # (setup_richmenu.py参照)、このlink呼び出し自体が失敗してもデフォルトの
                        # フェイルセーフにより未登録ユーザーには登録前メニューが表示され続ける
                        await line_client.link_rich_menu(user_id, RICHMENU_ID_PREREGISTER)
                    else:
                        await line_client.link_rich_menu(user_id, RICHMENU_ID_MAIN)
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
