# -*- coding: utf-8 -*-
import asyncio, sys
sys.path.insert(0, r"C:\Users\lemon\shindairaifuhaku\programing files")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\lemon\shindairaifuhaku\programing files\.env.dev")
from database import AsyncSessionLocal
from sqlalchemy import text

OUT = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\rigaku\rigaku_db_check3.txt"

async def main():
    lines = []
    async with AsyncSessionLocal() as s:
        r = await s.execute(text("""
            SELECT id, name, faculty, classification, credits FROM subjects
            WHERE name LIKE '微分積分%' OR name LIKE '化学実験%' OR name LIKE '線形代数%'
            ORDER BY name
        """))
        lines.append("=== LIKE 微分積分/化学実験/線形代数 ===")
        for row in r:
            lines.append(str(tuple(row)))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("done")

asyncio.run(main())
