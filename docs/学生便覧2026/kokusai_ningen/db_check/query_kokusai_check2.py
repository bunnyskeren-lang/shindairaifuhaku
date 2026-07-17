import asyncio, sys
sys.path.insert(0, r"C:\Users\lemon\shindairaifuhaku\programing files")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\lemon\shindairaifuhaku\programing files\.env.dev")
from database import AsyncSessionLocal
from sqlalchemy import text

OUT = r"C:\Users\lemon\shindairaifuhaku\docs\学生便覧2026\kokusai_ningen\kokusai_fresh_check2.txt"

async def main():
    lines = []
    async with AsyncSessionLocal() as s:
        lines.append("=== 再確認: credit_requirements/registration_caps/required_subjects 国際人間 (直前と同じか) ===")
        for tbl in ["credit_requirements", "registration_caps", "required_subjects"]:
            r = await s.execute(text(f"SELECT count(*) FROM {tbl} WHERE faculty LIKE '%国際人間%'"))
            lines.append(f"{tbl}: count={r.scalar()}")

        lines.append("")
        lines.append("=== 既存category_id一覧（衝突チェック用） ===")
        r = await s.execute(text("SELECT category_id, faculty, department FROM credit_requirements ORDER BY category_id"))
        for row in r.fetchall():
            lines.append(f"{row[0]} | faculty={row[1]} department={row[2]}")

        lines.append("")
        lines.append("=== kokusai_/gcul_/dcom_/ekyo_/kkyo_ prefix 衝突チェック ===")
        r = await s.execute(text("""
            SELECT category_id FROM credit_requirements
            WHERE category_id LIKE 'kokusai_%' OR category_id LIKE 'gcul_%'
               OR category_id LIKE 'dcom_%' OR category_id LIKE 'ekyo_%' OR category_id LIKE 'kkyo_%'
        """))
        rows = r.fetchall()
        lines.append(f"衝突件数: {len(rows)}")
        for row in rows:
            lines.append(row[0])

        lines.append("")
        lines.append("=== 共通専門基礎科目 (環境共生学科向け) faculty確認 ===")
        r = await s.execute(text("""
            SELECT DISTINCT faculty, classification FROM subjects
            WHERE name IN ('基礎無機化学１','物理学入門','生物学概論Ａ１','基礎地学１')
        """))
        for row in r.fetchall():
            lines.append(f"{row}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

asyncio.run(main())
