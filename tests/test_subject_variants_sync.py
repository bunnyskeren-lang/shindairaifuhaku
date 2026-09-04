"""line_bot/handler.py の科目名末尾バリアント判定は2026-08-25まで core/subject_variants.py と
手動で複製されていた（_vnum_match()・_VSEM正規表現）。片方だけ更新されると、LINE botの
メッセージ検索・科目一覧（handler.py側）とレビュー投稿フォーム・レビュー閲覧（core側、
[[project_review_view_variant_merge_20260824]]参照）でバリアントグループの判定結果が食い違い、
どちらか一方だけで統合表示が崩れる恐れがあった（normalize_instructor_name/normalize_subject_name
の複製で実際に起きたのと同種の不具合、tests/test_normalize_functions_sync.py参照）。

2026-08-25に判定規則の正規表現(_vnum_match/_VSEM)だけimportで共有したが、束ね方の手順
（グループ化・ラベル組み立て）自体はline_bot/handler.py側に別途手動複製されたままで、
2026-08-30まで同期漏れ（faculty+departmentスコープの追随漏れ）が実際に起きていた。
2026-08-30にcore.subject_variants.compute_variant_bases()へ判定・束ね方の実体そのものを
一本化し、line_bot/handler.pyはそれをimportして使う形にした。このテストはその状態が
将来のリファクタで崩れない（再び手動複製へ戻らない）ことを保証する。
"""
import core.subject_variants as subject_variants
import line_bot.handler as handler


def test_handler_uses_shared_variant_bases_implementation():
    assert handler.compute_variant_bases is subject_variants.compute_variant_bases


def test_shared_variant_bases_grouping_matches_flat_group_map():
    """compute_variant_bases()の3辞書から再構成したグループ化結果が、
    compute_variant_groups()（フラットなname→labelマップ）と一致することを確認する
    （line_bot/handler.py側は前者を、レビュー投稿フォーム等は後者を使うため、
    両者が同じ判定規則から矛盾なく導出されていることの保証）。
    num_basesのキーはletterを含む（2026-09-03）ため、reconstructed側もletterを
    ラベルに反映して組み立てる。"""
    names_with_fd = [
        ("生物学各論A1", "教養教育院", ""),
        ("生物学各論A2", "教養教育院", ""),
        ("生物学各論C1", "教養教育院", ""),
        ("生物学各論C2", "教養教育院", ""),
        ("微分積分1", "教養教育院", ""),
        ("微分積分2", "教養教育院", ""),
        ("外国語セミナーA(英語)", "教養教育院", ""),
        ("外国語セミナーB(英語)", "教養教育院", ""),
        ("単独科目", "教養教育院", ""),
    ]
    sem_bases, num_bases, _paren_num_bases, _letter_only_bases = subject_variants.compute_variant_bases(names_with_fd)
    flat = subject_variants.compute_variant_groups(names_with_fd)

    reconstructed: dict[str, str] = {}
    for (base_lang, _fac, _dept), members in sem_bases.items():
        for n, _sk in members:
            reconstructed[n] = base_lang
    for (base, letter, _fac, _dept, _tag), members in num_bases.items():
        label = f"{base}{letter}" if letter else base
        for n, _letter, _sk, _disp, _tag in members:
            reconstructed[n] = label

    assert reconstructed == flat
    assert "単独科目" not in flat
    # A系列とC系列は別グループ（letterがグループ化キーに含まれるため混ざらない）
    assert flat["生物学各論A1"] == flat["生物学各論A2"]
    assert flat["生物学各論C1"] == flat["生物学各論C2"]
    assert flat["生物学各論A1"] != flat["生物学各論C1"]


def test_compute_variant_full_labels_includes_bracketed_suffix():
    """compute_variant_full_labels()はcompute_variant_groups()と同じグループ判定を使うが、
    ベースラベルではなく「力学基礎(1/2)」のような接尾辞込みの完全ラベルを返す
    （管理画面のレビュー科目別集計向け、routers/admin/reviews.py参照）。"""
    names_with_fd = [
        ("生物学各論A1", "教養教育院", ""),
        ("生物学各論A2", "教養教育院", ""),
        ("力学基礎1", "工学部", ""),
        ("力学基礎2", "工学部", ""),
        ("外国語セミナーA(英語)", "教養教育院", ""),
        ("外国語セミナーB(英語)", "教養教育院", ""),
        ("単独科目", "教養教育院", ""),
    ]
    labels = subject_variants.compute_variant_full_labels(names_with_fd)

    assert labels["生物学各論A1"] == labels["生物学各論A2"] == "生物学各論(A1/A2)"
    assert labels["力学基礎1"] == labels["力学基礎2"] == "力学基礎(1/2)"
    assert labels["外国語セミナーA(英語)"] == "外国語セミナー(英語)(A/B)"
    assert "単独科目" not in labels


def test_numeral_letter_dual_branch_splits_by_letter():
    """「アルファベット＋数字」の二重枝分かれ（例:数学科教育論A1/A2/C1/C2）は、
    2026-09-03にユーザー指示で恒常ルール化。アルファベット部分は並行クラス（担当教員・
    内容が別）を表すことが多いため、数字部分（連番/クォーター）だけで束ねず、letterが
    異なるグループ同士は分離する。従来はletterをグループ化キーに含めておらず
    「数学科教育論(A1/A2/C1/C2)」のようにA系列とC系列が1グループに混ざって表示されて
    いた不具合の再発防止テスト。"""
    names_with_fd = [
        ("数学科教育論A1", "国際人間科学部", ""),
        ("数学科教育論A2", "国際人間科学部", ""),
        ("数学科教育論C1", "国際人間科学部", ""),
        ("数学科教育論C2", "国際人間科学部", ""),
    ]
    labels = subject_variants.compute_variant_full_labels(names_with_fd)
    assert labels["数学科教育論A1"] == labels["数学科教育論A2"] == "数学科教育論(A1/A2)"
    assert labels["数学科教育論C1"] == labels["数学科教育論C2"] == "数学科教育論(C1/C2)"

    names_with_cls = [
        ("数学科教育論A1", "国際人間科学部専門科目"),
        ("数学科教育論A2", "国際人間科学部専門科目"),
        ("数学科教育論C1", "国際人間科学部専門科目"),
        ("数学科教育論C2", "国際人間科学部専門科目"),
    ]
    display_result = subject_variants.compute_variant_display_groups(names_with_cls)
    assert display_result[("数学科教育論A1", "国際人間科学部専門科目")] == "数学科教育論 (A1/A2)"
    assert display_result[("数学科教育論A2", "国際人間科学部専門科目")] == "数学科教育論 (A1/A2)"
    assert display_result[("数学科教育論C1", "国際人間科学部専門科目")] == "数学科教育論 (C1/C2)"
    assert display_result[("数学科教育論C2", "国際人間科学部専門科目")] == "数学科教育論 (C1/C2)"


def test_letter_split_excluded_classifications_keep_letters_merged():
    """教養(外国語第1)/(外国語第2)（Academic English・ドイツ語/フランス語/ロシア語/中国語初級等）は
    語尾が「アルファベット＋数字」形式だが、アルファベット部分は数学科教育論のような並行クラス
    ではなくクラス分け（同一内容）を表す。2026-09-04にtest_numeral_letter_dual_branch_splits_by_letter
    のletter分離ルールを導入した際、語学科目もA系列/B系列に分裂する副作用が発覚したため、
    LETTER_SPLIT_EXCLUDED_CLASSIFICATIONS対象はletterをグループ化キーから除外し、
    従来通り数字部分だけで束ねる（ユーザー指示、2026-09-04）。"""
    names_with_fd = [
        ("Academic English Communication A1", "教養教育院", ""),
        ("Academic English Communication A2", "教養教育院", ""),
        ("Academic English Communication B1", "教養教育院", ""),
        ("Academic English Communication B2", "教養教育院", ""),
    ]
    excluded_names = frozenset(n for n, _, _ in names_with_fd)
    labels = subject_variants.compute_variant_full_labels(names_with_fd, letter_split_excluded_names=excluded_names)
    expected = "Academic English Communication(A1/A2/B1/B2)"
    assert labels["Academic English Communication A1"] == expected
    assert labels["Academic English Communication A2"] == expected
    assert labels["Academic English Communication B1"] == expected
    assert labels["Academic English Communication B2"] == expected

    names_with_cls = [
        ("ドイツ語初級A1", "教養(外国語第2)"),
        ("ドイツ語初級A2", "教養(外国語第2)"),
        ("ドイツ語初級B1", "教養(外国語第2)"),
        ("ドイツ語初級B2", "教養(外国語第2)"),
    ]
    display_result = subject_variants.compute_variant_display_groups(names_with_cls)
    expected_display = "ドイツ語初級 (A1/A2/B1/B2)"
    for name, cls in names_with_cls:
        assert display_result[(name, cls)] == expected_display


def test_letter_only_variants_are_never_merged():
    """末尾がA/B/C/Dのみ異なる「文字バリアント」の統合は2026-09-02にユーザー指示で
    恒常的に廃止した（並行クラスとトピック違いの独立科目が語尾アルファベットだけでは
    見分けられず誤統合が繰り返し問題になっていたため）。数字・ローマ数字・セミナー系の
    判定には影響しないことも確認する。"""
    names_with_fd = [
        ("心理学A", "教養教育院", ""),
        ("心理学B", "教養教育院", ""),
        ("力学基礎1", "工学部", ""),
        ("力学基礎2", "工学部", ""),
    ]

    groups = subject_variants.compute_variant_groups(names_with_fd)
    assert "心理学A" not in groups
    assert "心理学B" not in groups
    assert groups["力学基礎1"] == groups["力学基礎2"] == "力学基礎"

    labels = subject_variants.compute_variant_full_labels(names_with_fd)
    assert "心理学A" not in labels
    assert "心理学B" not in labels
    assert labels["力学基礎1"] == "力学基礎(1/2)"


def test_letter_only_merge_opt_in_for_kokusai_ningen_senmon_classification():
    """2026-09-04にユーザー指示で、末尾アルファベットのみが異なる文字バリアント統合の
    恒常廃止ルール（2026-09-02）に「国際人間科学部専門科目」classification限定の
    オプトイン例外を追加した。DB上のSubject行は分けたまま（レビュー投稿・閲覧は
    引き続き別科目扱い）、LINE bot科目一覧・管理画面科目一覧の表示のみ統合する。
    letter_only_included_namesを渡さなければ引き続き統合されないことも確認する
    （compute_variant_groups/compute_variant_full_labels＝レビュー関連機能が使う経路には
    一切影響しないことの保証）。"""
    names_with_fd = [
        ("日本文化交流論A", "国際人間科学部", ""),
        ("日本文化交流論B", "国際人間科学部", ""),
        ("保健体育科教育論A", "国際人間科学部", ""),
        ("保健体育科教育論B", "国際人間科学部", ""),
        ("保健体育科教育論C", "国際人間科学部", ""),
        ("保健体育科教育論D", "国際人間科学部", ""),
    ]
    included_names = frozenset(n for n, _, _ in names_with_fd)

    # letter_only_included_namesを渡した場合のみ統合される
    _sem, _num, _paren, letter_only = subject_variants.compute_variant_bases(
        names_with_fd, letter_only_included_names=included_names)
    assert len(letter_only) == 2  # 日本文化交流論・保健体育科教育論の2グループ
    day = next(v for k, v in letter_only.items() if k[0] == "日本文化交流論")
    assert {n for n, _, _ in day} == {"日本文化交流論A", "日本文化交流論B"}
    hoken = next(v for k, v in letter_only.items() if k[0] == "保健体育科教育論")
    assert {n for n, _, _ in hoken} == {"保健体育科教育論A", "保健体育科教育論B", "保健体育科教育論C", "保健体育科教育論D"}

    # 渡さなければ（既定）従来通り統合されない
    _sem2, _num2, _paren2, letter_only_default = subject_variants.compute_variant_bases(names_with_fd)
    assert letter_only_default == {}

    # レビュー投稿フォーム/api/preload等が使うcompute_variant_groups/full_labelsは
    # letter_only_included_namesを受け付けないため、この例外の影響を一切受けない
    groups = subject_variants.compute_variant_groups(names_with_fd)
    assert "日本文化交流論A" not in groups
    assert "保健体育科教育論A" not in groups

    # 管理画面向けcompute_variant_display_groups()はclassification単位でオプトインする
    names_with_cls = [(n, "国際人間科学部専門科目") for n, _, _ in names_with_fd]
    display_result = subject_variants.compute_variant_display_groups(names_with_cls)
    assert display_result[("日本文化交流論A", "国際人間科学部専門科目")] == "日本文化交流論 (A/B)"
    assert display_result[("日本文化交流論B", "国際人間科学部専門科目")] == "日本文化交流論 (A/B)"
    assert display_result[("保健体育科教育論A", "国際人間科学部専門科目")] == "保健体育科教育論 (A/B/C/D)"
    assert display_result[("保健体育科教育論D", "国際人間科学部専門科目")] == "保健体育科教育論 (A/B/C/D)"

    # 他のclassificationでは引き続き統合されない（恒常廃止ルールはそのまま）
    other_cls = [(n, "教養(人文)") for n, _, _ in names_with_fd]
    other_result = subject_variants.compute_variant_display_groups(other_cls)
    assert ("日本文化交流論A", "教養(人文)") not in other_result
    assert ("保健体育科教育論A", "教養(人文)") not in other_result


def test_num_excluded_names_keeps_health_sports_jisshu_separate():
    """健康・スポーツ科学実習1/2は、実習1と実習2で内容が異なる独立科目のため、
    2026-08-31にユーザー指示で数字バリアント統合対象から除外した
    （NUM_MERGE_EXCLUDED_NAMESはcompute_variant_bases()の既定引数のため、
    呼び出し側が明示的に渡さなくても除外される）。"""
    names_with_fd = [
        ("健康・スポーツ科学実習1", "教養教育院", ""),
        ("健康・スポーツ科学実習2", "教養教育院", ""),
        ("力学基礎1", "工学部", ""),
        ("力学基礎2", "工学部", ""),
    ]

    groups = subject_variants.compute_variant_groups(names_with_fd)
    assert "健康・スポーツ科学実習1" not in groups
    assert "健康・スポーツ科学実習2" not in groups
    assert groups["力学基礎1"] == groups["力学基礎2"] == "力学基礎"

    labels = subject_variants.compute_variant_full_labels(names_with_fd)
    assert "健康・スポーツ科学実習1" not in labels
    assert "健康・スポーツ科学実習2" not in labels

    names_with_cls = [
        ("健康・スポーツ科学実習1", "教養(健康・スポーツ)"),
        ("健康・スポーツ科学実習2", "教養(健康・スポーツ)"),
    ]
    display_result = subject_variants.compute_variant_display_groups(names_with_cls)
    assert ("健康・スポーツ科学実習1", "教養(健康・スポーツ)") not in display_result
    assert ("健康・スポーツ科学実習2", "教養(健康・スポーツ)") not in display_result


def test_compute_variant_display_groups_never_merges_letter_only_variants():
    """管理画面向けcompute_variant_display_groups()も、末尾がA/B/C/Dのみ異なる
    文字バリアントは（分類を問わず）統合しない（2026-09-02に恒常廃止）。"""
    names_with_cls = [
        ("心理学A", "教養(人文)"),
        ("心理学B", "教養(人文)"),
        ("構造設計A", "工学部専門科目"),
        ("構造設計B", "工学部専門科目"),
    ]
    result = subject_variants.compute_variant_display_groups(names_with_cls)
    assert ("心理学A", "教養(人文)") not in result
    assert ("心理学B", "教養(人文)") not in result
    assert ("構造設計A", "工学部専門科目") not in result
    assert ("構造設計B", "工学部専門科目") not in result


def test_compute_variant_full_labels_handles_remote_retake_tags():
    """無タグ・遠隔・再履修・遠隔＋再履修の4系統はそれぞれタグが完全一致するクラス同士
    でのみ束ねられ、互いには混在しないことを確認する。
    2026-08-31、遠隔・再履修タグ導入時に追加した当初は遠隔タグの有無だけで区別しており、
    無タグと再履修タグを同一グループに混在させていた。2026-09-02にユーザーから
    「再履修は再履修のみで統合して」と指示を受け、4タグ完全一致（無タグは無タグ同士、
    再履修は再履修同士）に変更した。"""
    names_with_fd = [
        ("力学基礎1", "工学部", ""),
        ("力学基礎2", "工学部", ""),
        ("力学基礎1（遠隔）", "工学部", ""),
        ("力学基礎2（遠隔）", "工学部", ""),
        ("力学基礎1（再履修）", "工学部", ""),
        ("力学基礎2（再履修）", "工学部", ""),
        ("力学基礎1（遠隔）（再履修）", "工学部", ""),
        ("力学基礎2（遠隔）（再履修）", "工学部", ""),
    ]
    labels = subject_variants.compute_variant_full_labels(names_with_fd)

    assert labels["力学基礎1"] == "力学基礎(1/2)"
    assert labels["力学基礎2"] == "力学基礎(1/2)"
    assert labels["力学基礎1（遠隔）"] == "力学基礎(1（遠隔）/2（遠隔）)"
    assert labels["力学基礎2（遠隔）"] == "力学基礎(1（遠隔）/2（遠隔）)"
    assert labels["力学基礎1（再履修）"] == "力学基礎(1（再履修）/2（再履修）)"
    assert labels["力学基礎2（再履修）"] == "力学基礎(1（再履修）/2（再履修）)"
    assert labels["力学基礎1（遠隔）（再履修）"] == "力学基礎(1（遠隔）（再履修）/2（遠隔）（再履修）)"
    assert labels["力学基礎2（遠隔）（再履修）"] == "力学基礎(1（遠隔）（再履修）/2（遠隔）（再履修）)"


def test_retake_tag_does_not_merge_with_plain_class():
    """再履修クラスは無タグの通常クラスとは統合されない（ベース名+数字1件だけの再履修
    クラスは、通常クラスの数字違いバリアントとまとめて統合されてはならない）。
    2026-09-02、ユーザー指示「再履修は再履修のみで統合して」の直接的な確認用テスト。"""
    names_with_fd = [
        ("微分積分1", "教養教育院", ""),
        ("微分積分2", "教養教育院", ""),
        ("微分積分1（再履修）", "教養教育院", ""),
    ]
    groups = subject_variants.compute_variant_groups(names_with_fd)
    # 通常クラス(1/2)は統合されるが、再履修クラスは仲間（同じ再履修タグの他の数字）が
    # いないため単独のままグループに属さない
    assert groups["微分積分1"] == "微分積分"
    assert groups["微分積分2"] == "微分積分"
    assert "微分積分1（再履修）" not in groups

    labels = subject_variants.compute_variant_full_labels(names_with_fd)
    assert labels["微分積分1"] == "微分積分(1/2)"
    assert "微分積分1（再履修）" not in labels


def test_student_id_split_classes_merge_into_base_numeral_group():
    """「微分積分1　Z（学番下3桁：001～110）」のような学籍番号下3桁での分割クラスは、
    ベース科目（微分積分1）と同じクラスの別枠でしかないため統合対象。2026-09-02、
    import_syllabus.pyの--also-coursesクラッシュバグ修正後の再インポートでこれらの
    分割クラスが初めてDBに投入され、_VNUMがマッチできず未統合のまま表示される不具合が
    見つかった。ベース名のみを表示し、分割の内訳（Z/T機械/B/E等）は表示に出さない
    （ユーザー指示、2026-09-02）。"""
    names_with_fd = [
        ("微分積分1", "教養教育院", ""),
        ("微分積分1 Z（学番下3桁：001～110）", "教養教育院", ""),
        ("微分積分1 Z（学番下3桁：111～）", "教養教育院", ""),
        ("微分積分2", "教養教育院", ""),
        ("微分積分2 Z（学番下3桁：001～110）", "教養教育院", ""),
        ("数理統計1", "教養教育院", ""),
        ("数理統計1　T機械(学番下3桁：501-522)，A", "教養教育院", ""),
        ("数理統計1　T機械（学番下3桁：523-)", "教養教育院", ""),
        ("微分積分入門1", "教養教育院", ""),
        ("微分積分入門1 B(学番下3桁：501-590)", "教養教育院", ""),
        ("微分積分入門1 E(学番下3桁：001-090)", "教養教育院", ""),
    ]
    groups = subject_variants.compute_variant_groups(names_with_fd)
    assert groups["微分積分1 Z（学番下3桁：001～110）"] == "微分積分"
    assert groups["微分積分1 Z（学番下3桁：111～）"] == "微分積分"
    assert groups["微分積分2 Z（学番下3桁：001～110）"] == "微分積分"
    assert groups["数理統計1　T機械(学番下3桁：501-522)，A"] == "数理統計"
    assert groups["数理統計1　T機械（学番下3桁：523-)"] == "数理統計"
    assert groups["微分積分入門1 B(学番下3桁：501-590)"] == "微分積分入門"
    assert groups["微分積分入門1 E(学番下3桁：001-090)"] == "微分積分入門"

    labels = subject_variants.compute_variant_full_labels(names_with_fd)
    # 学番分割の内訳(Z/T機械/B/E)はラベルに出さず、重複した数字も1つに畳む
    assert labels["微分積分1"] == "微分積分(1/2)"
    assert labels["微分積分1 Z（学番下3桁：001～110）"] == "微分積分(1/2)"
    assert labels["数理統計1"] == "数理統計(1)"
    assert labels["数理統計1　T機械(学番下3桁：501-522)，A"] == "数理統計(1)"


def test_student_id_parity_split_classes_merge_into_base_numeral_group():
    """「力学基礎1　Z　学籍番号：奇数」のような、括弧を使わず「学籍番号：奇数/偶数」で
    分割するクラスも_STUDENT_ID_SPLIT_REの対象（2026-09-02、力学基礎で発覚。既存の
    「Z（学番下3桁：...）」パターンとは表記が異なり、当初のregexではマッチできなかった）。"""
    names_with_fd = [
        ("力学基礎1", "教養教育院", ""),
        ("力学基礎1　Z　学籍番号：奇数", "教養教育院", ""),
        ("力学基礎1　Z　学籍番号：偶数", "教養教育院", ""),
        ("力学基礎2", "教養教育院", ""),
        ("力学基礎2　Z　学籍番号：奇数", "教養教育院", ""),
        ("力学基礎2　Z　学籍番号：偶数", "教養教育院", ""),
    ]
    groups = subject_variants.compute_variant_groups(names_with_fd)
    assert groups["力学基礎1　Z　学籍番号：奇数"] == "力学基礎"
    assert groups["力学基礎1　Z　学籍番号：偶数"] == "力学基礎"
    assert groups["力学基礎2　Z　学籍番号：奇数"] == "力学基礎"
    assert groups["力学基礎2　Z　学籍番号：偶数"] == "力学基礎"

    labels = subject_variants.compute_variant_full_labels(names_with_fd)
    assert labels["力学基礎1"] == "力学基礎(1/2)"
    assert labels["力学基礎1　Z　学籍番号：奇数"] == "力学基礎(1/2)"
    assert labels["力学基礎2　Z　学籍番号：偶数"] == "力学基礎(1/2)"


def test_paren_alias_numeral_variants_are_grouped():
    """「ライフコースの心理学1（発達心理学1）」のような、括弧付きの旧名・別名にも
    末尾数字を持つ科目名は、_VNUM（文字列末尾が直接数字/ローマ数字である前提）では
    マッチできないため、_VNUM_PARENによる別グループ(paren_num_bases)で扱う
    （2026-09-03、ユーザー指示でこのパターンをDB統合ではなく表示バリアント統合方式に
    変更した際に追加）。"""
    names_with_fd = [
        ("ライフコースの心理学1（発達心理学1）", "国際人間科学部", "発達コミュニティ学科"),
        ("ライフコースの心理学2（発達心理学2）", "国際人間科学部", "発達コミュニティ学科"),
        ("単独科目", "国際人間科学部", ""),
    ]
    groups = subject_variants.compute_variant_groups(names_with_fd)
    assert groups["ライフコースの心理学1（発達心理学1）"] == "ライフコースの心理学（発達心理学）"
    assert groups["ライフコースの心理学2（発達心理学2）"] == "ライフコースの心理学（発達心理学）"
    assert "単独科目" not in groups

    labels = subject_variants.compute_variant_full_labels(names_with_fd)
    assert labels["ライフコースの心理学1（発達心理学1）"] == "ライフコースの心理学(1/2)（発達心理学(1/2)）"
    assert labels["ライフコースの心理学2（発達心理学2）"] == "ライフコースの心理学(1/2)（発達心理学(1/2)）"


def test_paren_alias_numeral_variants_allow_mismatched_inner_outer_numbering():
    """「心の発達と教育2（教育・学校心理学1）」「心の発達と教育3（教育・学校心理学2）」の
    ように、括弧の外側（科目番号）と内側（別名の番号）の連番がずれている実例が国際人間
    科学部専門科目に存在するため、両者を独立した接尾辞として組み立てられることを確認する。"""
    names_with_fd = [
        ("心の発達と教育2（教育・学校心理学1）", "国際人間科学部", "発達コミュニティ学科"),
        ("心の発達と教育3（教育・学校心理学2）", "国際人間科学部", "発達コミュニティ学科"),
    ]
    labels = subject_variants.compute_variant_full_labels(names_with_fd)
    assert labels["心の発達と教育2（教育・学校心理学1）"] == "心の発達と教育(2/3)（教育・学校心理学(1/2)）"
    assert labels["心の発達と教育3（教育・学校心理学2）"] == "心の発達と教育(2/3)（教育・学校心理学(1/2)）"


def test_paren_alias_numeral_variant_single_member_not_grouped():
    """括弧付き別名パターンでも、同じ(main_base, paren_base, faculty, department, tag)の
    メンバーが1件だけならグループ化しない（他のバリアントパターンと同じ規則）。"""
    names_with_fd = [
        ("障害児発達学1（障害者・障害児心理学1）", "国際人間科学部", ""),
    ]
    groups = subject_variants.compute_variant_groups(names_with_fd)
    assert "障害児発達学1（障害者・障害児心理学1）" not in groups


def test_paren_alias_numeral_variants_in_display_groups():
    """管理画面向けcompute_variant_display_groups()でも括弧付き別名パターンが
    classification単位でグループ化されることを確認する。"""
    names_with_cls = [
        ("健康心理学1（健康・医療心理学1）", "国際人間科学部発達コミュニティ学科専門科目"),
        ("健康心理学2（健康・医療心理学2）", "国際人間科学部発達コミュニティ学科専門科目"),
    ]
    result = subject_variants.compute_variant_display_groups(names_with_cls)
    label = "健康心理学(1/2)（健康・医療心理学(1/2)）"
    assert result[("健康心理学1（健康・医療心理学1）", "国際人間科学部発達コミュニティ学科専門科目")] == label
    assert result[("健康心理学2（健康・医療心理学2）", "国際人間科学部発達コミュニティ学科専門科目")] == label
