# -*- coding: utf-8 -*-
import asyncio, sys
sys.path.insert(0, r"C:\Users\lemon\shindairaifuhaku\programing files")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\lemon\shindairaifuhaku\programing files\.env.dev")
from database import AsyncSessionLocal
from sqlalchemy import text

OUT = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\rigaku\rigaku_db_subjects.txt"

FACULTIES = [
    "理学部数学科",
    "理学部物理学科",
    "理学部化学科",
    "理学部生物学科",
    "理学部惑星学科",
]

async def main():
    lines = []
    async with AsyncSessionLocal() as s:
        for fac in FACULTIES:
            lines.append(f"\n===== {fac} =====")
            r = await s.execute(text("""
                SELECT sub.id, sub.name, sub.credits,
                       (SELECT count(*) FROM course_sections cs WHERE cs.subject_id = sub.id) AS cs_count
                FROM subjects sub
                WHERE sub.faculty = :fac
                ORDER BY sub.name
            """), {"fac": fac})
            for row in r:
                lines.append(str(tuple(row)))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")

asyncio.run(main())
