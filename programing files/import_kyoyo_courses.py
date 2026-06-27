"""
教養科目を一括インポートするスクリプト。
実行: python -X utf8 import_kyoyo_courses.py --env dev
"""
import argparse, asyncio, os, sys
from pathlib import Path


def load_env(env: str):
    env_file = Path(__file__).parent / (".env.dev" if env == "dev" else ".env")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


from sqlalchemy import select
from database import init_db, AsyncSessionLocal
from models import Subject

try:
    import pykakasi as _pykakasi
    _kks = _pykakasi.kakasi()
    def _reading(t: str) -> str:
        result = _kks.convert(t)
        hira = ''.join(item.get('hira', '') for item in result)
        roma = ''.join(item.get('hepburn', '') for item in result)
        return f"{hira} {roma}".lower().strip()
except Exception:
    def _reading(t: str) -> str:
        return ""


def ab(names):
    return [f"{n}{s}" for n in names for s in ('A', 'B')]

def abc(names):
    return [f"{n}{s}" for n in names for s in ('A', 'B', 'C')]

def abcd(names):
    return [f"{n}{s}" for n in names for s in ('A', 'B', 'C', 'D')]


# ══════════════════════════════════════════════
# 科目リスト（分類名は 教養(xxx) 形式）
# ══════════════════════════════════════════════

KIBAN = [
    "教養とは何か", "多言語と多文化の世界", "情報基礎", "データサイエンス基礎学",
]

JINBUN = (
    ["哲学", "論理学", "倫理学", "科学技術と倫理", "教育と人間形成"]
    + ab(["心理学", "教育学", "言語科学", "文学",
          "芸術と文化", "芸術史", "美術史", "科学史",
          "日本史", "東洋史", "アジア史", "西洋史", "考古学"])
)

SHAKAI = (
    ["社会生活と法", "国家と法", "政治と社会", "経済社会の発展",
     "経営学", "社会学", "教育と社会", "地理学",
     "社会思想史", "文化人類学", "越境する文化", "生活環境と技術"]
    + ab(["法学", "政治学", "経済学", "現代の経済", "現代社会論"])
)

SHIZEN = (
    ["現代物理学が描く世界", "身近な物理法則"]
    + ab(["統計学", "物理学", "化学", "生命科学",
          "医学", "保健学", "健康科学", "惑星学", "情報学"])
    + abcd(["数学", "生物学"])
)

SOGO = (
    ["ESD論(持続可能な社会づくり)基礎",
     "海への誘い", "瀬戸内海学入門", "阪神・淡路大震災と都市の安全",
     "地域社会形成基礎論", "ひょうご神戸学", "日本酒学入門",
     "神戸大学史", "神戸大学研究最前線", "社会基礎学",
     "価値創造論基礎", "アントレプレナーシップ入門"]
    + ab(["ESD論(持続可能な社会づくり)", "環境学入門",
          "ジェンダーとセクシュアリティ", "ボランティアと社会貢献活動",
          "職業と学び-キャリアデザインを考える"])
    + abc(["価値創造論", "社会と人権"])
)

# 外国語第1: 英語 (0.5単位)
GAIGO1 = [
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
GAIGO2 = [
    # ドイツ語
    "ドイツ語初級A1", "ドイツ語初級A2", "ドイツ語初級B1", "ドイツ語初級B2",
    "ドイツ語初級A3", "ドイツ語初級A4", "ドイツ語初級B3", "ドイツ語初級B4",
    "ドイツ語初級SA3", "ドイツ語初級SA4", "ドイツ語初級SB3", "ドイツ語初級SB4",
    "ドイツ語中級C1", "ドイツ語中級C2",
    # フランス語
    "フランス語初級A1", "フランス語初級A2", "フランス語初級B1", "フランス語初級B2",
    "フランス語初級A3", "フランス語初級A4", "フランス語初級B3", "フランス語初級B4",
    "フランス語初級SA3", "フランス語初級SA4", "フランス語初級SB3", "フランス語初級SB4",
    "フランス語中級C1", "フランス語中級C2",
    # 中国語
    "中国語初級A1", "中国語初級A2", "中国語初級B1", "中国語初級B2",
    "中国語初級A3", "中国語初級A4", "中国語初級B3", "中国語初級B4",
    "中国語初級SA3", "中国語初級SA4", "中国語初級SB3", "中国語初級SB4",
    "中国語中級C1", "中国語中級C2",
    # ロシア語
    "ロシア語初級A1", "ロシア語初級A2", "ロシア語初級B1", "ロシア語初級B2",
    "ロシア語初級A3", "ロシア語初級A4", "ロシア語初級B3", "ロシア語初級B4",
    "ロシア語中級C1", "ロシア語中級C2",
    # 第二外国語
    "第二外国語（ドイツ語）T1", "第二外国語（ドイツ語）T2", "第二外国語（ドイツ語）T3",
]

# 外国語第3: セミナー形式 (1単位)
GAIGO3 = [
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
KENKO = [
    "健康・スポーツ科学実習基礎",
    "健康・スポーツ科学講義A", "健康・スポーツ科学講義B",
    "健康・スポーツ科学実習1", "健康・スポーツ科学実習2",
]

COURSE_MAP = [
    (KIBAN,  "教養(基盤)"),
    (JINBUN, "教養(人文)"),
    (SHAKAI, "教養(社会)"),
    (SHIZEN, "教養(自然)"),
    (SOGO,   "教養(総合)"),
    (GAIGO1, "教養(外国語第1)"),
    (GAIGO2, "教養(外国語第2)"),
    (GAIGO3, "教養(外国語第3)"),
    (KENKO,  "教養(健康・スポーツ)"),
]


async def insert_courses(session, names, classification):
    if not names:
        return 0
    existing = set(
        (await session.execute(
            select(Subject.name).where(Subject.name.in_(names))
        )).scalars().all()
    )
    count = 0
    for name in names:
        if name in existing:
            continue
        session.add(Subject(
            name=name,
            classification=classification,
            category="教養",
            reading=_reading(name),
        ))
        count += 1
    return count


async def main(env: str):
    load_env(env)
    await init_db()
    async with AsyncSessionLocal() as session:
        totals = {}
        for names, cls in COURSE_MAP:
            n = await insert_courses(session, names, cls)
            totals[cls] = n
        await session.commit()

    print("インポート完了:")
    for cls, n in totals.items():
        if n:
            print(f"  {cls}: +{n}件")
    total = sum(totals.values())
    print(f"  合計: +{total}件")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    args = parser.parse_args()
    if args.env == "prod":
        confirm = input("本番DBにインポートします。よろしいですか？ (yes/no): ")
        if confirm.strip().lower() != "yes":
            print("中止しました")
            sys.exit(0)
    asyncio.run(main(args.env))
