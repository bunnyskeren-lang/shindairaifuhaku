import asyncio, sys
sys.path.insert(0, r"C:\Users\lemon\shindairaifuhaku\programing files")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\lemon\shindairaifuhaku\programing files\.env.dev")
from database import AsyncSessionLocal
from sqlalchemy import text

names = ["線形代数","基礎解析","物理基礎","物理数学","確率・統計・情報","データ解析",
"アルゴリズムとデータ構造","システムモデル","最適化理論1","制御工学1","信号処理1",
"数値解析1","コンピュータシステム1","コンピュータシステム2","コンピュータシステム3",
"情報通信工学","人工知能1","人工知能2","システム情報学入門","演習1","演習2","演習3","実験1"]

async def main():
    out = []
    async with AsyncSessionLocal() as s:
        for n in names:
            r = await s.execute(text("""
                SELECT s.id, s.name,
                       cs.id as cs_id, i.name as instructor,
                       syl.id as syl_id, syl.year, syl.target_grades
                FROM subjects s
                JOIN course_sections cs ON cs.subject_id = s.id
                LEFT JOIN instructors i ON i.id = cs.instructor_id
                LEFT JOIN syllabi syl ON syl.course_section_id = cs.id
                WHERE s.faculty = 'システム情報学部' AND s.name = :n
            """), {"n": n})
            rows = r.fetchall()
            out.append(f"=== {n} ===")
            if not rows:
                out.append("  NO MATCH")
            for row in rows:
                out.append("  " + str(tuple(row)))
    with open(r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\sysinfo\sysinfo_db_query3.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out))

asyncio.run(main())
