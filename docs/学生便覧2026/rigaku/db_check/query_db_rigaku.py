# -*- coding: utf-8 -*-
import asyncio, sys
sys.path.insert(0, r"C:\Users\lemon\shindairaifuhaku\programing files")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\lemon\shindairaifuhaku\programing files\.env.dev")
from database import AsyncSessionLocal
from sqlalchemy import text

OUT = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\rigaku\rigaku_db_result.txt"

async def main():
    lines = []
    async with AsyncSessionLocal() as s:
        # 1. faculty pattern check
        r = await s.execute(text("SELECT DISTINCT faculty, classification FROM subjects WHERE faculty LIKE '%理学部%' ORDER BY faculty, classification"))
        lines.append("=== subjects faculty/classification distinct (理学部) ===")
        for row in r:
            lines.append(str(row))

        r = await s.execute(text("SELECT faculty, count(*) FROM subjects WHERE faculty LIKE '%理学部%' GROUP BY faculty ORDER BY faculty"))
        lines.append("\n=== count by faculty ===")
        for row in r:
            lines.append(str(row))

        # 2. credit_requirements / registration_caps / required_subjects existing rows for rigaku
        for tbl in ["credit_requirements", "registration_caps", "required_subjects"]:
            r = await s.execute(text(f"SELECT DISTINCT faculty, department FROM {tbl} WHERE faculty LIKE '%理学部%' ORDER BY faculty, department"))
            lines.append(f"\n=== {tbl} rows LIKE 理学部 ===")
            found = False
            for row in r:
                found = True
                lines.append(str(row))
            if not found:
                lines.append("(no rows)")

        # 3. all distinct faculty/department across the 3 tables (per exec plan sec3 sql)
        for tbl in ["credit_requirements", "registration_caps", "required_subjects"]:
            r = await s.execute(text(f"SELECT DISTINCT faculty, department FROM {tbl} ORDER BY faculty, department"))
            lines.append(f"\n=== {tbl} ALL distinct faculty/department ===")
            for row in r:
                lines.append(str(row))

        # 4. category_id existing list (naming collision check)
        r = await s.execute(text("SELECT DISTINCT category_id FROM credit_requirements ORDER BY category_id"))
        lines.append("\n=== existing category_id list ===")
        for row in r:
            lines.append(str(row))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")

asyncio.run(main())
