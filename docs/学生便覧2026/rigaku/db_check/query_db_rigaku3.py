# -*- coding: utf-8 -*-
import asyncio, sys
sys.path.insert(0, r"C:\Users\lemon\shindairaifuhaku\programing files")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\lemon\shindairaifuhaku\programing files\.env.dev")
from database import AsyncSessionLocal
from sqlalchemy import text

OUT = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\rigaku\rigaku_db_check2.txt"

async def main():
    lines = []
    async with AsyncSessionLocal() as s:
        # search for 特別研究-like subjects across all faculties
        r = await s.execute(text("""
            SELECT id, name, faculty, classification, credits FROM subjects
            WHERE name LIKE '%特別研究%' ORDER BY faculty, name
        """))
        lines.append("=== subjects LIKE 特別研究 (all faculties) ===")
        for row in r:
            lines.append(str(tuple(row)))

        # kyoyo-kyoiku-in common basic subjects check (微分積分, 線形代数, etc for rigaku)
        r = await s.execute(text("""
            SELECT id, name, faculty, classification, credits FROM subjects
            WHERE name IN ('微分積分1','微分積分2','微分積分3','微分積分4','線形代数1','線形代数2','線形代数3','線形代数4','物理学実験','化学実験1','化学実験2')
            ORDER BY name, faculty
        """))
        lines.append("\n=== common-basic candidate subjects (微分積分/線形代数/物理学実験/化学実験1・2) ===")
        for row in r:
            lines.append(str(tuple(row)))

        # 数学講究 detail check
        r = await s.execute(text("""
            SELECT id, name, faculty, classification, credits FROM subjects WHERE name = '数学講究'
        """))
        lines.append("\n=== 数学講究 detail ===")
        for row in r:
            lines.append(str(tuple(row)))

        # course_sections + instructors count for a few key subjects (case3 check: many instructors?)
        for name in ["数学講究", "特別研究A", "特別研究B", "初年次セミナー（化学科）", "初年次セミナー（生物学科）", "初年次セミナー（惑星学科）"]:
            r = await s.execute(text("""
                SELECT sub.id, sub.name, count(cs.id) as section_count
                FROM subjects sub LEFT JOIN course_sections cs ON cs.subject_id = sub.id
                WHERE sub.name = :name
                GROUP BY sub.id, sub.name
            """), {"name": name})
            lines.append(f"\n=== course_section count for {name} ===")
            for row in r:
                lines.append(str(tuple(row)))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")

asyncio.run(main())
