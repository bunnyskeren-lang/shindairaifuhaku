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
    for (base, _fac, _dept), members in num_bases.items():
        for n, _letter, _sk, _disp, _tag in members:
            reconstructed[n] = base

    assert reconstructed == flat
    assert "単独科目" not in flat
