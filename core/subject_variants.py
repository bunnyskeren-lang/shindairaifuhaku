"""科目名の末尾バリアント統合ロジック（レビュー投稿フォーム・LINE bot・管理画面用）。

_vnum_match()・_VSEM（セミナー系・数字/ローマ数字のいずれもfaculty+department単位で
グループ化する。2026-08-25以前はclassification単位だったが、classificationは学部をまたいで
共有されうる表示カテゴリでしかなく、subjects.nameの実際の識別単位（UNIQUE制約
name+faculty+department）と食い違うことがあったため統一した。セミナー系は
2026-08-29以前はfaculty/department非依存で判定しており、別学部の同名バリアントを誤統合する
バグがあったため数字バリアントと同じ基準に揃えた）はここが唯一の定義で、
line_bot/handler.py はこのモジュールからimportして使う（2026-08-25以前は同一ロジックを
手動で複製していたが、byte単位の同期漏れリスクをなくすため一本化した）。

末尾のアルファベット（A/B/C/Dのみが異なる）だけを根拠にした統合は2026-09-02にユーザー指示で
恒常的に廃止した。並行クラス（同一内容の別クラス）と、トピックが異なる独立科目の見分けが
アルファベットの有無だけでは付かず、誤統合が繰り返し問題になっていたため。

compute_variant_groups()（レビュー投稿フォーム/api/preload・LINE botメッセージ検索向け）に対し、
グループ化そのものの手順（seenの積み上げ方・グループラベルの組み立て）はline_bot/handler.py
_build_course_bubbles()内にFlex Message構築と密結合した形で別途実装されている。
「どの科目名同士が同じグループになるか」の判定規則自体（上記_vnum_match/_VSEM）は共有済みだが、
グループの束ね方の手順を変更する場合は_build_course_bubbles()側も合わせて確認すること
（2026-08-29、この関数側の手順は文字バリアント・セミナー系の判定基準がfaculty/department
非依存のまま追随できておらず、compute_variant_groups()側だけ先に修正されていた状態で
1日近く残っていた。修正の際は判定規則だけでなく、束ね方の手順側の同期漏れも都度確認すること）。

compute_variant_display_groups() は管理画面の科目一覧（routers/admin/courses.py）向けに
別途追加したもので、一括編集・一括削除という破壊的操作の誤爆を避けるため、セミナー系も
含め全パターンをclassification単位でグループ化する（compute_variant_groups()より
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
# 基準（末尾の文字＋数字）を保ったまま追加の識別子として扱う。授業形態・受講条件が異なる
# クラス同士を同一視しないよう、タグが完全一致するクラス同士でのみ統合する（無タグ→無タグ
# 同士、遠隔→遠隔同士、再履修→再履修同士、遠隔＋再履修→遠隔＋再履修同士の4系統に分離。
# 2026-08-31は遠隔タグの有無だけで区別し無タグと再履修タグを同一グループに混在させていたが、
# 2026-09-02にユーザー指示で「再履修は再履修のみで統合」に変更し完全一致に揃えた）。
# タグの優先順位は表示上のソート用（無タグ→遠隔→再履修→両方の順）で、line_bot/handler.py
# 側の束ね方の手順でも同じ順序を使うため公開名にしている。
TAG_PRIORITY = {"": 0, "（遠隔）": 1, "（再履修）": 2, "（遠隔）（再履修）": 3}
REMOTE_TAG = "（遠隔）"
# 末尾の（遠隔）（再履修）タグに加え、それらでは説明できない任意の括弧付き説明書き
# （例:「環境基礎科学実験A1（主に地学）」の（主に地学）、「運動科学-1 （航海学領域）」の
# （航海学領域））も末尾に1つだけ許容する（2026-09-03、ユーザー指示で「科目名(1/2)（説明書き）」
# 形式の科目もバリアント統合表示の対象に追加。グループ化キー（tag）にこの説明書きを
# そのまま含める＝説明書きが完全一致するメンバー同士でのみ統合されるため、無関係な
# 科目同士が誤って混ざることはない。DB全体を対象にこの拡張の影響範囲を監査済みで、
# 誤爆は無く、むしろ経済学部・海洋政策科学部・文学部にも同種の未統合パターンが
# 存在していたことが分かり、副次的にそれらも統合されるようになった）。
_VNUM = re.compile(r'^(.*?)[\s　]*([A-ZＡ-Ｚ])?(\d+|[Ⅰ-Ⅻ])((?:（遠隔）|（再履修）)*(?:[\s　]*（[^（）]+）)?)$')
_VSEM = re.compile(r'^(.*?セミナー)([A-Z]|\d+)(\([^)]+\))$')
# 「ライフコースの心理学1（発達心理学1）」のような、括弧付きの旧名・別名にも末尾数字を
# 持つパターン用。_VNUMは文字列末尾が直接数字/ローマ数字であることを前提にしており、
# 末尾が全角括弧で終わるこの形式にはマッチできないため別regexで扱う（2026-09-03、
# ユーザー指示で表示バリアント統合方式に追加）。外側・内側の数字は独立に管理する
# （「心の発達と教育2（教育・学校心理学1）」のように外側と内側の連番がずれている
# ケースが実在するため、両者を別々の接尾辞として組み立てる）。
_VNUM_PAREN = re.compile(r'^(.*?)[\s　]*(\d+|[Ⅰ-Ⅻ])（(.*?)[\s　]*(\d+|[Ⅰ-Ⅻ])）((?:（遠隔）|（再履修）)*)$')
# 「微分積分1　Z（学番下3桁：001～110）」のような、大人数科目を学籍番号の下3桁で複数クラスに
# 分割した際の接尾辞。同じ科目の別クラスでしかなく統合対象だが、末尾が数字/ローマ数字＋
# （遠隔）（再履修）タグのみを想定する_VNUMではマッチできず、共通専門基礎科目（微分積分・
# 線形代数・数理統計）で48件が未統合のまま表示される不具合があった（2026-09-02発覚。原因は
# 2026-08-31にimport_syllabus.pyの--also-coursesクラッシュバグ(2026-07-30から約1ヶ月放置)を
# 修正した際の再インポートで、これらの分割クラスが初めてDBに投入されたこと）。数字の直後に
# 現れる接尾辞のため、_VNUMでマッチさせる前に取り除いてベース科目と同一グループに統合する。
# 開き括弧・閉じ括弧の全角/半角が生データ内で揃っていないケースがあるため両方を許容する。
# 「力学基礎1　Z　学籍番号：奇数」のように括弧を使わず「学籍番号：奇数/偶数」で分割する
# 別表記も同種のパターンとして2026-09-02に追加確認したため、2つ目の分岐で吸収する。
_STUDENT_ID_SPLIT_RE = re.compile(
    r'[\s　]*(?:Z|T機械|[A-Z])[（(]学番[^）)]*[）)](?:[，,][A-Z])?(?=(?:（遠隔）|（再履修）)*$)'
    r'|[\s　]*(?:Z|T機械|[A-Z])?[\s　]*学籍番号[：:](?:奇数|偶数)(?=(?:（遠隔）|（再履修）)*$)'
)


def is_remote_tagged(name: str) -> bool:
    """科目名の末尾に遠隔クラスタグ（REMOTE_TAG）が付いているかどうか。
    レビュー投稿フォーム側（routers/liff_api.py）が variantGroup とは別に、遠隔/対面の
    区別をフロントエンドのグループ化キーへ渡す用途で使う（変種グループのラベル文字列
    自体は遠隔/対面で同じ「ベース名」のままなので、ラベルだけでは区別できないため）。"""
    return REMOTE_TAG in name


# 健康・スポーツ科学実習1/2は末尾数字のみが異なるが、実習1と実習2は種目等の内容が異なる
# 独立した科目のため、2026-08-31にユーザー指示で数字バリアント統合対象から除外した。
# 分類単位ではなく科目名単位の除外（同分類内の他の数字バリアント科目までは対象にしない、
# というユーザー指示のため）。
NUM_MERGE_EXCLUDED_NAMES = frozenset({
    "健康・スポーツ科学実習1", "健康・スポーツ科学実習2",
})


# システム情報学部専門科目は、末尾の数字違いが実質的に独立した別内容の科目であるケースが
# 多く、バリアント統合（セミナー系・数字/ローマ数字の両方）が誤爆するとの理由で2026-09-02に
# ユーザー指示で分類（classification）単位で統合対象から恒常的に除外した。
# NUM_MERGE_EXCLUDED_NAMESが科目名単位の除外なのに対し、こちらはclassification単位。
# compute_variant_bases()等はclassificationを引数に取らないため、呼び出し側
# （core/cache.py・line_bot/handler.py・routers/admin/courses.py）が
# names_with_faculty_dept/names_with_classificationを組み立てる際に、この集合に属する
# classificationの科目をあらかじめ除いてから各compute_variant_*()関数へ渡す。
CLASSIFICATION_MERGE_EXCLUDED = frozenset({
    "システム情報学部専門科目",
})


def num_variant_suffix(members: list[tuple[str, str, int, str, str]]) -> str:
    """num_basesの1グループ分のmembersから、表示用の接尾辞文字列（例:"1/2/3/4"）を組み立てる。
    学番分割クラス（_STUDENT_ID_SPLIT_RE）はベース科目と同じ(letter, disp, tag)に潰れて
    複数のmembersが同じ表示内容になりうるため、重複を除いてから連結する（そのままだと
    「微分積分(1/1/1/2/2...)」のようになる）。line_bot/handler.py _build_course_bubbles()の
    束ね方の手順も同じ重複を踏むため、ここに一本化して共有する。"""
    members_sorted = sorted(members, key=lambda x: (x[1], x[2], TAG_PRIORITY.get(x[4], 9)))
    seen: set[tuple[str, str, str]] = set()
    parts = []
    for _n, letter, _sk, disp, tag in members_sorted:
        key = (letter, disp, tag)
        if key in seen:
            continue
        seen.add(key)
        parts.append(f"{letter}{disp}{tag}")
    return "/".join(parts)


def variant_tag_in_suffix(suffix: str) -> str:
    """num_variant_suffix()が組み立てた接尾辞文字列（例:"1（遠隔）（再履修）/2（遠隔）（再履修）"）
    から、そのグループのタグ（""/（遠隔）/（再履修）/（遠隔）（再履修）/任意の説明書きの5種）を
    復元する。同一グループ内のmembersは全て同じタグを持つ（num_basesのキーがタグ完全一致で
    グループ化されているため）ので、どのmemberの表記を見ても同じ結果になる。
    line_bot/handler.py _build_course_bubbles()がkind文字列（"numvariant:<suffix>"）だけから
    num_basesの元のキーを引き直す際に使う（TAG_PRIORITYの並び=長い順でないと「（遠隔）」が
    「（遠隔）（再履修）」より先にマッチして誤判定するため、長い順に固定で判定する）。
    既知の3種のいずれにも一致しない場合、_VNUMが追加で許容する任意の説明書き括弧
    （例:「（主に地学）」）をフォールバックで抽出する（説明書きはメンバー全員で完全一致するため、
    suffix内のどの出現を拾っても同じ文字列になる）。"""
    for tag in ("（遠隔）（再履修）", "（遠隔）", "（再履修）"):
        if tag in suffix:
            return tag
    m = re.search(r'（[^（）]+）', suffix)
    if m:
        return m.group(0)
    return ""


def _vnum_match(name: str) -> tuple[str, str, int, str, str] | None:
    name = _STUDENT_ID_SPLIT_RE.sub('', name)
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


def _sk_of(raw: str) -> int:
    return _ROMAN_VAL[raw] if raw in _ROMAN_VAL else int(raw)


def _vnum_paren_match(name: str) -> tuple[str, int, str, str, int, str, str] | None:
    """"AAA1（BBB1）"のような括弧付き別名パターン用のマッチャー（_vnum_matchの姉妹関数）。
    戻り値: (main_base, main_sk, main_raw, paren_base, paren_sk, paren_raw, tag)"""
    name = _STUDENT_ID_SPLIT_RE.sub('', name)
    m = _VNUM_PAREN.match(name)
    if not m:
        return None
    main_base = m.group(1).strip()
    main_raw = m.group(2)
    paren_base = m.group(3).strip()
    paren_raw = m.group(4)
    tag = m.group(5) or ""
    return main_base, _sk_of(main_raw), main_raw, paren_base, _sk_of(paren_raw), paren_raw, tag


def paren_num_variant_suffixes(members: list[tuple[str, int, str, int, str, str]]) -> tuple[str, str]:
    """paren_num_basesの1グループ分のmembersから、外側・内側それぞれの表示用接尾辞文字列
    （例: ("1/2", "1/2")）を組み立てる。外側と内側の連番はずれうる（心の発達と教育の例）ため
    独立に重複除去・ソートする。タグ（（遠隔）等）は同一グループ内で完全一致するため
    （paren_num_basesのグループ化キーにtagを含む）、各要素の末尾に付加してnum_variant_suffix()と
    同じ形式に揃える（line_bot/handler.py側のvariant_tag_in_suffix()がkind文字列全体から
    タグを復元できるようにするため）。"""
    tag = members[0][5] if members else ""
    main_sorted = sorted({(sk, raw) for _n, sk, raw, _psk, _praw, _tag in members})
    paren_sorted = sorted({(psk, praw) for _n, _sk, _raw, psk, praw, _tag in members})
    return ("/".join(f"{raw}{tag}" for _sk, raw in main_sorted),
            "/".join(f"{raw}{tag}" for _sk, raw in paren_sorted))


def compute_variant_bases(
    names_with_faculty_dept: list[tuple[str, str, str]],
    num_excluded_names: frozenset[str] = NUM_MERGE_EXCLUDED_NAMES,
) -> tuple[
    dict[tuple[str, str, str], list[tuple[str, str]]],
    dict[tuple[str, str, str, str, str], list[tuple[str, str, int, str, str]]],
    dict[tuple[str, str, str, str, str], list[tuple[str, int, str, int, str, str]]],
]:
    """バリアント判定の実体。(科目名, faculty, department)のリストから、セミナー系/
    数字・ローマ数字/括弧付き別名（数字・ローマ数字）の3種のバリアントグループを
    (base[+言語], faculty, department) キーで束ねた辞書(sem_bases, num_bases, paren_num_bases)を
    返す（メンバーが2件未満のキーは含めない）。
    num_bases・paren_num_basesのキーは、末尾のタグ（""/（遠隔）/（再履修）/（遠隔）（再履修）の
    4種）を追加で持つ（授業形態が異なるクラスを同一視しないよう、タグが完全一致するクラス同士
    でのみ統合する。2026-09-02にユーザー指示で「再履修は再履修のみで統合」に変更、
    無タグと再履修タグを混在させていた旧仕様（2026-08-31時点、REMOTE_TAG in tagの
    真偽値だけで区別）から4タグ完全一致に揃えた）。
    num_basesのキーはさらに先頭アルファベット（letter、無ければ""）も持つ（2026-09-03に
    ユーザー指示で恒常ルール化。「数学科教育論A1/A2/C1/C2」のような「アルファベット＋数字」
    形式は、アルファベット部分が並行クラス（担当教員・内容が別）を表すことが多く、数字部分
    （同じクラス内の連番/クォーター）とは意味が異なる。従来はletterをグループ化キーに含めず
    数字だけで束ねていたため「数学科教育論(A1/A2/C1/C2)」のようにA系列とC系列が1グループに
    混ざって表示されていたが、letterをキーに追加したことで「数学科教育論(A1/A2)」
    「数学科教育論(C1/C2)」の2グループに自動的に分離される。DB上は元々別々のSubject行のため
    この変更はDBには一切影響しない）。
    paren_num_basesは「ライフコースの心理学1（発達心理学1）」のような、括弧付きの旧名・
    別名にも末尾数字を持つ科目名を対象とする（2026-09-03追加、ユーザー指示で
    このパターンはDB統合ではなく表示バリアント統合方式で扱うことにした）。キーは
    (main_base, paren_base, faculty, department, tag)、valuesは
    (name, main_sk, main_raw, paren_sk, paren_raw, tag)のリスト。外側・内側の数字は
    連番がずれうる（「心の発達と教育2（教育・学校心理学1）」等）ため独立に管理する。

    末尾がA/B/C/Dのみ異なる「文字バリアント」の統合は2026-09-02にユーザー指示で恒常的に
    廃止した（並行クラスとトピック違いの独立科目が見分けられず誤統合が繰り返し問題に
    なっていたため）。DB上はいずれも元々別々のSubject行で、このモジュールは表示統合のみを
    扱うため、廃止してもDBには一切影響しない。

    compute_variant_groups()（フラットなname→labelマップ、レビュー投稿フォーム/api/preload用）と
    line_bot.handler._build_course_bubbles()（Flex Message構築、シラバス/レビューURL等メンバーの
    詳細情報が必要）の両方はこの関数の結果から必要な形に組み立てる。
    （2026-08-30、判定規則(_vnum_match/_VSEM)はimportで共有済みだったが「束ね方の手順」自体が
    line_bot/handler.py側に別途手動複製されており、同期漏れが繰り返し起きていた反省から
    実体をここに一本化した。セミナー系・数字/ローマ数字の2パターンは正規表現の形
    （セミナーは丸括弧付き、数字/ローマ数字は末尾が必ず数字かローマ数字）から互いに
    排他的なので、構築順序（先に確定した種別を後の判定から除外する等）を気にせず
    独立に計算してよい。）

    num_excluded_names（既定でNUM_MERGE_EXCLUDED_NAMES）に含まれる名前は、数字・ローマ数字
    バリアントの判定・グループ化から除外する（他のメンバーとも統合させない）。セミナー系の
    判定には影響しない。既定値そのものが除外対象のため、呼び出し側
    （core/cache.py・line_bot/handler.py）は明示的に渡す必要はない。
    """
    names = [n for n, _, _ in names_with_faculty_dept]
    fd_by_name = {n: (f, d) for n, f, d in names_with_faculty_dept}

    sem_bases: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for name in names:
        m = _VSEM.match(name)
        if m:
            fac, dept = fd_by_name.get(name, ("", ""))
            key = (m.group(1) + m.group(3), fac, dept)
            sem_bases.setdefault(key, []).append((name, m.group(2)))
    sem_bases = {k: v for k, v in sem_bases.items() if len(v) >= 2}

    num_bases: dict[tuple[str, str, str, str, str], list[tuple[str, str, int, str, str]]] = {}
    for name in names:
        if name in num_excluded_names:
            continue
        m = _vnum_match(name)
        if m:
            base, letter, sk, disp, tag = m
            fac, dept = fd_by_name.get(name, ("", ""))
            key = (base, letter, fac, dept, tag)
            num_bases.setdefault(key, []).append((name, letter, sk, disp, tag))
    num_bases = {k: v for k, v in num_bases.items() if len(v) >= 2}

    paren_num_bases: dict[tuple[str, str, str, str, str], list[tuple[str, int, str, int, str, str]]] = {}
    for name in names:
        if name in num_excluded_names:
            continue
        m = _vnum_paren_match(name)
        if m:
            main_base, main_sk, main_raw, paren_base, paren_sk, paren_raw, tag = m
            fac, dept = fd_by_name.get(name, ("", ""))
            key = (main_base, paren_base, fac, dept, tag)
            paren_num_bases.setdefault(key, []).append((name, main_sk, main_raw, paren_sk, paren_raw, tag))
    paren_num_bases = {k: v for k, v in paren_num_bases.items() if len(v) >= 2}

    return sem_bases, num_bases, paren_num_bases


def compute_variant_groups(
    names_with_faculty_dept: list[tuple[str, str, str]],
) -> dict[str, str]:
    """(科目名, faculty, department)のリストから、末尾の数字/ローマ数字/セミナー言語
    だけが異なる2件以上の科目名をグループ化し、科目名→表示用グループラベル（ベース名）の
    マップを返す。グループに属さない（＝バリアントが1件だけ、または該当パターンなし）
    科目名はマップに含めない。
    """
    sem_bases, num_bases, paren_num_bases = compute_variant_bases(names_with_faculty_dept)
    result: dict[str, str] = {}

    for (base_lang, _fac, _dept), members in sem_bases.items():
        for n, _sk in members:
            result[n] = base_lang

    for (base, _fac, _dept, _tag), members in num_bases.items():
        for n, _letter, _sk, _disp, _tag in members:
            if n not in result:
                result[n] = base

    for (main_base, paren_base, _fac, _dept, _tag), members in paren_num_bases.items():
        label = f"{main_base}（{paren_base}）"
        for n, _msk, _mraw, _psk, _praw, _tag in members:
            if n not in result:
                result[n] = label

    return result


def compute_variant_full_labels(
    names_with_faculty_dept: list[tuple[str, str, str]],
) -> dict[str, str]:
    """(科目名, faculty, department)のリストから、科目名 → 括弧付き接尾辞込みの完全な
    グループ表示名（例: "力学基礎(1/2)"、"生物学各論(A1/A2/C1/C2)"）のマップを返す。

    compute_variant_groups()はベースラベル（接尾辞を含まない科目名の共通部分）のみを返すため、
    ベースラベルだけでは元の科目名と見分けがつかない画面（管理画面のレビュー科目別集計等）
    向けに追加した。判定基準はcompute_variant_groups()と同一（compute_variant_bases()を共有）。
    グループに属さない科目名はマップに含めない。
    """
    sem_bases, num_bases, paren_num_bases = compute_variant_bases(names_with_faculty_dept)
    result: dict[str, str] = {}

    for (base_lang, _fac, _dept), members in sem_bases.items():
        suffix = "/".join(sk for _n, sk in sorted(members, key=lambda x: x[1]))
        label = f"{base_lang}({suffix})"
        for n, _sk in members:
            result[n] = label

    for (base, _fac, _dept, _tag), members in num_bases.items():
        label = f"{base}({num_variant_suffix(members)})"
        for n, _letter, _sk, _disp, _tag in members:
            if n not in result:
                result[n] = label

    for (main_base, paren_base, _fac, _dept, _tag), members in paren_num_bases.items():
        main_suffix, paren_suffix = paren_num_variant_suffixes(members)
        label = f"{main_base}({main_suffix})（{paren_base}({paren_suffix})）"
        for n, _msk, _mraw, _psk, _praw, _tag in members:
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
    セミナー系も含め全パターンをclassification単位でグループ化する
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

    # 末尾がA/B/C/Dのみ異なる「文字バリアント」の統合は2026-09-02にユーザー指示で恒常的に
    # 廃止した（compute_variant_bases()のモジュールdocstring参照）。

    # 2) 数字・ローマ数字バリアント（同一classification単位でグループ化。タグ（""/（遠隔）/
    # （再履修）/（遠隔）（再履修）の4種）が完全一致するクラス同士でのみ統合する）
    # NUM_MERGE_EXCLUDED_NAMESに属する科目名は数字バリアント統合の対象外（compute_variant_bases()参照）
    num_bases: dict[tuple[str, str, str], list[tuple[str, str, int, str, str]]] = {}
    for name, cls in items:
        if (name, cls) in assigned or name in NUM_MERGE_EXCLUDED_NAMES:
            continue
        m = _vnum_match(name)
        if m:
            base, letter, sk, disp, tag = m
            key = (base, cls, tag)
            num_bases.setdefault(key, []).append((name, letter, sk, disp, tag))
    for (base, cls, _tag), members in num_bases.items():
        if len(members) < 2:
            continue
        label = f"{base} ({num_variant_suffix(members)})"
        for n, _letter, _sk, _disp, _tag in members:
            result[(n, cls)] = label
            assigned.add((n, cls))

    # 3) 括弧付き別名バリアント（例: 障害児発達学1（障害者・障害児心理学1）→
    # 障害児発達学(1/2)（障害者・障害児心理学(1/2)）、同一classification単位でグループ化）
    paren_num_bases: dict[tuple[str, str, str, str], list[tuple[str, int, str, int, str, str]]] = {}
    for name, cls in items:
        if (name, cls) in assigned or name in NUM_MERGE_EXCLUDED_NAMES:
            continue
        m = _vnum_paren_match(name)
        if m:
            main_base, main_sk, main_raw, paren_base, paren_sk, paren_raw, tag = m
            key = (main_base, paren_base, cls, tag)
            paren_num_bases.setdefault(key, []).append((name, main_sk, main_raw, paren_sk, paren_raw, tag))
    for (main_base, paren_base, cls, _tag), members in paren_num_bases.items():
        if len(members) < 2:
            continue
        main_suffix, paren_suffix = paren_num_variant_suffixes(members)
        label = f"{main_base}({main_suffix})（{paren_base}({paren_suffix})）"
        for n, _msk, _mraw, _psk, _praw, _tag in members:
            result[(n, cls)] = label
            assigned.add((n, cls))

    return result
