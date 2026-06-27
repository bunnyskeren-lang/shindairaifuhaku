# -*- coding: utf-8 -*-
"""
dev → 本番 DB の同期スクリプト
同期対象テーブル（この5テーブルのみ）:
  classification_orders, subjects, instructors, course_sections, subject_credit_categories

絶対に同期しないテーブル:
  reviews, message_logs, user_profiles, user_activity, error_logs,
  push_subscriptions, richmenu_taps, user_syllabi, syllabi, schedules 等

実行方法（programing files/ から実行）:
  python -X utf8 sync_db_to_prod.py
"""
import asyncio, ssl, sys
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
    confirm = input("本番DBをdev DBの内容で上書きします。よろしいですか？ (yes/no): ")
    if confirm.strip().lower() != "yes":
        print("キャンセルしました。")
        return

    dev  = await asyncpg.connect(DEV_URL,  ssl=_ssl())
    prod = await asyncpg.connect(PROD_URL, ssl=_ssl())

    try:
        # ── 1. classification_orders ──────────────────────────────────────────
        # subjects.classification_id は ON DELETE SET NULL なので TRUNCATE しても安全
        cls_rows = await dev.fetch(
            "SELECT id, name, sort_order, parent_group, faculty FROM classification_orders ORDER BY id"
        )
        async with prod.transaction():
            await prod.execute("DELETE FROM classification_orders")
            if cls_rows:
                await prod.executemany(
                    "INSERT INTO classification_orders (id, name, sort_order, parent_group, faculty) "
                    "VALUES ($1, $2, $3, $4, $5)",
                    [(r["id"], r["name"], r["sort_order"], r["parent_group"], r["faculty"])
                     for r in cls_rows]
                )
        print(f"classification_orders: {len(cls_rows)}件")

        # ── 2. subjects: UPSERT by id ─────────────────────────────────────────
        # course_sections/reviews が subjects に CASCADE 依存するため TRUNCATE せず UPSERT
        subj_rows = await dev.fetch(
            "SELECT id, name, reading, faculty, classification_id, classification, "
            "category, senmon_group, sort_order, term, term_type, credits "
            "FROM subjects ORDER BY id"
        )
        async with prod.transaction():
            await prod.executemany(
                """
                INSERT INTO subjects
                  (id, name, reading, faculty, classification_id, classification,
                   category, senmon_group, sort_order, term, term_type, credits)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                ON CONFLICT (id) DO UPDATE SET
                  name=EXCLUDED.name, reading=EXCLUDED.reading, faculty=EXCLUDED.faculty,
                  classification_id=EXCLUDED.classification_id,
                  classification=EXCLUDED.classification,
                  category=EXCLUDED.category, senmon_group=EXCLUDED.senmon_group,
                  sort_order=EXCLUDED.sort_order, term=EXCLUDED.term,
                  term_type=EXCLUDED.term_type, credits=EXCLUDED.credits
                """,
                [(r["id"], r["name"], r["reading"], r["faculty"], r["classification_id"],
                  r["classification"], r["category"], r["senmon_group"], r["sort_order"],
                  r["term"], r["term_type"], r["credits"])
                 for r in subj_rows]
            )
        print(f"subjects: {len(subj_rows)}件 upsert")

        # ── 3. instructors: UPSERT by name ────────────────────────────────────
        instr_rows = await dev.fetch("SELECT id, name FROM instructors ORDER BY id")
        async with prod.transaction():
            await prod.executemany(
                "INSERT INTO instructors (name) VALUES ($1) ON CONFLICT (name) DO NOTHING",
                [(r["name"],) for r in instr_rows]
            )
        print(f"instructors: {len(instr_rows)}件 upsert")

        # prod の instructor name→id マッピングを取得（名前が同じでもIDが異なりうる）
        prod_instr_map: dict[str, int] = {
            r["name"]: r["id"]
            for r in await prod.fetch("SELECT id, name FROM instructors")
        }
        dev_instr_name: dict[int, str] = {r["id"]: r["name"] for r in instr_rows}

        # ── 4. course_sections: UPSERT by (subject_id, instructor_id) ─────────
        # subjects は id で UPSERT 済みなので subject_id はそのまま使える
        # instructor_id は prod での ID に変換する必要あり
        cs_rows = await dev.fetch(
            "SELECT id, subject_id, instructor_id, course_type, syllabus_url "
            "FROM course_sections ORDER BY id"
        )
        cs_params = []
        skipped = 0
        for r in cs_rows:
            instr_name = dev_instr_name.get(r["instructor_id"])
            prod_instr_id = prod_instr_map.get(instr_name) if instr_name else None
            if prod_instr_id is None:
                print(f"  WARNING: instructor '{instr_name}' が prod に見つかりません（course_section {r['id']} をスキップ）")
                skipped += 1
                continue
            cs_params.append((r["subject_id"], prod_instr_id, r["course_type"], r["syllabus_url"]))

        async with prod.transaction():
            await prod.executemany(
                """
                INSERT INTO course_sections (subject_id, instructor_id, course_type, syllabus_url)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (subject_id, instructor_id) DO UPDATE SET
                  course_type=EXCLUDED.course_type, syllabus_url=EXCLUDED.syllabus_url
                """,
                cs_params
            )
        print(f"course_sections: {len(cs_params)}件 upsert, {skipped}件スキップ")

        # ── 5. subject_credit_categories ──────────────────────────────────────
        # ユーザーデータへの依存なし → DELETE + INSERT で上書き
        scc_rows = await dev.fetch(
            "SELECT subject_id, category_id, credits FROM subject_credit_categories ORDER BY id"
        )
        async with prod.transaction():
            await prod.execute("DELETE FROM subject_credit_categories")
            if scc_rows:
                await prod.executemany(
                    "INSERT INTO subject_credit_categories (subject_id, category_id, credits) "
                    "VALUES ($1, $2, $3)",
                    [(r["subject_id"], r["category_id"], r["credits"]) for r in scc_rows]
                )
        print(f"subject_credit_categories: {len(scc_rows)}件")

    finally:
        await dev.close()
        await prod.close()

    print("\nDB同期完了")

if __name__ == "__main__":
    asyncio.run(main())
