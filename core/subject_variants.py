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
# 末尾の（遠隔）（再履修）タグ（programing files/import_syllabus.py clean_name()が付与、
# REMOTE/RETAKEクラスを別科目として登録する仕組み）は、数字/ローマ数字バリアントと同じ
# 基準（末尾の文字＋数字）を保ったまま追加の識別子として扱う。ただし遠隔クラスは対面クラスと
# 授業形態そのものが異なり同一視できないため、遠隔タグの有無でグループを分ける
# （遠隔は遠隔同士、それ以外（無タグ・再履修タグのみ）は無タグ・再履修同士で統合。
# 2026-08-31にユーザー指示で「同一グループに混在」から変更）。タグの優先順位は表示上の
# ソート用（無タグ→遠隔→再履修→両方の順）で、line_bot/handler.py側の束ね方の手順でも
# 同じ順序を使うため公開名にしている。
TAG_PRIORITY = {"": 0, "（遠隔）": 1, "（再履修）": 2, "（遠隔）（再履修）": 3}
_VNUM = re.compile(r'^(.*?)[\s　]*([A-ZＡ-Ｚ])?(\d+|[Ⅰ-Ⅻ])((?:（遠隔）|（再履修）)*)$')
_VSEM = re.compile(r'^(.*?セミナー)([A-Z]|\d+)(\([^)]+\))$')

# 教養(人文)/(社会)/(自然)/(総合)/(健康・スポーツ)は、末尾のA/B/C/Dが「並行クラス」ではなく
# トピック違いの独立した科目であることが多く、語尾アルファベットだけで1行に統合表示すると
# 内容の異なる科目を同一視してしまう。2026-08-31にユーザー指示でこの5分類のみ文字バリアント
# 統合（数字/ローマ数字・セミナー系は対象外）から除外した。DB上は元々別々のSubject行のまま
# （このモジュールは表示統合のみを扱うため、DBには一切影響しない）。
LETTER_MERGE_EXCLUDED_CLASSIFICATIONS = frozenset({
    "教養(人文)", "教養(社会)", "教養(自然)", "教養(総合)", "教養(健康・スポーツ)",
})


def _vnum_match(name: str) -> tuple[str, str, int, str, str] | None:
    m = _VNUM.match(name)
    if not m:
        return None
    base = m.group(1).strip()
    letter = (m.group(2) or "").translate(_FULLWIDTH_UPPER)
    raw = m.group(3)
    tag = m.group(4) or ""
    if raw in _ROMAN_VAL:
        return base, letter, _ROMAN_VAL[raw], raw, tag
    return base, letter, int(raw), raw, tag


def compute_variant_bases(
    names_with_faculty_dept: list[tuple[str, str, str]],
    letter_excluded_names: frozenset[str] = frozenset(),
) -> tuple[
    dict[tuple[str, str, str], list[tuple[str, str]]],
    dict[tuple[str, str, str], list[str]],
    dict[tuple[str, str, str, bool], list[tuple[str, str, int, str, str]]],
]:
    """バリアント判定の実体。(科目名, faculty, department)のリストから、セミナー系/文字(A-D)/
    数字・ローマ数字の3種のバリアントグループを (base[+言語], faculty, department) キーで
    束ねた辞書(sem_bases, letter_bases, num_bases)を返す（メンバーが2件未満のキーは含めない）。
    num_basesのキーのみ、末尾に遠隔タグの有無(bool)を追加で持つ（遠隔クラスと対面クラスは
    別グループとして扱うため）。

    compute_variant_groups()（フラットなname→labelマップ、レビュー投稿フォーム/api/preload用）と
    line_bot.handler._build_course_bubbles()（Flex Message構築、シラバス/レビューURL等メンバーの
    詳細情報が必要）の両方はこの関数の結果から必要な形に組み立てる。
    （2026-08-30、判定規則(_vnum_match/_VSEM)はimportで共有済みだったが「束ね方の手順」自体が
    line_bot/handler.py側に別途手動複製されており、同期漏れが繰り返し起きていた反省から
    実体をここに一本化した。セミナー系・文字(A-D)・数字/ローマ数字の3パターンは正規表現の
    形（セミナーは丸括弧付き、文字は末尾に数字を伴わない、数字/ローマ数字は末尾が必ず
    数字かローマ数字）から互いに排他的なので、構築順序（先に確定した種別を後の判定から
    除外する等）を気にせず独立に計算してよい。）

    letter_excluded_names（LETTER_MERGE_EXCLUDED_CLASSIFICATIONSに属する科目名の集合）に
    含まれる名前は、文字(A-D)バリアントの判定・グループ化から除外する（他のメンバーとも
    統合させない）。数字・ローマ数字・セミナー系の判定には影響しない。
    """
    names = [n for n, _, _ in names_with_faculty_dept]
    fd_by_name = {n: (f, d) for n, f, d in names_with_faculty_dept}
    name_fd_set = set(names_with_faculty_dept)

    sem_bases: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for name in names:
        m = _VSEM.match(name)
        if m:
            fac, dept = fd_by_name.get(name, ("", ""))
            key = (m.group(1) + m.group(3), fac, dept)
            sem_bases.setdefault(key, []).append((name, m.group(2)))
    sem_bases = {k: v for k, v in sem_bases.items() if len(v) >= 2}

    letter_bases: dict[tuple[str, str, str], list[str]] = {}
    for name in names:
        if not name or len(name) <= 1 or name[-1] not in ('A', 'B', 'C', 'D'):
            continue
        if name in letter_excluded_names:
            continue
        base = name[:-1]
        fac, dept = fd_by_name.get(name, ("", ""))
        key = (base, fac, dept)
        if key in letter_bases:
            continue
        variants = [
            s for s in 'ABCD'
            if (base + s, fac, dept) in name_fd_set and (base + s) not in letter_excluded_names
        ]
        if len(variants) >= 2:
            letter_bases[key] = variants

    num_bases: dict[tuple[str, str, str, bool], list[tuple[str, str, int, str, str]]] = {}
    for name in names:
        m = _vnum_match(name)
        if m:
            base, letter, sk, disp, tag = m
            fac, dept = fd_by_name.get(name, ("", ""))
            key = (base, fac, dept, "（遠隔）" in tag)
            num_bases.setdefault(key, []).append((name, letter, sk, disp, tag))
    num_bases = {k: v for k, v in num_bases.items() if len(v) >= 2}

    return sem_bases, letter_bases, num_bases


def compute_variant_groups(
    names_with_faculty_dept: list[tuple[str, str, str]],
    letter_excluded_names: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """(科目名, faculty, department)のリストから、末尾のアルファベット/数字/ローマ数字/セミナー言語
    だけが異なる2件以上の科目名をグループ化し、科目名→表示用グループラベル（ベース名）の
    マップを返す。グループに属さない（＝バリアントが1件だけ、または該当パターンなし）
    科目名はマップに含めない。letter_excluded_namesの扱いはcompute_variant_bases()参照。
    """
    sem_bases, letter_bases, num_bases = compute_variant_bases(names_with_faculty_dept, letter_excluded_names)
    result: dict[str, str] = {}

    for (base_lang, _fac, _dept), members in sem_bases.items():
        for n, _sk in members:
            result[n] = base_lang

    for (base, _fac, _dept), variants in letter_bases.items():
        for s in variants:
            name = base + s
            if name not in result:
                result[name] = base

    for (base, _fac, _dept, _remote), members in num_bases.items():
        for n, _letter, _sk, _disp, _tag in members:
            if n not in result:
                result[n] = base

    return result


def compute_variant_full_labels(
    names_with_faculty_dept: list[tuple[str, str, str]],
    letter_excluded_names: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """(科目名, faculty, department)のリストから、科目名 → 括弧付き接尾辞込みの完全な
    グループ表示名（例: "力学基礎(1/2)"、"生物学各論(A1/A2/C1/C2)"）のマップを返す。

    compute_variant_groups()はベースラベル（接尾辞を含まない科目名の共通部分）のみを返すため、
    ベースラベルだけでは元の科目名と見分けがつかない画面（管理画面のレビュー科目別集計等）
    向けに追加した。判定基準はcompute_variant_groups()と同一（compute_variant_bases()を共有）。
    グループに属さない科目名はマップに含めない。letter_excluded_namesの扱いは
    compute_variant_bases()参照。
    """
    sem_bases, letter_bases, num_bases = compute_variant_bases(names_with_faculty_dept, letter_excluded_names)
    result: dict[str, str] = {}

    for (base_lang, _fac, _dept), members in sem_bases.items():
        suffix = "/".join(sk for _n, sk in sorted(members, key=lambda x: x[1]))
        label = f"{base_lang}({suffix})"
        for n, _sk in members:
            result[n] = label

    for (base, _fac, _dept), variants in letter_bases.items():
        label = f"{base}({'/'.join(variants)})"
        for s in variants:
            name = base + s
            if name not in result:
                result[name] = label

    for (base, _fac, _dept, _remote), members in num_bases.items():
        members_sorted = sorted(members, key=lambda x: (x[1], x[2], TAG_PRIORITY.get(x[4], 9)))
        suffix = "/".join(f"{letter}{disp}{tag}" for _n, letter, _sk, disp, tag in members_sorted)
        label = f"{base}({suffix})"
        for n, _letter, _sk, _disp, _tag in members:
            if n not in result:
                result[n] = label

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
    # LETTER_MERGE_EXCLUDED_CLASSIFICATIONSに属する分類は文字バリアント統合の対象外
    # （compute_variant_bases()参照）
    letter_bases: dict[tuple[str, str], list[str]] = {}
    for name, cls in items:
        if (name, cls) in assigned or not name or len(name) <= 1 or (name, cls) not in item_set:
            continue
        if cls in LETTER_MERGE_EXCLUDED_CLASSIFICATIONS:
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

    # 3) 数字・ローマ数字バリアント（同一classification単位でグループ化。遠隔タグの有無でも
    # グループを分ける＝遠隔は遠隔同士、それ以外は それ以外同士でのみ統合する）
    num_bases: dict[tuple[str, str, bool], list[tuple[str, str, int, str, str]]] = {}
    for name, cls in items:
        if (name, cls) in assigned:
            continue
        m = _vnum_match(name)
        if m:
            base, letter, sk, disp, tag = m
            key = (base, cls, "（遠隔）" in tag)
            num_bases.setdefault(key, []).append((name, letter, sk, disp, tag))
    for (base, cls, _remote), members in num_bases.items():
        if len(members) < 2:
            continue
        members_sorted = sorted(members, key=lambda x: (x[1], x[2], TAG_PRIORITY.get(x[4], 9)))
        suffix = "/".join(f"{letter}{disp}{tag}" for _, letter, _sk, disp, tag in members_sorted)
        label = f"{base} ({suffix})"
        for n, _letter, _sk, _disp, _tag in members_sorted:
            result[(n, cls)] = label
            assigned.add((n, cls))

    return result
