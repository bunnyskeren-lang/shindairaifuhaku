"""
シラバスデータインポートスクリプト（全学部・教養共通の入り口）
使い方:
  python -X utf8 import_syllabus.py <データファイル.txt> [<データファイル2.txt> ...] [--env dev|prod]
  python -X utf8 import_syllabus.py ../data/syllabus_*.txt --env dev --also-courses

データファイルはシラバスサイトからコピペしたテキストをそのまま保存したもの。
1ファイルに複数学部・教養が混在していてもよい（所属列から判定する）。
所属列が「教養教育院」の行は科目名から分類（教養(人文)等）を自動判定するため、
--classification/--faculty を指定する必要はない。他学部を一括登録する場合は
--classification/--faculty で明示するか、無指定なら所属列から学部名を推定する。
時間割（syllabi/schedules、マイ時間割に表示するコマ情報）は第3・第4クォーター・後期・集中の
科目のみインポートします。担当教員（instructors/course_sections）は前期科目も含めて全学期分を
作成します（前期のみ開講の科目で担当教員が空欄になるのを防ぐため）。
"""
import asyncio
import os
import re
import sys
from pathlib import Path

_LOG_PATH = Path(__file__).parent / "import_debug.log"

def _log(msg: str) -> None:
    print(msg)
    with open(_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

SYLLABUS_BASE = "https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026/{path}/data/2026_{code}.html"
FACULTY_PATH: dict[str, str] = {
    "U": "20",
    "B": "06",
    "X": "15",  # システム情報学部
    "G": "20",  # 教養科目の一部で使われるコード（Uと同じpath）
    "Z": "14",  # 海洋政策科学部
    "E": "05",  # 経済学部
    "H": "13",  # 国際人間科学部
    "A": "10",  # 農学部
    "L": "01",  # 文学部
    "J": "04",  # 法学部
}

# 医学部は学科によって時間割コードの3文字目（Mの次）にさらに1文字付く場合と、
# 数字がそのまま続くが番号帯で学科が異なる場合がある（例: 医療創成工学科は
# "MB" のように英字が入る。医学科は900番台・看護学専攻は000-399番台）。
# 保健学科の他専攻（検査技術科学専攻・理学療法学専攻・作業療法学専攻）を追加する際は
# 実データの番号帯を確認し、既存レンジと重ならないよう追記・調整すること。
MEDICINE_SUBLETTERS: dict[str, str] = {
    "B": "0803",  # 医学部医療創成工学科
}
MEDICINE_RANGES: list[tuple[int, int, str]] = [
    (0, 399, "080201"),  # 医学部保健学科看護学専攻（暫定上限。他専攻データ確認後に調整）
    (900, 999, "0801"),  # 医学部医学科
]

# 保健学科の専攻同士は数字部分の範囲が重なりうる（看護学専攻・検査技術科学専攻ともに
# 000-300番台の数字を使う）ため、番号帯だけでは判別できない。import_syllabus.pyは
# 元データの「所属」列を持っているため、ここに登録された所属名は範囲判定より優先して
# 直接pathを決める。新しい専攻を追加する際は必ずこちらに追記すること
# （番号帯が本当に重ならないと確認できない限り、MEDICINE_RANGESへの追加だけで済ませない）。
DEPARTMENT_PATH_OVERRIDE: dict[str, str] = {
    "医学部保健学科看護学専攻": "080201",
    "医学部保健学科検査技術科学専攻": "080202",
    "医学部保健学科理学療法学専攻": "080203",
    "医学部保健学科作業療法学専攻": "080204",
}

# 工学部は学科ごとにpathが分かれるが、時間割コードの2文字目は学科をまたいで
# 「T」（現行カリキュラム）・「N」（旧カリキュラム科目、例: 創造思考ゼミナールⅠ-a（-21））を
# 共有しており数字部分の範囲でしか判別できない（例: 建築学科は000-099番台、
# 市民工学科は100-149番台、電気電子工学科は150-199番台。TとNで番号帯とpathの
# 対応は同じことを確認済み）。他学科を追加する際はこのリストに(下限, 上限, path)を追記すること。
# 追記時は実データの番号帯を確認し、既存レンジと重ならないよう上限も調整すること。
ENGINEERING_RANGES: list[tuple[int, int, str]] = [
    (0, 99, "0921"),      # 工学部建築学科
    (100, 149, "0922"),   # 工学部市民工学科
    (150, 199, "0923"),   # 工学部電気電子工学科
    (200, 249, "0924"),   # 工学部機械工学科
    (250, 299, "0925"),   # 工学部応用化学科
]
ENGINEERING_LETTERS = {"T", "N"}

def make_syllabus_url(code: str, department: str = "") -> str | None:
    if len(code) < 2:
        return None
    if department in DEPARTMENT_PATH_OVERRIDE:
        return SYLLABUS_BASE.format(path=DEPARTMENT_PATH_OVERRIDE[department], code=code)
    letter = code[1].upper()
    if letter in ENGINEERING_LETTERS:
        digits = code[2:]
        if not digits.isdigit():
            return None
        num = int(digits)
        for lo, hi, path in ENGINEERING_RANGES:
            if lo <= num <= hi:
                return SYLLABUS_BASE.format(path=path, code=code)
        return None
    if letter == "M":
        if len(code) >= 3 and code[2].isalpha():
            path = MEDICINE_SUBLETTERS.get(code[2].upper())
            if not path:
                return None
            return SYLLABUS_BASE.format(path=path, code=code)
        digits = code[2:]
        if not digits.isdigit():
            return None
        num = int(digits)
        for lo, hi, path in MEDICINE_RANGES:
            if lo <= num <= hi:
                return SYLLABUS_BASE.format(path=path, code=code)
        return None
    path = FACULTY_PATH.get(letter)
    if not path:
        return None
    return SYLLABUS_BASE.format(path=path, code=code)

def load_env(env: str):
    env_file = Path(__file__).parent / (".env.dev" if env == "dev" else ".env")
    if not env_file.exists():
        print(f"ERROR: {env_file} が見つかりません", file=sys.stderr)
        sys.exit(1)
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_FULLWIDTH_ALNUM = str.maketrans({
    chr(c): chr(c - 0xFEE0)
    for c in list(range(0xFF10, 0xFF1A)) + list(range(0xFF21, 0xFF3B)) + list(range(0xFF41, 0xFF5B))
})

def normalize_alnum(name: str) -> str:
    """全角英数字（Ａ-Ｚ, a-z, ０-９）のみ半角化する。括弧等の全角記号はそのまま残す。"""
    return name.translate(_FULLWIDTH_ALNUM)

_PAREN_F2H = str.maketrans("（）", "()")
_PAREN_H2F = str.maketrans("()", "（）")

def clean_name(name: str) -> str:
    name = re.sub(r'\((?:副|主)：[^)]+\)', '', name)
    name = re.sub(r'【[^】]*】', '', name)
    return name.strip()

def parse_slots(slot_str: str) -> list[tuple[str, int]]:
    slot_str = slot_str.strip()
    if slot_str == "集中":
        return [("集", 0)]
    slots = []
    current_day = None
    for part in slot_str.split(","):
        part = part.strip()
        m = re.match(r'^([月火水木金土日])(\d+)$', part)
        if m:
            current_day = m.group(1)
            slots.append((current_day, int(m.group(2))))
        elif part.isdigit() and current_day:
            slots.append((current_day, int(part)))
    return slots

def _is_timetable_term(term: str) -> bool:
    return term.startswith("第3") or term.startswith("第4") or term in ("後期", "集中")


def normalize_term_type(term: str) -> str | None:
    """開講区分の生値をsubjects.term_typeのCHECK制約値（通年/セメスター/クオーター/集中）へ変換する。"""
    if term.startswith("第") and "クォーター" in term:
        return "クオーター"
    if term in ("前期", "後期"):
        return "セメスター"
    if term in ("通年", "年度"):
        return "通年"
    if term == "集中":
        return "集中"
    return None


# ══════════════════════════════════════════════
# 教養科目の分類判定（旧import_kyoyo_courses.pyのCOURSE_MAPを移植）
# 所属列が「教養教育院」の行はここで科目名から分類を自動判定する
# ══════════════════════════════════════════════

def _ab(names):
    return [f"{n}{s}" for n in names for s in ('A', 'B')]

def _abc(names):
    return [f"{n}{s}" for n in names for s in ('A', 'B', 'C')]

def _abcd(names):
    return [f"{n}{s}" for n in names for s in ('A', 'B', 'C', 'D')]


_KIBAN = [
    "教養とは何か", "多言語と多文化の世界", "情報基礎", "データサイエンス基礎学",
]

_JINBUN = (
    ["哲学", "論理学", "倫理学", "科学技術と倫理", "教育と人間形成"]
    + _ab(["心理学", "教育学", "言語科学", "文学",
           "芸術と文化", "芸術史", "美術史", "科学史",
           "日本史", "東洋史", "アジア史", "西洋史", "考古学"])
)

_SHAKAI = (
    ["社会生活と法", "国家と法", "政治と社会", "経済社会の発展",
     "経営学", "社会学", "教育と社会", "地理学",
     "社会思想史", "文化人類学", "越境する文化", "生活環境と技術"]
    + _ab(["法学", "政治学", "経済学", "現代の経済", "現代社会論"])
)

_SHIZEN = (
    ["現代物理学が描く世界", "身近な物理法則"]
    + _ab(["統計学", "物理学", "化学", "生命科学",
           "医学", "保健学", "健康科学", "惑星学", "情報学"])
    + _abcd(["数学", "生物学"])
)

_SOGO = (
    ["ESD論(持続可能な社会づくり)基礎",
     "海への誘い", "瀬戸内海学入門", "阪神・淡路大震災と都市の安全",
     "地域社会形成基礎論", "ひょうご神戸学", "日本酒学入門",
     "神戸大学史", "神戸大学研究最前線", "社会基礎学",
     "価値創造論基礎", "アントレプレナーシップ入門"]
    + _ab(["ESD論(持続可能な社会づくり)", "環境学入門",
           "ジェンダーとセクシュアリティ", "ボランティアと社会貢献活動",
           "職業と学び-キャリアデザインを考える"])
    + _abc(["価値創造論", "社会と人権"])
)

# 外国語第1: 英語 (0.5単位)
_GAIGO1 = [
    "Academic English Communication A1",
    "Academic English Communication A2",
    "Academic English Communication B1",
    "Academic English Communication B2",
    "Academic English Communication B1（ACE）",
    "Academic English Communication B2（ACE）",
    "Academic English Literacy A1",
    "Academic English Literacy A2",
    "Academic English Literacy B1",
    "Academic English Literacy B2",
    "Academic English Literacy B1（ACE）",
    "Academic English Literacy B2（ACE）",
]

# 外国語第2: ドイツ語・フランス語・中国語・ロシア語の個別コース (0.5単位)
_GAIGO2 = [
    "ドイツ語初級A1", "ドイツ語初級A2", "ドイツ語初級B1", "ドイツ語初級B2",
    "ドイツ語初級A3", "ドイツ語初級A4", "ドイツ語初級B3", "ドイツ語初級B4",
    "ドイツ語初級SA3", "ドイツ語初級SA4", "ドイツ語初級SB3", "ドイツ語初級SB4",
    "ドイツ語中級C1", "ドイツ語中級C2",
    "フランス語初級A1", "フランス語初級A2", "フランス語初級B1", "フランス語初級B2",
    "フランス語初級A3", "フランス語初級A4", "フランス語初級B3", "フランス語初級B4",
    "フランス語初級SA3", "フランス語初級SA4", "フランス語初級SB3", "フランス語初級SB4",
    "フランス語中級C1", "フランス語中級C2",
    "中国語初級A1", "中国語初級A2", "中国語初級B1", "中国語初級B2",
    "中国語初級A3", "中国語初級A4", "中国語初級B3", "中国語初級B4",
    "中国語初級SA3", "中国語初級SA4", "中国語初級SB3", "中国語初級SB4",
    "中国語中級C1", "中国語中級C2",
    "ロシア語初級A1", "ロシア語初級A2", "ロシア語初級B1", "ロシア語初級B2",
    "ロシア語初級A3", "ロシア語初級A4", "ロシア語初級B3", "ロシア語初級B4",
    "ロシア語中級C1", "ロシア語中級C2",
    "第二外国語（ドイツ語）T1", "第二外国語（ドイツ語）T2", "第二外国語（ドイツ語）T3",
]

# 外国語第3: セミナー形式 (1単位)
_GAIGO3 = [
    "外国語セミナーA（中国語）", "外国語セミナーB（中国語）",
    "外国語セミナーC（中国語）", "外国語セミナーD（中国語）",
    "外国語セミナーE（中国語）", "外国語セミナーF（中国語）",
    "外国語セミナーA（ロシア語）", "外国語セミナーB（ロシア語）",
    "外国語セミナーC（ロシア語）", "外国語セミナーD（ロシア語）",
    "外国語セミナーE（ロシア語）", "外国語セミナーF（ロシア語）",
    "多言語セミナー1（スペイン語）", "多言語セミナー2（スペイン語）",
    "多言語セミナー3（スペイン語）", "多言語セミナー4（スペイン語）",
    "多言語セミナー1（イタリア語）", "多言語セミナー2（イタリア語）",
    "多言語セミナー3（イタリア語）", "多言語セミナー4（イタリア語）",
    "多言語セミナー1（韓国語）", "多言語セミナー2（韓国語）",
    "多言語セミナー3（韓国語）", "多言語セミナー4（韓国語）",
]

# 健康・スポーツ科学系
_KENKO = [
    "健康・スポーツ科学実習基礎",
    "健康・スポーツ科学講義A", "健康・スポーツ科学講義B",
    "健康・スポーツ科学実習1", "健康・スポーツ科学実習2",
]

_KYOYO_COURSE_MAP = [
    (_KIBAN,  "教養(基盤)"),
    (_JINBUN, "教養(人文)"),
    (_SHAKAI, "教養(社会)"),
    (_SHIZEN, "教養(自然)"),
    (_SOGO,   "教養(総合)"),
    (_GAIGO1, "教養(外国語第1)"),
    (_GAIGO2, "教養(外国語第2)"),
    (_GAIGO3, "教養(外国語第3)"),
    (_KENKO,  "教養(健康・スポーツ)"),
]

_KYOYO_NAME_TO_CLASSIFICATION: dict[str, str] = {
    name: cls for names, cls in _KYOYO_COURSE_MAP for name in names
}

# 完全一致リストに無い科目名向けのパターンフォールバック
# （学籍番号条件・偶奇・T機械等のサフィックスが付いた科目名を吸収する）
_KYOYO_PATTERN_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r'^力学基礎[12]'), "教養(自然)"),
    (re.compile(r'^化学実験[12]$'), "教養(自然)"),
    (re.compile(r'^基礎地学[12]$'), "教養(自然)"),
    (re.compile(r'^基礎(有機化学|無機化学|物理化学)[12]$'), "教養(自然)"),
    (re.compile(r'^微分積分(入門)?[1234]'), "教養(自然)"),
    (re.compile(r'^線形代数(入門)?[1234]'), "教養(自然)"),
    (re.compile(r'^数理統計[12]'), "教養(自然)"),
    (re.compile(r'^熱力学基礎'), "教養(自然)"),
    (re.compile(r'^物理学(入門|実験)'), "教養(自然)"),
    (re.compile(r'^生物学(各論|実験|概論)'), "教養(自然)"),
    (re.compile(r'^生物資源と農業'), "教養(自然)"),
    (re.compile(r'^相対論基礎$'), "教養(自然)"),
    (re.compile(r'^科学技術と社会'), "教養(自然)"),
    (re.compile(r'^連続体力学基礎$'), "教養(自然)"),
    (re.compile(r'^量子力学基礎$'), "教養(自然)"),
    (re.compile(r'^電磁気学基礎[12]$'), "教養(自然)"),
    (re.compile(r'^放射線科学$'), "教養(自然)"),
    (re.compile(r'^情報科学[12]$'), "教養(自然)"),
    (re.compile(r'^第三外国語（(ドイツ語|フランス語)）T[1234]$'), "教養(外国語第2)"),
    (re.compile(r'^外国語セミナー[A-F]（(ドイツ語|フランス語|英語)）$'), "教養(外国語第3)"),
    (re.compile(r'^多言語セミナー[1234]（(ウクライナ語|ハンガリー語|モンゴル語|ラテン語)）$'), "教養(外国語第3)"),
    (re.compile(r'^グローバルチャレンジ実習'), "教養(総合)"),
    (re.compile(r'^グローバル(リーダーシップ育成基礎演習|ラーニングスキルズ|エキスパートセミナー)$'), "教養(総合)"),
    (re.compile(r'^国際協力'), "教養(総合)"),
    (re.compile(r'^多文化共生のための日本語コミュニケーション$'), "教養(総合)"),
    (re.compile(r'^多文化共修セミナー'), "教養(総合)"),
    (re.compile(r'^複言語共修セミナー'), "教養(総合)"),
    (re.compile(r'^海外留学のすすめ[AB]$'), "教養(総合)"),
    (re.compile(r'^カタチの(文化学|自然学)'), "教養(総合)"),
    (re.compile(r'^データサイエンス'), "教養(基盤)"),
    (re.compile(r'^食と健康[AB]$'), "教養(健康・スポーツ)"),
    (re.compile(r'^大学教育論$'), "教養(人文)"),
    (re.compile(r'^心と行動$'), "教養(人文)"),
]

KYOYO_FACULTY = "教養教育院"
KYOYO_UNCLASSIFIED = "教養(未分類)"


def is_kyoyo_department(department: str) -> bool:
    return department.strip().startswith(KYOYO_FACULTY)


def default_classification(faculty_name: str) -> str | None:
    """専門科目のclassification未指定時のデフォルト分類名。
    学部名をそのまま使うとdisplay_ordersのparent_group（学部グループ名）と衝突し
    LINE botのドリルダウンでその科目群が選べなくなるため、必ず接尾語を付ける。"""
    return f"{faculty_name}専門科目" if faculty_name else None


async def ensure_classification_bucket(session, cls_name: str, faculty_name: str) -> None:
    """専門科目のclassificationがdisplay_orders(kind='classification')に無ければ、
    学部名でグループ化された受け皿として自動作成する（未分類のまま埋もれるのを防ぐ）。"""
    from sqlalchemy import select, func
    from models import DisplayOrder

    existing = (await session.execute(
        select(DisplayOrder).where(DisplayOrder.kind == "classification", DisplayOrder.name == cls_name)
    )).scalar_one_or_none()
    if existing is not None:
        return
    max_order = (await session.execute(
        select(func.max(DisplayOrder.sort_order)).where(DisplayOrder.kind == "classification")
    )).scalar() or 0
    session.add(DisplayOrder(
        kind="classification", name=cls_name,
        parent_group=faculty_name or None, faculty=faculty_name or "",
        sort_order=max_order + 10,
    ))
    await session.flush()


def classify_kyoyo(name: str) -> str | None:
    """教養科目の科目名から分類（教養(人文)等）を判定する。未知の科目名はNoneを返す。"""
    if name in _KYOYO_NAME_TO_CLASSIFICATION:
        return _KYOYO_NAME_TO_CLASSIFICATION[name]
    # 括弧・ダッシュの全角/半角表記ゆれを吸収して完全一致を再試行
    for alt in {
        name.translate(_PAREN_F2H).replace('－', '-'),
        name.translate(_PAREN_H2F).replace('-', '－'),
    } - {name}:
        if alt in _KYOYO_NAME_TO_CLASSIFICATION:
            return _KYOYO_NAME_TO_CLASSIFICATION[alt]
    for pattern, cls in _KYOYO_PATTERN_MAP:
        if pattern.match(name):
            return cls
    return None


def parse_file(filepath: str) -> list[dict]:
    text = Path(filepath).read_text(encoding="utf-8", errors="ignore")
    courses = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 8:
            parts = re.split(r' {2,}', line.strip())
        if len(parts) < 8:
            continue
        if not parts[0].strip().isdigit():
            continue
        year_str = parts[1].strip()
        if not year_str.isdigit():
            continue
        year = int(year_str)
        term = parts[2].strip()
        department = parts[3].strip()
        name = normalize_alnum(clean_name(parts[4].strip()))
        instructor = parts[5].strip()
        slot_str = parts[6].strip()
        timetable_code = parts[7].strip()
        slots = parse_slots(slot_str)
        if not slots:
            continue
        courses.append({
            "year": year,
            "term": term,
            "department": department,
            "name": name,
            "instructor": instructor,
            "timetable_code": timetable_code,
            "slots": slots,
        })
    return courses


async def import_courses(courses: list[dict], also_courses: bool = False,
                         classification: str = "", faculty: str = "",
                         auto_create: bool = True):
    from sqlalchemy import select
    from database import AsyncSessionLocal, init_db
    from models import Subject, Instructor, CourseSection, Syllabus, Schedule, normalize_instructor_name

    await init_db()

    counts = {"tt_added": 0, "tt_skipped": 0, "tt_unmatched": 0, "c_added": 0, "cs_added": 0}

    async def process_one(session, c):
            is_tt = _is_timetable_term(c["term"])
            is_kyoyo = is_kyoyo_department(c["department"])
            dept_faculty = faculty or c["department"].split("　")[0].split(" ")[0]

            # ── subjects テーブル（LINE bot 用）──
            # 教養教育院由来の行はcategory="教養"で、専門科目由来の行はfaculty（学部・学科）で
            # 絞り込む。これが無いと「卒業研究」「国際関係論」等の汎用的な科目名が
            # 別学部の同名科目に誤って相乗りしてしまう
            subj_filters = [Subject.name == c["name"]]
            if is_kyoyo:
                subj_filters.append(Subject.category == "教養")
            else:
                subj_filters.append(Subject.faculty == dept_faculty)
            subj = (await session.execute(
                select(Subject).where(*subj_filters)
            )).scalar_one_or_none()

            if subj is None:
                # 括弧の全角/半角表記ゆれを吸収して再検索（表示名は変更しない）
                for alt_name in {
                    c["name"].translate(_PAREN_F2H),
                    c["name"].translate(_PAREN_H2F),
                } - {c["name"]}:
                    alt_filters = [Subject.name == alt_name]
                    if is_kyoyo:
                        alt_filters.append(Subject.category == "教養")
                    else:
                        alt_filters.append(Subject.faculty == dept_faculty)
                    subj = (await session.execute(
                        select(Subject).where(*alt_filters)
                    )).scalar_one_or_none()
                    if subj is not None:
                        break

            if subj is None and is_kyoyo:
                # 教養教育院由来だが、既存レコードがcategory="専門"（共通専門基礎科目等）として
                # 意図的に区別されているケースがある。その場合は既存分類を壊さず再利用する
                # （category="教養"限定で再検索すると見つからず、重複INSERTでunique制約違反になるため）
                subj = (await session.execute(
                    select(Subject).where(Subject.name == c["name"])
                )).scalar_one_or_none()

            if subj is None:
                if also_courses:
                    if is_kyoyo:
                        subj = Subject(
                            name=c["name"],
                            classification=classify_kyoyo(c["name"]) or KYOYO_UNCLASSIFIED,
                            category="教養",
                            faculty=KYOYO_FACULTY,
                            reading="",
                            term_type=normalize_term_type(c["term"]),
                            credits=None,
                        )
                    else:
                        cls = classification or default_classification(dept_faculty)
                        subj = Subject(
                            name=c["name"],
                            classification=cls,
                            category="専門",
                            faculty=dept_faculty or None,
                            reading="",
                            term_type=normalize_term_type(c["term"]),
                            credits=None,
                        )
                    session.add(subj)
                    await session.flush()
                    if not is_kyoyo and cls:
                        await ensure_classification_bucket(session, cls, dept_faculty)
                    counts["c_added"] += 1
                elif not is_tt:
                    return
                elif not auto_create:
                    counts["tt_unmatched"] += 1
                    return
                else:
                    # 時間割用だが科目が未登録 → subject を仮登録
                    cls = classification or default_classification(dept_faculty)
                    subj = Subject(
                        name=c["name"],
                        classification=cls,
                        category="専門",
                        faculty=dept_faculty or None,
                        reading="",
                    )
                    session.add(subj)
                    await session.flush()
                    if not is_kyoyo and cls:
                        await ensure_classification_bucket(session, cls, dept_faculty)

            # Instructor / CourseSection は前期科目も含め全学期分を作成する
            # （担当教員が空欄になるのを防ぐため。時間割表示に使うsyllabi/schedulesのみ
            #   第3・第4クォーター・後期・集中に限定する）

            # Instructor を find-or-create（日本語名は空白を除去して統一する。
            # 実際の正規化は models.Instructor の @validates("name") が保証するが、
            # 検索用の値もここで揃えておかないと既存レコードにヒットせず重複INSERTになる）
            instructor_name = normalize_instructor_name(c["instructor"])
            instr = (await session.execute(
                select(Instructor).where(Instructor.name == instructor_name)
            )).scalar_one_or_none()
            if instr is None:
                instr = Instructor(name=instructor_name)
                session.add(instr)
                await session.flush()

            # CourseSection を find-or-create（既存の場合、syllabus_url が未設定なら補完する）
            # ※ syllabus 重複チェックより先に行う。既にSyllabusが登録済みでも
            #   URLだけ未設定というケース（レビュー投稿由来のセクション等）を補完するため。
            cs = (await session.execute(
                select(CourseSection).where(
                    CourseSection.subject_id == subj.id,
                    CourseSection.instructor_id == instr.id,
                )
            )).scalar_one_or_none()
            if cs is None:
                cs = CourseSection(
                    subject_id=subj.id,
                    instructor_id=instr.id,
                    syllabus_url=make_syllabus_url(c["timetable_code"], c["department"]),
                )
                session.add(cs)
                await session.flush()
                counts["cs_added"] += 1
            elif not cs.syllabus_url:
                cs.syllabus_url = make_syllabus_url(c["timetable_code"], c["department"])

            if not is_tt:
                return

            # ── syllabi / schedules ──
            # timetable_code 重複チェック
            existing_syl = (await session.execute(
                select(Syllabus).where(Syllabus.timetable_code == c["timetable_code"])
            )).scalar_one_or_none()
            if existing_syl:
                counts["tt_skipped"] += 1
                _log(f"  [重複スキップ:コード既存] {c['name']} / {c['instructor']} / {c['term']} / コード:{c['timetable_code']}")
                return

            # 同一セクション×同一クォーターは1件のみ保持可能（DB制約）。
            # 同じ科目×同じ教員が同クォーターに複数コマ持つ場合は先勝ちでスキップする
            existing_term_syl = (await session.execute(
                select(Syllabus).where(
                    Syllabus.course_section_id == cs.id,
                    Syllabus.year == c["year"],
                    Syllabus.academic_term == c["term"],
                )
            )).scalar_one_or_none()
            if existing_term_syl is not None:
                counts["tt_skipped"] += 1
                _log(f"  [重複スキップ:同一セクション] {c['name']} / {c['instructor']} / {c['term']} / 既存コード:{existing_term_syl.timetable_code} 今回:{c['timetable_code']}")
                return

            syl = Syllabus(
                course_section_id=cs.id,
                year=c["year"],
                academic_term=c["term"],
                timetable_code=c["timetable_code"],
                department=c["department"],
            )
            session.add(syl)
            await session.flush()

            for day, period in c["slots"]:
                session.add(Schedule(
                    syllabus_id=syl.id,
                    day_of_week=day,
                    period=period,
                ))
            counts["tt_added"] += 1

    # 数千件規模のバッチを1つのDBコネクションで抱え続けると、Supabase側の
    # アイドルタイムアウト等で接続が切れて全体がロールバックすることがあるため、
    # 一定件数ごとに新しいセッション（＝新しいコネクション）に切り替えてコミットする。
    # 失敗したバッチだけ再試行すればよく、既に処理済みの行は重複チェックで安全にスキップされる。
    BATCH_SIZE = 200
    for batch_start in range(0, len(courses), BATCH_SIZE):
        batch = courses[batch_start:batch_start + BATCH_SIZE]
        for attempt in range(3):
            try:
                async with AsyncSessionLocal() as session:
                    for c in batch:
                        await process_one(session, c)
                    await session.commit()
                break
            except Exception as e:
                if attempt == 2:
                    _log(f"  [バッチ失敗] rows {batch_start}-{batch_start + len(batch)}: {e!r}")
                    raise
                _log(f"  [バッチ再試行 {attempt + 1}/3] rows {batch_start}-{batch_start + len(batch)}: {e!r}")

    _log(f"時間割DB: {counts['tt_added']}件追加, {counts['tt_skipped']}件スキップ（重複）")
    _log(f"担当教員セクション(course_sections): {counts['cs_added']}件追加")
    if not auto_create and counts["tt_unmatched"]:
        _log(f"科目DBに未登録のためスキップ: {counts['tt_unmatched']}件")
    if also_courses:
        print(f"科目DB:  {counts['c_added']}件追加")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", help="シラバスデータファイルのパス（複数指定可）")
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument("--dry-run", action="store_true", help="DBに書き込まず件数だけ表示")
    parser.add_argument("--also-courses", action="store_true",
                        help="subjects テーブル（LINE bot 用）にも登録する。"
                             "所属列が「教養教育院」の行は科目名から分類・学部を自動判定する")
    parser.add_argument("--classification", default="",
                        help="subjects テーブルの分類名（例：共通専門科目）。教養科目には適用されない")
    parser.add_argument("--faculty", default="",
                        help="subjects テーブルの学部名（例：経営学部）。教養科目には適用されない")
    parser.add_argument("--no-auto-create", action="store_true",
                        help="科目名がsubjectsに未登録の場合、仮登録せずスキップする")
    args = parser.parse_args()

    load_env(args.env)

    courses = []
    for f in args.files:
        courses.extend(parse_file(f))
    tt_courses = [c for c in courses if _is_timetable_term(c["term"])]
    print(f"パース結果: {len(courses)}件全学期 / うち時間割対象（第3・第4Q・後期・集中）: {len(tt_courses)}件")

    if args.dry_run:
        for c in courses[:5]:
            print(f"  {c['timetable_code']} {c['name']} {c['slots']}")
        if len(courses) > 5:
            print(f"  ... 他{len(courses)-5}件")
        return

    if args.env == "prod":
        confirm = input("本番DBにインポートします。よろしいですか？ (yes/no): ")
        if confirm.strip().lower() != "yes":
            print("中止しました")
            return

    async def _main_async():
        await import_courses(
            courses,
            also_courses=args.also_courses,
            classification=args.classification,
            faculty=args.faculty,
            auto_create=not args.no_auto_create,
        )
        # 取り込み直後に開講年次・科目分類・単位数も自動で補完する。
        # run()/run_credits()はどちらも「まだ値が無いもの」だけを対象にするため、
        # 今回追加した科目・時間割だけが処理され、全件再スキャンにはならない。
        # import_courses()と同じイベントループ内で呼ぶ必要がある（別のasyncio.run()に
        # 分けるとSQLAlchemyの非同期コネクションプールが前のループに紐づいたまま
        # 「Event loop is closed」になるため）。
        import fetch_syllabus_info as fsi
        print("開講年次・科目分類を取得中...")
        await fsi.run(dry_run=False, force=False)
        print("単位数を取得中...")
        await fsi.run_credits(dry_run=False, force=False)

    asyncio.run(_main_async())

if __name__ == "__main__":
    main()
