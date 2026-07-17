import asyncio, sys
sys.path.insert(0, r"C:\Users\lemon\shindairaifuhaku\programing files")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\lemon\shindairaifuhaku\programing files\.env.dev")
from database import AsyncSessionLocal
from sqlalchemy import text

names = ["教養とは何か","多言語と多文化の世界","情報基礎","データサイエンス基礎学",
         "システム情報学応用","C3","C³","実験2","卒業研究","数値解析2"]

async def main():
    out = []
    async with AsyncSessionLocal() as s:
        for n in names:
            r = await s.execute(text("""
                SELECT s.id, s.name, s.faculty, s.classification, s.credits,
                       (SELECT count(*) FROM course_sections cs WHERE cs.subject_id = s.id) as n_sections
                FROM subjects s WHERE s.name LIKE :n
            """), {"n": f"%{n}%"})
            rows = r.fetchall()
            out.append(f"=== search '{n}' ===")
            if not rows:
                out.append("  NOT FOUND")
            for row in rows:
                out.append("  " + str(tuple(row)))
    with open(r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\sysinfo\sysinfo_db_query2.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out))

asyncio.run(main())
