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
# 2026年度学生便覧（binran_2026.pdf）別表第2（履修要件）で確認済みの12科目のみ
_SENMON2_EXACT = {
    '経営管理', '経営戦略', '経営史', '経営数学', '経営統計',
    'コーポレートファイナンス', '財務会計', '管理会計',
    'マーケティング', '金融システム', '交通論',
}
# 前方一致が必要な科目（旧カリ「簿記」・新カリ「簿記Ⅰ」等のバリアントを同一視）
_SENMON2_PREFIX = ('簿記',)

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

# ── 工学部機械工学科 必修科目判定 ──────────────────────────────────────────────
# 学生便覧2026別表第1ホ（◎印）で確認済み。成績表印字はクォーター表記・(a)/(b)表記が
# 付くことがあるため前方一致で判定する（例:「機械工学実習Ⅰ(1Q)」「プログラミング演習Ⅱ(b)」）
_KIKAI_KYOTSU_HISSHU = ('線形代数1', '線形代数2', '微分積分1', '微分積分2')
_KIKAI_SENMON_HISSHU = (
    'プログラミング演習Ⅰ', 'プログラミング演習Ⅱ', 'プログラミング演習Ⅲ',
    '初年次セミナー（機械工学）', '機械工学基礎',
    '基礎力学Ⅰ', '基礎力学Ⅱ', '機械基礎数学Ⅰ', '機械基礎数学Ⅱ',
    '材料力学Ⅰ', '流体工学', '熱力学Ⅰ', '安全工学・工学倫理Ⅰ',
    '機械工学実習Ⅰ', '機械製図Ⅰ', '機械工学実験', '機械工学実習Ⅱ', '機械製図Ⅱ',
    '機械設計製作演習Ⅰ', '機械設計製作演習Ⅱ', '先端機械工学詳論', '卒業研究',
)


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
            # 修正理由: 部分一致(in)だと「共通専門基礎科目」が「専門科目」を含んでしまい、
            # 誤って専門科目欄に混入していたため、前後の空白を除いた完全一致にする
            current_sec = sec_m.group(1).strip()
            continue
        # 教養科目の内訳（基盤系/人文系/自然系/総合系/外国語系）は【】でなく（）の小見出しで
        # 区切られる。行全体が「（見出し）」のみの場合だけ更新する
        # （成績明細行の右側に印字される集計欄「教養科目（基盤系） 4.0」等を誤検出しないため）
        sub_m = _re.match(r'^[（(]([^）)]+)[）)]\s*$', line.strip())
        if sub_m:
            current_sec = sub_m.group(1).strip()
            continue
        m = course_re.match(line.strip())
        if not m:
            continue
        if m.group(3) == '不可':
            # 不可は未修得のため単位数を集計に含めない
            continue
        name = m.group(1).strip()
        cr = float(m.group(2))
        if '外国語' in current_sec:
            gaigo_courses.append({
                "name": name, "credits": cr,
                "is_english": 'Academic English' in name,
                "is_foreign": any(name.startswith(p) for p in _GAIGO_FOREIGN),
            })
        elif current_sec in ('専門科目', '共通専門基礎科目', '専門基礎科目'):
            senmon_courses.append({"name": name, "credits": cr, "section": current_sec})

    # 区分表の「その他の科目」は全学共通授業科目ブロックと専門科目ブロックの両方に
    # 同名で存在するため、<<...>>の大区分見出しでテキストを分割して区別する
    zengaku_m = _re.search(r'<<\s*全学共通授業科目', text)
    senmon_blk_m = _re.search(r'<<\s*専門科目', text)
    if zengaku_m and senmon_blk_m and zengaku_m.start() < senmon_blk_m.start():
        zengaku_block = text[zengaku_m.start():senmon_blk_m.start()]
        senmon_block = text[senmon_blk_m.start():]
    else:
        zengaku_block = senmon_block = text

    def _summary(label: str, block: str) -> float:
        mt = _re.search(_re.escape(label) + r'\s+([\d.]+)', block)
        return float(mt.group(1)) if mt else 0.0

    return {
        "gaigo_courses": gaigo_courses,
        "senmon_courses": senmon_courses,
        "summaries": {
            # 旧カリキュラム（教養科目を人文/自然/社会/総合の内訳なしで一括集計していた表記）
            "総合教養科目":   _summary('総合教養科目', zengaku_block),
            "基礎教養科目":   _summary('基礎教養科目', zengaku_block),
            "情報科目":       _summary('情報科目', zengaku_block),
            "外国語科目":     _summary('外国語科目', zengaku_block),
            # 新カリキュラム（区分表に「教養科目（○○系）」として内訳が個別に印字される）
            "人文系":         _summary('教養科目（人文系）', zengaku_block),
            "自然系":         _summary('教養科目（自然系）', zengaku_block),
            "社会系":         _summary('教養科目（社会系）', zengaku_block),
            "総合系":         _summary('教養科目（総合系）', zengaku_block),
            "基盤系":         _summary('教養科目（基盤系）', zengaku_block),
            # ラベル表記に「教養科目」接頭辞や「系」が付かない成績表（例:「健康・スポーツ科学」）
            # もあるため、新カリ表記で見つからない場合はそちらにフォールバックする
            "健康・スポーツ科学系": (
                _summary('教養科目（健康・スポーツ科学系）', zengaku_block)
                or _summary('健康・スポーツ科学', zengaku_block)
            ),
            "共通専門基礎科目": _summary('共通専門基礎科目', senmon_block),
            "専門基礎科目":   _summary('専門基礎科目', senmon_block),
            "専門科目":       _summary('専門科目', senmon_block),
            # 「関連科目」（本学部生の履修を許可された法学部・経済学部の専門科目）の集計に使用
            "法学部科目":     _summary('法学部科目', senmon_block),
            "経済学部科目":   _summary('経済学部科目', senmon_block),
            # 「その他必要と認める科目」の一部（他学部専門科目の枠）に使用
            "その他の科目_専門": _summary('その他の科目', senmon_block),
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
        if grp == '初年次':
            shonen += cr
        elif grp == '第1群':
            senmon1 += cr
        elif grp == '第2群':
            senmon2 += cr
        elif grp == 'グローバル':
            global_c += cr

    senmon_total = s.get("専門科目", 0.0)
    senmon3 = max(0.0, round(senmon_total - shonen - senmon1 - senmon2 - global_c, 1))
    kyotsu_val = s.get("共通専門基礎科目", 0.0)

    # 機械工学科: 共通専門基礎科目／専門基礎科目／専門科目それぞれを必修・選択に仕分ける
    # （専門基礎科目は便覧上すべて選択科目のため必修集計なし）
    kikai_kyotsu_hisshu = kikai_kyotsu_sentaku = 0.0
    kikai_senmonkiso_sentaku = 0.0
    kikai_senmon_hisshu = kikai_senmon_sentaku = 0.0
    for c in raw.get("senmon_courses", []):
        section = c.get("section", "専門科目")
        cr = c["credits"]
        if section == '共通専門基礎科目':
            if any(c["name"].startswith(p) for p in _KIKAI_KYOTSU_HISSHU):
                kikai_kyotsu_hisshu += cr
            else:
                kikai_kyotsu_sentaku += cr
        elif section == '専門基礎科目':
            kikai_senmonkiso_sentaku += cr
        elif section == '専門科目':
            if any(c["name"].startswith(p) for p in _KIKAI_SENMON_HISSHU):
                kikai_senmon_hisshu += cr
            else:
                kikai_senmon_sentaku += cr

    # 教養科目（基盤系）は新カリキュラムの区分名。旧カリキュラム（基礎教養科目＋情報科目）の
    # 成績表しか無い場合はそちらにフォールバックする
    kiban = s.get("基盤系", 0.0) or (s.get("基礎教養科目", 0.0) + s.get("情報科目", 0.0))

    # 人文系・自然系・社会系・総合系は新カリキュラムの成績表が区分別に集計値を印字するため、
    # そのまま読み取る。ただし人文/自然/社会/総合の内訳が無く「総合教養科目」で一括集計する
    # 旧カリキュラムの成績表（内訳が判別できない）は、便宜上まとめて総合系に計上する
    jinbun_val = s.get("人文系", 0.0)
    shizen_val = s.get("自然系", 0.0)
    shakai_val = s.get("社会系", 0.0)
    sougou_val = s.get("総合系", 0.0)
    if jinbun_val + shizen_val + shakai_val + sougou_val == 0 and s.get("総合教養科目", 0.0) > 0:
        sougou_val = s.get("総合教養科目", 0.0)

    # 関連科目（本学部生の履修を許可された法学部・経済学部の専門科目）
    kanren = s.get("法学部科目", 0.0) + s.get("経済学部科目", 0.0)
    # その他必要と認める科目（健康・スポーツ科学系＋他学部専門科目の枠）。
    # 外国語第Ⅱ・第Ⅲの一部（C1/C2クラス等）や共通専門基礎科目の超過分（6単位超・上限2単位）は
    # PDFの記載からは判別できないため未対応（超過分はフロントエンド側で別途加算する）
    sonota = s.get("健康・スポーツ科学系", 0.0) + s.get("その他の科目_専門", 0.0)

    return {
        "jinbun":      round(jinbun_val, 1),
        "shizen":      round(shizen_val, 1),
        "shakai":      round(shakai_val, 1),
        "sougou":      round(sougou_val, 1),
        "kyoyo_kiban": round(kiban, 1),
        "kanren":  round(kanren, 1),
        "sonota":  round(sonota, 1),
        "gaigo1":  round(gaigo1, 1), "gaigo2":  round(gaigo2, 1),
        "kyotsu":  round(kyotsu_val, 1),
        "kenko":   round(s.get("健康・スポーツ科学系", 0.0), 1),
        "shonen":  round(shonen, 1),  "senmon1": round(senmon1, 1),
        "senmon2": round(senmon2, 1), "global":  round(global_c, 1),
        "senmon3": round(senmon3, 1),
        # 経営学部の群分類ロジック（第1群〜第3群等）に依存しない、共通専門基礎科目＋専門科目の
        # 素の合計。群分けの無い学科（機械工学科等）の単位チェッカーはこちらを使う
        "senmon_all": round(kyotsu_val + senmon_total, 1),
        # 共通専門基礎科目と専門科目の必要単位数が別枠で定められている学部（農学部等）向けに、
        # 専門科目のみ（共通専門基礎科目を含まない）の値も別途返す
        "senmon": round(senmon_total, 1),
        # 機械工学科: 区分（共通専門基礎科目／専門基礎科目／専門科目）×必修・選択の内訳
        "kikai_kyotsu_hisshu":      round(kikai_kyotsu_hisshu, 1),
        "kikai_kyotsu_sentaku":     round(kikai_kyotsu_sentaku, 1),
        "kikai_senmonkiso_sentaku": round(kikai_senmonkiso_sentaku, 1),
        "kikai_senmon_hisshu":      round(kikai_senmon_hisshu, 1),
        "kikai_senmon_sentaku":     round(kikai_senmon_sentaku, 1),
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
