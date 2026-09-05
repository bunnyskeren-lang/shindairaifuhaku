import asyncio
import random
import re as _re
import time
from collections import Counter, defaultdict

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
    CLASSIFICATION_MERGE_EXCLUDED,
    LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONS,
    LETTER_SPLIT_EXCLUDED_CLASSIFICATIONS,
    MANUAL_VARIANT_GROUPS,
    TAG_PRIORITY,
    compute_variant_bases,
    letter_variant_suffix,
    num_variant_suffix,
    paren_num_variant_suffixes,
    variant_letter_in_suffix,
    variant_tag_in_suffix,
)
from database import AsyncSessionLocal
from line_bot.flex_builders import (
    get_course_flex,
    make_category_entry_flex,
    make_classification_grid_flex,
    make_classification_select_flex,
    make_help_flex,
    make_no_review_flex,
    make_omikuji_card,
    make_onitan_card,
    make_rakutan_card,
    make_registration_flex,
    make_review_badge_legend,
    make_search_result_card,
)
from models import SubjectUnlock, UserProfile


async def _user_banned(user_id: str) -> bool:
    return await moderation.is_banned(user_id)


async def _get_unlocked_subject_ids(user_id: str) -> set[int]:
    """指定ユーザーがレビュー閲覧権を消費して解除済みのsubject_id集合を返す。
    ユーザー個別のデータなのでキャッシュせず毎回引く（subject_unlocksはline_user_id
    始まりの複合主キーのため軽量）。"""
    if not user_id:
        return set()
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(SubjectUnlock.subject_id).where(SubjectUnlock.line_user_id == user_id)
        )).scalars().all()
    return set(rows)


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
# 値は50KB(LINE Flex Messageのcarousel JSONサイズ上限)から逆算している。2026-09-03の
# 科目一覧カードUI改善（レビュー/シラバスの丸ピル・チェックマークバッジ追加）で1件あたりの
# JSONサイズが従来の3〜4倍に増えたため、48/50のままだと「教養(自然)」「工学部電気電子工学科
# 専門科目」等の分類で普通に50KBを超えて全く返信が届かなくなっていた（実測: 旧値のまま
# だと最大104KB、この値なら最大でも46KB程度に収まる）。
_ALPHA_SPLIT_THRESHOLD = 18
_ALPHA_CHUNK_SIZE = 18

# ── ドリルダウンpostbackデータの統一形式 ────────────────────────────
# 従来は画面ごとに"教養:"/"専門:"/"専門F:"/生の分類名 とpostbackデータの形式が
# バラバラで、パス情報（学部・学科等）が一部の画面でしか渡らず、パンくず表示や
# 「一つ前の階層に戻る」ボタンを画面共通のロジックで組み立てられなかった。
# 常に (category, faculty, department, classification) の4要素をこの順で"|"区切りで
# 持たせる1つの形式に統一する（2026-09-02、系統/学部タップ後のルーティングUX改善）。
# 旧形式のボタンは、デプロイ前に送信済みのメッセージ内にまだ残っている可能性があるため、
# handle_message側の解釈は当面残す（新規生成はすべてこの形式のみを使う）。
_NAV_PREFIX = "nav:"


def _make_nav_data(category: str = "", faculty: str = "", department: str = "", classification: str = "") -> str:
    return f"{_NAV_PREFIX}{category}|{faculty}|{department}|{classification}"


def _parse_nav_data(data: str) -> tuple[str, str, str, str] | None:
    if not data.startswith(_NAV_PREFIX):
        return None
    parts = data[len(_NAV_PREFIX):].split("|")
    if len(parts) != 4:
        return None
    return parts[0], parts[1], parts[2], parts[3]


def _breadcrumb(category: str = "", faculty: str = "", department: str = "", classification: str = "") -> str:
    parts = ["科目一覧"]
    if category:
        parts.append(f"{category}科目")
    if faculty:
        parts.append(faculty)
    if department:
        parts.append(department)
    if classification:
        parts.append(classification)
    return " › ".join(parts)


def _reading_key(subj) -> str:
    return (subj.reading or "").strip() or (subj.name or "")


def _build_alpha_split_menu(rows: list, category: str, classification: str,
                             faculty: str, department: str) -> list | None:
    """rowsがよみがな順の均等分割メニューを挟むべき件数のとき、選択メニューのFlexMessageを
    組み立てて返す。絞り込み軸（分類または学部）が定まらない場合はNoneを返し、
    呼び出し側は通常の一覧表示にフォールバックする。"""
    if not classification and not faculty:
        return None
    row_prefix = _make_nav_data(category, faculty, department, classification)
    by_reading = sorted(rows, key=_reading_key)
    chunks = [by_reading[i:i + _ALPHA_CHUNK_SIZE] for i in range(0, len(by_reading), _ALPHA_CHUNK_SIZE)]
    items = []
    for i, chunk in enumerate(chunks):
        first_ch = _reading_key(chunk[0])[:1] or "?"
        last_ch = _reading_key(chunk[-1])[:1] or "?"
        label = f"{first_ch}〜{last_ch}" if first_ch != last_ch else first_ch
        items.append((f"{label}（{len(chunk)}件）", f"{row_prefix}::R:{i}"))

    # 「一つ前の階層に戻る」＝学部が分かっていればその学部の学科/分類選択画面を作り直す
    # （_handle_faculty_menuは学部名だけから学科分割/分類分割いずれかを再判定できるため、
    # ここではどちらの選択画面だったか覚えておく必要がない）。学部が無い（教養や学部横断
    # 分類）場合は選択画面が存在しないため、トップ（教養/専門タブ）に戻るボタンのみになる。
    back_label = back_data = None
    if faculty:
        back_label, back_data = "◀ 学部・学科選択に戻る", _make_nav_data(category="専門", faculty=faculty)
    home_label = home_data = None
    if category:
        home_label, home_data = f"🏠 {category}科目一覧に戻る", category

    return [make_classification_select_flex(
        items, set(),
        title="📚 科目一覧",
        subtitle=f"{len(rows)}件あります。よみがな順で絞り込んでください",
        header_color="#6366f1",
        breadcrumb=_breadcrumb(category, faculty, department, classification),
        back_label=back_label, back_data=back_data,
        home_label=home_label, home_data=home_data,
    )]


async def _build_course_bubbles(rows: list, reviewed_names: set, cls_sort,
                                 breadcrumb: str = "", reading_label: str = "", reading_count: int = 0,
                                 unlocked_names: frozenset = frozenset()) -> list:
    """絞り込み・ソート済みのSubject行から、末尾バリアント統合・シラバス/レビューURL付与済みの
    FlexBubble一覧を組み立てる（8バブル単位のカルーセル分割は呼び出し側で行う）。
    breadcrumb/reading_label/reading_countはヘッダーの現在地表示（パンくず・よみがな
    範囲チップ）に使う。よみがな絞り込みを経由していない場合reading_labelは空文字になり
    チップは表示されない（2026-09-03、科目一覧カードUI改善）。
    unlocked_namesはリクエスト元ユーザーが解除済みの科目名集合（ユーザー個別、既定は空）。
    非空の場合のみ呼び出し側でキャッシュを迂回して都度組み立てる想定
    （2026-09-04、解除済み科目の可視化対応）。"""
    # バリアント判定・束ね方の実体はcore.subject_variants.compute_variant_bases()に一本化済み
    # （faculty+department単位でグループ化する理由等は同関数のdocstring参照）
    names_with_fd = [(c.name, c.faculty or "", c.department or "") for c in rows
                      if (c.classification or "") not in CLASSIFICATION_MERGE_EXCLUDED]
    _letter_split_excluded_names = frozenset(
        c.name for c in rows if (c.classification or "") in LETTER_SPLIT_EXCLUDED_CLASSIFICATIONS)
    # LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONS（国際人間科学部専門科目のみ）に属する科目名は
    # 末尾アルファベットのみが異なる文字バリアントも統合対象に含める（2026-09-04追加、
    # 表示のみの例外。core.subject_variants.LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONS参照）
    _letter_only_included_names = frozenset(
        c.name for c in rows if (c.classification or "") in LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONS)
    _sem_bases, _num_bases, _paren_num_bases, _letter_only_bases = compute_variant_bases(
        names_with_fd, letter_split_excluded_names=_letter_split_excluded_names,
        letter_only_included_names=_letter_only_included_names)

    _num_variant_names = {n for _items in _num_bases.values() for n, _, _, _, _ in _items}
    _num_base_for = {n: _key for _key, _items in _num_bases.items() for n, _, _, _, _ in _items}
    seen_num_base: set[tuple[str, str, str, str, str]] = set()

    def _resolve_num_key(name: str, kind: str, fd: tuple[str, str]) -> tuple[str, str, str, str, str] | None:
        """"numvariant:<suffix>"のkind文字列から_num_basesの元のキーを引き直す。
        通常はvariant_letter_in_suffix(kind)（接尾辞の先頭文字）がそのままletterと一致するが、
        LETTER_SPLIT_EXCLUDED_CLASSIFICATIONS対象科目（語学科目等）はletterをグループ化キーに
        含めない（letter=""）ため、まず実際のletterで試し、見つからなければ""でも試す
        （2026-09-04追加。呼び出し側3箇所の重複ロジックをここに一本化）。"""
        tag = variant_tag_in_suffix(kind)
        letter = variant_letter_in_suffix(kind)
        key = (name, letter, fd[0], fd[1], tag)
        if key in _num_bases:
            return key
        key2 = (name, "", fd[0], fd[1], tag)
        if key2 in _num_bases:
            return key2
        return None

    _sem_variant_names = {n for _items in _sem_bases.values() for n, _ in _items}
    _sem_base_for = {n: _key for _key, _items in _sem_bases.items() for n, _ in _items}
    seen_sem_base: set[tuple[str, str, str]] = set()

    _paren_num_variant_names = {n for _items in _paren_num_bases.values() for n, _, _, _, _, _ in _items}
    _paren_num_base_for = {n: _key for _key, _items in _paren_num_bases.items() for n, _, _, _, _, _ in _items}
    seen_paren_num_base: set[tuple[str, str, str, str, str]] = set()

    _letter_only_variant_names = {n for _items in _letter_only_bases.values() for n, _, _ in _items}
    _letter_only_base_for = {n: _key for _key, _items in _letter_only_bases.items() for n, _, _ in _items}
    seen_letter_only_base: set[tuple[str, str, str, str]] = set()

    # 手動グループ（MANUAL_VARIANT_GROUPS）。グループ内の全科目名が今回のrowsに揃っている
    # 場合のみ適用する（core.subject_variants.MANUAL_VARIANT_GROUPS参照）。
    _rows_name_set = {c.name for c in rows}
    _manual_group_members: dict[str, tuple[str, ...]] = {}
    _manual_name_to_label: dict[str, str] = {}
    for _mg in MANUAL_VARIANT_GROUPS:
        if all(n in _rows_name_set for n in _mg["names"]):
            _manual_group_members[_mg["label"]] = _mg["names"]
            for n in _mg["names"]:
                _manual_name_to_label[n] = _mg["label"]
    seen_manual_label: set[str] = set()

    # syllabus_url は全件キャッシュから取得（DBアクセスなし）
    _sv_by_id = await cache.get_syllabus_urls_cached()
    course_syllabus_urls: dict[str, str] = {c.name: _sv_by_id[c.id] for c in rows if c.id in _sv_by_id}
    course_liff_urls: dict[str, str] = {c.name: make_course_liff_url(c.id) for c in rows}
    # entries内タプルの末尾2要素は、variant/numvariant種別の科目は(faculty, department)を保持する
    # （_num_bases/_sem_basesの正しいキーをclassificationからだけでは復元できないため。
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
        if name in _manual_name_to_label:
            label = _manual_name_to_label[name]
            if label not in seen_manual_label:
                seen_manual_label.add(label)
                groups[cls].append((label, "manual", "", ""))
            continue
        if name in _sem_variant_names:
            base = _sem_base_for[name]
            if base not in seen_sem_base:
                seen_sem_base.add(base)
                items_sorted = sorted(_sem_bases[base], key=lambda x: x[1])
                suffix = "/".join(sk for _, sk in items_sorted)
                groups[cls].append((base[0], f"variant:{suffix}", base[1], base[2]))
            continue
        if name in _num_variant_names:
            key = _num_base_for[name]
            if key not in seen_num_base:
                seen_num_base.add(key)
                suffix = num_variant_suffix(_num_bases[key])
                # key = (base, letter, fac, dept, tag)。fd(fac, dept)はkey[2], key[3]
                groups[cls].append((key[0], f"numvariant:{suffix}", key[2], key[3]))
            continue
        if name in _paren_num_variant_names:
            key = _paren_num_base_for[name]
            if key not in seen_paren_num_base:
                seen_paren_num_base.add(key)
                main_suffix, paren_suffix = paren_num_variant_suffixes(_paren_num_bases[key])
                groups[cls].append((key[0], f"parennumvariant:{main_suffix}|{key[1]}|{paren_suffix}", key[2], key[3]))
            continue
        if name in _letter_only_variant_names:
            key = _letter_only_base_for[name]
            if key not in seen_letter_only_base:
                seen_letter_only_base.add(key)
                suffix = letter_variant_suffix(_letter_only_bases[key])
                # key = (base, fac, dept, tag)
                groups[cls].append((key[0], f"lettervariant:{suffix}", key[1], key[2]))
            continue
        groups[cls].append((name, "single", "", ""))

    def _cat_order(cls: str) -> int:
        return 0 if cls_category.get(cls, "") == "教養" else 1
    all_groups = sorted(groups.items(), key=lambda x: (_cat_order(x[0]), cls_sort(x[0])))

    def _parse_parennum_kind(kind: str) -> tuple[str, str, str]:
        """"parennumvariant:{main_suffix}|{paren_base}|{paren_suffix}"を分解する。"""
        main_suffix, paren_base, paren_suffix = kind.split(":", 1)[1].split("|", 2)
        return main_suffix, paren_base, paren_suffix

    def _entry_any_in(name: str, kind: str, fd: tuple[str, str], names: set) -> bool:
        """kindが表すバリアントグループ内のいずれかの科目名がnamesに含まれるか判定する
        共通ロジック（レビュー有無・解除済み判定の両方で使う、2026-09-04）。"""
        if kind == "single":
            return name in names
        if kind == "manual":
            return any(n in names for n in _manual_group_members.get(name, ()))
        if kind.startswith("variant:"):
            suffixes = kind.split(":", 1)[1].split("/")
            if any(name + s in names for s in suffixes):
                return True
            key = (name, fd[0], fd[1])
            if key in _sem_bases:
                return any(n in names for n, _ in _sem_bases[key])
            return False
        if kind.startswith("numvariant:"):
            key = _resolve_num_key(name, kind, fd)
            if key in _num_bases:
                return any(n in names for n, _, _, _, _ in _num_bases[key])
            return False
        if kind.startswith("parennumvariant:"):
            _main_suffix, paren_base, _paren_suffix = _parse_parennum_kind(kind)
            key = (name, paren_base, fd[0], fd[1], variant_tag_in_suffix(kind))
            if key in _paren_num_bases:
                return any(n in names for n, _, _, _, _, _ in _paren_num_bases[key])
            return False
        if kind.startswith("lettervariant:"):
            key = (name, fd[0], fd[1], variant_tag_in_suffix(kind))
            if key in _letter_only_bases:
                return any(n in names for n, _, _ in _letter_only_bases[key])
            return False
        return False

    def _entry_has_review(name: str, kind: str, fd: tuple[str, str] = ("", "")) -> bool:
        return _entry_any_in(name, kind, fd, reviewed_names)

    def _entry_is_unlocked(name: str, kind: str, fd: tuple[str, str] = ("", "")) -> bool:
        return _entry_any_in(name, kind, fd, unlocked_names)

    def _group_syllabus_url(name: str, kind: str, fd: tuple[str, str] = ("", "")) -> str:
        # 統合表示（variant/numvariant）はbase名がそのままSubject.nameと一致しないため、
        # グループ内で表示順（最も左側）を優先し、そのシラバスURLを代表として採用する
        # （左側が無ければ右側の変種のURLで代替する）。
        if kind == "single":
            return course_syllabus_urls.get(name, "")
        if kind == "manual":
            for n in _manual_group_members.get(name, ()):
                url = course_syllabus_urls.get(n, "")
                if url:
                    return url
            return ""
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
            key = _resolve_num_key(name, kind, fd)
            if key in _num_bases:
                for n, _, _, _, _ in sorted(_num_bases[key], key=lambda x: (x[1], x[2], TAG_PRIORITY.get(x[4], 9))):
                    url = course_syllabus_urls.get(n, "")
                    if url:
                        return url
        if kind.startswith("parennumvariant:"):
            _main_suffix, paren_base, _paren_suffix = _parse_parennum_kind(kind)
            key = (name, paren_base, fd[0], fd[1], variant_tag_in_suffix(kind))
            if key in _paren_num_bases:
                for n, _, _, _, _, _ in sorted(_paren_num_bases[key], key=lambda x: (x[1], x[3])):
                    url = course_syllabus_urls.get(n, "")
                    if url:
                        return url
        if kind.startswith("lettervariant:"):
            key = (name, fd[0], fd[1], variant_tag_in_suffix(kind))
            if key in _letter_only_bases:
                for n, _letter, _tag in sorted(_letter_only_bases[key], key=lambda x: x[1]):
                    url = course_syllabus_urls.get(n, "")
                    if url:
                        return url
        return ""

    # 66pxでは「📝 レビュー」「📄 シラバス」(絵文字+太字4文字)がぎりぎり収まらず見切れて
    # いたため、余裕を持たせた値に拡大する（2026-09-05）。バブルはmega幅で余白が十分あるため
    # 科目名側を圧迫する心配はない。
    # → 90pxだと逆にレビュー/シラバスの文字が科目名側に寄りすぎ、科目名の右端も窮屈という
    # 指摘を受け、両スロットとも1文字分(約13px)縮小。各スロットの開始位置は科目名が2文字分
    # 広がる分だけ右にずれるため、右寄せ(align=end)しているレビュー/シラバスの文字は
    # 差し引き1文字分だけ右に移動する（2026-09-05）。
    _LINK_SLOT_WIDTH = "77px"

    def _link_slot(emoji: str, label: str, color: str, url: str) -> FlexBox:
        # 色付きの丸ピル（塗りつぶし/アウトライン）は複数回試したが、いずれも「結局色や
        # 絵文字の違いにしか見えない」という指摘が続いたため、背景ボックスをやめて
        # 「レビュー」「シラバス」という文字そのものをタップ対象にした下線付きテキスト
        # リンクに変更する（2026-09-03）。
        # 固定幅のスロットに1つずつ配置し、片方しか無い行でも空スロットで位置を
        # 埋めることで、シラバス単独の行・レビュー単独の行・両方ある行のいずれでも
        # 「シラバス」「レビュー」の文字位置（＝科目名の右端）が縦にそろうようにする
        # （2026-09-05、両方あるときに科目名の右端がそろわないというUX指摘への対応）。
        content = (
            FlexText(
                text=f"{emoji} {label}", size="xs", weight="bold", color=color,
                decoration="underline", align="end",
                action=URIAction(label=f"{emoji}{label}"[:20], uri=url),
            ) if url else FlexText(text=" ", size="xs")
        )
        return FlexBox(layout="vertical", width=_LINK_SLOT_WIDTH, flex=0, contents=[content])

    def _status_badge(has_review: bool, is_unlocked: bool = False) -> FlexBox:
        # ●○の点だけでは判別しづらいというUX指摘を受け、系統/学部ブラウズ等と同じ
        # チェックマークバッジ（make_review_badge相当）に統一する（2026-09-03）。
        # 解除済み科目は✓の代わりに🔓を表示する（2026-09-04、優先表示。説明は不要とのユーザー指示）。
        # 🔓自体に視認性があるため、✓のような背景円は付けない
        if is_unlocked:
            return FlexBox(
                layout="vertical", width="16px", height="16px", flex=0,
                justify_content="center", align_items="center",
                contents=[FlexText(text="🔓", size="xxs", align="center")],
            )
        if has_review:
            return FlexBox(
                layout="vertical", width="16px", height="16px", flex=0,
                corner_radius="999px", background_color="#4f46e5",
                justify_content="center", align_items="center",
                contents=[FlexText(text="✓", size="xxs", color="#ffffff", weight="bold", align="center")],
            )
        return FlexBox(
            layout="vertical", width="16px", height="16px", flex=0,
            corner_radius="999px", border_width="1.5px", border_color="#cbd5e1",
            justify_content="center", align_items="center",
            contents=[FlexText(text=" ", size="xxs", color="#ffffff")],
        )

    def _make_bubble(classification: str, entries: list) -> FlexBubble:
        btn_contents = []
        for idx, (name, kind, fac, dept) in enumerate(entries):
            fd = (fac, dept)
            if kind.startswith("variant:"):
                suffix = kind.split(":", 1)[1]
                display = f"{name} ({suffix})"
            elif kind.startswith("numvariant:"):
                # （遠隔）（再履修）タグは元々各要素末尾に個別付与されている（例:
                # "1（再履修）/2（再履修）"）が、視認性のため要素側からタグを取り除き
                # グループ全体の末尾に半角括弧で1回だけ付け直す（2026-09-03、ユーザー指示。
                # 例: "線形代数(1/2/3/4)(再履修)"）。kind文字列自体（タグ埋め込み込み）は
                # _entry_has_review等がvariant_tag_in_suffix()でタグを復元するのに使うため
                # 変更しない
                suffix = kind.split(":", 1)[1]
                tag = variant_tag_in_suffix(kind)
                if tag:
                    nums = "/".join(p[:-len(tag)] for p in suffix.split("/"))
                    display = f"{name}({nums}){tag.replace('（', '(').replace('）', ')')}"
                else:
                    display = f"{name}({suffix})"
            elif kind.startswith("parennumvariant:"):
                main_suffix, paren_base, paren_suffix = _parse_parennum_kind(kind)
                display = f"{name}({main_suffix})（{paren_base}({paren_suffix})）"
            elif kind.startswith("lettervariant:"):
                # numvariantと同じタグ剥がしロジック（要素側の個別タグを取り除き、
                # グループ全体の末尾に半角括弧で1回だけ付け直す）
                suffix = kind.split(":", 1)[1]
                tag = variant_tag_in_suffix(kind)
                if tag:
                    letters = "/".join(p[:-len(tag)] for p in suffix.split("/"))
                    display = f"{name}({letters}){tag.replace('（', '(').replace('）', ')')}"
                else:
                    display = f"{name}({suffix})"
            else:
                display = name
            has_review = _entry_has_review(name, kind, fd)
            is_unlocked = _entry_is_unlocked(name, kind, fd)
            syl_url = _group_syllabus_url(name, kind, fd)
            has_content = has_review or bool(syl_url)
            text_color = "#0f172a" if has_content else "#94a3b8"
            if kind == "single":
                liff_url = course_liff_urls.get(name, "")
            elif kind == "manual":
                members = _manual_group_members.get(name, ())
                first_name = members[0] if members else ""
                liff_url = course_liff_urls.get(first_name, "") if first_name else ""
            elif kind.startswith("variant:"):
                first_suffix = kind.split(":", 1)[1].split("/")[0]
                liff_url = course_liff_urls.get(name + first_suffix, "")
            elif kind.startswith("numvariant:") and (key := _resolve_num_key(name, kind, fd)) in _num_bases:
                first_name = min(_num_bases[key], key=lambda x: (x[1], x[2], TAG_PRIORITY.get(x[4], 9)))[0]
                liff_url = course_liff_urls.get(first_name, "")
            elif kind.startswith("parennumvariant:"):
                _main_suffix, paren_base, _paren_suffix = _parse_parennum_kind(kind)
                pkey = (name, paren_base, fac, dept, variant_tag_in_suffix(kind))
                if pkey in _paren_num_bases:
                    first_name = min(_paren_num_bases[pkey], key=lambda x: (x[1], x[3]))[0]
                    liff_url = course_liff_urls.get(first_name, "")
                else:
                    liff_url = ""
            elif kind.startswith("lettervariant:"):
                lkey = (name, fac, dept, variant_tag_in_suffix(kind))
                if lkey in _letter_only_bases:
                    first_name = min(_letter_only_bases[lkey], key=lambda x: x[1])[0]
                    liff_url = course_liff_urls.get(first_name, "")
                else:
                    liff_url = ""
            else:
                liff_url = ""

            if liff_url:
                # 科目カード（Postbackでchat内に表示するFlexカード）は、ユーザーが
                # 科目名を直接テキスト入力したときのみ表示する。一覧・ランキング等での
                # 科目名タップはバリアント選択を挟まず常に科目詳細LIFFへ直接遷移させる
                name_action = URIAction(label=display[:20], uri=liff_url)
            else:
                name_action = PostbackAction(label=display[:20], data=name)

            row_children = [
                _status_badge(has_review, is_unlocked),
                FlexText(text=display, wrap=True, size="sm", color=text_color, flex=1,
                          weight="bold" if has_review else "regular"),
            ]
            if liff_url or syl_url:
                # 両方の項目を固定幅スロットで並べる（flex=0で「必要な幅だけ」に固定しないと、
                # 科目名テキスト(flex=1)とデフォルトのflex=1同士で横幅を50/50に分け合ってしまい、
                # 科目名が長い行では文字が見切れる。2026-09-05）。
                row_children.append(FlexBox(
                    layout="horizontal", spacing="xs", flex=0,
                    contents=[
                        _link_slot("📝", "レビュー", "#4338ca", liff_url),
                        _link_slot("📄", "シラバス", "#047857", syl_url),
                    ],
                ))

            # 修正理由: 従来は科目名の細いテキスト部分のみがタップ対象だった。1エントリー1行に
            # まとめ、行全体をタップ対象にする（2026-09-03、科目一覧カードUI改善）。
            # エントリーごとの背景色/枠線付きカード化は、件数×バブル数で乗算されFlexメッセージの
            # JSONサイズが膨らみすぎるため見送っている（個別エントリーではなくバッジ＋下線付き
            # テキストリンクで視認性を確保する方針）。
            if idx > 0:
                btn_contents.append(FlexSeparator(margin="sm"))
            btn_contents.append(FlexBox(
                layout="horizontal", spacing="sm", align_items="flex-start",
                action=name_action,
                contents=row_children,
            ))

        base_cls = classification.rstrip("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮")
        faculty_str = cls_faculty.get(base_cls, "")
        home_category = cls_category.get(base_cls, "")
        cat_icon = "📚" if home_category == "教養" else "🎓" if home_category == "専門" else "📖"

        header_contents = []
        if breadcrumb:
            header_contents.append(FlexText(text=breadcrumb, size="xxs", color="#c7d2fe", wrap=True))
        header_contents.append(FlexBox(
            layout="horizontal", spacing="xs", align_items="center", margin="xs",
            contents=[
                FlexBox(
                    layout="vertical", width="20px", height="20px", flex=0,
                    corner_radius="999px", background_color="#ffffff2e",
                    justify_content="center", align_items="center",
                    contents=[FlexText(text=cat_icon, size="xs", align="center")],
                ),
                FlexText(text=classification, weight="bold", color="#ffffff", size="sm", flex=1),
            ],
        ))

        # 現在地表示: よみがな絞り込みを経由している場合は範囲「げ〜し（6件）」を、
        # 学部が分かっていれば学部名も並べて1行で表示する（2026-09-03、現在地が
        # 分かりにくいというUX指摘への対応）。半透明ピルのチップにして視認性を上げる
        # （見た目刷新に伴いプレーンテキストから変更。JSONサイズ増はバブル1個につき
        # ボックス1つ分のみで軽微）。
        locator_parts = []
        if reading_label:
            locator_parts.append(f"{reading_label}（{reading_count}件）")
        if faculty_str:
            locator_parts.append(faculty_str)
        if locator_parts:
            header_contents.append(FlexBox(
                layout="vertical", flex=0, margin="sm",
                padding_all="4px", padding_start="10px", padding_end="10px",
                corner_radius="999px", background_color="#ffffff29",
                contents=[FlexText(text=" ・ ".join(locator_parts), size="xxs", color="#ffffff", wrap=True)],
            ))

        # 修正理由: 従来はこのカルーセルが最終画面で、別の系統/学部を見たくなっても
        # テキストを打ち直す以外に手段が無かった。教養/専門タブに一気に戻るボタンを
        # 全バブルのフッターに付ける（2026-09-02、系統/学部タップ後のルーティングUX改善）。
        # ✓バッジの意味がバブル単体では分からないというUX指摘を受け、系統/学部ブラウズ等と
        # 同じ凡例（make_review_badge_legend）を必ず添える（2026-09-03）。
        footer_contents = [make_review_badge_legend()]
        if home_category:
            footer_contents.append(FlexBox(
                layout="vertical", justify_content="center", align_items="center",
                action=PostbackAction(label=f"{home_category}科目一覧に戻る"[:20], data=home_category),
                background_color="#eef2ff", border_width="1.5px", border_color="#c7d2fe",
                corner_radius="10px", padding_all="10px",
                contents=[FlexText(text=f"‹ {home_category}科目一覧に戻る", size="xs", weight="bold",
                                     color="#4338ca", align="center")],
            ))
        footer = FlexBox(layout="vertical", spacing="sm", padding_all="md", contents=footer_contents)
        return FlexBubble(
            # 「レビュー」「シラバス」の下線テキストリンクが折り返して見切れるため横幅を拡大
            # （2026-09-04）。カルーセル内バブルはgiga不可のためmegaが最大
            size="mega",
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
            footer=footer,
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
                              department: str = "", reading_row: str = "", line_user_id: str = "") -> list:
    _cl_key = f"{category}:{classification}:{faculty}:{department}:{reading_row}"
    # 解除済み科目バッジ（line_user_id依存）はユーザー個別の情報なので、通常の全ユーザー共通
    # キャッシュ（_cl_key）とは相性が悪い。解除済みが1件もないユーザーが大半のため、
    # unlocked_idsが空の間は従来どおりキャッシュを使い、非空のときだけこのリクエストに限り
    # キャッシュを迂回して都度組み立てる（共有キャッシュへは書き込まない）
    # （2026-09-04、解除済み科目の可視化対応）
    unlocked_ids = await _get_unlocked_subject_ids(line_user_id)
    _cached = cache.get_course_list_cache(_cl_key)
    if _cached is not None and not unlocked_ids:
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

    # よみがな範囲チップ（例:「げ〜し（6件）」）は、_build_alpha_split_menuで提示した
    # ラベルと同じ計算方法で作る。どのよみがな範囲を今見ているか一目で分かるように
    # ヘッダーに表示するため（2026-09-03、現在地が分かりにくいというUX指摘への対応）。
    reading_label = ""
    reading_count = 0
    if reading_row:
        try:
            _idx = int(reading_row)
        except ValueError:
            _idx = -1
        by_reading = sorted(rows, key=_reading_key)
        rows = by_reading[_idx * _ALPHA_CHUNK_SIZE:(_idx + 1) * _ALPHA_CHUNK_SIZE] if _idx >= 0 else []
        if rows:
            first_ch = _reading_key(rows[0])[:1] or "?"
            last_ch = _reading_key(rows[-1])[:1] or "?"
            reading_label = f"{first_ch}〜{last_ch}" if first_ch != last_ch else first_ch
            reading_count = len(rows)

    rows = sorted(rows, key=lambda c: (_cls_sort(c.classification or ""), c.sort_order, c.name or ""))
    unlocked_names = frozenset(c.name for c in rows if c.id in unlocked_ids) if unlocked_ids else frozenset()

    if _cached is not None and not unlocked_names:
        # unlocked_idsは非空だったが、この絞り込み範囲(rows)には該当が無かった → 個人化不要、
        # 通常どおり共有キャッシュを返してよい
        return _cached

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

    bubbles = await _build_course_bubbles(
        rows, reviewed_names, _cls_sort,
        breadcrumb=_breadcrumb(category, faculty, department, classification),
        reading_label=reading_label, reading_count=reading_count,
        unlocked_names=unlocked_names,
    )

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
    if not unlocked_names:
        cache.set_course_list_cache(_cl_key, result)
    return result


# ── Ranking ──────────────────────────────────────────────────────

async def _get_rakutan_ranking() -> list:
    # 「楽単度が最も高い」科目群からランダムに5件選ぶ。抽選結果は毎回変えたいのでキャッシュしないが、
    # 母集団(科目ごとの最良楽単度)はcache.get_ease_extremes_cached()側でTTLキャッシュ済み
    # (2026-09-03、リクエストの都度Subject×CourseSection×Review全件JOINしていた重い設計を解消)
    extremes = await cache.get_ease_extremes_cached()
    if not extremes:
        return [TextMessage(text=f"まだ承認済みレビューがありません。\nレビューを投稿してください！\n\n{make_review_liff_url()}")]
    course_best: dict[int, tuple[str, str]] = {sid: (name, best) for sid, (name, best, _worst) in extremes.items()}
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
    # 「鬼単度が最も高い」(=楽単度が最も低い)科目群からランダムに5件選ぶ。抽選結果は毎回変えたいので
    # キャッシュしないが、母集団(科目ごとの最悪楽単度)はcache.get_ease_extremes_cached()側で
    # TTLキャッシュ済み(2026-09-03、リクエストの都度全件JOINしていた重い設計を解消)
    extremes = await cache.get_ease_extremes_cached()
    if not extremes:
        return [TextMessage(text=f"まだ承認済みレビューがありません。\nレビューを投稿してください！\n\n{make_review_liff_url()}")]
    course_best: dict[int, tuple[str, str]] = {sid: (name, worst) for sid, (name, _best, worst) in extremes.items()}
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
    # キャッシュしないが、抽選対象の絞り込みはキャッシュ済みの科目名セットで行い、楽単度も
    # cache.get_ease_extremes_cached()側でTTLキャッシュ済みの値を使うためDBアクセスなしで完結する。
    # 修正理由(2026-09-03): 2026-08-25の修正で全件JOINは解消していたが、抽選で選ばれた10件の
    # 楽単度取得のためだけに毎回DBへ問い合わせるセッション開閉コストが残っていた。楽単5選・鬼単5選
    # (_get_rakutan_ranking/_get_onitan_ranking)と同じキャッシュを使い、DB往復自体を無くす。
    reviewed_names, (cbn, _call), extremes = await asyncio.gather(
        cache.get_reviewed_cached(),
        cache.get_courses_cached(),
        cache.get_ease_extremes_cached(),
    )
    if not reviewed_names:
        return [TextMessage(text=f"まだ承認済みレビューがありません。\nレビューを投稿してください！\n\n{make_review_liff_url()}")]
    pool = [cbn[name] for name in reviewed_names if name in cbn]
    random.shuffle(pool)
    selected_subjects = pool[:10]
    items = [
        {
            "rank": i, "id": s.id, "name": s.name,
            "stars": EASE_STARS.get(extremes.get(s.id, ("", "", ""))[1], ""),
            "ease": extremes.get(s.id, ("", "", ""))[1],
        }
        for i, s in enumerate(selected_subjects, 1)
    ]
    return [make_omikuji_card(items)]


# ── Message handler ─────────────────────────────────────────────

async def _handle_category_entry() -> list:
    """科目一覧の入口画面（教養/専門どちらを見るか2タイルから選んでもらう、案A）。"""
    _menu_key = "menu:entry"
    _cached = cache.get_course_list_cache(_menu_key)
    if _cached is not None:
        return _cached
    _, _all_courses = await cache.get_courses_cached()
    edu_count = sum(1 for c in _all_courses if c.category == "教養")
    senmon_count = sum(1 for c in _all_courses if c.category == "専門")
    result = [make_category_entry_flex(edu_count, senmon_count)]
    cache.set_course_list_cache(_menu_key, result)
    return result


async def _handle_kyoyo_menu(user_id: str = "") -> list:
    _menu_key = "menu:教養"
    # 解除済みバッジはユーザー個別のため、解除済みが1件でもあるユーザーはこのメニューレベルの
    # 共有キャッシュを迂回する（fallback先のhandle_course_listで個別に反映される。
    # 2026-09-04、解除済み科目の可視化対応）
    unlocked_ids = await _get_unlocked_subject_ids(user_id)
    _cached = cache.get_course_list_cache(_menu_key)
    if _cached is not None and not unlocked_ids:
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
        counts = Counter(c.classification for c in edu_courses)
        items = [(cls, _make_nav_data(category="教養", classification=cls), counts[cls]) for cls in clss]
        result = [make_classification_grid_flex("教養", items, reviewed_cls)]
        cache.set_course_list_cache(_menu_key, result)
        return result
    # 分類が1つも無い（登録科目0件）場合の即時一覧フォールバック。個別化されうるので
    # メニュー単位のキャッシュには書き込まない（handle_course_list側で自身のキャッシュを管理）
    return await handle_course_list(category="教養", line_user_id=user_id)


async def _handle_senmon_menu(user_id: str = "") -> list:
    _menu_key = "menu:専門"
    unlocked_ids = await _get_unlocked_subject_ids(user_id)
    _cached = cache.get_course_list_cache(_menu_key)
    if _cached is not None and not unlocked_ids:
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
        fac_counts = {fac: sum(1 for c in sen_courses if (c.faculty or "").startswith(fac))
                      for fac in faculties_present}
        cls_counts = {cls: sum(1 for c in sen_courses if c.classification == cls) for cls in other_clss}
        items = [(fac, _make_nav_data(category="専門", faculty=fac), fac_counts[fac]) for fac in faculties_present] + \
                [(cls, _make_nav_data(category="専門", classification=cls), cls_counts[cls]) for cls in other_clss]
        result = [make_classification_grid_flex("専門", items, display_reviewed)]
        cache.set_course_list_cache(_menu_key, result)
        return result
    return await handle_course_list(category="専門", line_user_id=user_id)


async def _handle_faculty_menu(t: str, user_id: str = "") -> list:
    _menu_key = f"menu:fac:{t}"
    unlocked_ids = await _get_unlocked_subject_ids(user_id)
    _cached = cache.get_course_list_cache(_menu_key)
    if _cached is not None and not unlocked_ids:
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
        items = [(_dept_label(d), _make_nav_data(category="専門", faculty=t, department=d)) for d in dept_sorted]
        result = [make_classification_select_flex(
            items, reviewed_dept_labels,
            title=f"🎓 {t} 専門科目",
            subtitle="学科・専攻を選んでください",
            header_color="#0ea5e9",
            breadcrumb=_breadcrumb("専門"),
            back_label="◀ 学部選択に戻る",
            back_data="専門",
        )]
    elif len(cls_values) > 1:
        # 学科・専攻の区別はないが、分類（群科目等）が複数ある学部（経営学部等）→ 分類一覧
        cls_sorted = sorted(cls_values, key=_cls_sort)
        reviewed_cls = {cls for cls in cls_sorted
                         if any(c.name in reviewed_names_sen for c in fac_courses if c.classification == cls)}
        items = [(cls, _make_nav_data(category="専門", faculty=t, classification=cls)) for cls in cls_sorted]
        result = [make_classification_select_flex(
            items, reviewed_cls,
            title=f"🎓 {t} 専門科目",
            subtitle="分類を選んでください",
            header_color="#0ea5e9",
            breadcrumb=_breadcrumb("専門"),
            back_label="◀ 学部選択に戻る",
            back_data="専門",
        )]
    else:
        # 学科・専攻も複数分類も無い学部 → 即座に科目一覧（件数が多ければ内部で50音行選択に切替）。
        # 個別化されうるのでメニュー単位のキャッシュには書き込まない
        return await handle_course_list(category="専門", classification=next(iter(cls_values)),
                                         line_user_id=user_id)
    cache.set_course_list_cache(_menu_key, result)
    return result


_MSG_SEARCH_LIMIT = 10


async def _handle_course_search(t: str, user_id: str) -> list:
    # 全操作をキャッシュから（DBアクセスなし）。バリアントグループ化・表示名の曖昧さ解消は
    # 検索語に依存しない前処理のため、cache.get_search_index_cached()側で事前計算済み
    # （2026-09-03、自由入力メッセージのたびに5000件超を再計算していたレイテンシ改善で移設。
    # バリアントグループ（末尾の数字/文字/セミナー言語のみが異なる科目、例:
    # 生物学各論A1/A2/C1/C2、外国語セミナーA(英語)/B(英語)）は科目詳細ページ側で
    # レビューを統合表示する([[project_review_view_variant_merge_20260824]])ため、
    # どの変種を選んでも結果は同じになる。選択バブルは廃止し、検索結果ではグループを
    # 1件（グループ内で名前が最も若い科目を代表）に集約して扱う詳細はcache.py参照）。
    _reviewed_names, (cbn, _all_courses), search_rows = await asyncio.gather(
        cache.get_reviewed_cached(),
        cache.get_courses_cached(),
        cache.get_search_index_cached(),
    )

    # Exact course name match
    exact = cbn.get(t)
    if exact:
        if exact.name not in _reviewed_names:
            return [make_no_review_flex(exact, user_id)]
        return [await get_course_flex(exact, user_id)]

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
    # 科目数が多い分類・学部で挟まれる50音行選択（例:"nav:専門|||国際人間科学部専門科目::R:0"）の
    # 行指定部分を切り離す。handle_course_list呼び出し以外の分岐判定には影響しない。
    _reading_row = ""
    if "::R:" in t:
        t, _reading_row = t.split("::R:", 1)

    if t in ["レビュー閲覧", "レビューを閲覧", "科目一覧", "科目", "授業一覧", "一覧"]:
        return await _handle_category_entry()

    if t in ["教養", "教養科目", "教養一覧"]:
        return await _handle_kyoyo_menu(user_id)

    # 系統/学部タップ後のドリルダウン（統一postbackデータ形式、2026-09-02〜）。
    # (category, faculty, department, classification) の4要素から、どの粒度の画面へ
    # 進むべきかをここで一括判定する（学科・分類の絞り込みが必要な学部は選択画面を、
    # 絞り込み不要な学部・系統・分類は直接科目一覧を返す）。
    _nav = _parse_nav_data(t)
    if _nav is not None:
        _cat, _fac, _dept, _cls = _nav
        if _dept:
            return await handle_course_list(category="専門", faculty=_fac, department=_dept,
                                             reading_row=_reading_row, line_user_id=user_id)
        if _cls:
            return await handle_course_list(category=_cat, classification=_cls,
                                             reading_row=_reading_row, line_user_id=user_id)
        if _fac:
            return await _handle_faculty_menu(_fac, user_id)
        if _cat:
            return await handle_course_list(category=_cat, reading_row=_reading_row, line_user_id=user_id)

    # ここから下は旧形式（"教養:"/"専門:"/"専門F:"/分類名そのまま）のpostbackデータ用の
    # 後方互換フォールバック。新規生成するボタンはすべて上のnav:形式に統一済みだが、
    # デプロイ前に送信済みのメッセージ内にまだ古い形式のボタンが残っている可能性があるため
    # 解釈だけは残す（新規追加時にこの節を増やす必要はない）。
    if t.startswith("教養:"):
        cls = t[len("教養:"):]
        return await handle_course_list(category="教養", classification=cls,
                                         reading_row=_reading_row, line_user_id=user_id)

    if t.startswith("専門:"):
        cls = t[len("専門:"):]
        return await handle_course_list(category="専門", classification=cls,
                                         reading_row=_reading_row, line_user_id=user_id)

    if t.startswith("専門F:"):
        fac_dept = t[len("専門F:"):]
        fac, _, dept = fac_dept.partition("|")
        return await handle_course_list(category="専門", faculty=fac, department=dept,
                                         reading_row=_reading_row, line_user_id=user_id)

    # 分類名の直接タップ（例：「教養(社会)」、旧形式）
    if t in await cache.get_cls_set():
        return await handle_course_list(classification=t, reading_row=_reading_row, line_user_id=user_id)

    if t == "専門comingsoon":
        return [TextMessage(text="🚧 専門科目一覧は現在準備中です。\nもうしばらくお待ちください！")]

    if t in ["専門科目", "専門", "専門一覧"]:
        return await _handle_senmon_menu(user_id)

    # 学部名タップ（例："経営学部"、旧形式。現在は生成しないが後方互換のため残す）
    if t in await cache.get_faculty_order():
        return await _handle_faculty_menu(t, user_id)

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
                # 修正理由: LINEはブロック解除でも新規フォローと同じFollowEventを送るため、
                # 登録済みユーザーが再フォローしても従来は毎回「会員登録」Flexを無条件で
                # 返していた(2026-09-02、大西さんが登録済みなのに再度会員登録を求められた
                # 事象の根本原因)。登録要否を先に判定し、登録済みなら使い方案内を返す
                incomplete = await _registration_incomplete(user_id)
                try:
                    if incomplete:
                        register_url = make_register_url(user_id)
                        await line_client.reply(event.reply_token, [make_registration_flex(register_url)])
                    else:
                        await line_client.reply(event.reply_token, [make_help_flex()])
                    asyncio.create_task(save_log_bg(user_id, "in", "[follow]"))
                except Exception as exc:
                    await save_error_log(exc, action="follow")
                try:
                    if incomplete:
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
