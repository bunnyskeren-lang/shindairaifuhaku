# -*- coding: utf-8 -*-
"""
dev → 本番 DB の同期スクリプト（科目名・教員のみ）
同期対象テーブル（この2テーブルのみ）:
  subjects, instructors

実行方法（programing files/ から実行）:
  python -X utf8 sync_subjects_instructors_to_prod.py
"""
import asyncio
import ssl
import sys
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8")
import asyncpg

DEV_URL  = "postgresql://postgres.ofsvkcptzngbsxtdbqzj:Developerr6363st@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"
PROD_URL = "postgresql://postgres.sagubqrhjnzrtcvlmzqy:Linebot6363st@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres"

def _ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

async def main():
    confirm = input("本番DBのsubjects/instructorsをdev DBの内容で上書きします。よろしいですか？ (yes/no): ")
    if confirm.strip().lower() != "yes":
        print("キャンセルしました。")
        return

    dev  = await asyncpg.connect(DEV_URL,  ssl=_ssl())
    prod = await asyncpg.connect(PROD_URL, ssl=_ssl())

    try:
        # ── subjects: UPSERT by name ────────────────────────────────────────
        # id直接UPSERTだと、devとprodのシーケンスが独立して進むため、本番管理画面から
        # 個別追加された科目のidとdevの新規科目idが偶然一致すると、無関係な科目が
        # 無警告に上書きされる事故が起こりうる（instructorsと同じくnameで名寄せする）。
        subj_rows = await dev.fetch(
            "SELECT id, name, reading, faculty, classification, "
            "category, senmon_group, sort_order, term_type, credits "
            "FROM subjects ORDER BY id"
        )
        dup_names = [name for name, cnt in
                     Counter(r["name"] for r in subj_rows).items() if cnt > 1]
        if dup_names:
            print(f"ERROR: dev subjects.name に重複があるため同期を中止しました: {dup_names[:10]}")
            return

        async with prod.transaction():
            await prod.executemany(
                """
                INSERT INTO subjects
                  (name, reading, faculty, classification,
                   category, senmon_group, sort_order, term_type, credits)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (name) DO UPDATE SET
                  reading=EXCLUDED.reading, faculty=EXCLUDED.faculty,
                  classification=EXCLUDED.classification,
                  category=EXCLUDED.category, senmon_group=EXCLUDED.senmon_group,
                  sort_order=EXCLUDED.sort_order,
                  term_type=EXCLUDED.term_type, credits=EXCLUDED.credits
                """,
                [(r["name"], r["reading"], r["faculty"],
                  r["classification"], r["category"], r["senmon_group"], r["sort_order"],
                  r["term_type"], r["credits"])
                 for r in subj_rows]
            )
        print(f"subjects: {len(subj_rows)}件 upsert")

        # ── instructors: UPSERT by name ────────────────────────────────────
        instr_rows = await dev.fetch("SELECT id, name, sort_order FROM instructors ORDER BY id")
        async with prod.transaction():
            await prod.executemany(
                "INSERT INTO instructors (name, sort_order) VALUES ($1, $2) "
                "ON CONFLICT (name) DO UPDATE SET sort_order = EXCLUDED.sort_order",
                [(r["name"], r["sort_order"]) for r in instr_rows]
            )
        print(f"instructors: {len(instr_rows)}件 upsert")

    finally:
        await dev.close()
        await prod.close()

    print("\n同期完了（subjects/instructorsのみ）")

if __name__ == "__main__":
    asyncio.run(main())
