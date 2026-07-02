import io
import re as _re

from core import cache

try:
    import pdfplumber as _pdfplumber
    PDFPLUMBER_OK = True
except ImportError:
    PDFPLUMBER_OK = False

# ── 経営学部 成績表 PDF パース ──────────────────────────────────────────────

SENMON_GROUPS = ["第1群", "第2群", "第3群", "グローバル", "初年次"]

_SENMON1 = {'経営学基礎論', '会計学基礎論', '市場システム基礎論'}

# 完全一致で判定する第2群科目
# 「会計学特殊講義」「経営学入門演習」等の3群科目と区別するため完全一致のみ使用
_SENMON2_EXACT = {
    # 旧カリキュラム第2群科目（経営.pdf 掲載11科目）
    '経営管理', '経営戦略', '経営史', '経営数学', '経営統計',
    'コーポレートファイナンス', '財務会計', '管理会計',
    'マーケティング', '金融システム', '交通論',
    # 2026年度カリキュラム追加（ナンバリングコード B1BB202 確認済み）
    '経済学', '統計学', '民法', '数学Ⅰ', '数学Ⅱ',
    '経営学入門', '会計学', '国際経営', '経営組織', '財務管理',
    '生産管理', '経営情報', 'ビジネス法',
}
# 前方一致が必要な科目（旧カリ「簿記」・新カリ「簿記Ⅰ」等のバリアントを同一視）
_SENMON2_PREFIX = ('簿記', '数学（')

TERM_PAT = _re.compile(r'前期|[12]Q|第[12]クオーター|第[12]Q')

_GLOBAL_PREFIXES = (
    'Academic Reading and Writing',
    'International Business',
    'International Management',
    'Introduction to Finance',
    'Introduction to Marketing',
    'Introduction to Management',
    'Introduction to Accounting',
    'Business Presentation',
    'Business Strategy',
    'Business Leadership',
    'Advanced Financial',
    'Advanced Study',
    'Portfolio Management',
    'Portfolio Theory',
    'Entrepreneurial',
    'Capstone',
    'Overview of Corporate',
    'Foundations of Securities',
    'Managerial Accounting',
    'Organization Theory',
    'Marketing Management',
    'Corporate Finance',
    'Operations Management',
    'Statistics for Business',
    'Sustainability Management',
    'Innovation and',
    'Supply Chain',
    'Brand Management',
    'Mergers and',
    'Human Resource Management',
    'Global ',
    '外国文献講義',
    '外国書講読',
)
_GAIGO_FOREIGN = ('ロシア語', 'ドイツ語', 'フランス語', '中国語', '韓国語', 'スペイン語',
                   'アラビア語', 'イタリア語', 'ポルトガル語', '朝鮮語')


def is_senmon2(name: str) -> bool:
    return name in _SENMON2_EXACT or any(name.startswith(p) for p in _SENMON2_PREFIX)


def classify_senmon(name: str) -> str:
    """専門科目を群に分類する。DBに登録があればそちらを優先。"""
    db = cache.get_senmon_group(name)
    if db:
        return db
    if '初年次セミナー' in name:
        return '初年次'
    if name in _SENMON1:
        return '第1群'
    if is_senmon2(name):
        return '第2群'
    if any(name.startswith(p) for p in _GLOBAL_PREFIXES):
        return 'グローバル'
    return '第3群'


def extract_seiseki_raw(text: str) -> dict:
    """PDFテキストから生の科目リストとサマリー値を抽出する（分類はしない）。"""
    gaigo_courses: list[dict] = []
    senmon_courses: list[dict] = []
    current_sec = ''
    course_re = _re.compile(r'^(?:＊\s+)?(.+?)\s+(\d+(?:\.\d+)?)\s+(秀|優|良|可|不可|合格|認定)')
    for line in text.splitlines():
        sec_m = _re.search(r'【(.*?)】', line)
        if sec_m:
            current_sec = sec_m.group(1)
            continue
        m = course_re.match(line.strip())
        if not m:
            continue
        name = m.group(1).strip()
        cr = float(m.group(2))
        if '外国語' in current_sec:
            gaigo_courses.append({
                "name": name, "credits": cr,
                "is_english": 'Academic English' in name,
                "is_foreign": any(name.startswith(p) for p in _GAIGO_FOREIGN),
            })
        elif '専門科目' in current_sec:
            senmon_courses.append({"name": name, "credits": cr})

    def _summary(label: str) -> float:
        mt = _re.search(_re.escape(label) + r'\s+([\d.]+)', text)
        return float(mt.group(1)) if mt else 0.0

    return {
        "gaigo_courses": gaigo_courses,
        "senmon_courses": senmon_courses,
        "summaries": {
            "総合教養科目":   _summary('総合教養科目'),
            "基礎教養科目":   _summary('基礎教養科目'),
            "情報科目":       _summary('情報科目'),
            "共通専門基礎科目": _summary('共通専門基礎科目'),
            "専門科目":       _summary('専門科目'),
            "外国語科目":     _summary('外国語科目'),
        },
    }


def classify_seiseki_raw(raw: dict) -> dict:
    """生データから単位区分の合計を計算する（DB分類を参照）。"""
    s = raw.get("summaries", {})
    gaigo1 = gaigo2 = 0.0
    for c in raw.get("gaigo_courses", []):
        if c.get("is_english"):
            gaigo1 += c["credits"]
        elif c.get("is_foreign"):
            gaigo2 += c["credits"]
    gaiko_total = s.get("外国語科目", 0.0)
    if gaigo1 + gaigo2 == 0 and gaiko_total > 0:
        gaigo1 = gaigo2 = round(gaiko_total / 2, 1)

    shonen = senmon1 = senmon2 = global_c = 0.0
    for c in raw.get("senmon_courses", []):
        grp = classify_senmon(c["name"])
        cr = c["credits"]
        if grp == '初年次':   shonen   += cr
        elif grp == '第1群':  senmon1  += cr
        elif grp == '第2群':  senmon2  += cr
        elif grp == 'グローバル': global_c += cr

    senmon_total = s.get("専門科目", 0.0)
    senmon3 = max(0.0, round(senmon_total - shonen - senmon1 - senmon2 - global_c, 1))
    return {
        "kyoyo_kei":   round(s.get("総合教養科目", 0.0), 1),
        "kyoyo_kiban": round(s.get("基礎教養科目", 0.0) + s.get("情報科目", 0.0), 1),
        "gaigo1":  round(gaigo1, 1), "gaigo2":  round(gaigo2, 1),
        "kyotsu":  round(s.get("共通専門基礎科目", 0.0), 1),
        "shonen":  round(shonen, 1),  "senmon1": round(senmon1, 1),
        "senmon2": round(senmon2, 1), "global":  round(global_c, 1),
        "senmon3": round(senmon3, 1),
    }


def parse_seiseki_pdf(data: bytes) -> dict:
    if not PDFPLUMBER_OK:
        raise RuntimeError("pdfplumber not available")
    with _pdfplumber.open(io.BytesIO(data)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    gpa = None
    gpa_m = _re.search(r'G\s*P\s*A\s+[\d.]+\s+\d+\s+([\d.]+)', text)
    if gpa_m:
        gpa = float(gpa_m.group(1))
    raw = extract_seiseki_raw(text)
    return {"gpa": gpa, "credits": classify_seiseki_raw(raw), "raw": raw}
