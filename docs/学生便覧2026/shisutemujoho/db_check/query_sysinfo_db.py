import asyncio, sys
sys.path.insert(0, r"C:\Users\lemon\shindairaifuhaku\programing files")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\lemon\shindairaifuhaku\programing files\.env.dev")
from database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    out = []
    async with AsyncSessionLocal() as s:
        r = await s.execute(text("""
            SELECT s.id, s.name, s.faculty, s.classification, s.credits, s.term_type,
                   (SELECT count(*) FROM course_sections cs WHERE cs.subject_id = s.id) as n_sections
            FROM subjects s
            WHERE s.faculty = 'システム情報学部'
            ORDER BY s.name
        """))
        rows = r.fetchall()
        out.append(f"subjects count: {len(rows)}")
        for row in rows:
            out.append(str(tuple(row)))

        r2 = await s.execute(text("SELECT DISTINCT faculty, department FROM credit_requirements ORDER BY faculty, department"))
        out.append("\n--- credit_requirements distinct faculty/department ---")
        for row in r2.fetchall():
            out.append(str(tuple(row)))

        r3 = await s.execute(text("SELECT DISTINCT faculty, department FROM registration_caps ORDER BY faculty, department"))
        out.append("\n--- registration_caps distinct faculty/department ---")
        for row in r3.fetchall():
            out.append(str(tuple(row)))

        r4 = await s.execute(text("SELECT DISTINCT faculty, department FROM required_subjects ORDER BY faculty, department"))
        out.append("\n--- required_subjects distinct faculty/department ---")
        for row in r4.fetchall():
            out.append(str(tuple(row)))

        r5 = await s.execute(text("SELECT category_id FROM credit_requirements ORDER BY category_id"))
        out.append("\n--- existing credit_requirements category_id (all faculties, for naming collision check) ---")
        for row in r5.fetchall():
            out.append(str(row[0]))

    with open(r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\sysinfo\sysinfo_db_query_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out))

asyncio.run(main())
