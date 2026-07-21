import pytest

from core.seiseki import PDFPLUMBER_OK, classify_seiseki_raw, classify_senmon, extract_seiseki_raw, is_kikai_hisshu, is_senmon2, parse_seiseki_pdf


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


def test_is_kikai_hisshu_kyotsu_and_senmon():
    assert is_kikai_hisshu("線形代数1", kyotsu=True)
    assert not is_kikai_hisshu("その他共通科目", kyotsu=True)
    assert is_kikai_hisshu("機械工学実習Ⅰ(1Q)", kyotsu=False)
    assert is_kikai_hisshu("卒業研究", kyotsu=False)
    assert not is_kikai_hisshu("その他専門科目", kyotsu=False)


def test_classify_senmon_and_kikai_hisshu_do_not_collide_on_same_name():
    # 「卒業研究」は機械工学科では必修科目リストに含まれるが、経営学部の群分類には
    # 一切影響しない（別学部キーで判定するため、DB未設定時は第3群にフォールバックする）
    assert classify_senmon("卒業研究") == "第3群"
    assert is_kikai_hisshu("卒業研究", kyotsu=False)


def test_classify_seiseki_raw_aggregates_by_group():
    raw = {
        "summaries": {
            "人文系": 4.0,
            "自然系": 2.0,
            "社会系": 2.0,
            "総合系": 2.0,
            "基盤系": 4.0,
            "共通専門基礎科目": 6.0,
            "専門科目": 20.0,
            "外国語科目": 8.0,
        },
        "gaigo_courses": [
            {"name": "Academic English Communication", "credits": 4.0, "is_english": True, "is_foreign": False},
            {"name": "ドイツ語Ⅰ", "credits": 4.0, "is_english": False, "is_foreign": True},
        ],
        "senmon_courses": [
            {"name": "初年次セミナー", "credits": 2.0, "section": "専門科目"},
            {"name": "経営学基礎論", "credits": 2.0, "section": "専門科目"},
            {"name": "経営管理", "credits": 2.0, "section": "専門科目"},
            {"name": "Global Business", "credits": 2.0, "section": "専門科目"},
            {"name": "その他専門科目", "credits": 12.0, "section": "専門科目"},
        ],
    }
    result = classify_seiseki_raw(raw)
    assert result["jinbun"] == 4.0
    assert result["shizen"] == 2.0
    assert result["shakai"] == 2.0
    assert result["sougou"] == 2.0
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
    assert raw["senmon_courses"] == [{"name": "経営管理", "credits": 2.0, "section": "専門科目"}]
    assert raw["summaries"]["総合教養科目"] == 10.0
    assert raw["summaries"]["専門科目"] == 2.0


def test_extract_seiseki_raw_excludes_fukashi_courses():
    text = (
        "【専門科目】\n"
        "経営管理  2.0  良\n"
        "経営戦略  2.0  不可\n"
        "【外国語科目】\n"
        "ドイツ語Ⅰ  2.0  不可\n"
    )
    raw = extract_seiseki_raw(text)
    assert raw["senmon_courses"] == [{"name": "経営管理", "credits": 2.0, "section": "専門科目"}]
    assert raw["gaigo_courses"] == []


def test_classify_seiseki_raw_old_curriculum_falls_back_to_sougou():
    # 人文/自然/社会/総合系の内訳が無く「総合教養科目」で一括集計する旧カリキュラムの成績表
    raw = {
        "summaries": {
            "総合教養科目": 1.0,
            "基礎教養科目": 3.0,
            "情報科目": 1.0,
        },
        "gaigo_courses": [],
        "senmon_courses": [],
    }
    result = classify_seiseki_raw(raw)
    assert result["jinbun"] == 0.0
    assert result["shizen"] == 0.0
    assert result["shakai"] == 0.0
    assert result["sougou"] == 1.0
    assert result["kyoyo_kiban"] == 4.0


def test_classify_seiseki_raw_kanren_and_sonota():
    raw = {
        "summaries": {
            "法学部科目": 2.0,
            "経済学部科目": 4.0,
            "健康・スポーツ科学系": 1.0,
            "その他の科目_専門": 3.0,
        },
        "gaigo_courses": [],
        "senmon_courses": [],
    }
    result = classify_seiseki_raw(raw)
    assert result["kanren"] == 6.0
    assert result["sonota"] == 4.0


def test_extract_seiseki_raw_separates_duplicate_sonota_label_by_block():
    # 「その他の科目」は全学共通授業科目ブロックと専門科目ブロックの両方に同名で存在するため、
    # <<...>> の大区分見出しで分割して区別する
    text = (
        "<<全学共通授業科目 >>\n"
        "【外国語科目】\n"
        "ドイツ語Ⅰ  2.0  優 その他の科目 5.0\n"
        "<<専門科目 >>\n"
        "【専門科目】\n"
        "経営管理  2.0  良 その他の科目 3.0\n"
    )
    raw = extract_seiseki_raw(text)
    assert raw["summaries"]["その他の科目_専門"] == 3.0


def test_extract_seiseki_raw_kenko_label_fallback_without_suffix():
    text = (
        "<<全学共通授業科目 >>\n"
        "【外国語科目】\n"
        "ドイツ語Ⅰ  2.0  優 健康・スポーツ科学 1.5\n"
    )
    raw = extract_seiseki_raw(text)
    assert raw["summaries"]["健康・スポーツ科学系"] == 1.5


# ── 異常系 ──────────────────────────────────────────────────────────────────

def test_extract_seiseki_raw_empty_text_returns_zeroed_structure():
    raw = extract_seiseki_raw("")
    assert raw["gaigo_courses"] == []
    assert raw["senmon_courses"] == []
    assert all(v == 0.0 for v in raw["summaries"].values())


def test_extract_seiseki_raw_ignores_unmatched_lines():
    # 科目行の正規表現にマッチしない行（ヘッダー・空行・注記等）は無視され例外にならない
    text = (
        "成績照会\n"
        "\n"
        "氏名: 神戸太郎\n"
        "【専門科目】\n"
        "これは科目行ではない\n"
        "経営管理  2.0  良\n"
    )
    raw = extract_seiseki_raw(text)
    assert raw["senmon_courses"] == [{"name": "経営管理", "credits": 2.0, "section": "専門科目"}]


def test_extract_seiseki_raw_all_grade_labels_except_fukashi_are_counted():
    # 秀・優・良・可・合格・認定はすべて修得済みとして扱う。「不可」だけ除外する
    text = (
        "【専門科目】\n"
        "科目秀  2.0  秀\n"
        "科目優  2.0  優\n"
        "科目良  2.0  良\n"
        "科目可  2.0  可\n"
        "科目合格  2.0  合格\n"
        "科目認定  2.0  認定\n"
        "科目不可  2.0  不可\n"
    )
    raw = extract_seiseki_raw(text)
    names = {c["name"] for c in raw["senmon_courses"]}
    assert names == {"科目秀", "科目優", "科目良", "科目可", "科目合格", "科目認定"}
    assert "科目不可" not in names


def test_classify_seiseki_raw_handles_completely_empty_dict():
    # summaries/gaigo_courses/senmon_coursesが一切無い辞書でもKeyErrorにならず全項目0を返す
    result = classify_seiseki_raw({})
    assert result["senmon1"] == 0.0
    assert result["senmon3"] == 0.0
    assert result["gaigo1"] == 0.0
    assert result["kikai_kyotsu_hisshu"] == 0.0
    assert result["senmon_all"] == 0.0


def test_classify_senmon_empty_string_falls_back_to_dai3():
    assert classify_senmon("") == "第3群"


def test_is_kikai_hisshu_empty_string_is_false():
    assert not is_kikai_hisshu("", kyotsu=True)
    assert not is_kikai_hisshu("", kyotsu=False)


def test_parse_seiseki_pdf_raises_when_pdfplumber_unavailable(monkeypatch):
    monkeypatch.setattr("core.seiseki.PDFPLUMBER_OK", False)
    with pytest.raises(RuntimeError):
        parse_seiseki_pdf(b"dummy")


# ── 境界値 ──────────────────────────────────────────────────────────────────

def test_is_senmon2_prefix_boundary_does_not_match_unrelated_names():
    # 前方一致は「簿記」で始まる名前のみ対象。似た文字列を含むだけでは誤マッチしない
    assert is_senmon2("簿記")
    assert is_senmon2("簿記Ⅰ")
    assert is_senmon2("簿記論")  # 「簿記」で始まるので前方一致対象
    assert not is_senmon2("財務簿記")  # 先頭が「簿記」でないため対象外
    assert not is_senmon2("経営管理論")  # 完全一致リストの「経営管理」とは別科目


def test_classify_seiseki_raw_senmon3_clamped_to_zero_when_breakdown_exceeds_total():
    # 専門科目の内訳合計(第1群等)がsummariesの専門科目合計を上回る不整合データでも、
    # senmon3（その他専門科目）は負値にならずゼロにクランプされる
    raw = {
        "summaries": {"専門科目": 2.0},
        "gaigo_courses": [],
        "senmon_courses": [
            {"name": "経営学基礎論", "credits": 10.0, "section": "専門科目"},  # 第1群、合計を上回る
        ],
    }
    result = classify_seiseki_raw(raw)
    assert result["senmon1"] == 10.0
    assert result["senmon3"] == 0.0


def test_classify_seiseki_raw_gaigo_odd_total_rounds_half_and_half():
    # 外国語科目の内訳(英語/その他)が無く合計のみ判明している場合、均等に按分する。
    # 奇数値(3.0)は0.5刻みで丸められる
    raw = {
        "summaries": {"外国語科目": 3.0},
        "gaigo_courses": [],
        "senmon_courses": [],
    }
    result = classify_seiseki_raw(raw)
    assert result["gaigo1"] == 1.5
    assert result["gaigo2"] == 1.5


def test_classify_seiseki_raw_zero_credits_course_counts_as_zero():
    raw = {
        "summaries": {"専門科目": 0.0},
        "gaigo_courses": [],
        "senmon_courses": [{"name": "経営管理", "credits": 0.0, "section": "専門科目"}],
    }
    result = classify_seiseki_raw(raw)
    assert result["senmon2"] == 0.0
    assert result["senmon3"] == 0.0


@pytest.mark.skipif(not PDFPLUMBER_OK, reason="pdfplumber not installed")
def test_parse_seiseki_pdf_gpa_absent_returns_none(monkeypatch):
    class _FakePage:
        def extract_text(self):
            return "GPAの記載が無い成績表テキスト"

    class _FakePdf:
        pages = [_FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("core.seiseki._pdfplumber.open", lambda *a, **kw: _FakePdf())
    result = parse_seiseki_pdf(b"dummy")
    assert result["gpa"] is None
