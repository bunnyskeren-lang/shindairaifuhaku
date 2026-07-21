from types import SimpleNamespace

from core.config import make_syllabus_url, syllabus_department_key

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
