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
    両者が同じ判定規則から矛盾なく導出されていることの保証）。"""
    names_with_fd = [
        ("生物学各論A1", "教養教育院", ""),
        ("生物学各論A2", "教養教育院", ""),
        ("生物学各論C1", "教養教育院", ""),
        ("微分積分1", "教養教育院", ""),
        ("微分積分2", "教養教育院", ""),
        ("外国語セミナーA(英語)", "教養教育院", ""),
        ("外国語セミナーB(英語)", "教養教育院", ""),
        ("単独科目", "教養教育院", ""),
    ]
    sem_bases, letter_bases, num_bases = subject_variants.compute_variant_bases(names_with_fd)
    flat = subject_variants.compute_variant_groups(names_with_fd)

    reconstructed: dict[str, str] = {}
    for (base_lang, _fac, _dept), members in sem_bases.items():
        for n, _sk in members:
            reconstructed[n] = base_lang
    for (base, _fac, _dept), variants in letter_bases.items():
        for s in variants:
            reconstructed[base + s] = base
    for (base, _fac, _dept, _remote), members in num_bases.items():
        for n, _letter, _sk, _disp, _tag in members:
            reconstructed[n] = base

    assert reconstructed == flat
    assert "単独科目" not in flat


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


def test_letter_excluded_names_keeps_variants_separate():
    """教養(人文)/(社会)/(自然)/(総合)/(健康・スポーツ)の科目名は、letter_excluded_namesに
    渡すことで文字(A-D)バリアント統合の対象から外せる（2026-08-31、DB上は元々別Subjectの
    ままだが表示だけ統合していたものを、この5分類に限り統合しない方針に変更）。
    数字・ローマ数字・セミナー系の判定には影響しないことも確認する。"""
    names_with_fd = [
        ("心理学A", "教養教育院", ""),
        ("心理学B", "教養教育院", ""),
        ("力学基礎1", "工学部", ""),
        ("力学基礎2", "工学部", ""),
    ]
    excluded = frozenset({"心理学A", "心理学B"})

    groups = subject_variants.compute_variant_groups(names_with_fd, letter_excluded_names=excluded)
    assert "心理学A" not in groups
    assert "心理学B" not in groups
    assert groups["力学基礎1"] == groups["力学基礎2"] == "力学基礎"

    labels = subject_variants.compute_variant_full_labels(names_with_fd, letter_excluded_names=excluded)
    assert "心理学A" not in labels
    assert "心理学B" not in labels
    assert labels["力学基礎1"] == "力学基礎(1/2)"


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


def test_compute_variant_display_groups_excludes_kyoyo_letter_classifications():
    """管理画面向けcompute_variant_display_groups()もLETTER_MERGE_EXCLUDED_CLASSIFICATIONSに
    属する分類では文字バリアントを統合しない。"""
    names_with_cls = [
        ("心理学A", "教養(人文)"),
        ("心理学B", "教養(人文)"),
        ("構造設計A", "工学部専門科目"),
        ("構造設計B", "工学部専門科目"),
    ]
    result = subject_variants.compute_variant_display_groups(names_with_cls)
    assert ("心理学A", "教養(人文)") not in result
    assert ("心理学B", "教養(人文)") not in result
    assert result[("構造設計A", "工学部専門科目")] == "構造設計 (A/B)"


def test_compute_variant_full_labels_handles_remote_retake_tags():
    """遠隔タグの有無でグループを分け、無タグ・再履修タグ同士／遠隔・遠隔＋再履修タグ同士
    それぞれで束ねられグループラベルに反映されることを確認する
    （2026-08-31、遠隔・再履修タグ導入時に追加。当初は遠隔/対面を同一グループに混在させて
    いたが、遠隔クラスは対面クラスと授業形態が異なり同一視できないためユーザー指示で分離）。"""
    names_with_fd = [
        ("力学基礎1", "工学部", ""),
        ("力学基礎1（遠隔）", "工学部", ""),
        ("力学基礎1（再履修）", "工学部", ""),
        ("力学基礎1（遠隔）（再履修）", "工学部", ""),
    ]
    labels = subject_variants.compute_variant_full_labels(names_with_fd)

    assert labels["力学基礎1"] == "力学基礎(1/1（再履修）)"
    assert labels["力学基礎1（再履修）"] == "力学基礎(1/1（再履修）)"
    assert labels["力学基礎1（遠隔）"] == "力学基礎(1（遠隔）/1（遠隔）（再履修）)"
    assert labels["力学基礎1（遠隔）（再履修）"] == "力学基礎(1（遠隔）/1（遠隔）（再履修）)"


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
