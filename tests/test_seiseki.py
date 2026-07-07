from core.seiseki import classify_seiseki_raw, classify_senmon, extract_seiseki_raw, is_senmon2


def test_classify_senmon_groups():
    assert classify_senmon("初年次セミナー") == "初年次"
    assert classify_senmon("経営学基礎論") == "第1群"
    assert classify_senmon("経営管理") == "第2群"
    assert classify_senmon("簿記Ⅰ") == "第2群"
    assert classify_senmon("Global Business") == "グローバル"
    assert classify_senmon("未知の専門科目") == "第3群"


def test_is_senmon2_prefix_and_exact():
    assert is_senmon2("経営管理")
    assert is_senmon2("簿記Ⅰ")
    assert not is_senmon2("会計学特殊講義")


def test_classify_seiseki_raw_aggregates_by_group():
    raw = {
        "summaries": {
            "総合教養科目": 10.0,
            "基礎教養科目": 2.0,
            "情報科目": 2.0,
            "共通専門基礎科目": 6.0,
            "専門科目": 20.0,
            "外国語科目": 8.0,
        },
        "gaigo_courses": [
            {"name": "Academic English Communication", "credits": 4.0, "is_english": True, "is_foreign": False},
            {"name": "ドイツ語Ⅰ", "credits": 4.0, "is_english": False, "is_foreign": True},
        ],
        "senmon_courses": [
            {"name": "初年次セミナー", "credits": 2.0},
            {"name": "経営学基礎論", "credits": 2.0},
            {"name": "経営管理", "credits": 2.0},
            {"name": "Global Business", "credits": 2.0},
            {"name": "その他専門科目", "credits": 12.0},
        ],
    }
    result = classify_seiseki_raw(raw)
    assert result["kyoyo_kei"] == 10.0
    assert result["kyoyo_kiban"] == 4.0
    assert result["gaigo1"] == 4.0
    assert result["gaigo2"] == 4.0
    assert result["kyotsu"] == 6.0
    assert result["shonen"] == 2.0
    assert result["senmon1"] == 2.0
    assert result["senmon2"] == 2.0
    assert result["global"] == 2.0
    assert result["senmon3"] == 12.0


def test_extract_seiseki_raw_parses_course_lines():
    text = (
        "【外国語科目】\n"
        "Academic English Communication  2.0  優\n"
        "【専門科目】\n"
        "経営管理  2.0  良\n"
        "総合教養科目 10.0\n"
        "専門科目 2.0\n"
    )
    raw = extract_seiseki_raw(text)
    assert raw["gaigo_courses"] == [
        {"name": "Academic English Communication", "credits": 2.0, "is_english": True, "is_foreign": False},
    ]
    assert raw["senmon_courses"] == [{"name": "経営管理", "credits": 2.0}]
    assert raw["summaries"]["総合教養科目"] == 10.0
    assert raw["summaries"]["専門科目"] == 2.0
