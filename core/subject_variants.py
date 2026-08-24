"""科目名の末尾バリアント統合ロジック（レビュー投稿フォーム・管理画面用）。

line_bot/handler.py の _vnum_match()・variant/numvariant 判定ロジックと同じ規則
（文字A/B/C/Dとセミナー系はclassification非依存、数字・ローマ数字はclassification単位）
をレビュー投稿フォーム（/api/preload）向けに複製したもの。Flex Message構築に密結合した
handler.py側のロジックとは独立させているため、判定規則を変更する場合は両方を更新すること。

compute_variant_display_groups() は管理画面の科目一覧（routers/admin/courses.py）向けに
別途追加したもので、一括編集・一括削除という破壊的操作の誤爆を避けるため、文字バリアント・
セミナー系も含め全パターンをclassification単位でグループ化する（compute_variant_groups()より
グループ化条件が厳しい）。
"""
import re

_FULLWIDTH_UPPER = str.maketrans(
    "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
)
_ROMAN_VAL = {chr(0x2160 + i): i + 1 for i in range(12)}  # Ⅰ→1 ... Ⅻ→12
_VNUM = re.compile(r'^(.*?)[\s　]*([A-ZＡ-Ｚ])?(\d+|[Ⅰ-Ⅻ])$')
_VSEM = re.compile(r'^(.*?セミナー)([A-Z]|\d+)(\([^)]+\))$')


def _vnum_match(name: str) -> tuple[str, str, int, str] | None:
    m = _VNUM.match(name)
    if not m:
        return None
    base = m.group(1).strip()
    letter = (m.group(2) or "").translate(_FULLWIDTH_UPPER)
    raw = m.group(3)
    if raw in _ROMAN_VAL:
        return base, letter, _ROMAN_VAL[raw], raw
    return base, letter, int(raw), raw


def compute_variant_groups(names_with_classification: list[tuple[str, str]]) -> dict[str, str]:
    """(科目名, classification)のリストから、末尾のアルファベット/数字/ローマ数字/セミナー言語
    だけが異なる2件以上の科目名をグループ化し、科目名→表示用グループラベル（ベース名）の
    マップを返す。グループに属さない（＝バリアントが1件だけ、または該当パターンなし）
    科目名はマップに含めない。
    """
    names = [n for n, _ in names_with_classification]
    name_set = set(names)
    cls_by_name = dict(names_with_classification)
    result: dict[str, str] = {}

    # 1) セミナー系（外国語セミナーA(英語) → 外国語セミナー(英語)）
    sem_bases: dict[str, list[str]] = {}
    for name in names:
        m = _VSEM.match(name)
        if m:
            sem_bases.setdefault(m.group(1) + m.group(3), []).append(name)
    for base_lang, members in sem_bases.items():
        if len(members) >= 2:
            for n in members:
                result[n] = base_lang

    # 2) 文字バリアント（末尾がA/B/C/Dのみ、数字を伴わないもの）
    for name in names:
        if name in result or not name or len(name) <= 1:
            continue
        if name[-1] in ('A', 'B', 'C', 'D'):
            base = name[:-1]
            variants = [s for s in 'ABCD' if base + s in name_set]
            if len(variants) >= 2:
                result[name] = base

    # 3) 数字・ローマ数字バリアント（同一classification単位でグループ化）
    num_bases: dict[tuple[str, str], list[str]] = {}
    for name in names:
        if name in result:
            continue
        m = _vnum_match(name)
        if m:
            base, _letter, _sk, _disp = m
            key = (base, cls_by_name.get(name) or "その他")
            num_bases.setdefault(key, []).append(name)
    for (base, _cls), members in num_bases.items():
        if len(members) >= 2:
            for n in members:
                result[n] = base

    return result


def compute_variant_display_groups(names_with_classification: list[tuple[str, str]]) -> dict[str, str]:
    """(科目名, classification)のリストから、末尾のみが異なる2件以上の科目名をグループ化し、
    科目名 → 表示用グループラベル（例: "生物学各論 (A1/A2/C1/C2)"）のマップを返す。
    書式はLINE bot科目一覧（line_bot/handler.py _make_bubble の f"{name} ({suffix})"）に合わせる。
    管理画面での一括編集・一括削除に使うため、compute_variant_groups()と異なり
    文字バリアント・セミナー系も含め全パターンをclassification単位でグループ化する
    （同名科目が別学部・別分類に存在する場合の誤統合を避けるため）。
    グループに属さない（バリアントが1件だけ、または該当パターンなし）科目名はマップに含めない。
    """
    result: dict[str, str] = {}
    assigned: set[str] = set()
    names = [n for n, _ in names_with_classification]
    name_set = set(names)
    cls_by_name = dict(names_with_classification)

    def _cls_of(name: str) -> str:
        return cls_by_name.get(name) or "その他"

    # 1) セミナー系（外国語セミナーA(英語) → 外国語セミナー(英語) (A/B/C/D)）
    sem_bases: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for name in names:
        m = _VSEM.match(name)
        if m:
            sem_bases.setdefault((m.group(1) + m.group(3), _cls_of(name)), []).append((name, m.group(2)))
    for (base, _cls), members in sem_bases.items():
        if len(members) < 2:
            continue
        members_sorted = sorted(members, key=lambda x: x[1])
        label = f"{base} ({'/'.join(sk for _, sk in members_sorted)})"
        for n, _sk in members_sorted:
            result[n] = label
            assigned.add(n)

    # 2) 文字バリアント（末尾がA/B/C/Dのみ、数字を伴わないもの）
    letter_bases: dict[tuple[str, str], list[str]] = {}
    for name in names:
        if name in assigned or not name or len(name) <= 1:
            continue
        if name[-1] in ('A', 'B', 'C', 'D'):
            base = name[:-1]
            key = (base, _cls_of(name))
            variants = [s for s in 'ABCD' if base + s in name_set and _cls_of(base + s) == key[1]]
            if len(variants) >= 2:
                letter_bases[key] = variants
    for (base, _cls), variants in letter_bases.items():
        label = f"{base} ({'/'.join(variants)})"
        for s in variants:
            result[base + s] = label
            assigned.add(base + s)

    # 3) 数字・ローマ数字バリアント（同一classification単位でグループ化）
    num_bases: dict[tuple[str, str], list[tuple[str, str, int, str]]] = {}
    for name in names:
        if name in assigned:
            continue
        m = _vnum_match(name)
        if m:
            base, letter, sk, disp = m
            key = (base, _cls_of(name))
            num_bases.setdefault(key, []).append((name, letter, sk, disp))
    for (base, _cls), members in num_bases.items():
        if len(members) < 2:
            continue
        members_sorted = sorted(members, key=lambda x: (x[1], x[2]))
        suffix = "/".join(f"{letter}{disp}" for _, letter, _sk, disp in members_sorted)
        label = f"{base} ({suffix})"
        for n, _letter, _sk, _disp in members_sorted:
            result[n] = label
            assigned.add(n)

    return result
