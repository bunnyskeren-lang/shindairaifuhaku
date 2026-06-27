# -*- coding: utf-8 -*-
"""
旧テーブル削除スクリプト
新スキーマ（subjects/reviews等）に完全移行後、動作確認が取れてから実行すること。

削除対象:
  course_views, category_courses, user_courses, course_slots,
  syllabus_courses, course_instructors, pending_reviews, courses

実行方法:
  python -X utf8 drop_old_tables.py --env dev     # dev DBで確認
  python -X utf8 drop_old_tables.py --env prod    # 本番（要確認プロンプト）
"""
import argparse, asyncio, ssl, sys
import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

DB_URLS = {
    "dev":  "postgresql://postgres.ofsvkcptzngbsxtdbqzj:Developerr6363st@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres",
    "prod": "postgresql://postgres.sagubqrhjnzrtcvlmzqy:Linebot6363st@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres",
}

# 削除順序（FK依存関係に従い子テーブルから削除）
DROP_ORDER = [
    "course_views",
    "category_courses",
    "user_courses",
    "course_slots",
    "syllabus_courses",
    "course_instructors",
    "pending_reviews",
    "courses",
]


async def check_counts(conn: asyncpg.Connection) -> dict:
    counts = {}
    for table in DROP_ORDER:
        try:
            n = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            counts[table] = n
        except Exception:
            counts[table] = "（存在しない）"
    return counts


async def run(env: str):
    url = DB_URLS[env]
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    conn = await asyncpg.connect(dsn=url, ssl=ssl_ctx)
    try:
        print(f"\n【{env.upper()} DB の旧テーブル件数確認】")
        counts = await check_counts(conn)
        for table, n in counts.items():
            print(f"  {table}: {n} 件")

        # 新テーブルの件数も確認
        print("\n【新テーブルの件数確認】")
        for new_table in ["subjects", "instructors", "course_sections", "syllabi", "schedules", "reviews", "course_section_views", "user_syllabi", "subject_credit_categories"]:
            try:
                n = await conn.fetchval(f"SELECT COUNT(*) FROM {new_table}")
                print(f"  {new_table}: {n} 件")
            except Exception as e:
                print(f"  {new_table}: エラー ({e})")

        print()
        if env == "prod":
            ans = input("本番DBの旧テーブルを削除します。本当によいですか？ (yes/no): ")
            if ans.strip().lower() != "yes":
                print("キャンセルしました。")
                return

        print("\n【旧テーブル削除中...】")
        for table in DROP_ORDER:
            if isinstance(counts.get(table), str):
                print(f"  SKIP {table} （テーブルが存在しない）")
                continue
            try:
                await conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
                print(f"  DROP TABLE {table} ✓")
            except Exception as e:
                print(f"  ERROR {table}: {e}")

        print("\n削除完了。")

    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["dev", "prod"], required=True)
    args = parser.parse_args()
    asyncio.run(run(args.env))


if __name__ == "__main__":
    main()
