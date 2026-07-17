import asyncio, sys
sys.path.insert(0, r"C:\Users\lemon\shindairaifuhaku\programing files")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\lemon\shindairaifuhaku\programing files\.env.dev")
from database import AsyncSessionLocal
from sqlalchemy import text

OUT = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\kokusai_ningen\kokusai_fresh_check.txt"

candidates = [
    "初年次セミナー",
    "Academic Communication（英）",
    "グローバルイシュー概論",
    "グローバルイシュー演習",
    "情報科学概論",
    "情報リテラシー演習1",
    "情報リテラシー演習2",
    "グローバル文化特別演習Ⅰ",
    "グローバル文化特別演習Ⅱ",
    "発達コミュニティ概論",
    "発達コミュニティ演習1",
    "発達コミュニティ演習2",
    "地域社会学",
    "環境共生学概論1",
    "環境共生学概論2",
    "環境共生学概論3",
    "子ども教育学概論",
    "子ども教育学演習1",
    "子ども教育学演習2",
    "子ども教育学演習3",
    "子ども教育学演習4",
    "観察実習Ⅰ",
    "観察実習Ⅱ",
    "初等教育事前・事後指導",
    "初等教育実地研究",
    "教職実践演習（幼・小）",
]

async def main():
    lines = []
    async with AsyncSessionLocal() as s:
        lines.append("=== credit_requirements/registration_caps/required_subjects 国際人間 現状 (再確認) ===")
        for tbl in ["credit_requirements", "registration_caps", "required_subjects"]:
            r = await s.execute(text(f"SELECT count(*) FROM {tbl} WHERE faculty LIKE '%国際人間%'"))
            lines.append(f"{tbl}: count={r.scalar()}")

        lines.append("")
        lines.append("=== candidate subjects: CourseSection/instructor counts (fresh) ===")
        for name in candidates:
            r = await s.execute(text("""
                SELECT s.id, s.name, s.credits, s.term_type,
                       (SELECT count(*) FROM course_sections cs WHERE cs.subject_id = s.id) AS section_count,
                       (SELECT count(DISTINCT cs.instructor_id) FROM course_sections cs WHERE cs.subject_id = s.id) AS instructor_count
                FROM subjects s
                WHERE s.faculty = '国際人間科学部' AND s.name = :name
            """), {"name": name})
            rows = r.fetchall()
            if not rows:
                lines.append(f"[NOT FOUND] {name}")
            for row in rows:
                lines.append(f"{name}: id={row[0]} credits={row[2]} term={row[3]} sections={row[4]} instructors={row[5]}")

        lines.append("")
        lines.append("=== 卒業研究 variants fresh check ===")
        r = await s.execute(text("""
            SELECT s.name, count(cs.id) as sc
            FROM subjects s LEFT JOIN course_sections cs ON cs.subject_id = s.id
            WHERE s.faculty = '国際人間科学部' AND s.name LIKE '卒業研究%'
            GROUP BY s.name ORDER BY s.name
        """))
        for row in r.fetchall():
            lines.append(f"{row[0]}: sections={row[1]}")

        lines.append("")
        lines.append("=== syllabi existence check (year=2026) for candidates ===")
        for name in candidates:
            r = await s.execute(text("""
                SELECT count(*) FROM syllabi syl
                JOIN course_sections cs ON cs.id = syl.course_section_id
                JOIN subjects s ON s.id = cs.subject_id
                WHERE s.faculty = '国際人間科学部' AND s.name = :name AND syl.year = 2026
            """), {"name": name})
            lines.append(f"{name}: syllabi(year=2026)={r.scalar()}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

asyncio.run(main())
