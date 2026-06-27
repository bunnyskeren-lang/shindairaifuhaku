"""
経営学部専門科目の担当教員を course_instructors テーブルに登録するスクリプト。
courses.instructor フィールドのデータを course_instructors に移行する。

実行:
  python -X utf8 import_keiei_instructors.py --env dev
  python -X utf8 import_keiei_instructors.py --env dev --dry-run
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path


def load_env(env: str):
    env_file = Path(__file__).parent / (".env.dev" if env == "dev" else ".env")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


async def run(env: str, dry_run: bool):
    from sqlalchemy import select
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from database import AsyncSessionLocal, init_db
    from models import Course, CourseInstructor

    await init_db()

    # 経営学部の courses を取得
    async with AsyncSessionLocal() as s:
        courses = (await s.execute(
            select(Course).where(Course.faculty == "経営学部")
        )).scalars().all()
    print(f"経営学部 courses: {len(courses)} 件")

    added = 0
    skipped_no_inst = 0
    skipped_exists = 0

    for c in courses:
        if not c.instructor or not c.instructor.strip():
            skipped_no_inst += 1
            continue

        async with AsyncSessionLocal() as s:
            existing = (await s.execute(
                select(CourseInstructor).where(CourseInstructor.course_id == c.id)
            )).scalars().first()

            if existing:
                skipped_exists += 1
                continue

            if dry_run:
                print(f"  [dry-run] {c.name} → {c.instructor} (url={c.syllabus_url})")
            else:
                # 担当教員が複数いる場合は「・」「、」「,」で分割
                import re
                names = re.split(r'[・、,，/]', c.instructor)
                for name in names:
                    name = name.strip()
                    if not name:
                        continue
                    s.add(CourseInstructor(
                        course_id=c.id,
                        name=name,
                        url=c.syllabus_url or None,
                    ))
                await s.commit()
            added += 1

    print(f"追加: {added} 件")
    print(f"スキップ（担当教員なし）: {skipped_no_inst} 件")
    print(f"スキップ（登録済み）: {skipped_exists} 件")
    if dry_run:
        print("[dry-run] 実際には書き込みしていません")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    load_env(args.env)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run(args.env, args.dry_run))


if __name__ == "__main__":
    main()
