"""科目名の末尾バリアント統合ロジック（レビュー投稿フォーム用）。

line_bot/handler.py の _vnum_match()・variant/numvariant 判定ロジックと同じ規則
（文字A/B/C/Dとセミナー系はclassification非依存、数字・ローマ数字はclassification単位）
をレビュー投稿フォーム（/api/preload）向けに複製したもの。Flex Message構築に密結合した
handler.py側のロジックとは独立させているため、判定規則を変更する場合は両方を更新すること。
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
