# -*- coding: utf-8 -*-
import asyncio, sys
sys.path.insert(0, r"C:\Users\lemon\shindairaifuhaku\programing files")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\lemon\shindairaifuhaku\programing files\.env.dev")
from database import AsyncSessionLocal
from sqlalchemy import text

OUT = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\rigaku\rigaku_db_check4.txt"

IDS = [237,238,574,577,247,248,249,250,244,236,500]

async def main():
    lines = []
    async with AsyncSessionLocal() as s:
        for sid in IDS:
            r = await s.execute(text("""
                SELECT sub.id, sub.name, count(cs.id) as section_count
                FROM subjects sub LEFT JOIN course_sections cs ON cs.subject_id = sub.id
                WHERE sub.id = :sid
                GROUP BY sub.id, sub.name
            """), {"sid": sid})
            for row in r:
                lines.append(str(tuple(row)))
        # also check 数学講究 instructor count & syllabus year
        r = await s.execute(text("""
            SELECT cs.id, i.name, syl.year
            FROM course_sections cs
            JOIN instructors i ON i.id = cs.instructor_id
            LEFT JOIN syllabi syl ON syl.course_section_id = cs.id
            WHERE cs.subject_id = 4410
        """))
        lines.append("\n=== 数学講究 course_section detail ===")
        for row in r:
            lines.append(str(tuple(row)))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")

asyncio.run(main())
