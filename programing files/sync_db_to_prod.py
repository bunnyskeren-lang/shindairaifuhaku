# -*- coding: utf-8 -*-
"""
dev → 本番 DB の同期スクリプト
同期対象テーブル（この6テーブルのみ）:
  credit_requirements, display_orders, subjects, instructors, course_sections, subject_credit_categories

絶対に同期しないテーブル:
  reviews, message_logs, user_profiles, user_activity, error_logs,
  push_subscriptions, richmenu_taps, user_syllabi, syllabi, schedules 等

UPSERTに加えて、本番のみに存在する行（devで削除・変更済みの行）も削除する。
ただし display_orders 以外（subjects/instructors/course_sections）は、
course_sections経由でsyllabi（時間割登録データ）やreviewsが紐づく場合、
CASCADE/RESTRICTでユーザーデータを巻き込む恐れがあるため削除せず、
「KEEP(要確認)」としてログ表示するのみに留める（手動での判断・対応が必要）。

実行方法（programing files/ から実行）:
  python -X utf8 sync_db_to_prod.py
"""
import asyncio
import json
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
        # ── 0. credit_requirements: UPSERT by category_id ───────────────────────
        # subject_credit_categories.category_id が参照するFK先。先にUPSERTしておかないと
        # 後段のsubject_credit_categories INSERTがFK違反になる（2026-07-16に実際に発生）。
        # 孤児削除はsubject_credit_categoriesの洗い替え後（末尾）に行う。
        cr_rows = await dev.fetch(
            "SELECT category_id, label, group_name, sort_order, required_credits, "
            "note, faculty, department, combined_of, max_credits "
            "FROM credit_requirements ORDER BY category_id"
        )
        async with prod.transaction():
            await prod.executemany(
                """
                INSERT INTO credit_requirements
                  (category_id, label, group_name, sort_order, required_credits,
                   note, faculty, department, combined_of, max_credits)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10)
                ON CONFLICT (category_id) DO UPDATE SET
                  label=EXCLUDED.label, group_name=EXCLUDED.group_name,
                  sort_order=EXCLUDED.sort_order, required_credits=EXCLUDED.required_credits,
                  note=EXCLUDED.note, faculty=EXCLUDED.faculty, department=EXCLUDED.department,
                  combined_of=EXCLUDED.combined_of, max_credits=EXCLUDED.max_credits
                """,
                [(r["category_id"], r["label"], r["group_name"], r["sort_order"], r["required_credits"],
                  r["note"], r["faculty"], r["department"],
                  json.dumps(r["combined_of"]) if r["combined_of"] is not None else None,
                  r["max_credits"])
                 for r in cr_rows]
            )
        print(f"credit_requirements: {len(cr_rows)}件 upsert")

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

        # display_orders: 本番のみに存在する行を削除（FK依存なし・削除しても安全）
        dev_do_keys = {(r["kind"], r["name"], r["faculty"]) for r in cls_rows}
        prod_do_rows = await prod.fetch("SELECT id, kind, name, faculty FROM display_orders")
        orphan_do = [r for r in prod_do_rows if (r["kind"], r["name"], r["faculty"]) not in dev_do_keys]
        if orphan_do:
            async with prod.transaction():
                await prod.executemany(
                    "DELETE FROM display_orders WHERE id=$1", [(r["id"],) for r in orphan_do]
                )
        print(f"display_orders: 本番のみに存在した{len(orphan_do)}件を削除")

        # ── 2. subjects: UPSERT by (name, faculty) ─────────────────────────────
        # id直接UPSERTだと、devとprodのシーケンスが独立して進むため、本番管理画面から
        # 個別追加された科目のidとdevの新規科目idが偶然一致すると、無関係な科目が
        # 無警告に上書きされる事故が起こりうる（instructorsと同じく名前で名寄せする）。
        # 「卒業研究」等、学部をまたいで同名科目が実在するため、名寄せキーはnameだけでなく
        # (name, faculty)の組で行う（DB側もuq_subjects_name_facultyに変更済み）。
        # 注意: faculty=NULLの行（共通専門基礎科目など2件）はPostgresの仕様上NULL同士が
        # 一致しないため ON CONFLICT が発火せず、再同期のたびにINSERTが重複しうる。
        # 該当は少数のためひとまず許容し、見つかった場合は手動で重複解消する。
        # course_sections/subject_credit_categories は下でdev id→prod idに変換して同期する。
        subj_rows = await dev.fetch(
            "SELECT id, name, reading, faculty, classification, "
            "category, senmon_group, sort_order, term_type, credits "
            "FROM subjects ORDER BY id"
        )
        dup_keys = [key for key, cnt in
                    Counter((r["name"], r["faculty"]) for r in subj_rows).items() if cnt > 1]
        if dup_keys:
            raise RuntimeError(f"dev subjects.(name,faculty) に重複があるため同期を中止しました: {dup_keys[:10]}")

        async with prod.transaction():
            await prod.executemany(
                """
                INSERT INTO subjects
                  (name, reading, faculty, classification,
                   category, senmon_group, sort_order, term_type, credits)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (name, faculty) DO UPDATE SET
                  reading=EXCLUDED.reading,
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

        # prod の subject (name,faculty)→id マッピングを取得（devとprodでidが異なりうる）
        prod_subj_map: dict[tuple, int] = {
            (r["name"], r["faculty"]): r["id"]
            for r in await prod.fetch("SELECT id, name, faculty FROM subjects")
        }
        dev_subj_id_to_prod_id: dict[int, int] = {
            r["id"]: prod_subj_map[(r["name"], r["faculty"])]
            for r in subj_rows if (r["name"], r["faculty"]) in prod_subj_map
        }

        # subjects: 本番のみに存在する科目を削除。ただし course_sections 経由で
        # syllabi（時間割データ）や reviews が紐づく場合は、CASCADE/RESTRICTで
        # ユーザーデータを巻き込む恐れがあるため削除せず一覧表示のみに留める。
        dev_subj_keys = {(r["name"], r["faculty"]) for r in subj_rows}
        orphan_subjects = [
            (name_faculty, pid) for name_faculty, pid in prod_subj_map.items()
            if name_faculty not in dev_subj_keys
        ]
        deleted_subj = 0
        kept_subj = []
        for (name, faculty), pid in orphan_subjects:
            cs_ids = [x["id"] for x in await prod.fetch(
                "SELECT id FROM course_sections WHERE subject_id=$1", pid)]
            if cs_ids:
                syllabi_count = await prod.fetchval(
                    "SELECT COUNT(*) FROM syllabi WHERE course_section_id = ANY($1::int[])", cs_ids)
                reviews_count = await prod.fetchval(
                    "SELECT COUNT(*) FROM reviews WHERE course_section_id = ANY($1::int[])", cs_ids)
            else:
                syllabi_count = reviews_count = 0
            if syllabi_count or reviews_count:
                kept_subj.append((name, faculty, syllabi_count, reviews_count))
                continue
            await prod.execute("DELETE FROM subjects WHERE id=$1", pid)
            deleted_subj += 1
        print(f"subjects: 本番のみの{len(orphan_subjects)}件中 {deleted_subj}件削除、"
              f"{len(kept_subj)}件は時間割登録/レビューが紐づくため保持")
        for name, faculty, sc, rc in kept_subj:
            print(f"  KEEP(要確認): {name} ({faculty}) syllabi={sc} reviews={rc}")

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

        # instructors: 本番のみに存在する教員を削除。subjects と同様に
        # course_sections 経由で syllabi/reviews が紐づく場合は保持する。
        dev_instr_names = set(dev_instr_name.values())
        orphan_instr = [
            (name, pid) for name, pid in prod_instr_map.items() if name not in dev_instr_names
        ]
        deleted_instr = 0
        kept_instr = []
        for name, pid in orphan_instr:
            cs_ids = [x["id"] for x in await prod.fetch(
                "SELECT id FROM course_sections WHERE instructor_id=$1", pid)]
            if cs_ids:
                syllabi_count = await prod.fetchval(
                    "SELECT COUNT(*) FROM syllabi WHERE course_section_id = ANY($1::int[])", cs_ids)
                reviews_count = await prod.fetchval(
                    "SELECT COUNT(*) FROM reviews WHERE course_section_id = ANY($1::int[])", cs_ids)
            else:
                syllabi_count = reviews_count = 0
            if syllabi_count or reviews_count:
                kept_instr.append((name, syllabi_count, reviews_count))
                continue
            await prod.execute("DELETE FROM instructors WHERE id=$1", pid)
            deleted_instr += 1
        print(f"instructors: 本番のみの{len(orphan_instr)}件中 {deleted_instr}件削除、"
              f"{len(kept_instr)}件は時間割登録/レビューが紐づくため保持")
        for name, sc, rc in kept_instr:
            print(f"  KEEP(要確認): {name} syllabi={sc} reviews={rc}")

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

        # course_sections: 本番のみに存在する組み合わせを削除
        # （subjects/instructors削除で対応済みのものはCASCADEで既に消えているため、
        #  ここに残るのは「科目・教員は両方存在するが組み合わせだけがdevに無い」ケース）。
        # syllabi/reviewsが紐づく場合は保持する。
        desired_cs_keys = {(s, i) for s, i, _ in cs_params}
        prod_cs_rows = await prod.fetch("SELECT id, subject_id, instructor_id FROM course_sections")
        orphan_cs = [r for r in prod_cs_rows if (r["subject_id"], r["instructor_id"]) not in desired_cs_keys]
        deleted_cs = 0
        kept_cs = 0
        for r in orphan_cs:
            syllabi_count = await prod.fetchval(
                "SELECT COUNT(*) FROM syllabi WHERE course_section_id=$1", r["id"])
            reviews_count = await prod.fetchval(
                "SELECT COUNT(*) FROM reviews WHERE course_section_id=$1", r["id"])
            if syllabi_count or reviews_count:
                kept_cs += 1
                continue
            await prod.execute("DELETE FROM course_sections WHERE id=$1", r["id"])
            deleted_cs += 1
        print(f"course_sections: 本番のみの{len(orphan_cs)}件中 {deleted_cs}件削除、"
              f"{kept_cs}件は時間割登録/レビューが紐づくため保持")

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

        # credit_requirements: 本番のみに存在するカテゴリを削除。
        # subject_credit_categoriesを上で洗い替え済みのため、RESTRICT違反は起きない。
        dev_cr_ids = {r["category_id"] for r in cr_rows}
        prod_cr_ids = {r["category_id"] for r in await prod.fetch("SELECT category_id FROM credit_requirements")}
        orphan_cr = prod_cr_ids - dev_cr_ids
        if orphan_cr:
            async with prod.transaction():
                await prod.executemany(
                    "DELETE FROM credit_requirements WHERE category_id=$1",
                    [(cid,) for cid in orphan_cr]
                )
        print(f"credit_requirements: 本番のみに存在した{len(orphan_cr)}件を削除")

    finally:
        await dev.close()
        await prod.close()

    print("\nDB同期完了")

if __name__ == "__main__":
    asyncio.run(main())
