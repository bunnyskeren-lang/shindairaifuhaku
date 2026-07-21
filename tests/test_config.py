from types import SimpleNamespace

from core.config import is_profile_complete, make_syllabus_url, normalize_instructor_name, normalize_subject_name, stars, syllabus_department_key

BASE = "https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026"


def test_empty_or_too_short_timetable_code():
    assert make_syllabus_url("", "") == ""
    assert make_syllabus_url("3", "") == ""


def test_simple_faculty_letters():
    assert make_syllabus_url("3U020", "") == f"{BASE}/20/data/2026_3U020.html"  # 教養
    assert make_syllabus_url("3B379", "") == f"{BASE}/06/data/2026_3B379.html"  # 経営学部
    assert make_syllabus_url("1X058", "") == f"{BASE}/15/data/2026_1X058.html"  # システム情報学部


def test_unknown_faculty_letter_returns_empty():
    assert make_syllabus_url("3Q123", "") == ""


def test_engineering_ranges_by_department_number_band():
    assert make_syllabus_url("2T005", "") == f"{BASE}/0921/data/2026_2T005.html"  # 建築(0-99)
    assert make_syllabus_url("2T120", "") == f"{BASE}/0922/data/2026_2T120.html"  # 市民工学(100-149)
    assert make_syllabus_url("2T175", "") == f"{BASE}/0923/data/2026_2T175.html"  # 電気電子(150-199)
    assert make_syllabus_url("2T220", "") == f"{BASE}/0924/data/2026_2T220.html"  # 機械(200-249)
    assert make_syllabus_url("2T275", "") == f"{BASE}/0925/data/2026_2T275.html"  # 応用化学(250-299)
    # letter "N" も同じ番号帯マッピングを使う
    assert make_syllabus_url("2N005", "") == f"{BASE}/0921/data/2026_2N005.html"


def test_engineering_range_boundaries_and_out_of_range():
    assert make_syllabus_url("2T099", "") == f"{BASE}/0921/data/2026_2T099.html"  # 建築の上限
    assert make_syllabus_url("2T100", "") == f"{BASE}/0922/data/2026_2T100.html"  # 市民工学の下限
    assert make_syllabus_url("2T350", "") == ""  # どの番号帯にも属さない
    assert make_syllabus_url("2TXX", "") == ""  # 数字以外は判定不能


def test_medicine_numeric_ranges():
    assert make_syllabus_url("1M950", "") == f"{BASE}/0801/data/2026_1M950.html"  # 医学科(900-999)
    assert make_syllabus_url("1M100", "") == f"{BASE}/080201/data/2026_1M100.html"  # 保健学科看護学専攻(0-399)
    assert make_syllabus_url("1M500", "") == ""  # 400-899はどちらのレンジにも属さない


def test_medicine_subletter():
    assert make_syllabus_url("1MB05", "") == f"{BASE}/0803/data/2026_1MB05.html"  # 医療創成工学科
    assert make_syllabus_url("1MZ05", "") == ""  # 未知のサブレター


def test_department_path_override_takes_priority_over_letter():
    # departmentオーバーライドが該当する場合は、timetable_codeの2文字目による
    # 通常判定（letterごとの番号帯・辞書引き）より必ず優先される
    assert make_syllabus_url("1X058", "工学部") == f"{BASE}/09/data/2026_1X058.html"
    assert make_syllabus_url("3B379", "理学部数学科") == f"{BASE}/0701/data/2026_3B379.html"
    assert make_syllabus_url("1M100", "医学部保健学科検査技術科学専攻") == f"{BASE}/080202/data/2026_1M100.html"


def test_syllabus_department_key_concatenates_faculty_and_department():
    subj = SimpleNamespace(faculty="工学部", department="建築学科")
    assert syllabus_department_key(subj) == "工学部建築学科"


def test_syllabus_department_key_handles_none():
    subj = SimpleNamespace(faculty=None, department=None)
    assert syllabus_department_key(subj) == ""
    subj2 = SimpleNamespace(faculty="教養教育院", department=None)
    assert syllabus_department_key(subj2) == "教養教育院"


# ── 異常系 ──────────────────────────────────────────────────────────────────

def test_make_syllabus_url_none_timetable_code_returns_empty():
    assert make_syllabus_url(None, "") == ""


def test_make_syllabus_url_lowercase_letter_is_normalized():
    # timetable_code[1]は判定のためだけ大文字化される(URLへの埋め込みは元の表記のまま)ため、
    # 小文字入力でも同じpath("/20/")が選ばれる
    assert make_syllabus_url("3u020", "") == f"{BASE}/20/data/2026_3u020.html"


def test_medicine_subletter_non_alpha_third_char_falls_back_to_numeric_range():
    # 3文字目が数字の場合はサブレター判定に入らず通常の番号帯判定になる
    assert make_syllabus_url("1M950", "") == f"{BASE}/0801/data/2026_1M950.html"


def test_normalize_instructor_name_empty_and_none():
    assert normalize_instructor_name("") == ""
    assert normalize_instructor_name(None) is None


def test_normalize_subject_name_empty_and_none():
    assert normalize_subject_name("") == ""
    assert normalize_subject_name(None) is None


def test_is_profile_complete_none_profile_is_false():
    assert not is_profile_complete(None)


def test_is_profile_complete_partial_fields_missing():
    p = SimpleNamespace(name="神戸太郎", student_id="2345678S", faculty="経営学部", grade=2, department=None)
    assert not is_profile_complete(p)


# ── 境界値 ──────────────────────────────────────────────────────────────────

def test_medicine_range_boundaries():
    assert make_syllabus_url("1M399", "") == f"{BASE}/080201/data/2026_1M399.html"  # 保健学科の上限
    assert make_syllabus_url("1M400", "") == ""  # 400は保健学科レンジの外、医学科レンジにも入らない
    assert make_syllabus_url("1M899", "") == ""  # 医学科レンジの下限直前
    assert make_syllabus_url("1M900", "") == f"{BASE}/0801/data/2026_1M900.html"  # 医学科の下限


def test_normalize_subject_name_converts_half_to_full_roman_numeral():
    assert normalize_subject_name("線形代数I") == "線形代数Ⅰ"
    assert normalize_subject_name("線形代数II") == "線形代数Ⅱ"
    assert normalize_subject_name("簿記III") == "簿記Ⅲ"


def test_normalize_subject_name_does_not_touch_roman_letters_inside_words():
    # 単語中のI/II/V等（AI、TOEIC、IV(ローマ数字ではなく型番等)のような英数字に挟まれた文字）は変換しない
    assert normalize_subject_name("AI基礎論") == "AI基礎論"
    assert normalize_subject_name("TOEIC対策") == "TOEIC対策"


def test_stars_clamps_to_1_to_5_range():
    assert stars(0) == "★☆☆☆☆"
    assert stars(1) == "★☆☆☆☆"
    assert stars(5) == "★★★★★"
    assert stars(6) == "★★★★★"
    assert stars(-3) == "★☆☆☆☆"


def test_is_profile_complete_all_fields_present():
    p = SimpleNamespace(name="神戸太郎", student_id="2345678S", faculty="経営学部", grade=2, department="")
    # department が空文字の場合、falsy値なので未完了扱いになる(仕様通りの挙動を確認)
    assert not is_profile_complete(p)
    p2 = SimpleNamespace(name="神戸太郎", student_id="2345678S", faculty="経営学部", grade=2, department="教養教育院")
    assert is_profile_complete(p2)
