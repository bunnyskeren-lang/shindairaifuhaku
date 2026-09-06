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
# この説明書き括弧は「（a）」「（b）」のような英字1文字だけの括弧は除外する（2026-09-06、
# 文学部専門科目「地理学演習Ⅰ（a）/（b）」「地理学演習Ⅱ（a）/（b）」で発覚。除外しないと
# ローマ数字違い（Ⅰ/Ⅱ、担当教員も別）をまたいで同じ（a）タグ同士が誤って1グループに
# 混ざってしまう（「地理学演習 (Ⅰ（a）/Ⅱ（a）)」のような表示になり、本来別内容の科目が
# 統合される）。英字1文字の括弧は_VLETTER_PARENが専用で処理するため、こちらでは除外して
# _vnum_matchをNone返却させ、_vletter_only_match側（LETTER_ONLY_MERGE_INCLUDED_
# CLASSIFICATIONSに属する科目のみ）に処理を譲る。
_VNUM = re.compile(r'^(.*?)[\s　]*([A-ZＡ-Ｚ])?(\d+|[Ⅰ-Ⅻ])((?:（遠隔）|（再履修）)*(?:[\s　]*（(?![a-zA-Z]）)[^（）]+）)?)$')
# 「環境形成科学演習1A/1B/1C・2A/2B/2C」のような、数字が先・アルファベットが末尾の
# 二重枝分かれパターン用（_VNUMとは逆順）。2026-09-04にユーザー指示で「アルファベットは
# 別科目、数字は表示統合」の恒常ルールとした（同日の「アルファベット+数字」順パターン
# ([[letterをグループ化キーに追加]]した恒常ルール)と方向を揃えるため）。_vnum_match()は
# 末尾アルファベットをbase名に結合して返す（letterは""扱い）ことで、既存のnum_bases/
# num_variant_suffix()の仕組みをそのまま再利用できるようにしている
# （例:「環境形成科学演習A(1/2)」「環境形成科学演習B(1/2)」）。
_VNUM_TRAILING_LETTER = re.compile(r'^(.*?)[\s　]*(\d+|[Ⅰ-Ⅻ])([A-ZＡ-Ｚ])((?:（遠隔）|（再履修）)*(?:[\s　]*（(?![a-zA-Z]）)[^（）]+）)?)$')
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
# LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONS向け。末尾に数字を伴わずアルファベット
# 1文字のみが異なるパターン（例:"日本文化交流論A"/"日本文化交流論B"）。_VNUMは末尾の
# 数字/ローマ数字を必須とするためこのパターンにはマッチしない（数字が無い時点で
# 排他的なので、_VNUM等より判定順が前後しても互いに衝突しない）。
_VLETTER_ONLY = re.compile(r'^(.*?)[\s　]*([A-ZＡ-Ｚ])((?:（遠隔）|（再履修）)*(?:[\s　]*（[^（）]+）)?)$')
# 文学部専門科目「アメリカ文学史（a）」「アメリカ文学史（b）」のような、末尾が小文字1文字を
# 括弧で囲んだ形式の文字バリアント（_VLETTER_ONLYとは別記法のため専用regexにした）。
# 2026-09-06、ユーザー指示で対象58ペア全件のcourse_sections担当教員を突き合わせ、
# (a)/(b)間で教員が完全一致することを確認済み（並行クラスではなく同一内容の複数開講枠）。
_VLETTER_PAREN = re.compile(r'^(.*?)[\s　]*[（(]([a-zA-Z])[）)]((?:（遠隔）|（再履修）)*(?:[\s　]*（[^（）]+）)?)$')


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
# 2026-09-06以降、同種の個別除外は管理画面の「統合解除」ボタン（subjects.variant_merge_excluded
# 列、routers/admin/courses.py）で管理者がその場で切り替えられるようにしたため、
# このハードコード集合には追加しない（既存の健康・スポーツ科学実習1/2のみ後方互換で残す）。
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


# 外国語第1（Academic English等）・外国語第2（ドイツ語/フランス語/ロシア語/中国語初級等）は
# 語尾が「アルファベット＋数字」形式（例:"Academic English Communication A1/A2/B1/B2"、
# "ドイツ語初級A1/A2/A3/A4"）だが、アルファベット部分は数学科教育論のような並行クラス
# （担当教員・内容が別）ではなく、クラス分け（同一内容）を表す。2026-09-04にA〜D系列の
# letterをグループ化キーに追加する恒常ルール（数学科教育論のようなケース向け）を導入した際、
# 語学科目もまとめてA系列/B系列に分裂してしまう副作用が発覚したため、この2分類のみletter分離
# ルールの対象外とした（ユーザー指示）。CLASSIFICATION_MERGE_EXCLUDEDと異なり統合自体は
# 維持し、letterだけをグループ化キーから除く（数字部分での統合は引き続き行う）。
LETTER_SPLIT_EXCLUDED_CLASSIFICATIONS = frozenset({
    "教養(外国語第1)", "教養(外国語第2)",
})


# 末尾がA/B/C/Dのみ異なる「文字バリアント」統合は2026-09-02に恒常廃止したが、
# 2026-09-04にユーザー指示で「国際人間科学部専門科目」classification限定の例外を追加した
# （このclassification配下（教育系・語学系の専門科目）は、A/B/C/Dが並行クラス（担当教員・
# 内容が別）ではなく同一科目の複数開講枠を表すケースが多いことをユーザーが確認済み）。
# ただしこのclassification単位のオプトインは教員一致を個別確認せずに機械的に統合して
# しまうため、2026-09-06に全件突き合わせで3件の教員不一致が発覚し、以降は
# 個別ペア単位のMANUAL_VARIANT_GROUPSへ移行した（下記LETTER_ONLY_MERGE_INCLUDED_
# CLASSIFICATIONSのコメント参照）。他のclassificationには一切影響しない（例えば
# 数学科教育論A1/A2/C1/C2のようなアルファベット+数字パターンは、そもそもこのオプトイン
# 集合とは別のマッチャー(_vnum_match/_VNUM)が扱うため対象外のまま）。
# 表示統合のみ（DB上のSubject行は分けたまま）: レビュー投稿・閲覧・チケット共有は
# 引き続きA/B/C/Dそれぞれ別科目として扱う（core.cache.get_variant_map_cached()・
# get_variant_group_subject_ids()はこのモジュールのcompute_variant_groups()を使うが、
# letter_only_included_namesを渡していないため無関係）。影響するのはLINE bot科目一覧
# （line_bot/handler.py _build_course_bubbles）と管理画面科目一覧
# （routers/admin/courses.py compute_variant_display_groups()）の2画面のみ。
#
# 「文学部専門科目」は2026-09-06にユーザー指示で追加。末尾が"（a）""（b）"（小文字1文字を
# 全角/半角括弧で囲む記法、_VLETTER_PAREN）の58ペア（アメリカ文学史等）全件について
# course_sectionsの担当教員を突き合わせ、(a)/(b)間で教員が完全一致することを確認済み
# （並行クラスではなく同一内容の複数開講枠と判断）。「国語学演習（a）」のみ（b）が存在せず
# 単独のため統合対象外（メンバー2件未満は自動的にグループ化されない）。
#
# 「国際人間科学部専門科目」は2026-09-06にこの集合から除外した。classification単位の
# オプトインは教員一致を個別確認しないまま全ペアを機械的に統合してしまうため、同じ日に
# ユーザー指示で全19ベースのcourse_sections担当教員を実際に突き合わせたところ、
# 保健体育科教育論（A/B/C=前田正登、D=高見和至）・理科教育論（A/B=岡部舞、C=三宅志穂）・
# 社会調査法（A=永田夏来、B=中川理季、完全不一致）の3ベースで教員不一致が発覚した
# （並行クラスを誤統合していた）。教員が完全一致する16ベースと、部分一致する2ベースの
# 一致する枝のみをMANUAL_VARIANT_GROUPSへ個別移行し、社会調査法は統合対象から外した
# （下記MANUAL_VARIANT_GROUPS参照）。
LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONS = frozenset({
    "教養(人文)", "教養(社会)", "教養(自然)", "教養(総合)",
    "文学部専門科目",
})


# 教養科目の4大分類（人文/社会/自然/総合）配下の「アジア史A/B」のような文字バリアントは、
# 並行クラス（担当教員が異なるだけで同一内容）であることをユーザーが確認済み（2026-09-05）。
# LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONSに追加したことでLINE bot科目一覧・管理画面
# 科目一覧では「(A/B)」とまとめて表示されるが、レビュー投稿の募集枠共有・重複投稿防止
# （get_variant_map_cached()が担う）には一切影響しない（compute_variant_groups()には
# letter_only_included_namesを渡していないため）。一方でこのユーザー要望は「投稿枠はA/B別だが
# 閲覧は1つのLIFFページにまとめたい」というもので、既存のLETTER_ONLY_MERGE_INCLUDED_
# CLASSIFICATIONS（国際人間科学部専門科目）は閲覧も意図的に分離したままにする設計だったため、
# 閲覧統合の可否だけを別軸のオプトイン集合として切り出した。この集合に属する科目のみ
# routers/liff_api.py `_group_subject_ids()`がcompute_letter_view_groups()の結果を使って
# レビュー閲覧・チケット共有をA/B全体でまとめる。
LETTER_ONLY_VIEW_MERGE_CLASSIFICATIONS = frozenset({
    "教養(人文)", "教養(社会)", "教養(自然)", "教養(総合)",
})


# 医学部保健学科は「看護学専攻」「理学療法学専攻」「作業療法学専攻」「検査技術科学専攻」の
# 4専攻がdepartment違いの別Subjectとして登録されている。専攻をまたいで科目名が完全一致する
# 科目は、レビューを共有し1件集まった時点で他専攻分も含めて募集を締め切る恒常ルールとした
# （2026-09-06、ユーザー指示）。上記のLETTER_ONLY_VIEW_MERGE_CLASSIFICATIONS等と異なり、
# これは「閲覧だけ統合」ではなく募集枠（MAX_REVIEWS_PER_COURSE_SECTION）そのものを専攻横断で
# 共有する必要があるため、compute_variant_groups()の結果を使うcore.cache.
# get_variant_group_subject_ids()（レビュー投稿の重複防止・募集枠共有・レビュー閲覧統合の
# 実体）側でfaculty/department完全一致という通常の絞り込みに対する例外として扱う
# （department自体が専攻ごとに異なる値のため、通常の同一department絞り込みでは統合できない）。
HOKEN_GAKKA_FACULTY = "医学部"
HOKEN_GAKKA_DEPARTMENT_PREFIX = "保健学科"


def is_hoken_gakka_senko(faculty: str, department: str) -> bool:
    """医学部保健学科の専攻（看護学/理学療法学/作業療法学/検査技術科学）配下の科目か判定する。"""
    return faculty == HOKEN_GAKKA_FACULTY and (department or "").startswith(HOKEN_GAKKA_DEPARTMENT_PREFIX)


def hoken_gakka_senko_label(department: str) -> str:
    """department文字列から「保健学科」プレフィックスを除いた専攻名部分を返す（表示用）。"""
    return (department or "").removeprefix(HOKEN_GAKKA_DEPARTMENT_PREFIX) or department


# 海洋政策科学部「経済学基礎論」「経営学基礎論」等、科目名自体が「N-M」形式で枝分かれし、
# かつ枝ごとに（海洋ガバナンス領域）タグの有無や「1-(1/2)」のような表記揺れが不揃いなケース
# （2026-09-04、ユーザー指示）。_VNUM系の正規表現はタグ完全一致を要求するため機械的には
# 統合できず、個別にグループを列挙する手動オーバーライド方式にした。表示のみの統合
# （LINE bot科目一覧・管理画面科目一覧のみ）で、compute_variant_groups()（レビュー投稿・
# 閲覧チケット共有等の機能面）には一切適用しない（LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONS
# と同じ設計方針）。グループ内の全科目名がその時点の対象リストに揃っていない場合は
# 適用しない（一部科目が別分類にフィルタされて渡された場合等の誤爆防止）。
MANUAL_VARIANT_GROUPS: tuple[dict, ...] = (
    {
        "names": (
            "経済学基礎論1-(1/2)(海洋ガバナンス領域)",
            "経済学基礎論2-1(海洋ガバナンス領域)",
            "経済学基礎論2-2（海洋ガバナンス領域）",
        ),
        "label": "経済学基礎論(1-1,1-2,2-1,2-2)",
    },
    {
        "names": (
            "経営学基礎論1-1(海洋ガバナンス領域)",
            "経営学基礎論1-2（海洋ガバナンス領域）",
            "経営学基礎論2-1（海洋ガバナンス領域）",
            "経営学基礎論2-2",
        ),
        "label": "経営学基礎論(1-1,1-2,2-1,2-2)",
    },
    {
        "names": (
            "固体地球科学1-1",
            "固体地球科学1-2（海洋基礎科学領域）",
            "固体地球科学2-(1/2)",
        ),
        "label": "固体地球科学(1-1,1-2,2-1,2-2)",
    },
    # 理学部惑星学科「惑星学基礎○演習」（2026-09-06、ユーザー指示）。ローマ数字が
    # 科目名の末尾ではなく「基礎」と「演習」の間（中間）に挟まる形式のため、末尾に
    # 数字/ローマ数字があることを前提とする_VNUMではマッチできず手動グループにした。
    {
        "names": (
            "惑星学基礎Ⅰ演習",
            "惑星学基礎Ⅱ演習",
            "惑星学基礎Ⅲ演習",
            "惑星学基礎Ⅳ演習",
            "惑星学基礎Ⅴ演習",
        ),
        "label": "惑星学基礎演習(Ⅰ/Ⅱ/Ⅲ/Ⅳ/Ⅴ)",
    },
    # 理学部惑星学科「惑星学基礎」（2026-09-06、ユーザー指示）。単独の「惑星学基礎Ⅰ」
    # （通年2単位）は削除しクォーター分割の「Ⅰ-1」「Ⅰ-2」（各1単位）を残す方針にしたため、
    # 元々別々の自動グループだった「惑星学基礎Ⅰ-(1/2)」（_VNUM基底"惑星学基礎Ⅰ-"）と
    # 「惑星学基礎(Ⅱ/Ⅲ/Ⅳ/Ⅴ)」（_VNUM基底"惑星学基礎"）を1つの表示グループへ統合する。
    # どちらも_VNUMに単独でマッチし2件以上の自動グループを構成してしまうため、
    # _MANUAL_VARIANT_GROUP_NAMESによる自動グループ化除外と組み合わせて使う。
    {
        "names": (
            "惑星学基礎Ⅰ-1",
            "惑星学基礎Ⅰ-2",
            "惑星学基礎Ⅱ",
            "惑星学基礎Ⅲ",
            "惑星学基礎Ⅳ",
            "惑星学基礎Ⅴ",
        ),
        "label": "惑星学基礎(Ⅰ-(1/2)/Ⅱ/Ⅲ/Ⅳ/Ⅴ)",
    },
    # 国際人間科学部グローバル文化学科専門科目のA/B系6科目（2026-09-06、ユーザー指示）。
    # classification="国際人間科学部グローバル文化学科専門科目"は2026-09-02の学科別分類
    # 導入で「国際人間科学部専門科目」から分割済みのため、LETTER_ONLY_MERGE_INCLUDED_
    # CLASSIFICATIONS（分割前の親classification名のみを含む）には含まれない。全6ペアとも
    # course_sectionsの担当教員がA/B間で完全一致し（英語3科目はシラバスも同一科目が
    # 第3クォーター(A)/第4クォーター(B)に分割されただけと確認済み）、並行クラスではない。
    # 他の同classification内の未確認A/Bペアまで一括で巻き込まないよう、classification単位の
    # オプトインではなく個別ペアのみをここに列挙する。
    {
        "names": ("English for Professional Purposes A", "English for Professional Purposes B"),
        "label": "English for Professional Purposes (A/B)",
    },
    {
        "names": ("English Presentation Skills A", "English Presentation Skills B"),
        "label": "English Presentation Skills (A/B)",
    },
    {
        "names": ("ITコミュニケーションデザインA", "ITコミュニケーションデザインB"),
        "label": "ITコミュニケーションデザイン(A/B)",
    },
    {
        "names": ("アメリカ文化論A", "アメリカ文化論B"),
        "label": "アメリカ文化論(A/B)",
    },
    {
        "names": ("オセアニア社会文化論A", "オセアニア社会文化論B"),
        "label": "オセアニア社会文化論(A/B)",
    },
    {
        "names": ("環大西洋文化論A", "環大西洋文化論B"),
        "label": "環大西洋文化論(A/B)",
    },
    # 同classification内の残り17ペア（2026-09-06、ユーザー指示で追加確認）。上記6ペアと同じ
    # 理由（担当教員がA/B間で完全一致・並行クラスではない）で追加。同classification内には
    # このほかに教員が食い違うペア（グローバル化と現代世界・情報科学概論等）や、A/B/C/Dの
    # うちA=B・C=Dだが互いには一致しないペア（グローバル文化形成基礎演習等、team-teaching
    # オムニバス科目とみられる）も存在するが、それらは同一内容と確認できていないため
    # 意図的に含めていない（要ユーザー確認）。
    {
        "names": ("コミュニケーション比較論A", "コミュニケーション比較論B"),
        "label": "コミュニケーション比較論(A/B)",
    },
    {
        "names": ("ヨーロッパ社会文化論A", "ヨーロッパ社会文化論B"),
        "label": "ヨーロッパ社会文化論(A/B)",
    },
    {
        "names": ("北アジア歴史社会論A", "北アジア歴史社会論B"),
        "label": "北アジア歴史社会論(A/B)",
    },
    {
        "names": ("多文化政治社会論A", "多文化政治社会論B"),
        "label": "多文化政治社会論(A/B)",
    },
    {
        "names": ("平和構築論A", "平和構築論B"),
        "label": "平和構築論(A/B)",
    },
    {
        "names": ("日本メディア文化論A", "日本メディア文化論B"),
        "label": "日本メディア文化論(A/B)",
    },
    {
        "names": ("日本思想文化論A", "日本思想文化論B"),
        "label": "日本思想文化論(A/B)",
    },
    {
        "names": ("日本歴史文化論A", "日本歴史文化論B"),
        "label": "日本歴史文化論(A/B)",
    },
    {
        "names": ("日本社会文化論A", "日本社会文化論B"),
        "label": "日本社会文化論(A/B)",
    },
    {
        "names": ("東アジア政治社会論A", "東アジア政治社会論B"),
        "label": "東アジア政治社会論(A/B)",
    },
    {
        "names": ("東南アジア政治文化論A", "東南アジア政治文化論B"),
        "label": "東南アジア政治文化論(A/B)",
    },
    {
        "names": ("東南アジア社会文化論A", "東南アジア社会文化論B"),
        "label": "東南アジア社会文化論(A/B)",
    },
    {
        "names": ("比較政治社会論A", "比較政治社会論B"),
        "label": "比較政治社会論(A/B)",
    },
    {
        "names": ("比較政策論A", "比較政策論B"),
        "label": "比較政策論(A/B)",
    },
    {
        "names": ("現代IT入門A", "現代IT入門B"),
        "label": "現代IT入門(A/B)",
    },
    {
        "names": ("現代社会理論A", "現代社会理論B"),
        "label": "現代社会理論(A/B)",
    },
    {
        "names": ("社会システム科学A", "社会システム科学B"),
        "label": "社会システム科学(A/B)",
    },
    {
        "names": ("グローバル文化特別演習A", "グローバル文化特別演習B"),
        "label": "グローバル文化特別演習(A/B)",
    },
    # オムニバス形式の必修演習6科目（2026-09-06、ユーザー指示）。A/B/C/Dの4クラスが
    # 存在するが、course_sections担当教員を突き合わせるとA=Bチーム・C=Dチームで教員が
    # 完全に分かれており（チーム同士は互いに重複がないか一部のみ重複）、A〜D全体が
    # 同一開講回ではなくチーム別の別開講回（学期違い等）と判断されるため、ユーザー指示で
    # A/BとC/Dを別々の表示グループに分けて統合する（A〜D全体を1グループにはしない）。
    {
        "names": ("グローバルコミュニケーション基礎演習A", "グローバルコミュニケーション基礎演習B"),
        "label": "グローバルコミュニケーション基礎演習(A/B)",
    },
    {
        "names": ("グローバルコミュニケーション基礎演習C", "グローバルコミュニケーション基礎演習D"),
        "label": "グローバルコミュニケーション基礎演習(C/D)",
    },
    {
        "names": ("グローバルコミュニケーション発展演習A", "グローバルコミュニケーション発展演習B"),
        "label": "グローバルコミュニケーション発展演習(A/B)",
    },
    {
        "names": ("グローバルコミュニケーション発展演習C", "グローバルコミュニケーション発展演習D"),
        "label": "グローバルコミュニケーション発展演習(C/D)",
    },
    {
        "names": ("グローバル文化形成基礎演習A", "グローバル文化形成基礎演習B"),
        "label": "グローバル文化形成基礎演習(A/B)",
    },
    {
        "names": ("グローバル文化形成基礎演習C", "グローバル文化形成基礎演習D"),
        "label": "グローバル文化形成基礎演習(C/D)",
    },
    {
        "names": ("グローバル文化形成発展演習A", "グローバル文化形成発展演習B"),
        "label": "グローバル文化形成発展演習(A/B)",
    },
    {
        "names": ("グローバル文化形成発展演習C", "グローバル文化形成発展演習D"),
        "label": "グローバル文化形成発展演習(C/D)",
    },
    {
        "names": ("グローバル社会動態基礎演習A", "グローバル社会動態基礎演習B"),
        "label": "グローバル社会動態基礎演習(A/B)",
    },
    {
        "names": ("グローバル社会動態基礎演習C", "グローバル社会動態基礎演習D"),
        "label": "グローバル社会動態基礎演習(C/D)",
    },
    {
        "names": ("グローバル社会動態発展演習A", "グローバル社会動態発展演習B"),
        "label": "グローバル社会動態発展演習(A/B)",
    },
    {
        "names": ("グローバル社会動態発展演習C", "グローバル社会動態発展演習D"),
        "label": "グローバル社会動態発展演習(C/D)",
    },
    # 「国際人間科学部専門科目」classification（学科不明分。2026-09-04にLETTER_ONLY_MERGE_
    # INCLUDED_CLASSIFICATIONSへ追加されていたが、2026-09-06に全19ベースの
    # course_sections担当教員を突き合わせたところ3ベースで不一致が発覚したため、この
    # classificationを同集合から除外し個別ペアへ移行した。以下16ベースは教員完全一致を確認済み。
    {"names": ("Academic Communication（仏）A", "Academic Communication（仏）B"),
     "label": "Academic Communication（仏）(A/B)"},
    {"names": ("Academic Communication（独）A", "Academic Communication（独）B"),
     "label": "Academic Communication（独）(A/B)"},
    {"names": ("Academic Writing（仏）A", "Academic Writing（仏）B"),
     "label": "Academic Writing（仏）(A/B)"},
    {"names": ("Academic Writing（独）A", "Academic Writing（独）B"),
     "label": "Academic Writing（独）(A/B)"},
    {"names": ("Academic Writing（英）A", "Academic Writing（英）B"),
     "label": "Academic Writing（英）(A/B)"},
    {"names": ("Cultures and Societies in Japan A", "Cultures and Societies in Japan B"),
     "label": "Cultures and Societies in Japan (A/B)"},
    {"names": ("グローバル正義論A", "グローバル正義論B"), "label": "グローバル正義論(A/B)"},
    {"names": ("ジェンダー社会文化論A", "ジェンダー社会文化論B"), "label": "ジェンダー社会文化論(A/B)"},
    {"names": ("メディア社会文化論A", "メディア社会文化論B"), "label": "メディア社会文化論(A/B)"},
    {"names": ("中学校教育実地研究A", "中学校教育実地研究B"), "label": "中学校教育実地研究(A/B)"},
    {"names": ("国際コミュニケーション演習A", "国際コミュニケーション演習B"), "label": "国際コミュニケーション演習(A/B)"},
    {"names": ("国際関係論A", "国際関係論B"), "label": "国際関係論(A/B)"},
    {"names": ("家庭科教育論A", "家庭科教育論B"), "label": "家庭科教育論(A/B)"},
    {"names": ("日本文化交流論A", "日本文化交流論B"), "label": "日本文化交流論(A/B)"},
    {"names": ("視覚文化論A", "視覚文化論B"), "label": "視覚文化論(A/B)"},
    {"names": ("近現代政治思想論A", "近現代政治思想論B"), "label": "近現代政治思想論(A/B)"},
    # 保健体育科教育論はA/B/C=前田正登、D=高見和至と判明したため、A/B/Cのみを1グループにし
    # Dは統合対象から外す。理科教育論はA/B=岡部舞、C=三宅志穂のためA/Bのみ統合しCは外す。
    # 社会調査法（A=永田夏来、B=中川理季）は完全不一致のため統合対象に含めない。
    {"names": ("保健体育科教育論A", "保健体育科教育論B", "保健体育科教育論C"),
     "label": "保健体育科教育論(A/B/C)"},
    {"names": ("理科教育論A", "理科教育論B"), "label": "理科教育論(A/B)"},
    # 「国際人間科学部環境共生学科専門科目」classification（2026-09-06、ユーザー指示）。
    # 全15ベースをcourse_sections担当教員で突き合わせ、以下7ベースが教員完全一致と確認済み。
    # 残り8ベース（数理科学研究・環境地球科学・環境形成科学演習1/2・環境物理学・環境物質科学・
    # 環境生命科学、いずれも教員不一致）は統合対象に含めない。
    {"names": ("かたちの数理A", "かたちの数理B"), "label": "かたちの数理(A/B)"},
    {"names": ("ライフスタイル論A", "ライフスタイル論B"), "label": "ライフスタイル論(A/B)"},
    {"names": ("環境モデル解析A", "環境モデル解析B"), "label": "環境モデル解析(A/B)"},
    {"names": ("環境基礎物理学A", "環境基礎物理学B"), "label": "環境基礎物理学(A/B)"},
    {"names": ("衣環境論A", "衣環境論B"), "label": "衣環境論(A/B)"},
    {"names": ("計算代数A", "計算代数B"), "label": "計算代数(A/B)"},
    {"names": ("食環境論A", "食環境論B"), "label": "食環境論(A/B)"},
    # 環境形成科学実験はA=井上真理、B=島田良子、C=D=福田博也と判明したため、C/Dのみ統合する。
    {"names": ("環境形成科学実験C", "環境形成科学実験D"), "label": "環境形成科学実験(C/D)"},
    # 「国際人間科学部発達コミュニティ学科専門科目」classification（2026-09-06、ユーザー指示）。
    # 全3ベースを突き合わせ、以下2ベースが教員完全一致と確認済み（創造の発想とプロセスは
    # 岸本吉弘/塚脇淳で不一致のため対象外）。
    {"names": ("ESD生涯学習論A", "ESD生涯学習論B"), "label": "ESD生涯学習論(A/B)"},
    {"names": ("近現代文化言説論A", "近現代文化言説論B"), "label": "近現代文化言説論(A/B)"},
    # 工学部電気電子工学科専門科目（2026-09-06、ユーザー指示）。
    # course_sections担当教員が両方とも服部吉晃で一致することを確認済み。
    {"names": ("固体物性工学A", "固体物性工学B"), "label": "固体物性工学(A/B)"},
    # 工学部機械工学科専門科目（2026-09-06、ユーザー指示）。末尾が半角括弧小文字の
    # "(a)"/"(b)"表記（_VLETTER_PARENは検出するが、このclassificationはLETTER_ONLY_MERGE_
    # INCLUDED_CLASSIFICATIONSに未登録のためオプトインされない）。course_sections担当教員が
    # 両方とも片岡武で一致することを確認済み。
    {"names": ("機械工学実験(a)", "機械工学実験(b)"), "label": "機械工学実験(a/b)"},
    # 工学部応用化学科専門科目（2026-09-06、ユーザー指示）。
    # course_sections担当教員がA/B/Cとも大村直人で一致することを確認済み。
    {"names": ("移動現象論A", "移動現象論B", "移動現象論C"), "label": "移動現象論(A/B/C)"},
)

# MANUAL_VARIANT_GROUPSに属する科目名の集合。これらは自動グループ化（_VNUM等）に
# 単独でマッチしてしまうケースがあるため、compute_variant_display_groups()の自動ステップ
# （2〜4）では素通りさせ、必ずステップ5の手動グループにのみ割り当てさせる
# （2026-09-06、惑星学基礎で導入。自動ステップが先に一部だけを別グループとして
# 確定させてしまうと、ステップ5の「グループ内の全科目名が揃っている場合のみ適用」
# 判定に失敗し手動グループが適用されなくなるため）。
_MANUAL_VARIANT_GROUP_NAMES: frozenset[str] = frozenset(
    n for group in MANUAL_VARIANT_GROUPS for n in group["names"]
)


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


def letter_variant_suffix(members: list[tuple[str, str, str]]) -> str:
    """letter_only_basesの1グループ分のmembersから、表示用の接尾辞文字列（例:"A/B"）を組み立てる。
    num_variant_suffix()と同じ形式（各要素末尾にタグを個別付与してから重複除去・連結）にし、
    line_bot/handler.py _make_bubble()側のタグ剥がしロジック（numvariantと共通）を
    そのまま再利用できるようにする。"""
    members_sorted = sorted(members, key=lambda x: x[1])
    seen: set[str] = set()
    parts = []
    for _n, letter, tag in members_sorted:
        if letter in seen:
            continue
        seen.add(letter)
        parts.append(f"{letter}{tag}")
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


def variant_letter_in_suffix(kind: str) -> str:
    """num_variant_suffix()が組み立てた接尾辞を含むkind文字列（例:"numvariant:A1/A2"）から、
    そのグループの先頭アルファベット（letter、無ければ""）を復元する。同一グループ内の
    membersは全てletterが完全一致する（2026-09-03、num_basesのグループ化キーにletterを
    追加したことに伴い新設。「数学科教育論A1/A2/C1/C2」のようなアルファベット＋数字の
    二重枝分かれ科目で、A系列とC系列を別グループとして扱うため）。
    line_bot/handler.py _build_course_bubbles()がkind文字列だけからnum_basesの元のキーを
    引き直す際にvariant_tag_in_suffix()と対で使う。"""
    suffix = kind.split(":", 1)[1] if ":" in kind else kind
    if suffix and suffix[0].isascii() and suffix[0].isalpha():
        return suffix[0].upper()
    return ""


def _vnum_match(name: str) -> tuple[str, str, int, str, str] | None:
    name = _STUDENT_ID_SPLIT_RE.sub('', name)
    m = _VNUM.match(name)
    if m:
        base = m.group(1).strip()
        letter = (m.group(2) or "").translate(_FULLWIDTH_UPPER)
        raw = m.group(3)
        tag = m.group(4) or ""
        if raw in _ROMAN_VAL:
            return base, letter, _ROMAN_VAL[raw], raw, tag
        return base, letter, int(raw), raw, tag
    m2 = _VNUM_TRAILING_LETTER.match(name)
    if m2:
        # 末尾アルファベットをbase名に結合して返す（letter=""）。A系列/B系列が
        # base名の時点で別文字列になるため、num_basesのグループ化キー
        # (base, group_letter, fac, dept, tag) は変更せずそのまま系列ごとに分離される。
        base = m2.group(1).strip() + m2.group(3).translate(_FULLWIDTH_UPPER)
        raw = m2.group(2)
        tag = m2.group(4) or ""
        if raw in _ROMAN_VAL:
            return base, "", _ROMAN_VAL[raw], raw, tag
        return base, "", int(raw), raw, tag
    return None


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


def _vletter_only_match(name: str) -> tuple[str, str, str] | None:
    """LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONS向け。"AAA""B"のような末尾アルファベット
    1文字のみのパターン、および"AAA（a）""AAA（b）"のような括弧付き小文字1文字のパターン
    （_VLETTER_PAREN）をマッチさせる（_vnum_matchの姉妹関数）。
    戻り値: (base, letter, tag)"""
    name = _STUDENT_ID_SPLIT_RE.sub('', name)
    m = _VLETTER_ONLY.match(name)
    if m:
        base = m.group(1).strip()
        letter = m.group(2).translate(_FULLWIDTH_UPPER)
        tag = m.group(3) or ""
        return base, letter, tag
    m = _VLETTER_PAREN.match(name)
    if not m:
        return None
    base = m.group(1).strip()
    letter = m.group(2).upper()
    tag = m.group(3) or ""
    return base, letter, tag


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
    letter_split_excluded_names: frozenset[str] = frozenset(),
    letter_only_included_names: frozenset[str] = frozenset(),
) -> tuple[
    dict[tuple[str, str, str], list[tuple[str, str]]],
    dict[tuple[str, str, str, str, str], list[tuple[str, str, int, str, str]]],
    dict[tuple[str, str, str, str, str], list[tuple[str, int, str, int, str, str]]],
    dict[tuple[str, str, str, str], list[tuple[str, str, str]]],
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

    letter_split_excluded_names（既定で空集合）に含まれる名前は、letterをグループ化キーに
    含めない（＝A系列/B系列等に分裂させず、数字部分だけで束ねる。2026-09-04追加）。
    LETTER_SPLIT_EXCLUDED_CLASSIFICATIONSに属するclassificationの科目名を渡す想定
    （呼び出し側がclassificationを見て名前集合を組み立てる。この関数自体はclassificationを
    引数に取らないため）。メンバー個々のletter（表示接尾辞の組み立てに使う）自体は
    変更しない。

    letter_only_included_names（既定で空集合）に含まれる名前のみ、4つ目の辞書
    letter_only_basesの対象となる。末尾がA/B/C/Dのみ異なる「文字バリアント」の統合は
    2026-09-02にユーザー指示で恒常廃止したが、2026-09-04にユーザー指示で
    LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONS（国際人間科学部専門科目のみ）限定の
    オプトイン例外を追加した。呼び出し側がclassificationを見て名前集合を組み立てて渡す
    （letter_split_excluded_namesと同じ設計）。キーは(base, faculty, department, tag)、
    valuesは(name, letter, tag)のリスト。既定は空集合＝従来通り一切統合しない。
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
            group_letter = "" if name in letter_split_excluded_names else letter
            key = (base, group_letter, fac, dept, tag)
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

    letter_only_bases: dict[tuple[str, str, str, str], list[tuple[str, str, str]]] = {}
    for name in names:
        if name not in letter_only_included_names:
            continue
        m = _vletter_only_match(name)
        if m:
            base, letter, tag = m
            fac, dept = fd_by_name.get(name, ("", ""))
            key = (base, fac, dept, tag)
            letter_only_bases.setdefault(key, []).append((name, letter, tag))
    letter_only_bases = {k: v for k, v in letter_only_bases.items() if len(v) >= 2}

    return sem_bases, num_bases, paren_num_bases, letter_only_bases


def compute_variant_groups(
    names_with_faculty_dept: list[tuple[str, str, str]],
    letter_split_excluded_names: frozenset[str] = frozenset(),
    num_excluded_names: frozenset[str] = NUM_MERGE_EXCLUDED_NAMES,
) -> dict[str, str]:
    """(科目名, faculty, department)のリストから、末尾の数字/ローマ数字/セミナー言語
    だけが異なる2件以上の科目名をグループ化し、科目名→表示用グループラベル（ベース名）の
    マップを返す。グループに属さない（＝バリアントが1件だけ、または該当パターンなし）
    科目名はマップに含めない。letter_split_excluded_namesはcompute_variant_bases()参照。
    letter_only_included_namesを渡さない（既定空集合）ため、末尾アルファベットのみが
    異なる文字バリアントは常に統合されない（レビュー投稿フォーム/api/preload・LINE bot
    メッセージ検索・レビュー閲覧チケット共有はこの関数の結果に依存するため、
    LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONSの例外は意図的にここには適用しない。
    表示のみ統合したいline_bot/handler.py _build_course_bubbles()・
    compute_variant_display_groups()は別途letter_only_included_namesを渡す）。
    num_excluded_names（既定でNUM_MERGE_EXCLUDED_NAMES）は数字・ローマ数字バリアントの
    統合対象から除外する科目名の集合。呼び出し側がsubjects.variant_merge_excluded=trueの
    科目名をNUM_MERGE_EXCLUDED_NAMESと合わせて渡すことで、管理画面の「統合解除」ボタン
    （2026-09-06）による動的な除外にも対応する。
    """
    sem_bases, num_bases, paren_num_bases, _letter_only_bases = compute_variant_bases(
        names_with_faculty_dept, num_excluded_names=num_excluded_names,
        letter_split_excluded_names=letter_split_excluded_names)
    result: dict[str, str] = {}

    for (base_lang, _fac, _dept), members in sem_bases.items():
        for n, _sk in members:
            result[n] = base_lang

    for (base, letter, _fac, _dept, _tag), members in num_bases.items():
        # letterが空でない場合はラベルに連結し、A系列/C系列のようにletterが異なる
        # グループ同士が同じラベル文字列になって誤って再統合されないようにする
        # （grouping label自体は画面に表示されず、キーとしてのみ使われるため連結でよい）
        label = f"{base}{letter}" if letter else base
        for n, _letter2, _sk, _disp, _tag in members:
            if n not in result:
                result[n] = label

    for (main_base, paren_base, _fac, _dept, _tag), members in paren_num_bases.items():
        label = f"{main_base}（{paren_base}）"
        for n, _msk, _mraw, _psk, _praw, _tag in members:
            if n not in result:
                result[n] = label

    return result


def compute_letter_view_groups(
    names_with_faculty_dept: list[tuple[str, str, str]],
) -> dict[str, tuple[str, list[str], dict[str, str]]]:
    """LETTER_ONLY_VIEW_MERGE_CLASSIFICATIONS向け。末尾アルファベットのみが異なる科目名を
    グループ化し、科目名 → (ベースラベル, グループ内科目名リスト(A→B→C順), 科目名→letterの辞書)
    のマップを返す。呼び出し側（core/cache.py）がLETTER_ONLY_VIEW_MERGE_CLASSIFICATIONSに
    属する科目のみを渡す想定（この関数自体はclassificationを引数に取らない）。

    compute_variant_groups()（レビュー投稿フォーム・重複防止・募集枠共有向け）とは意図的に
    別関数にしている。教養科目のA/B並行クラスは「投稿枠・重複防止は引き続きA/B別科目のまま、
    レビュー閲覧だけを1つのLIFFページにまとめたい」という要望（2026-09-05）のため、
    compute_variant_groups()の結果（get_variant_map_cached()経由でレビュー投稿フォーム・
    募集枠共有にも波及する）とは混ぜられない。

    グループに属さない（バリアントが1件だけ）科目名はマップに含めない。"""
    all_names = frozenset(n for n, _, _ in names_with_faculty_dept)
    _, _, _, letter_only_bases = compute_variant_bases(
        names_with_faculty_dept, letter_only_included_names=all_names)
    result: dict[str, tuple[str, list[str], dict[str, str]]] = {}
    for (base, _fac, _dept, _tag), members in letter_only_bases.items():
        members_sorted = sorted(members, key=lambda x: x[1])
        names_sorted = [n for n, _letter, _tag2 in members_sorted]
        letters = {n: letter for n, letter, _tag2 in members}
        for n in names_sorted:
            result[n] = (base, names_sorted, letters)
    return result


def compute_variant_full_labels(
    names_with_faculty_dept: list[tuple[str, str, str]],
    letter_split_excluded_names: frozenset[str] = frozenset(),
    num_excluded_names: frozenset[str] = NUM_MERGE_EXCLUDED_NAMES,
) -> dict[str, str]:
    """(科目名, faculty, department)のリストから、科目名 → 括弧付き接尾辞込みの完全な
    グループ表示名（例: "力学基礎(1/2)"、"生物学各論(A1/A2/C1/C2)"）のマップを返す。

    compute_variant_groups()はベースラベル（接尾辞を含まない科目名の共通部分）のみを返すため、
    ベースラベルだけでは元の科目名と見分けがつかない画面（管理画面のレビュー科目別集計等）
    向けに追加した。判定基準はcompute_variant_groups()と同一（compute_variant_bases()を共有）。
    グループに属さない科目名はマップに含めない。letter_split_excluded_names/num_excluded_namesは
    compute_variant_groups()参照。letter_only_included_namesを渡さない理由は
    compute_variant_groups()のdocstring参照（レビュー関連機能への意図しない波及を防ぐため）。
    """
    sem_bases, num_bases, paren_num_bases, _letter_only_bases = compute_variant_bases(
        names_with_faculty_dept, num_excluded_names=num_excluded_names,
        letter_split_excluded_names=letter_split_excluded_names)
    result: dict[str, str] = {}

    for (base_lang, _fac, _dept), members in sem_bases.items():
        suffix = "/".join(sk for _n, sk in sorted(members, key=lambda x: x[1]))
        label = f"{base_lang}({suffix})"
        for n, _sk in members:
            result[n] = label

    for (base, _letter, _fac, _dept, _tag), members in num_bases.items():
        label = f"{base}({num_variant_suffix(members)})"
        for n, _letter2, _sk, _disp, _tag in members:
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
    extra_excluded_names: frozenset[str] = frozenset(),
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
    extra_excluded_names（既定で空集合）はNUM_MERGE_EXCLUDED_NAMESに加えて数字・ローマ数字
    バリアント統合対象から除外する科目名の集合。呼び出し側（routers/admin/courses.py）が
    subjects.variant_merge_excluded=trueの科目名を渡すことで、管理画面の「統合解除」ボタン
    （2026-09-06）による動的な除外を反映する。
    """
    result: dict[tuple[str, str], str] = {}
    assigned: set[tuple[str, str]] = set()
    items = list(dict.fromkeys((n, c or "") for n, c in names_with_classification))

    # 1) セミナー系（外国語セミナーA(英語) → 外国語セミナー(英語) (A/B/C/D)）
    sem_bases: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for name, cls in items:
        if name in NUM_MERGE_EXCLUDED_NAMES or name in extra_excluded_names:
            continue
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
    # 廃止した（compute_variant_bases()のモジュールdocstring参照）。ただし2026-09-04に
    # LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONS（国際人間科学部専門科目のみ）限定の
    # オプトイン例外を追加した（本関数末尾のブロック4参照）。

    # 2) 数字・ローマ数字バリアント（同一classification単位でグループ化。タグ（""/（遠隔）/
    # （再履修）/（遠隔）（再履修）の4種）が完全一致するクラス同士でのみ統合する）
    # NUM_MERGE_EXCLUDED_NAMESに属する科目名は数字バリアント統合の対象外（compute_variant_bases()参照）
    num_bases: dict[tuple[str, str, str, str], list[tuple[str, str, int, str, str]]] = {}
    for name, cls in items:
        if ((name, cls) in assigned or name in NUM_MERGE_EXCLUDED_NAMES
                or name in extra_excluded_names or name in _MANUAL_VARIANT_GROUP_NAMES):
            continue
        m = _vnum_match(name)
        if m:
            base, letter, sk, disp, tag = m
            group_letter = "" if cls in LETTER_SPLIT_EXCLUDED_CLASSIFICATIONS else letter
            key = (base, group_letter, cls, tag)
            num_bases.setdefault(key, []).append((name, letter, sk, disp, tag))
    for (base, _letter, cls, _tag), members in num_bases.items():
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
        if ((name, cls) in assigned or name in NUM_MERGE_EXCLUDED_NAMES
                or name in extra_excluded_names or name in _MANUAL_VARIANT_GROUP_NAMES):
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

    # 4) アルファベットのみ末尾が異なる「文字バリアント」統合（2026-09-04、ユーザー指示で
    # LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONS（国際人間科学部専門科目のみ）限定の
    # オプトイン例外として追加。恒常廃止ルールの対象外はこのclassificationのみで、
    # 他のclassificationには一切影響しない）。
    letter_only_bases: dict[tuple[str, str, str], list[tuple[str, str, str]]] = {}
    for name, cls in items:
        if ((name, cls) in assigned or cls not in LETTER_ONLY_MERGE_INCLUDED_CLASSIFICATIONS
                or name in extra_excluded_names or name in _MANUAL_VARIANT_GROUP_NAMES):
            continue
        m = _vletter_only_match(name)
        if m:
            base, letter, tag = m
            key = (base, cls, tag)
            letter_only_bases.setdefault(key, []).append((name, letter, tag))
    for (base, cls, _tag), members in letter_only_bases.items():
        if len(members) < 2:
            continue
        label = f"{base} ({letter_variant_suffix(members)})"
        for n, _letter, _tag in members:
            result[(n, cls)] = label
            assigned.add((n, cls))

    # 5) 手動グループ（MANUAL_VARIANT_GROUPS）。グループ内の全科目名が対象リストに揃っている
    # 場合のみ適用する。
    names_present = {n for n, _c in items}
    for group in MANUAL_VARIANT_GROUPS:
        if not all(n in names_present for n in group["names"]):
            continue
        if any(n in extra_excluded_names for n in group["names"]):
            continue
        for n, c in items:
            if (n, c) in assigned or n not in group["names"]:
                continue
            result[(n, c)] = group["label"]
            assigned.add((n, c))

    return result
