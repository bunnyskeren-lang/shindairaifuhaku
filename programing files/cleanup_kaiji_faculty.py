# -*- coding: utf-8 -*-
"""
「海事科学部」（2021年に「海洋政策科学部」へ改称済みの旧名称）を
学部が関わる全テーブルから調査・削除するスクリプト。

■ 使い方（programing files/ から実行）
  1. まず調査のみ実行して、何が引っかかるか確認する:
       python -X utf8 cleanup_kaiji_faculty.py --env dev
  2. 内容を確認したうえで、実際に削除する:
       python -X utf8 cleanup_kaiji_faculty.py --env dev --execute

■ 削除の安全設計
  - subjects の削除は course_sections → reviews が RESTRICT 制約のため、
    レビューが1件でも紐づいている科目は削除に失敗する（レビューを黙って
    巻き添え削除しない）。失敗した場合はレビューがある科目として一覧表示する。
  - user_profiles（実際のLINEユーザーの登録プロフィール）は自動削除せず、
    「海事科学部」→「海洋政策科学部」への更新のみ行う（実在ユーザーの
    登録抹消を避けるため）。該当者がいた場合は件数を表示する。
  - syllabi/schedules は user_syllabi（学生の時間割登録）に影響しうるため、
    --execute でも自動削除せず、該当があれば件数のみ報告して警告する。
"""
import argparse
import asyncio
import ssl
import sys

sys.stdout.reconfigure(encoding="utf-8")
import asyncpg

DEV_URL = "postgresql://postgres.ofsvkcptzngbsxtdbqzj:Developerr6363st@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"
PROD_URL = "postgresql://postgres.sagubqrhjnzrtcvlmzqy:Linebot6363st@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres"

OLD_FACULTY = "海事科学部"
OLD_DEPT = "海事科学科"
NEW_FACULTY = "海洋政策科学部"
NEW_DEPT = "海洋政策科学科"


def _ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def report(conn) -> dict:
    print("=== 調査結果 ===")

    subj_rows = await conn.fetch(
        "SELECT id, name, faculty FROM subjects WHERE faculty = $1 ORDER BY id", OLD_FACULTY
    )
    print(f"\n[subjects] faculty='{OLD_FACULTY}': {len(subj_rows)}件")
    review_counts = {}
    for r in subj_rows:
        cnt = await conn.fetchval(
            """
            SELECT COUNT(*) FROM reviews rv
            JOIN course_sections cs ON cs.id = rv.course_section_id
            WHERE cs.subject_id = $1
            """,
            r["id"],
        )
        review_counts[r["id"]] = cnt
        print(f"  id={r['id']} name={r['name']!r} reviews={cnt}件")

    do_faculty_rows = await conn.fetch(
        "SELECT id, kind, name, faculty FROM display_orders WHERE name = $1 OR faculty = $1",
        OLD_FACULTY,
    )
    print(f"\n[display_orders] name/faculty='{OLD_FACULTY}': {len(do_faculty_rows)}件")
    for r in do_faculty_rows:
        print(f"  id={r['id']} kind={r['kind']} name={r['name']!r} faculty={r['faculty']!r}")

    cr_rows = await conn.fetch(
        "SELECT category_id, label, faculty FROM credit_requirements WHERE faculty = $1", OLD_FACULTY
    )
    print(f"\n[credit_requirements] faculty='{OLD_FACULTY}': {len(cr_rows)}件")
    for r in cr_rows:
        print(f"  category_id={r['category_id']} label={r['label']!r}")

    up_rows = await conn.fetch(
        "SELECT line_user_id, name, faculty, department FROM user_profiles "
        "WHERE faculty = $1 OR department = $2",
        OLD_FACULTY, OLD_DEPT,
    )
    print(f"\n[user_profiles] faculty/department が旧名称: {len(up_rows)}件（実在ユーザー。削除ではなく更新のみ行う）")
    for r in up_rows:
        print(f"  line_user_id={r['line_user_id']} name={r['name']!r} faculty={r['faculty']!r} department={r['department']!r}")

    syl_rows = await conn.fetch(
        "SELECT id, course_section_id, department FROM syllabi WHERE department = $1", OLD_DEPT
    )
    print(f"\n[syllabi] department='{OLD_DEPT}': {len(syl_rows)}件（user_syllabiへの影響に注意、自動削除しない）")
    for r in syl_rows:
        print(f"  id={r['id']} course_section_id={r['course_section_id']}")

    return {
        "subjects": subj_rows,
        "review_counts": review_counts,
        "display_orders": do_faculty_rows,
        "credit_requirements": cr_rows,
        "user_profiles": up_rows,
        "syllabi": syl_rows,
    }


async def execute(conn, found: dict):
    print("\n=== 削除・更新を実行します ===")

    # subjects: レビューが紐づく科目は RESTRICT により削除失敗する（意図的）
    deleted, blocked = 0, []
    for r in found["subjects"]:
        try:
            async with conn.transaction():
                await conn.execute("DELETE FROM subjects WHERE id = $1", r["id"])
            deleted += 1
        except asyncpg.ForeignKeyViolationError:
            blocked.append(r)
    print(f"subjects: {deleted}件削除")
    if blocked:
        print(f"  ※ レビューが紐づくため削除できなかった科目: {len(blocked)}件（このスクリプトでは削除しません）")
        for r in blocked:
            print(f"    id={r['id']} name={r['name']!r} reviews={found['review_counts'][r['id']]}件")

    do_ids = [r["id"] for r in found["display_orders"]]
    if do_ids:
        await conn.execute("DELETE FROM display_orders WHERE id = ANY($1::int[])", do_ids)
    print(f"display_orders: {len(do_ids)}件削除")

    cr_ids = [r["category_id"] for r in found["credit_requirements"]]
    if cr_ids:
        await conn.execute("DELETE FROM credit_requirements WHERE category_id = ANY($1::text[])", cr_ids)
    print(f"credit_requirements: {len(cr_ids)}件削除")

    up_ids = [r["line_user_id"] for r in found["user_profiles"]]
    if up_ids:
        await conn.execute(
            "UPDATE user_profiles SET faculty = CASE WHEN faculty = $2 THEN $3 ELSE faculty END, "
            "department = CASE WHEN department = $4 THEN $5 ELSE department END "
            "WHERE line_user_id = ANY($1::text[])",
            up_ids, OLD_FACULTY, NEW_FACULTY, OLD_DEPT, NEW_DEPT,
        )
    print(f"user_profiles: {len(up_ids)}件を「{NEW_FACULTY}」に更新（削除はしていません）")

    if found["syllabi"]:
        print(f"\n※ syllabi に{len(found['syllabi'])}件残っています。user_syllabi（学生の時間割登録）への影響を確認できないため、"
              f"このスクリプトでは何もしていません。内容を確認のうえ個別に対応してください。")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["dev", "prod"], required=True)
    parser.add_argument("--execute", action="store_true", help="指定しない場合は調査(dry-run)のみ")
    args = parser.parse_args()

    if args.env == "prod":
        confirm = input("本番DBに対して実行しようとしています。よろしいですか？ (yes/no): ")
        if confirm.strip().lower() != "yes":
            print("キャンセルしました。")
            return
        url = PROD_URL
    else:
        url = DEV_URL

    conn = await asyncpg.connect(url, ssl=_ssl())
    try:
        found = await report(conn)
        if args.execute:
            confirm = input("\n上記の内容で削除・更新を実行します。よろしいですか？ (yes/no): ")
            if confirm.strip().lower() != "yes":
                print("キャンセルしました。")
                return
            await execute(conn, found)
        else:
            print("\n(dry-run) 実際に削除するには --execute を付けて再実行してください。")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
