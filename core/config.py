import os
import re as _re
from datetime import timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or ""
if not ADMIN_PASSWORD:
    raise RuntimeError("環境変数 ADMIN_PASSWORD が未設定です")
REVIEW_FORM_URL = os.environ.get("REVIEW_FORM_URL", "https://shindairaifuhaku.onrender.com")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_EMAIL = os.environ.get("VAPID_EMAIL", "admin@example.com")
DEV_DATABASE_URL = os.environ.get("DEV_DATABASE_URL", "")
SELF_URL = os.environ.get("SELF_URL", "").rstrip("/")
LIFF_ID = os.environ.get("LIFF_ID", "2010406205-emxo5rhE")
TIMETABLE_LIFF_ID = os.environ.get("TIMETABLE_LIFF_ID", "")
REGISTER_LIFF_ID = os.environ.get("REGISTER_LIFF_ID", "")
REVIEW_LIFF_ID = os.environ.get("REVIEW_LIFF_ID", "")
RICHMENU_ID_PREREGISTER = os.environ.get("RICHMENU_ID_PREREGISTER", "")
try:
    KYOYO_REQUIRED_CREDITS = int(os.environ.get("KYOYO_REQUIRED_CREDITS", "1"))
except ValueError:
    KYOYO_REQUIRED_CREDITS = 1
APP_URL = os.environ.get("APP_URL", "https://shindairaifuhaku.onrender.com")
IS_DEV = os.environ.get("ENV", "prod") == "dev"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
BACKUP_BUCKET = os.environ.get("BACKUP_BUCKET", "db-backups")
BACKUP_ENABLED = os.environ.get("BACKUP_ENABLED", "false").lower() in ("1", "true", "yes")
try:
    BACKUP_RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", "15"))
except ValueError:
    BACKUP_RETENTION_DAYS = 15
try:
    BACKUP_INTERVAL_HOURS = float(os.environ.get("BACKUP_INTERVAL_HOURS", "1"))
except ValueError:
    BACKUP_INTERVAL_HOURS = 1

STUDENT_ID_RE = _re.compile(r'^\d{7}(MM|ME|MH|[LHJEBSTAZX])$')
LINE_USER_ID_RE = _re.compile(r'^U[0-9a-f]{32}$')

# マイ時間割・必修科目自動登録が対象とする年度
DEFAULT_ACADEMIC_YEAR = 2026

# 登録フォーム用の学部・学科選択肢（templates/liff/timetable.html のプロフィール学部プルダウンと同じ11学部）
FACULTIES = [
    "文学部", "国際人間科学部", "法学部", "経済学部", "経営学部",
    "システム情報学部", "理学部", "医学部", "工学部", "農学部", "海洋政策科学部",
]

FACULTY_DEPARTMENTS = {
    "文学部": ["人文学科"],
    "国際人間科学部": ["グローバル文化学科", "発達コミュニティ学科", "環境共生学科", "子ども教育学科"],
    "法学部": ["法律学科"],
    "経済学部": ["経済学科"],
    "経営学部": ["経営学科"],
    "システム情報学部": ["システム情報学科"],
    "理学部": ["数学科", "物理学科", "化学科", "生物学科", "惑星学科"],
    "医学部": ["医学科", "医療創成工学科", "保健学科"],
    "工学部": ["建築学科", "市民工学科", "電気電子工学科", "機械工学科", "応用化学科"],
    # 農学部は2年次からコースに分かれ、卒業要件（単位チェッカー）もコース単位で異なるため、
    # 工学部の学科と同じ扱いでコース名をdepartmentの値とする（学科名は使わない）
    "農学部": [
        "生産環境工学コース", "食料環境経済学コース",
        "応用動物学コース", "応用植物学コース",
        "応用生命化学コース", "応用機能生物学コース",
    ],
    "海洋政策科学部": ["海洋基礎科学領域", "海洋応用科学領域", "海洋ガバナンス領域", "航海学領域", "機関学領域"],
}

# 農学部はコース未選択（1年次等）でも単位チェッカーを表示するため、代表コースにフォールバックする。
# templates/liff/timetable.html の _CREDIT_CHECKER_DEFAULT_DEPARTMENT と同じ内容を保つこと
CREDIT_CHECKER_DEFAULT_DEPARTMENT = {"農学部": "生産環境工学コース"}

# 2年次からコース分岐する学部は、初回登録時点で所属コースが存在しない学生（1年次等）がいるため、
# 会員登録フォームで「コース未定」を選べるようにする。この値が送信されたらdepartmentはNULLで保存する。
# templates/form_register.html の DEPARTMENT_UNDECIDED_FACULTIES と同期を保つこと
DEPARTMENT_UNDECIDED_VALUE = "未定"
DEPARTMENT_UNDECIDED_FACULTIES = {"農学部"}

JST = timezone(timedelta(hours=9))

ADMIN_COOKIE = "admin_tok"
ADMIN_TOKEN_TTL = 4 * 3600

PRIVACY_URL = APP_URL + "/privacy"
CONTACT_EMAIL = "bunnyskeren@gmail.com"

_CLS_ORDER_KEYS = ["基盤", "人文", "社会", "自然", "総合", "健康", "外国語"]

EASE_ORDER = {"SS": 0, "S": 1, "A": 2, "B": 3, "C": 4}
EASE_LABEL = {"SS": "天国", "S": "楽々", "A": "標準", "B": "大変", "C": "修羅場"}
EASE_COLOR = {"SS": "#10b981", "S": "#6366f1", "A": "#f59e0b", "B": "#f97316", "C": "#ef4444"}
EASE_STARS = {"SS": "★★★★★", "S": "★★★★☆", "A": "★★★☆☆", "B": "★★☆☆☆", "C": "★☆☆☆☆"}

RANK_MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}
VARIANT_ICONS = {0: "🅰", 1: "🅱", 2: "🅲", 3: "🅳"}
VARIANT_COLORS = ["#6366f1", "#0d9488", "#f59e0b", "#ef4444"]

# 以下、programing files/import_syllabus.pyのFACULTY_PATH/DEPARTMENT_PATH_OVERRIDE/
# ENGINEERING_RANGES/MEDICINE_RANGES/MEDICINE_SUBLETTERSと同じ対応表。
# シラバスURL生成ロジックを変更する際は、fetch_syllabus_info.py・
# templates/liff/timetable.html（JS版FACULTY_PATH_JS）も含め4箇所すべてを同時に更新すること
_SYLLABUS_FACULTY_PATH = {"U": "20", "B": "06", "X": "15", "G": "20", "Z": "14", "H": "13", "E": "05", "A": "10", "L": "01", "J": "04"}

_ENGINEERING_RANGES = [
    (0, 99, "0921"),      # 工学部建築学科
    (100, 149, "0922"),   # 工学部市民工学科
    (150, 199, "0923"),   # 工学部電気電子工学科
    (200, 249, "0924"),   # 工学部機械工学科
    (250, 299, "0925"),   # 工学部応用化学科
]

# 医学部は学科によって時間割コードの3文字目（Mの次）にさらに1文字付く場合と、
# 数字がそのまま続くが番号帯で学科が異なる場合がある
_MEDICINE_SUBLETTERS = {
    "B": "0803",  # 医学部医療創成工学科
}
_MEDICINE_RANGES = [
    (0, 399, "080201"),  # 医学部保健学科看護学専攻（暫定上限。他専攻データ確認後に調整）
    (900, 999, "0801"),  # 医学部医学科
]

# 番号帯だけでは学科・専攻を判別できない所属（理学部各学科・医学部保健学科の非看護学専攻・
# 工学部の全学科共通科目）は所属名（=Subject.faculty）で直接pathを決める
_DEPARTMENT_PATH_OVERRIDE = {
    "医学部保健学科看護学専攻": "080201",
    "医学部保健学科検査技術科学専攻": "080202",
    "医学部保健学科理学療法学専攻": "080203",
    "医学部保健学科作業療法学専攻": "080204",
    "理学部数学科": "0701",
    "理学部物理学科": "0702",
    "理学部化学科": "0703",
    "理学部生物学科": "0704",
    "理学部惑星学科": "0707",
    "工学部": "09",
}


def make_syllabus_url(timetable_code: str, department: str = "") -> str:
    if not timetable_code or len(timetable_code) < 2:
        return ""
    if department in _DEPARTMENT_PATH_OVERRIDE:
        path = _DEPARTMENT_PATH_OVERRIDE[department]
        return f"https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026/{path}/data/2026_{timetable_code}.html"
    letter = timetable_code[1].upper()
    if letter in ("T", "N"):
        digits = timetable_code[2:]
        if not digits.isdigit():
            return ""
        num = int(digits)
        for lo, hi, path in _ENGINEERING_RANGES:
            if lo <= num <= hi:
                return f"https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026/{path}/data/2026_{timetable_code}.html"
        return ""
    if letter == "M":
        if len(timetable_code) >= 3 and timetable_code[2].isalpha():
            path = _MEDICINE_SUBLETTERS.get(timetable_code[2].upper(), "")
            if not path:
                return ""
            return f"https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026/{path}/data/2026_{timetable_code}.html"
        digits = timetable_code[2:]
        if not digits.isdigit():
            return ""
        num = int(digits)
        for lo, hi, path in _MEDICINE_RANGES:
            if lo <= num <= hi:
                return f"https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026/{path}/data/2026_{timetable_code}.html"
        return ""
    path = _SYLLABUS_FACULTY_PATH.get(letter, "")
    if not path:
        return ""
    return f"https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026/{path}/data/2026_{timetable_code}.html"


def is_profile_complete(p) -> bool:
    """UserProfile行が氏名・学籍番号・学部・学年・学科すべて入力済みか判定する。"""
    return bool(p and p.name and p.student_id and p.faculty and p.grade and p.department)


def make_register_url(user_id: str) -> str:
    """会員登録画面のURL。REGISTER_LIFF_ID設定済みならLIFFとして開き、
    登録完了後にliff.closeWindow()でLINEのトーク画面へ自動で戻れるようにする。"""
    if REGISTER_LIFF_ID:
        return f"https://liff.line.me/{REGISTER_LIFF_ID}?uid={user_id}"
    return f"{APP_URL}/register?uid={user_id}"


def normalize_instructor_name(name: str) -> str:
    if not name:
        return name
    return name.replace(' ', '').replace('　', '')


# 科目名の「I」「II」等の半角ローマ数字表記をDB上の表記（全角ローマ数字）へ統一する。
# 前後が英数字でない場合のみ変換対象とし、"AI"や"TOEIC"等の単語中のI/II誤爆を防ぐ。
_HALF_TO_FULL_ROMAN = {
    'IX': 'Ⅸ', 'IV': 'Ⅳ', 'VIII': 'Ⅷ', 'VII': 'Ⅶ', 'VI': 'Ⅵ',
    'III': 'Ⅲ', 'II': 'Ⅱ', 'I': 'Ⅰ', 'V': 'Ⅴ', 'X': 'Ⅹ',
}
_ROMAN_NUMERAL_RE = _re.compile(r'(?<![A-Za-z0-9])(IX|IV|VIII|VII|VI|III|II|I|V|X)(?![A-Za-z0-9])')


def normalize_subject_name(name: str) -> str:
    if not name:
        return name
    return _ROMAN_NUMERAL_RE.sub(lambda m: _HALF_TO_FULL_ROMAN[m.group(1)], name)


def cls_order(name: str) -> int:
    for i, kw in enumerate(_CLS_ORDER_KEYS):
        if kw in (name or ""):
            return i
    return len(_CLS_ORDER_KEYS)


def make_cls_sort(cls_map: dict):
    def key(name: str) -> int:
        if name in cls_map:
            return cls_map[name]
        return cls_order(name) + 100000
    return key


def stars(n: int) -> str:
    n = max(1, min(5, n))
    return "★" * n + "☆" * (5 - n)


try:
    import pykakasi as _pykakasi
    _kks = _pykakasi.kakasi()

    def reading(text: str) -> str:
        result = _kks.convert(text)
        hira = ''.join(item.get('hira', '') for item in result)
        roma = ''.join(item.get('hepburn', '') for item in result)
        return f"{hira} {roma}".lower().strip()
except Exception:
    def reading(text: str) -> str:
        return ""
