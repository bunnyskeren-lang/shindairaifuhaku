import os
import re as _re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote as _urllib_quote

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
# 管理画面ログイン(セッションCookie)とは独立に、通知購読(service worker登録+push subscribe)
# だけを許可するための秘密トークン。iPhoneでアプリがOSに回収されCookieが消えても、
# 一度この秘密リンクで購読しておけば管理画面に再ログインしなくても通知は届き続ける。
PUSH_ENABLE_TOKEN = os.environ.get("PUSH_ENABLE_TOKEN", "")
SELF_URL = os.environ.get("SELF_URL", "").rstrip("/")
LIFF_ID = os.environ.get("LIFF_ID", "2010406205-emxo5rhE")
REGISTER_LIFF_ID = os.environ.get("REGISTER_LIFF_ID", "")
REVIEW_LIFF_ID = os.environ.get("REVIEW_LIFF_ID", "")
CONTACT_LIFF_ID = os.environ.get("CONTACT_LIFF_ID", "")
RICHMENU_ID_PREREGISTER = os.environ.get("RICHMENU_ID_PREREGISTER", "")
RICHMENU_ID_MAIN = os.environ.get("RICHMENU_ID_MAIN", "")
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


def normalize_student_id(raw: str) -> str:
    """学籍番号入力の前後・途中の空白（半角・全角とも）を除去し大文字化する。
    途中にスペースが入る誤入力（例:「234 5678S」）もSTUDENT_ID_REに通すための共通処理"""
    return _re.sub(r'[\s　]+', '', raw).upper()

# 1科目×1担当教員（course_section）あたりのレビュー投稿受付上限（待機中+承認済みの合計）
MAX_REVIEWS_PER_COURSE_SECTION = 1

# チーム開講（複数教員が輪番で担当）科目向けに、レビュー投稿フォームの担当教員候補へ
# 全科目常に出す擬似候補のラベル。実在のInstructorではなく、submit時は科目の代表
# course_section（id昇順の先頭）へ束ね、selected_instructor にこの文字列を保存する。
# 残り枠バッジ・1件上限の管理対象外（同一学籍番号でのオムニバス重複のみ防ぐ）。
OMNIBUS_INSTRUCTOR_LABEL = "オムニバス"

# オンデマンド配信のため担当教員によらず授業内容が同一な科目（科目名, 学部）。
# 1件のレビューがあれば他の教員のクラスにも実質流用できるため、全course_sectionで
# レビュー募集を締め切り、科目詳細ページには他教員クラスも同一内容である旨を表示する。
# 情報基礎・教養とは何か（いずれも教養教育院）- 2026-08-31追加。
# 2026-09-08: 以前は subjects.id をハードコード（{704, 702}）していたが、
# 共通専門基礎科目のシラバス再インポートで id が振り直され、id=702/704 が
# それぞれ「基礎無機化学1」「基礎地学1」を指すようになり、レビュー0件のこれらの科目が
# レビュー投稿フォームで「募集終了」と誤表示されていた。id ではなく（科目名, 学部）で
# 指定し、実 id への解決は core.cache.get_on_demand_subject_ids_cached() が毎回行う。
ON_DEMAND_SAME_CONTENT_SUBJECTS = frozenset({
    ("情報基礎", "教養教育院"),
    ("教養とは何か", "教養教育院"),
})
ON_DEMAND_SAME_CONTENT_NOTE = "※オンデマンド配信であり、他教員のクラスも内容は同一です"

# レビューが承認されるごとに付与される、任意の科目のレビュー閲覧権チケット枚数。
# 2026-09-07より、レビュー投稿先の科目カテゴリで枚数を分ける（教養2枚・専門1枚）。
# 実際の付与・消費判定は review_approval_unlock_credits() に集約する。
REVIEW_APPROVAL_UNLOCK_CREDITS_KYOYO = 2
REVIEW_APPROVAL_UNLOCK_CREDITS_SENMON = 1
# カテゴリが取得できない/教養・専門以外のレビュー（通常発生しない）向けフォールバック
REVIEW_APPROVAL_UNLOCK_CREDITS = 1

# reviews.credit_granted_at は1列で3状態を表す（2026-09-08〜）:
#   - NULL                     … まだ付与も番兵マークもしていない。承認時にチケットを付与すべき
#   - CREDIT_GRANTED_SENTINEL  … 付与はしない（レビュー報酬を現金買取 payment_limit へ換算済み）
#   - 実時刻                    … その時刻にチケットを実際に付与した
# NULL のままにすると /admin/reviews/approve の冪等ガード（credit_granted_at IS NULL）を
# すり抜けて再付与されてしまうため、「付与しない」ケースにも実時刻ではなくこの固定値を入れる。
# 判定は必ず下の credit_grant_pending() / credit_tickets_were_granted() /
# credit_tickets_granted_clause() を経由する（生の IS NULL / isnot(None) を新規に書かない。
# 番兵レビューを誤って「付与済み」に数えるバグの温床になる）。
CREDIT_GRANTED_SENTINEL = datetime(1970, 1, 1, tzinfo=timezone.utc)


def credit_grant_pending(credit_granted_at) -> bool:
    """このレビューは承認時に閲覧チケットを付与すべきか（まだ付与も番兵マークもされていない）。"""
    return credit_granted_at is None


def credit_tickets_were_granted(credit_granted_at) -> bool:
    """このレビューで閲覧チケットを実際に付与したか（Python オブジェクト版）。
    NULL（未付与）も番兵値（現金換算済み・付与なし）も False。"""
    return credit_granted_at is not None and credit_granted_at > CREDIT_GRANTED_SENTINEL


def credit_tickets_granted_clause(credit_granted_at_col):
    """credit_tickets_were_granted() の SQLAlchemy 条件版（models を import せず列を受け取る）。
    管理画面の「付与」集計・支払い済み化時のチケット消費で使う。"""
    return credit_granted_at_col > CREDIT_GRANTED_SENTINEL


def review_approval_unlock_credits(category) -> int:
    """このカテゴリの科目へのレビューが1件承認されたとき付与するチケット枚数。"""
    c = (category or "").strip()
    if c == "教養":
        return REVIEW_APPROVAL_UNLOCK_CREDITS_KYOYO
    if c == "専門":
        return REVIEW_APPROVAL_UNLOCK_CREDITS_SENMON
    return REVIEW_APPROVAL_UNLOCK_CREDITS

# 会員登録（初回のUserProfile作成時）に全員へプレゼントするレビュー閲覧権チケット枚数
REGISTRATION_WELCOME_UNLOCK_CREDITS = 1

# 虚偽投稿等でLINE bot利用を永久停止（UserProfile.banned_at）されたユーザーへの定型応答
BAN_MESSAGE_TEXT = "現在、このアカウントはご利用を停止しております。心当たりがある場合は、お問い合わせフォームよりご連絡ください。"

# レビュー投稿を受け付ける科目のcategory（Subject.category）。2026-08-31より教養科目のみに限定。
# LINE botの投稿導線（Flexの「レビューを投稿する」ボタン）はこの値のみで判定する。
REVIEW_SUBMISSION_CATEGORY = "教養"
REVIEW_SUBMISSION_RESTRICTED_MESSAGE = "現在、レビュー投稿は教養科目のみ受け付けています"

# 専門科目のcategory値。2026-09-07より、レビュー投稿フォーム(/)に限り、投稿者本人が
# 会員登録した学部の専門科目もレビュー投稿できるようにする（LINE botの投稿導線・レビュー
# 閲覧は従来どおり教養科目のみ）。判定は subject_submittable_for_profile() に集約する。
REVIEW_SUBMISSION_SENMON_CATEGORY = "専門"
REVIEW_SUBMISSION_FACULTY_MISMATCH_MESSAGE = "この専門科目は、ご登録の学部の学生のみレビューを投稿できます"

# 共通専門基礎科目（微分積分・力学基礎・化学実験等）は category="専門" だが faculty がこの値で、
# 理系を中心に複数学部の学生が履修する。教養科目と同じく学部を問わずレビュー投稿可とする
# （ユーザー指示 2026-09-07）。
KYOTSU_SENMON_KISO_FACULTY = "教養教育院"

# 海洋政策科学部は会員登録時に「領域」（FACULTY_DEPARTMENTS["海洋政策科学部"]）を選ばせるが、
# 領域はゆるやかで学生は他領域の専門科目も広く履修する。そのため、この学部の学生に限り、
# 登録した領域に関係なく学部の専門科目すべてをレビュー投稿できるようにする
# （＝専門科目の学部一致チェックのみ行い、学科／領域の突合はスキップする。ユーザー指示 2026-09-08）。
KAIYO_SEISAKU_FACULTY = "海洋政策科学部"

# 農学部は会員登録時に「コース」単位（FACULTY_DEPARTMENTS["農学部"]）で登録させるが、
# 専門科目の分類（subjects.classification / subjects.department）は「学科」単位で管理する。
# レビュー投稿フォームの候補絞り込みは、この対応表でコース→学科に変換したうえで学科単位で行う
# （ユーザー指示 2026-09-08）。
NOGAKU_COURSE_TO_DEPARTMENT = {
    "生産環境工学コース": "食料環境システム学科",
    "食料環境経済学コース": "食料環境システム学科",
    "応用動物学コース": "資源生命科学科",
    "応用植物学コース": "資源生命科学科",
    "応用生命化学コース": "生命機能科学科",
    "応用機能生物学コース": "生命機能科学科",
}
# subjects.department に入りうる農学部の学科名（database.py init_db() のバックフィル対象）。
# classification が "{学科名}専門科目" の科目のみ埋め、"農学部専門科目" /
# "農学部専門科目（学科不明）" は学科不明として空のまま残す（全コースから投稿可を維持）。
NOGAKU_DEPARTMENTS = ("食料環境システム学科", "資源生命科学科", "生命機能科学科")


def nogaku_profile_department_to_gakka(profile_department) -> str:
    """農学部プロフィールのコース名を学科名へ変換する。未定・対応表に無い値はそのまま返す。"""
    pd = (profile_department or "").strip()
    return NOGAKU_COURSE_TO_DEPARTMENT.get(pd, pd)


def subject_submittable_for_profile(
    category,
    subject_faculty,
    subject_department,
    profile_faculty,
    profile_department,
) -> bool:
    """レビュー投稿フォームでこの科目にレビューを投稿できるか判定する。

    - 教養科目（category == REVIEW_SUBMISSION_CATEGORY）: 全員可
    - 共通専門基礎科目（category == "専門" かつ faculty == KYOTSU_SENMON_KISO_FACULTY）: 全員可
    - その他の専門科目（category == REVIEW_SUBMISSION_SENMON_CATEGORY）: 投稿者本人の学部と一致必須。
      学科は「一致」「科目側が学科不明（空/NULL）」「投稿者が学科未登録（空/NULL）」の
      いずれかで可（ユーザー指示 2026-09-07）。農学部のみ、登録はコース単位・科目分類は
      学科単位なので、投稿者のコース名を学科名へ変換してから突合する（ユーザー指示 2026-09-08）。
      海洋政策科学部のみ、登録領域を問わず学部の専門科目すべてを投稿可とする（学部一致のみ判定し
      学科／領域の突合はスキップ。ユーザー指示 2026-09-08）
    - それ以外のcategory: 不可
    """
    if category == REVIEW_SUBMISSION_CATEGORY:
        return True
    if category != REVIEW_SUBMISSION_SENMON_CATEGORY:
        return False
    sf = (subject_faculty or "").strip()
    if sf == KYOTSU_SENMON_KISO_FACULTY:
        return True
    pf = (profile_faculty or "").strip()
    if not sf or not pf or sf != pf:
        return False
    if pf == KAIYO_SEISAKU_FACULTY:
        return True
    sd = (subject_department or "").strip()
    pd = (profile_department or "").strip()
    if pf == "農学部":
        pd = nogaku_profile_department_to_gakka(pd)
    return not sd or not pd or sd == pd

# レビューを閲覧可能な科目のcategory（Subject.category）。2026-09-06より専門科目は
# チケット解除・件数/評価集計表示を含め一切閲覧不可にする（ユーザー指示。将来的に専門科目の
# レビュー投稿自体は解禁予定だが、閲覧解禁は別途指示があるまで行わない）。
REVIEW_VIEW_CATEGORY = "教養"
REVIEW_VIEW_RESTRICTED_MESSAGE = "専門科目のレビュー閲覧機能は、現在準備中です🙇‍♀️\n\nレビュー投稿は募集中です！✨"
REVIEW_VIEW_RESTRICTED_FORM_LABEL = "📝 レビュー投稿フォームはこちら"


def student_email(student_id: str) -> str:
    """学籍番号から大学メールアドレスを導出する（例：2345678S → 2345678s@stu.kobe-u.ac.jp）。"""
    return f"{student_id.strip().lower()}@stu.kobe-u.ac.jp"

# 登録フォーム用の学部・学科選択肢（11学部）
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
    "医学部": [
        "医学科", "医療創成工学科",
        "保健学科看護学専攻", "保健学科検査技術科学専攻",
        "保健学科理学療法学専攻", "保健学科作業療法学専攻",
    ],
    "工学部": ["建築学科", "市民工学科", "電気電子工学科", "機械工学科", "応用化学科"],
    # 農学部は2年次からコースに分かれるため、
    # 工学部の学科と同じ扱いでコース名をdepartmentの値とする（学科名は使わない）
    "農学部": [
        "生産環境工学コース", "食料環境経済学コース",
        "応用動物学コース", "応用植物学コース",
        "応用生命化学コース", "応用機能生物学コース",
    ],
    "海洋政策科学部": ["海洋基礎科学領域", "海洋応用科学領域", "海洋ガバナンス領域", "航海学領域", "機関学領域"],
}


# 2年次からコース分岐する学部は、初回登録時点で所属コースが存在しない学生（1年次等）がいるため、
# 会員登録フォームで「コース未定」を選べるようにする。この値が送信されたらdepartmentはNULLで保存する。
# templates/form_register.html の DEPARTMENT_UNDECIDED_FACULTIES と同期を保つこと
DEPARTMENT_UNDECIDED_VALUE = "未定"
DEPARTMENT_UNDECIDED_FACULTIES = {"農学部"}

JST = timezone(timedelta(hours=9))

ADMIN_COOKIE = "admin_tok"
ADMIN_TOKEN_TTL = 4 * 3600

PRIVACY_URL = APP_URL + "/privacy"
TERMS_URL = APP_URL + "/terms"
CONTACT_URL = APP_URL + "/contact"

_CLS_ORDER_KEYS = ["基盤", "人文", "社会", "自然", "総合", "健康", "外国語"]

EASE_ORDER = {"SS": 0, "S": 1, "A": 2, "B": 3, "C": 4}
EASE_LABEL = {"SS": "天国", "S": "楽々", "A": "標準", "B": "大変", "C": "修羅場"}
EASE_COLOR = {"SS": "#10b981", "S": "#6366f1", "A": "#f59e0b", "B": "#f97316", "C": "#ef4444"}
EASE_STARS = {"SS": "★★★★★", "S": "★★★★☆", "A": "★★★☆☆", "B": "★★☆☆☆", "C": "★☆☆☆☆"}

# 以下、programing files/fetch_syllabus_info.pyのFACULTY_PATH/DEPARTMENT_PATH_OVERRIDE/
# ENGINEERING_RANGES/MEDICINE_RANGES/MEDICINE_SUBLETTERSと同じ対応表。
# シラバスURL生成ロジックを変更する際は、programing files/fetch_syllabus_info.py側も
# 同時に更新すること
# （programing files/import_syllabus.py側の同名ロジックは2026-07に呼び出し元が
# 無くなり死んでいたため削除済み。import_syllabus.pyはシラバスURLを保存せず、
# シラバス投入後にfetch_syllabus_info.py側のロジックで動的生成する設計）
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


def syllabus_department_key_from_parts(faculty: str | None, department: str | None) -> str:
    """make_syllabus_url()の department 引数用に、学部名+学科名を連結して
    旧syllabi.department（シラバス生データの所属列）と同じ形の文字列を再現する。
    _DEPARTMENT_PATH_OVERRIDEのキーは学部名+学科名を連結した複合文字列のままのため、
    make_syllabus_url() に生の faculty / department を渡す箇所は必ずこのヘルパーを通すこと。"""
    return f"{faculty or ''}{department or ''}"


def syllabus_department_key(subject) -> str:
    """syllabus_department_key_from_parts() の Subject オブジェクト版。"""
    return syllabus_department_key_from_parts(subject.faculty, subject.department)


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


# 会員登録フォームの必須質問「神大生協が運営するアルバイト求人サイトを閲覧したことがありますか」。
# 回答は下記2択のいずれか。DBには文字列でそのまま保存する（"いいえ" も回答済みとして
# is_profile_complete() を通過させるため、真偽値ではなく選択肢文字列で持つ）。
COOP_JOBSITE_KNOWN_QUESTION = "神大生協が運営するアルバイト求人サイトを閲覧したことがありますか"
COOP_JOBSITE_KNOWN_CHOICES = ("はい", "いいえ")


def is_profile_complete(p) -> bool:
    """UserProfile行が氏名・学籍番号・学部・学科・生協求人サイト認知の質問すべて入力済みか判定する。

    coop_jobsite_known は2026-09-07に必須化した項目。既存の登録済みユーザーはこの列がNULLの
    ままになり本関数がFalseを返すため、次回操作時に一度だけ会員登録フォームへ誘導される
    （友だち追加者の把握が目的）。一度回答すれば以降は再登録を求められない。"""
    return bool(
        p and p.name and p.student_id and p.faculty and p.department
        and getattr(p, "coop_jobsite_known", None)
    )


def make_register_url(user_id: str) -> str:
    """会員登録画面のURL。REGISTER_LIFF_ID設定済みならLIFFとして開き、
    登録完了後にliff.closeWindow()でLINEのトーク画面へ自動で戻れるようにする。"""
    if REGISTER_LIFF_ID:
        return f"https://liff.line.me/{REGISTER_LIFF_ID}?uid={user_id}"
    return f"{APP_URL}/register?uid={user_id}"


def make_review_liff_url(course_name: str = "", user_id: str = "") -> str:
    """レビュー投稿フォーム（/、REVIEW_LIFF_IDのエンドポイントURL）へのURL。
    生のHTTPS URLで開かせるとLINEの通常のアプリ内ブラウザ扱いになりliff.isInClient()が
    falseになって自動ログインできない（[[feedback_liff_links_must_use_liffline_me]]と同じ理由）。
    必ず https://liff.line.me/{REVIEW_LIFF_ID}?course=...&uid=... 形式で開かせる。
    REVIEW_LIFF_ID未設定時は直URLにフォールバックする。"""
    parts = []
    if course_name:
        parts.append(f"course={_urllib_quote(course_name)}")
    if user_id:
        parts.append(f"uid={user_id}")
    params = "&".join(parts)
    base = f"https://liff.line.me/{REVIEW_LIFF_ID}" if REVIEW_LIFF_ID else REVIEW_FORM_URL
    return f"{base}?{params}" if params else base


def make_course_liff_url(course_id) -> str:
    """科目詳細LIFFページのURL。LINEのFlexMessage等から「https://{APP_URL}/liff/course?...」の
    ような生のHTTPS URLをそのまま開かせると、LINEの通常のアプリ内ブラウザで開かれるだけで
    LIFFクライアントとしては認識されず、liff.isInClient()がfalseになりliff.isLoggedIn()も
    自動ログインされない（2026-08-24、レビュー閲覧権チケットの解除ボタンが常に未ログイン扱いに
    なる不具合として発覚。make_register_urlが同じ理由でLIFF URL形式を使っているのと同様の対応）。
    必ず https://liff.line.me/{LIFF_ID}?course_id=... 形式で開かせ、LINE側にLIFFとして
    認識させる。LIFF_ID未設定時（テスト環境等）は直URLにフォールバックする。"""
    if LIFF_ID:
        return f"https://liff.line.me/{LIFF_ID}?course_id={course_id}"
    return f"{APP_URL}/liff/course?course_id={course_id}"


def escape_like(s: str) -> str:
    """ILIKE検索語のワイルドカード(\\, %, _)をエスケープする。呼び出し側はescape="\\\\"を指定すること。"""
    return s.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


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


def subject_sort_reading_key(subj) -> str:
    """科目一覧のよみがな順ソート用キー。readingが空文字の科目（バックフィル前後の一瞬）は
    name（漢字）にフォールバックする。line_bot/handler.pyとrouters/admin/courses.pyで
    同じ規則を共有するための一元化（2026-09-06、旧・両ファイルへの重複実装を統合）。"""
    return (subj.reading or "").strip() or (subj.name or "")
