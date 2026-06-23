"""
シラバスデータインポートスクリプト
使い方:
  python import_syllabus.py <データファイル.txt> [--env dev|prod]

データファイルはシラバスサイトからコピペしたテキストをそのまま保存したもの。
第3・第4クォーターの科目のみインポートします。
"""
import asyncio
import os
import re
import sys
from pathlib import Path

def load_env(env: str):
    env_file = Path(__file__).parent / (".env.dev" if env == "dev" else ".env")
    if not env_file.exists():
        print(f"ERROR: {env_file} が見つかりません", file=sys.stderr)
        sys.exit(1)
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

def parse_slots(slot_str: str) -> list[tuple[str, int]]:
    """
    "月1"     → [("月", 1)]
    "月1,2"   → [("月", 1), ("月", 2)]
    "月3,4"   → [("月", 3), ("月", 4)]
    "集中"    → [("集", 0)]
    """
    slot_str = slot_str.strip()
    if slot_str == "集中":
        return [("集", 0)]
    m = re.match(r'^([月火水木金土日])(.+)$', slot_str)
    if not m:
        return []
    day = m.group(1)
    periods_str = m.group(2)
    slots = []
    for p in periods_str.split(","):
        p = p.strip()
        if p.isdigit():
            slots.append((day, int(p)))
    return slots

def parse_file(filepath: str) -> list[dict]:
    text = Path(filepath).read_text(encoding="utf-8", errors="ignore")
    courses = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 8:
            # タブ区切りでない場合は2文字以上の半角スペースで分割（HTMLテーブルのコピペ対応）
            parts = re.split(r' {2,}', line.strip())
        if len(parts) < 8:
            continue
        # No. が数字であることを確認
        if not parts[0].strip().isdigit():
            continue
        year_str = parts[1].strip()
        if not year_str.isdigit():
            continue
        year = int(year_str)
        term = parts[2].strip()
        department = parts[3].strip()
        name = parts[4].strip()
        instructor = parts[5].strip()
        slot_str = parts[6].strip()
        timetable_code = parts[7].strip()

        # 第3・第4クォーターのみ
        if not (term.startswith("第3") or term.startswith("第4")):
            continue

        slots = parse_slots(slot_str)
        if not slots:
            continue

        courses.append({
            "year": year,
            "term": term,
            "department": department,
            "name": name,
            "instructor": instructor,
            "timetable_code": timetable_code,
            "slots": slots,
        })
    return courses

async def import_courses(courses: list[dict]):
    from sqlalchemy import select
    from database import AsyncSessionLocal, init_db
    from models import SyllabusCourse, CourseSlot

    await init_db()

    added = 0
    skipped = 0
    async with AsyncSessionLocal() as session:
        for c in courses:
            existing = (await session.execute(
                select(SyllabusCourse).where(SyllabusCourse.timetable_code == c["timetable_code"])
            )).scalar_one_or_none()
            if existing:
                skipped += 1
                continue

            sc = SyllabusCourse(
                year=c["year"],
                term=c["term"],
                department=c["department"],
                name=c["name"],
                instructor=c["instructor"],
                timetable_code=c["timetable_code"],
            )
            session.add(sc)
            await session.flush()

            for day, period in c["slots"]:
                session.add(CourseSlot(
                    syllabus_course_id=sc.id,
                    day_of_week=day,
                    period=period,
                ))
            added += 1

        await session.commit()
    print(f"完了: {added}件追加, {skipped}件スキップ（重複）")

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="シラバスデータファイルのパス")
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument("--dry-run", action="store_true", help="DBに書き込まず件数だけ表示")
    args = parser.parse_args()

    load_env(args.env)

    courses = parse_file(args.file)
    print(f"パース結果: {len(courses)}件（第3・第4Q）")

    if args.dry_run:
        for c in courses[:5]:
            print(f"  {c['timetable_code']} {c['name']} {c['slots']}")
        if len(courses) > 5:
            print(f"  ... 他{len(courses)-5}件")
        return

    if args.env == "prod":
        confirm = input("本番DBにインポートします。よろしいですか？ (yes/no): ")
        if confirm.strip().lower() != "yes":
            print("中止しました")
            return

    asyncio.run(import_courses(courses))

if __name__ == "__main__":
    main()
