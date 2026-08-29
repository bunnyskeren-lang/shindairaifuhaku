"""科目名の末尾バリアント統合ロジック（レビュー投稿フォーム・LINE bot・管理画面用）。

_vnum_match()・_VSEM（文字A/B/C/D・セミナー系・数字/ローマ数字のいずれもfaculty+department単位で
グループ化する。2026-08-25以前はclassification単位だったが、classificationは学部をまたいで
共有されうる表示カテゴリでしかなく、subjects.nameの実際の識別単位（UNIQUE制約
name+faculty+department）と食い違うことがあったため統一した。文字バリアント・セミナー系は
2026-08-29以前はfaculty/department非依存で判定しており、別学部の同名バリアントを誤統合する
バグがあったため数字バリアントと同じ基準に揃えた）はここが唯一の定義で、
line_bot/handler.py はこのモジュールからimportして使う（2026-08-25以前は同一ロジックを
手動で複製していたが、byte単位の同期漏れリスクをなくすため一本化した）。

compute_variant_groups()（レビュー投稿フォーム/api/preload・LINE botメッセージ検索向け）に対し、
グループ化そのものの手順（seenの積み上げ方・グループラベルの組み立て）はline_bot/handler.py
_build_course_bubbles()内にFlex Message構築と密結合した形で別途実装されている。
「どの科目名同士が同じグループになるか」の判定規則自体（上記_vnum_match/_VSEM）は共有済みだが、
グループの束ね方の手順を変更する場合は_build_course_bubbles()側も合わせて確認すること
（2026-08-29、この関数側の手順は文字バリアント・セミナー系の判定基準がfaculty/department
非依存のまま追随できておらず、compute_variant_groups()側だけ先に修正されていた状態で
1日近く残っていた。修正の際は判定規則だけでなく、束ね方の手順側の同期漏れも都度確認すること）。

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


def compute_variant_groups(names_with_faculty_dept: list[tuple[str, str, str]]) -> dict[str, str]:
    """(科目名, faculty, department)のリストから、末尾のアルファベット/数字/ローマ数字/セミナー言語
    だけが異なる2件以上の科目名をグループ化し、科目名→表示用グループラベル（ベース名）の
    マップを返す。グループに属さない（＝バリアントが1件だけ、または該当パターンなし）
    科目名はマップに含めない。
    """
    names = [n for n, _, _ in names_with_faculty_dept]
    fd_by_name = {n: (f, d) for n, f, d in names_with_faculty_dept}
    name_fd_set = set(names_with_faculty_dept)
    result: dict[str, str] = {}

    # 1) セミナー系（外国語セミナーA(英語) → 外国語セミナー(英語)、同一faculty+department単位）
    sem_bases: dict[tuple[str, str, str], list[str]] = {}
    for name in names:
        m = _VSEM.match(name)
        if m:
            fac, dept = fd_by_name.get(name, ("", ""))
            sem_bases.setdefault((m.group(1) + m.group(3), fac, dept), []).append(name)
    for (base_lang, _fac, _dept), members in sem_bases.items():
        if len(members) >= 2:
            for n in members:
                result[n] = base_lang

    # 2) 文字バリアント（末尾がA/B/C/Dのみ、数字を伴わないもの、同一faculty+department単位でグループ化。
    # 2026-08-29以前はfaculty/department非依存で判定しており、別学部の同名バリアントを誤統合しうる
    # バグがあった（数字バリアントの3)は元からfaculty+department単位）。
    for name in names:
        if name in result or not name or len(name) <= 1:
            continue
        if name[-1] in ('A', 'B', 'C', 'D'):
            base = name[:-1]
            fac, dept = fd_by_name.get(name, ("", ""))
            variants = [s for s in 'ABCD' if (base + s, fac, dept) in name_fd_set]
            if len(variants) >= 2:
                result[name] = base

    # 3) 数字・ローマ数字バリアント（同一faculty+department単位でグループ化）
    num_bases: dict[tuple[str, str, str], list[str]] = {}
    for name in names:
        if name in result:
            continue
        m = _vnum_match(name)
        if m:
            base, _letter, _sk, _disp = m
            fac, dept = fd_by_name.get(name, ("", ""))
            key = (base, fac, dept)
            num_bases.setdefault(key, []).append(name)
    for (base, _fac, _dept), members in num_bases.items():
        if len(members) >= 2:
            for n in members:
                result[n] = base

    return result


def compute_variant_display_groups(
    names_with_classification: list[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    """(科目名, classification)のリストから、同一classification内で末尾のみが異なる2件以上の
    科目名をグループ化し、(科目名, classification) → 表示用グループラベル
    （例: "生物学各論 (A1/A2/C1/C2)"）のマップを返す。
    書式はLINE bot科目一覧（line_bot/handler.py _make_bubble の f"{name} ({suffix})"）に合わせる。
    管理画面での一括編集・一括削除に使うため、compute_variant_groups()と異なり
    文字バリアント・セミナー系も含め全パターンをclassification単位でグループ化する
    （同名科目が別学部・別分類に存在する場合の誤統合を避けるため）。
    戻り値のキーを(科目名, classification)のペアにしているのは、同じ科目名が複数の
    classificationにまたがって別々のSubjectとして存在するケース（例:「病理学Ⅰ」が
    理学療法学専攻・検査技術科学専攻・作業療法学専攻でそれぞれ別科目として存在する）を
    区別するため。科目名だけをキーにすると同名別科目のclassificationが1つに潰れ、
    本来別グループのバリアントが誤って1グループに結合される
    （2026-08-28、「病理学 (Ⅰ/Ⅰ/Ⅰ/Ⅱ/Ⅱ/Ⅱ)」のような重複ラベルが出るバグとして発覚し修正）。
    グループに属さない（バリアントが1件だけ、または該当パターンなし）科目はマップに含めない。
    """
    result: dict[tuple[str, str], str] = {}
    assigned: set[tuple[str, str]] = set()
    items = list(dict.fromkeys((n, c or "") for n, c in names_with_classification))
    item_set = set(items)

    # 1) セミナー系（外国語セミナーA(英語) → 外国語セミナー(英語) (A/B/C/D)）
    sem_bases: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for name, cls in items:
        m = _VSEM.match(name)
        if m:
            sem_bases.setdefault((m.group(1) + m.group(3), cls), []).append((name, cls, m.group(2)))
    for (base, _cls), members in sem_bases.items():
        if len(members) < 2:
            continue
        members_sorted = sorted(members, key=lambda x: x[2])
        label = f"{base} ({'/'.join(sk for _, _, sk in members_sorted)})"
        for n, c, _sk in members_sorted:
            result[(n, c)] = label
            assigned.add((n, c))

    # 2) 文字バリアント（末尾がA/B/C/Dのみ、数字を伴わないもの）
    letter_bases: dict[tuple[str, str], list[str]] = {}
    for name, cls in items:
        if (name, cls) in assigned or not name or len(name) <= 1 or (name, cls) not in item_set:
            continue
        if name[-1] in ('A', 'B', 'C', 'D'):
            base = name[:-1]
            key = (base, cls)
            if key in letter_bases:
                continue
            variants = [s for s in 'ABCD' if (base + s, cls) in item_set]
            if len(variants) >= 2:
                letter_bases[key] = variants
    for (base, cls), variants in letter_bases.items():
        label = f"{base} ({'/'.join(variants)})"
        for s in variants:
            result[(base + s, cls)] = label
            assigned.add((base + s, cls))

    # 3) 数字・ローマ数字バリアント（同一classification単位でグループ化）
    num_bases: dict[tuple[str, str], list[tuple[str, str, int, str]]] = {}
    for name, cls in items:
        if (name, cls) in assigned:
            continue
        m = _vnum_match(name)
        if m:
            base, letter, sk, disp = m
            key = (base, cls)
            num_bases.setdefault(key, []).append((name, letter, sk, disp))
    for (base, cls), members in num_bases.items():
        if len(members) < 2:
            continue
        members_sorted = sorted(members, key=lambda x: (x[1], x[2]))
        suffix = "/".join(f"{letter}{disp}" for _, letter, _sk, disp in members_sorted)
        label = f"{base} ({suffix})"
        for n, _letter, _sk, _disp in members_sorted:
            result[(n, cls)] = label
            assigned.add((n, cls))

    return result
