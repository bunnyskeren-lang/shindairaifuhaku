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
シラバス（syllabi、シラバスURL生成に使うtimetable_code情報）・担当教員（instructors/
course_sections）ともに全学期（前期・後期・クォーター・集中）を対象にインポートします
（2026-07-14以前は前期を除外していたが、前期のシラバスデータも投入する運用に変更した）。
"""
import asyncio
import re
from pathlib import Path

from _env import load_env

_LOG_PATH = Path(__file__).parent / "import_debug.log"

def _log(msg: str) -> None:
    print(msg)
    with open(_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

# subjects.faculty/departmentは「学部名のみ」「学科名（未設定なら空文字）」のペア形式で持つ
# （credit_requirements等の既存テーブルと同じ形。database.py init_db()の一回限りバックフィルと
# 同じ16パターン）。dept_faculty（所属列から作った学部+学科の複合文字列、分類名生成や
# display_orders用には従来通り使う）とは別に、Subject.faculty/department書き込み専用の
# 分割結果が必要なときはこの関数を使うこと。
_FACULTY_DEPARTMENT_SPLIT: dict[str, tuple[str, str]] = {
    "医学部保健学科作業療法学専攻": ("医学部", "保健学科作業療法学専攻"),
    "医学部保健学科検査技術科学専攻": ("医学部", "保健学科検査技術科学専攻"),
    "医学部保健学科理学療法学専攻": ("医学部", "保健学科理学療法学専攻"),
    "医学部保健学科看護学専攻": ("医学部", "保健学科看護学専攻"),
    "医学部医学科": ("医学部", "医学科"),
    "医学部医療創成工学科": ("医学部", "医療創成工学科"),
    "工学部市民工学科": ("工学部", "市民工学科"),
    "工学部建築学科": ("工学部", "建築学科"),
    "工学部応用化学科": ("工学部", "応用化学科"),
    "工学部機械工学科": ("工学部", "機械工学科"),
    "工学部電気電子工学科": ("工学部", "電気電子工学科"),
    "理学部化学科": ("理学部", "化学科"),
    "理学部惑星学科": ("理学部", "惑星学科"),
    "理学部数学科": ("理学部", "数学科"),
    "理学部物理学科": ("理学部", "物理学科"),
    "理学部生物学科": ("理学部", "生物学科"),
}


def split_faculty_department(dept_faculty: str) -> tuple[str, str]:
    """dept_faculty（所属列から作った学部+学科の複合文字列、または単なる学部名）を
    Subject.faculty/department書き込み用の(学部名, 学科名)ペアに分割する。"""
    return _FACULTY_DEPARTMENT_SPLIT.get(dept_faculty, (dept_faculty, ""))


_FULLWIDTH_ALNUM = str.maketrans({
    chr(c): chr(c - 0xFEE0)
    for c in list(range(0xFF10, 0xFF1A)) + list(range(0xFF21, 0xFF3B)) + list(range(0xFF41, 0xFF5B))
})

def normalize_alnum(name: str) -> str:
    """全角英数字（Ａ-Ｚ, a-z, ０-９）のみ半角化する。括弧等の全角記号はそのまま残す。"""
    return name.translate(_FULLWIDTH_ALNUM)

_HALFWIDTH_TO_FULLWIDTH_ALNUM = str.maketrans({
    chr(c - 0xFEE0): chr(c)
    for c in list(range(0xFF10, 0xFF1A)) + list(range(0xFF21, 0xFF3B)) + list(range(0xFF41, 0xFF5B))
})

_PAREN_F2H = str.maketrans("（）", "()")
_PAREN_H2F = str.maketrans("()", "（）")

_CROSSLIST_RE = re.compile(r'\((?P<kind>副|主)[：:][^)]+\)')
# 科目名の【...】タグのうち、遠隔・再履修は同じ教員が通常クラスと同じ科目・同じ学期に
# 別コマとして持つことが多く、course_sections/syllabi の一意制約（同一科目×同一教員は
# 1コマしか保持できない）で通常クラス側に潰れて消えてしまう。そのため他のタグ（【不動産】等、
# 科目名そのものの一部）と違い削除せず名前末尾に残し、別科目として扱う（2026-08-31）。
# 両方のタグが同時に付く科目名（【遠隔】【再履修】等、別々の括弧で両方マッチする場合）は
# 片方だけ拾って落とすと再び一意制約衝突に戻ってしまうため、両方とも遠隔→再履修の順で
# 連結する（core/subject_variants.pyのTAG_PRIORITYもこの順序・連結済み文字列に対応済み）。
_SPECIAL_BRACKET_SUFFIXES = [
    (re.compile(r'【[^】]*遠隔[^】]*】'), "（遠隔）"),
    (re.compile(r'【[^】]*再履修[^】]*】'), "（再履修）"),
]


def clean_name(name: str) -> str:
    name = _CROSSLIST_RE.sub('', name)
    suffix = "".join(
        tag_suffix for pattern, tag_suffix in _SPECIAL_BRACKET_SUFFIXES if pattern.search(name)
    )
    name = re.sub(r'【[^】]*】', '', name).strip()
    if suffix and not name.endswith(suffix):
        name = f"{name}{suffix}"
    return name


def is_crosslist_secondary(raw_name: str) -> bool:
    """シラバス生データの科目名が「(副：〜)」注記付き（大学側のクロスリストで、別の
    「(主：〜)」科目と同一の物理授業を指す）かどうかを判定する。

    「(主：〜)」「(副：〜)」は同一授業に対して大学が便宜上2つの科目名を発行している
    印で、両方をそのままsubjectsに投入すると同一授業がLINE bot上で別科目として
    二重表示されてしまう（2026-08-29、国際人間科学部「衣環境論」等の調査で発覚。
    全学で調査したところ経済学部の「（編入生）」科目を中心に60件以上の重複が
    見つかり、手動で整理した）。再発防止のため、「(副：〜)」側は科目名として
    登録せずスキップし、「(主：〜)」側だけをsubjectsに登録する。
    """
    m = _CROSSLIST_RE.search(raw_name)
    return bool(m) and m.group('kind') == '副'

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
    # 以前は第3・第4クォーター・後期・集中のみに限定していたが、前期(第1・第2クォーター)の
    # シラバスデータも投入する運用に変わったため全学期を対象にする。
    return True


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
    (_KENKO,  "教養(健康・スポーツ)"),
]

_KYOYO_NAME_TO_CLASSIFICATION: dict[str, str] = {
    name: cls for names, cls in _KYOYO_COURSE_MAP for name in names
}

# 完全一致リストに無い科目名向けのパターンフォールバック
# （学籍番号条件・偶奇・T機械等のサフィックスが付いた科目名を吸収する）
_KYOYO_PATTERN_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r'^力学基礎[12]'), "共通専門基礎科目"),
    (re.compile(r'^化学実験[12]$'), "共通専門基礎科目"),
    (re.compile(r'^基礎地学[12]$'), "共通専門基礎科目"),
    (re.compile(r'^基礎(有機化学|無機化学|物理化学)[12]$'), "共通専門基礎科目"),
    (re.compile(r'^微分積分(入門)?[1234]'), "共通専門基礎科目"),
    (re.compile(r'^線形代数(入門)?[1234]'), "共通専門基礎科目"),
    (re.compile(r'^数理統計[12]'), "共通専門基礎科目"),
    (re.compile(r'^熱力学基礎'), "教養(自然)"),
    (re.compile(r'^物理学入門$'), "共通専門基礎科目"),
    (re.compile(r'^物理学実験'), "教養(自然)"),
    (re.compile(r'^生物学(各論|実験|概論)'), "共通専門基礎科目"),
    (re.compile(r'^生物資源と農業'), "教養(自然)"),
    (re.compile(r'^相対論基礎$'), "教養(自然)"),
    (re.compile(r'^科学技術と社会'), "教養(自然)"),
    (re.compile(r'^連続体力学基礎$'), "教養(自然)"),
    (re.compile(r'^量子力学基礎$'), "教養(自然)"),
    (re.compile(r'^電磁気学基礎[12]$'), "共通専門基礎科目"),
    (re.compile(r'^放射線科学$'), "教養(自然)"),
    (re.compile(r'^情報科学[12]$'), "共通専門基礎科目"),
    (re.compile(r'^第三外国語（(ドイツ語|フランス語)）T[1234]$'), "教養(外国語第3)"),
    (re.compile(r'^グローバルチャレンジ実習'), "教養(総合)"),
    (re.compile(r'^グローバル(リーダーシップ育成基礎演習|ラーニングスキルズ|エキスパートセミナー)$'), "教養(総合)"),
    (re.compile(r'^国際協力'), "教養(総合)"),
    (re.compile(r'^多文化共生のための日本語コミュニケーション$'), "教養(総合)"),
    (re.compile(r'^多文化共修セミナー'), "教養(総合)"),
    (re.compile(r'^複言語共修セミナー'), "教養(総合)"),
    (re.compile(r'^海外留学のすすめ[AB]$'), "教養(総合)"),
    (re.compile(r'^カタチの(文化学|自然学)'), "教養(総合)"),
    (re.compile(r'^食と健康[AB]$'), "教養(健康・スポーツ)"),
    (re.compile(r'^大学教育論$'), "教養(人文)"),
    (re.compile(r'^心と行動$'), "共通専門基礎科目"),
]

KYOYO_FACULTY = "教養教育院"
KYOYO_UNCLASSIFIED = "教養(未分類)"
# 教養教育院が開講するが理系学部の必修科目として「専門」扱いする分類
# （微分積分・線形代数・情報科学など。教養(自然)と違いcategory="専門"になる）
KYOYO_KYOTSU_SENMON_KISO = "共通専門基礎科目"


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
    # 遠隔・再履修クラスはclean_name()で名前末尾に「（遠隔）」「（再履修）」が付与されている
    # （両方同時に付くこともある）ため、分類判定は元の科目名（接尾辞を全て外したもの）で行う
    base_name = name
    stripped = True
    while stripped:
        stripped = False
        for _pattern, _suffix in _SPECIAL_BRACKET_SUFFIXES:
            if base_name.endswith(_suffix):
                base_name = base_name[:-len(_suffix)]
                stripped = True
    if base_name in _KYOYO_NAME_TO_CLASSIFICATION:
        return _KYOYO_NAME_TO_CLASSIFICATION[base_name]
    # 括弧・ダッシュの全角/半角表記ゆれを吸収して完全一致を再試行
    for alt in {
        base_name.translate(_PAREN_F2H).replace('－', '-'),
        base_name.translate(_PAREN_H2F).replace('-', '－'),
    } - {base_name}:
        if alt in _KYOYO_NAME_TO_CLASSIFICATION:
            return _KYOYO_NAME_TO_CLASSIFICATION[alt]
    # パターンはすべて半角数字で書かれているため、全角数字の科目名（過去に手動投入された
    # ものが一部残る）も拾えるよう半角化してから照合する
    normalized_name = normalize_alnum(base_name)
    for pattern, cls in _KYOYO_PATTERN_MAP:
        if pattern.match(base_name) or pattern.match(normalized_name):
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
        raw_name = parts[4].strip()
        if is_crosslist_secondary(raw_name):
            # 「(副：〜)」側は「(主：〜)」側と同一の物理授業を指すクロスリストのため、
            # 別科目として二重登録しない（clean_name/is_crosslist_secondaryのdocstring参照）
            continue
        name = normalize_alnum(clean_name(raw_name))
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
    from models import Subject, Instructor, CourseSection, Syllabus, normalize_instructor_name

    await init_db()

    counts = {"tt_added": 0, "tt_skipped": 0, "tt_unmatched": 0, "c_added": 0, "cs_added": 0}

    async def process_one(session, c):
            is_tt = _is_timetable_term(c["term"])
            is_kyoyo = is_kyoyo_department(c["department"])
            dept_faculty = faculty or c["department"].split("　")[0].split(" ")[0]
            # dept_facultyは分類名生成(default_classification)・display_orders登録
            # (ensure_classification_bucket)には従来通りの複合文字列のまま使うが、
            # Subject.faculty/departmentへの書き込み・検索は分離した値を使う
            pure_faculty, dept_suffix = split_faculty_department(dept_faculty)

            # ── subjects テーブル（LINE bot 用）──
            # 教養教育院由来の行はcategory="教養"で、専門科目由来の行はfaculty/department（学部・学科）で
            # 絞り込む。これが無いと「卒業研究」「国際関係論」等の汎用的な科目名が
            # 別学部の同名科目に誤って相乗りしてしまう
            subj_filters = [Subject.name == c["name"]]
            if is_kyoyo:
                subj_filters.append(Subject.category == "教養")
            else:
                subj_filters.append(Subject.faculty == pure_faculty)
                subj_filters.append(Subject.department == dept_suffix)
            subj = (await session.execute(
                select(Subject).where(*subj_filters)
            )).scalar_one_or_none()

            if subj is None:
                # 括弧・英数字の全角/半角表記ゆれを吸収して再検索（表示名は変更しない）。
                # parse_file()でc["name"]は英数字が半角化済みのため、既存レコードが
                # 全角英数字のまま登録されているケース（旧import_kyoyo_courses.py由来等）
                # を拾えないと同一科目が別レコードとして重複登録されてしまう
                fullwidth_alnum_name = c["name"].translate(_HALFWIDTH_TO_FULLWIDTH_ALNUM)
                for alt_name in {
                    c["name"].translate(_PAREN_F2H),
                    c["name"].translate(_PAREN_H2F),
                    fullwidth_alnum_name,
                    fullwidth_alnum_name.translate(_PAREN_F2H),
                    fullwidth_alnum_name.translate(_PAREN_H2F),
                } - {c["name"]}:
                    alt_filters = [Subject.name == alt_name]
                    if is_kyoyo:
                        alt_filters.append(Subject.category == "教養")
                    else:
                        alt_filters.append(Subject.faculty == pure_faculty)
                        alt_filters.append(Subject.department == dept_suffix)
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
                        kyoyo_cls = classify_kyoyo(c["name"]) or KYOYO_UNCLASSIFIED
                        subj = Subject(
                            name=c["name"],
                            classification=kyoyo_cls,
                            category="専門" if kyoyo_cls == KYOYO_KYOTSU_SENMON_KISO else "教養",
                            faculty=KYOYO_FACULTY,
                            reading="",
                            term_type=normalize_term_type(c["term"]),
                            credits=None,
                        )
                    else:
                        cls = classification or default_classification(dept_faculty)
                        # 修正理由: department列はnullable=False+空文字プレースホルダ方式なのに
                        # facultyだけpure_faculty=""をNoneに変換して非対称にしていた
                        # (将来faculty列をNOT NULL化する前提を崩さないよう空文字のまま渡す)
                        subj = Subject(
                            name=c["name"],
                            classification=cls,
                            category="専門",
                            faculty=pure_faculty,
                            department=dept_suffix,
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
                        faculty=pure_faculty,
                        department=dept_suffix,
                        reading="",
                    )
                    session.add(subj)
                    await session.flush()
                    if not is_kyoyo and cls:
                        await ensure_classification_bucket(session, cls, dept_faculty)

            # Instructor / CourseSection は前期科目も含め全学期分を作成する
            # （担当教員が空欄になるのを防ぐため。時間割表示に使うsyllabi/schedulesのみ
            #   第3・第4クォーター・後期・集中に限定する）

            # Instructor を find-or-create（半角/全角スペースを除去して統一する。
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

            # CourseSection を find-or-create
            # ※ syllabus 重複チェックより先に行う（レビュー投稿由来で既に存在する場合がある）。
            cs = (await session.execute(
                select(CourseSection).where(
                    CourseSection.subject_id == subj.id,
                    CourseSection.instructor_id == instr.id,
                )
            )).scalar_one_or_none()
            if cs is None:
                cs = CourseSection(subject_id=subj.id, instructor_id=instr.id)
                session.add(cs)
                await session.flush()
                counts["cs_added"] += 1

            if not is_tt:
                return

            # ── syllabi ──
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
            )
            session.add(syl)
            await session.flush()
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
    print(f"パース結果: {len(courses)}件（全学期を時間割対象としてインポートします）")

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
        # 取り込み直後に単位数も自動で補完する。run_credits()は「まだ値が無いもの」
        # だけを対象にするため、今回追加した科目・時間割だけが処理され、全件再スキャン
        # にはならない。import_courses()と同じイベントループ内で呼ぶ必要がある
        # （別のasyncio.run()に分けるとSQLAlchemyの非同期コネクションプールが前の
        # ループに紐づいたまま「Event loop is closed」になるため）。
        # 対象年次・科目分類を取得していたrun()は2026-07-30のMy時間割機能全廃止で
        # fetch_syllabus_info.py側から削除済みのため、ここでも呼ばない。
        import fetch_syllabus_info as fsi
        print("単位数を取得中...")
        await fsi.run_credits(dry_run=False, force=False)

    asyncio.run(_main_async())

if __name__ == "__main__":
    main()
