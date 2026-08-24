from core.grading_method import (
    build_grading_method_from_edit_text,
    format_grading_method_for_edit,
    parse_grading_method,
)


def test_parse_empty():
    assert parse_grading_method(None) == []
    assert parse_grading_method("") == []


def test_parse_new_json_format():
    raw = '[{"label": "出席", "text": "たまにあり"}, {"label": "評価", "text": "定期試験(60%)"}]'
    assert parse_grading_method(raw) == [
        {"label": "出席", "text": "たまにあり"},
        {"label": "評価", "text": "定期試験(60%)"},
    ]


def test_parse_legacy_delimited_format():
    raw = "形式:講義・演習 / 出席:たまにあり / 難しい"
    assert parse_grading_method(raw) == [
        {"label": "形式", "text": "講義・演習"},
        {"label": "出席", "text": "たまにあり"},
        {"label": "", "text": "難しい"},
    ]


def test_parse_legacy_format_with_slash_in_free_text_does_not_crash():
    # 旧形式は補足欄の'/'や':'でパースが崩れうるが、クラッシュはしない
    raw = "評価:定期試験(補足:60/40くらい)"
    result = parse_grading_method(raw)
    assert result  # 何かしらパースされる（厳密な形は保証しない）


def test_parse_malformed_json_falls_back_to_legacy():
    # JSON.stringifyの結果が[:2000]で途中切断された場合を想定
    raw = '[{"label": "評価", "text": "定期試験'
    result = parse_grading_method(raw)
    assert isinstance(result, list)  # クラッシュしないことだけ保証


def test_format_for_edit_roundtrip():
    raw = '[{"label": "出席", "text": "たまにあり"}, {"label": "", "text": "難しい"}]'
    edit_text = format_grading_method_for_edit(raw)
    assert edit_text == "出席: たまにあり\n難しい"


def test_build_from_edit_text():
    edit_text = "出席: たまにあり\n難しい\n\n評価: 定期試験(60%)"
    result = build_grading_method_from_edit_text(edit_text)
    assert parse_grading_method(result) == [
        {"label": "出席", "text": "たまにあり"},
        {"label": "", "text": "難しい"},
        {"label": "評価", "text": "定期試験(60%)"},
    ]


def test_build_from_empty_edit_text_returns_none():
    assert build_grading_method_from_edit_text("") is None
    assert build_grading_method_from_edit_text("   \n  \n") is None


def test_roundtrip_stable():
    original = [{"label": "形式", "text": "講義・演習(補足:週替わり)"}, {"label": "出席", "text": "なし"}]
    import json
    raw = json.dumps(original, ensure_ascii=False)
    assert parse_grading_method(raw) == original
