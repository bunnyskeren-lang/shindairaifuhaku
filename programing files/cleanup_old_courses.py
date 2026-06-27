"""
今日インポートした科目以外を全削除するスクリプト。
関連する PendingReview も削除。
実行: python cleanup_old_courses.py
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, delete
from database import init_db, AsyncSessionLocal
from models import Course, PendingReview

# import_kyoyo_courses.py と同じリストを参照
from import_kyoyo_courses import COURSE_MAP

TODAY_NAMES: set[str] = set()
for names, _ in COURSE_MAP:
    TODAY_NAMES.update(names)


async def main():
    await init_db()
    async with AsyncSessionLocal() as session:
        # 削除対象の科目を確認
        all_courses = (await session.execute(select(Course))).scalars().all()
        to_delete = [c for c in all_courses if c.name not in TODAY_NAMES]

        if not to_delete:
            print("削除対象なし")
            return

        print(f"削除対象: {len(to_delete)}件")
        for c in to_delete:
            print(f"  [{c.classification or '未分類'}] {c.name}")

        confirm = input("\n本当に削除しますか？ (yes/no): ").strip().lower()
        if confirm != "yes":
            print("キャンセルしました")
            return

        old_names = [c.name for c in to_delete]
        old_ids = [c.id for c in to_delete]

        # 関連レビューを削除
        rev_result = await session.execute(
            delete(PendingReview).where(PendingReview.course_name.in_(old_names))
        )
        # 科目を削除
        course_result = await session.execute(
            delete(Course).where(Course.id.in_(old_ids))
        )
        await session.commit()

        print(f"\n削除完了: 科目 {course_result.rowcount}件 / レビュー {rev_result.rowcount}件")


if __name__ == "__main__":
    asyncio.run(main())
