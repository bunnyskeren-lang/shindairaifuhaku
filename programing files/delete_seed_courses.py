"""
courses.py のサンプル科目をDBから全削除するスクリプト。
関連するレビュー（PendingReview）も一緒に削除されます。

実行: python delete_seed_courses.py
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import delete
from database import init_db, AsyncSessionLocal
from models import Course, PendingReview
from courses import COURSES

SEED_NAMES = list(COURSES.keys())


async def main():
    await init_db()
    async with AsyncSessionLocal() as session:
        deleted_reviews = (await session.execute(
            delete(PendingReview).where(PendingReview.course_name.in_(SEED_NAMES))
        )).rowcount
        deleted_courses = (await session.execute(
            delete(Course).where(Course.name.in_(SEED_NAMES))
        )).rowcount
        await session.commit()

    print(f"削除完了: 科目 {deleted_courses}件 / レビュー {deleted_reviews}件")


if __name__ == "__main__":
    asyncio.run(main())
