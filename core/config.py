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

STUDENT_ID_RE = _re.compile(r'^\d{7}(MM|ME|MH|[LHJEBSTAZX])$')
LINE_USER_ID_RE = _re.compile(r'^U[0-9a-f]{32}$')

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
    "農学部": ["食料環境システム学科", "資源生命科学科", "生命機能科学科"],
    "海洋政策科学部": ["海洋政策科学科"],
}

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

_SYLLABUS_FACULTY_PATH = {"U": "20", "B": "06", "X": "15"}


def make_syllabus_url(timetable_code: str) -> str:
    if not timetable_code or len(timetable_code) < 2:
        return ""
    path = _SYLLABUS_FACULTY_PATH.get(timetable_code[1].upper(), "")
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
    if any('぀' <= c <= '鿿' for c in name):
        return name.replace(' ', '')
    return name


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
