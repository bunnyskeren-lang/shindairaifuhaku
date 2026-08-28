"""line_bot/handler.py の科目名末尾バリアント判定は2026-08-25まで core/subject_variants.py と
手動で複製されていた（_vnum_match()・_VSEM正規表現）。片方だけ更新されると、LINE botの
メッセージ検索・科目一覧（handler.py側）とレビュー投稿フォーム・レビュー閲覧（core側、
[[project_review_view_variant_merge_20260824]]参照）でバリアントグループの判定結果が食い違い、
どちらか一方だけで統合表示が崩れる恐れがあった（normalize_instructor_name/normalize_subject_name
の複製で実際に起きたのと同種の不具合、tests/test_normalize_functions_sync.py参照）。
現在はline_bot/handler.pyがcore/subject_variants.pyから直接importする形に一本化したため、
このテストはその状態が将来のリファクタで崩れない（再び手動複製へ戻らない）ことを保証する。
"""
import core.subject_variants as subject_variants
import line_bot.handler as handler


def test_handler_vnum_match_is_the_shared_implementation():
    assert handler._vnum_match is subject_variants._vnum_match


def test_handler_vsem_is_the_shared_implementation():
    assert handler._VSEM is subject_variants._VSEM
