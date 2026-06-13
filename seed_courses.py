"""
courses.py のデータをDBに投入するスクリプト。
実行: python seed_courses.py

注意: .env の DATABASE_URL に本番の PostgreSQL URL を設定してから実行してください。
"""
import asyncio

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select

from database import init_db, AsyncSessionLocal
from models import Course, PendingReview
from courses import COURSES


def get_category(classification: str) -> str:
    return "専門" if "専門" in classification else "教養"


async def seed():
    await init_db()

    added_courses = 0
    added_reviews = 0
    skipped = 0

    async with AsyncSessionLocal() as session:
        for name, data in COURSES.items():
            # 科目を追加（既存の場合はスキップ）
            existing = (await session.execute(
                select(Course).where(Course.name == name)
            )).scalar_one_or_none()

            if existing is None:
                classification = data.get("classification", "")
                course = Course(
                    name=name,
                    instructor=data.get("instructor", ""),
                    classification=classification,
                    category=get_category(classification),
                    syllabus_url=data.get("syllabus_url") or None,
                )
                session.add(course)
                added_courses += 1
                print(f"  [科目] {name} ({get_category(data.get('classification',''))}/{data.get('classification','')})")
            else:
                skipped += 1

            # サンプルレビューを追加（承認済みとして投入）
            if "rating" in data and "comment" in data:
                review_exists = (await session.execute(
                    select(PendingReview).where(
                        PendingReview.course_name == name,
                        PendingReview.submitter_name == "サンプル",
                    )
                )).scalar_one_or_none()

                if review_exists is None:
                    session.add(PendingReview(
                        submitter_name="サンプル",
                        course_name=name,
                        rating=data["rating"],
                        ease_rating=data["ease_rating"],
                        grading_method=data.get("evaluation") or None,
                        comment=data["comment"],
                        is_approved=True,
                    ))
                    added_reviews += 1

        await session.commit()

    print(f"\n完了: 科目 {added_courses}件追加 / {skipped}件スキップ, レビュー {added_reviews}件追加")


if __name__ == "__main__":
    asyncio.run(seed())
