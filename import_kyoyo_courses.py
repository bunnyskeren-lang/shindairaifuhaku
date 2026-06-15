"""
教養科目を一括インポートするスクリプト。
実行: python import_kyoyo_courses.py
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from database import init_db, AsyncSessionLocal
from models import Course

try:
    import pykakasi as _pykakasi
    _kks = _pykakasi.kakasi()
    def _reading(text: str) -> str:
        result = _kks.convert(text)
        hira = ''.join(item.get('hira', '') for item in result)
        roma = ''.join(item.get('hepburn', '') for item in result)
        return f"{hira} {roma}".lower().strip()
except Exception:
    def _reading(text: str) -> str:
        return ""


def expand_ab(names: list[str]) -> list[str]:
    result = []
    for name in names:
        result.append(f"{name}A")
        result.append(f"{name}B")
    return result


def expand_variants(names: list[str], suffixes: str) -> list[str]:
    result = []
    for name in names:
        for s in suffixes:
            result.append(f"{name}{s}")
    return result


def expand_abcd(names: list[str]) -> list[str]:
    return expand_variants(names, 'ABCD')


# ─── 教養(人文) ───
JINBUN_SINGLE = [
    "哲学", "倫理学", "科学技術と倫理",
    "心理学A", "心理学B",
    "教育学A", "教育学B", "教育と人間形成",
    "言語科学A", "言語科学B",
    "文学A", "文学B",
]
JINBUN_AB = expand_ab([
    "芸術と文化", "芸術史", "美術史", "科学史",
    "日本史", "東洋史", "アジア史", "西洋史", "考古学",
])
JINBUN_COURSES = JINBUN_SINGLE + JINBUN_AB

# ─── 教養(社会) ───
SHAKAI_SINGLE = [
    "社会生活と法", "国家と法", "政治と社会", "経済社会の発展",
    "経営学", "社会学", "教育と社会", "地理学",
    "社会思想史", "文化人類学", "越境する文化", "生活環境と技術",
]
SHAKAI_AB = expand_ab([
    "法学", "政治学", "経済学", "現代の経済", "現代社会論",
])
SHAKAI_COURSES = SHAKAI_SINGLE + SHAKAI_AB

# ─── 教養(自然) ───
SHIZEN_SINGLE = [
    "現代物理学が描く世界", "身近な物理法則",
]
SHIZEN_AB = expand_ab([
    "統計学", "物理学", "化学", "生命科学",
    "医学", "保健学", "健康科学", "惑星学", "情報学",
])
SHIZEN_ABCD = expand_abcd(["数学", "生物学"])
SHIZEN_COURSES = SHIZEN_SINGLE + SHIZEN_AB + SHIZEN_ABCD

# ─── 教養(総合) ───
SOGO_SINGLE = [
    "ESD論(持続可能な社会づくり)基礎",
    "海への誘い", "瀬戸内海学入門", "阪神・淡路大震災と都市の安全",
    "地域社会形成基礎論", "ひょうご神戸学", "日本酒学入門",
    "神戸大学史", "神戸大学研究最前線", "社会基礎学",
    "価値創造論基礎", "アントレプレナーシップ入門",
]
SOGO_AB = expand_ab([
    "ESD論(持続可能な社会づくり)", "環境学入門",
    "ジェンダーとセクシュアリティ", "ボランティアと社会貢献活動",
    "職業と学び-キャリアデザインを考える",
])
SOGO_ABC = expand_variants(["価値創造論", "社会と人権"], 'ABC')
SOGO_COURSES = SOGO_SINGLE + SOGO_AB + SOGO_ABC


async def insert_courses(session, names: list[str], classification: str):
    existing = set(
        (await session.execute(select(Course.name).where(Course.name.in_(names)))).scalars().all()
    )
    count = 0
    for name in names:
        if name in existing:
            continue
        session.add(Course(
            name=name,
            instructor="",
            classification=classification,
            category="教養",
            reading=_reading(name),
        ))
        count += 1
    return count


async def main():
    await init_db()
    async with AsyncSessionLocal() as session:
        n1 = await insert_courses(session, JINBUN_COURSES, "教養(人文)")
        n2 = await insert_courses(session, SHAKAI_COURSES, "教養(社会)")
        n3 = await insert_courses(session, SHIZEN_COURSES, "教養(自然)")
        n4 = await insert_courses(session, SOGO_COURSES, "教養(総合)")
        await session.commit()

    print(f"インポート完了:")
    print(f"  教養(人文): {n1}件")
    print(f"  教養(社会): {n2}件")
    print(f"  教養(自然): {n3}件")
    print(f"  教養(総合): {n4}件")
    print(f"  合計: {n1 + n2 + n3 + n4}件")


if __name__ == "__main__":
    asyncio.run(main())
