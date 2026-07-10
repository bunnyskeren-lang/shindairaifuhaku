# -*- coding: utf-8 -*-
"""
dev → 本番 DB の同期スクリプト
同期対象テーブル（この5テーブルのみ）:
  display_orders, subjects, instructors, course_sections, subject_credit_categories

絶対に同期しないテーブル:
  reviews, message_logs, user_profiles, user_activity, error_logs,
  push_subscriptions, richmenu_taps, user_syllabi, syllabi, schedules 等

実行方法（programing files/ から実行）:
  python -X utf8 sync_db_to_prod.py
"""
import asyncio
import os
import ssl
import sys
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8")
import asyncpg
from dotenv import load_dotenv

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_SCRIPT_DIR, ".env"), override=True)

DEV_URL = os.environ.get("DEV_DATABASE_URL", "")
PROD_URL = os.environ.get("DATABASE_URL", "")
if not DEV_URL or not PROD_URL:
    print("programing files/.env に DEV_DATABASE_URL / DATABASE_URL が設定されていません。")
    sys.exit(1)

def _ssl():
    """ルートのcore/db_ssl.pyと同じくSupabaseのCA証明書をpinningして検証する。
    DISABLE_SSL_VERIFY=1 で（緊急時の切り戻し用に）検証を無効化できる。"""
    if os.environ.get("DISABLE_SSL_VERIFY", "").lower() in ("1", "true", "yes"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    ca_path = os.path.join(_SCRIPT_DIR, "..", "certs", "supabase-ca.crt")
    return ssl.create_default_context(cafile=ca_path)

async def main():
    confirm = input("本番DBをdev DBの内容で上書きします。よろしいですか？ (yes/no): ")
    if confirm.strip().lower() != "yes":
        print("キャンセルしました。")
        return

    dev  = await asyncpg.connect(DEV_URL,  ssl=_ssl())
    prod = await asyncpg.connect(PROD_URL, ssl=_ssl())

    try:
        # ── 1. display_orders: UPSERT by (kind, name, faculty) ──────────────────
        # 分類/学部/単位要件グループの並び順マスタ。文字列一致で参照するのみで FK 依存はない。
        # 以前はid直接コピー+DELETE&INSERTだったが、本番側のオートインクリメントの
        # シーケンス値が更新されないまま既存idと同じ値の行が入るため、直後に管理画面から
        # 新規追加すると id衝突でエラーになりうる不具合があった。他テーブルと同様に
        # (kind, name, faculty)のUNIQUE制約を使った名前ベースUPSERTに統一する。
        cls_rows = await dev.fetch(
            "SELECT kind, name, sort_order, parent_group, faculty FROM display_orders ORDER BY id"
        )
        async with prod.transaction():
            if cls_rows:
                await prod.executemany(
                    """
                    INSERT INTO display_orders (kind, name, sort_order, parent_group, faculty)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (kind, name, faculty) DO UPDATE SET
                      sort_order=EXCLUDED.sort_order, parent_group=EXCLUDED.parent_group
                    """,
                    [(r["kind"], r["name"], r["sort_order"], r["parent_group"], r["faculty"])
                     for r in cls_rows]
                )
        print(f"display_orders: {len(cls_rows)}件 upsert")

        # ── 2. subjects: UPSERT by name ────────────────────────────────────────
        # id直接UPSERTだと、devとprodのシーケンスが独立して進むため、本番管理画面から
        # 個別追加された科目のidとdevの新規科目idが偶然一致すると、無関係な科目が
        # 無警告に上書きされる事故が起こりうる（instructorsと同じくnameで名寄せする）。
        # course_sections/subject_credit_categories は下でdev id→prod idに変換して同期する。
        subj_rows = await dev.fetch(
            "SELECT id, name, reading, faculty, classification, "
            "category, senmon_group, sort_order, term_type, credits "
            "FROM subjects ORDER BY id"
        )
        dup_names = [name for name, cnt in
                     Counter(r["name"] for r in subj_rows).items() if cnt > 1]
        if dup_names:
            raise RuntimeError(f"dev subjects.name に重複があるため同期を中止しました: {dup_names[:10]}")

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

        # prod の subject name→id マッピングを取得（devとprodでidが異なりうる）
        prod_subj_map: dict[str, int] = {
            r["name"]: r["id"]
            for r in await prod.fetch("SELECT id, name FROM subjects")
        }
        dev_subj_id_to_prod_id: dict[int, int] = {
            r["id"]: prod_subj_map[r["name"]]
            for r in subj_rows if r["name"] in prod_subj_map
        }

        # ── 3. instructors: UPSERT by name ────────────────────────────────────
        instr_rows = await dev.fetch("SELECT id, name, sort_order FROM instructors ORDER BY id")
        async with prod.transaction():
            await prod.executemany(
                "INSERT INTO instructors (name, sort_order) VALUES ($1, $2) "
                "ON CONFLICT (name) DO UPDATE SET sort_order = EXCLUDED.sort_order",
                [(r["name"], r["sort_order"]) for r in instr_rows]
            )
        print(f"instructors: {len(instr_rows)}件 upsert")

        # prod の instructor name→id マッピングを取得（名前が同じでもIDが異なりうる）
        prod_instr_map: dict[str, int] = {
            r["name"]: r["id"]
            for r in await prod.fetch("SELECT id, name FROM instructors")
        }
        dev_instr_name: dict[int, str] = {r["id"]: r["name"] for r in instr_rows}

        # ── 4. course_sections: UPSERT by (subject_id, instructor_id) ─────────
        # subject_id・instructor_id ともに prod での ID に変換する必要あり
        cs_rows = await dev.fetch(
            "SELECT id, subject_id, instructor_id, syllabus_url "
            "FROM course_sections ORDER BY id"
        )
        cs_params = []
        skipped = 0
        for r in cs_rows:
            prod_subj_id = dev_subj_id_to_prod_id.get(r["subject_id"])
            if prod_subj_id is None:
                print(f"  WARNING: subject_id {r['subject_id']} が prod に見つかりません（course_section {r['id']} をスキップ）")
                skipped += 1
                continue
            instr_name = dev_instr_name.get(r["instructor_id"])
            prod_instr_id = prod_instr_map.get(instr_name) if instr_name else None
            if prod_instr_id is None:
                print(f"  WARNING: instructor '{instr_name}' が prod に見つかりません（course_section {r['id']} をスキップ）")
                skipped += 1
                continue
            cs_params.append((prod_subj_id, prod_instr_id, r["syllabus_url"]))

        async with prod.transaction():
            await prod.executemany(
                """
                INSERT INTO course_sections (subject_id, instructor_id, syllabus_url)
                VALUES ($1, $2, $3)
                ON CONFLICT (subject_id, instructor_id) DO UPDATE SET
                  syllabus_url=EXCLUDED.syllabus_url
                """,
                cs_params
            )
        print(f"course_sections: {len(cs_params)}件 upsert, {skipped}件スキップ")

        # ── 5. subject_credit_categories ──────────────────────────────────────
        # ユーザーデータへの依存なし → DELETE + INSERT で上書き（subject_id は prod ID に変換）
        scc_rows = await dev.fetch(
            "SELECT subject_id, category_id, credits FROM subject_credit_categories ORDER BY id"
        )
        scc_params = []
        scc_skipped = 0
        for r in scc_rows:
            prod_subj_id = dev_subj_id_to_prod_id.get(r["subject_id"])
            if prod_subj_id is None:
                scc_skipped += 1
                continue
            scc_params.append((prod_subj_id, r["category_id"], r["credits"]))
        async with prod.transaction():
            await prod.execute("DELETE FROM subject_credit_categories")
            if scc_params:
                await prod.executemany(
                    "INSERT INTO subject_credit_categories (subject_id, category_id, credits) "
                    "VALUES ($1, $2, $3)",
                    scc_params
                )
        print(f"subject_credit_categories: {len(scc_params)}件, {scc_skipped}件スキップ")

    finally:
        await dev.close()
        await prod.close()

    print("\nDB同期完了")

if __name__ == "__main__":
    asyncio.run(main())
