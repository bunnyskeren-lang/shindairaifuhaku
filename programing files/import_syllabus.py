"""
シラバスデータインポートスクリプト
使い方:
  python import_syllabus.py <データファイル.txt> [--env dev|prod]
  python import_syllabus.py <データファイル.txt> --env dev --also-courses --classification 共通専門科目 --faculty 経営学部

データファイルはシラバスサイトからコピペしたテキストをそのまま保存したもの。
第3・第4クォーターおよび後期の科目のみインポートします。
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

def clean_name(name: str) -> str:
    """(副：...) や (主：...) サフィックスを除去してクリーンな科目名を返す。"""
    return re.sub(r'\((?:副|主)：[^)]+\)', '', name).strip()

def parse_slots(slot_str: str) -> list[tuple[str, int]]:
    """
    "月1"     → [("月", 1)]
    "月3,4"   → [("月", 3), ("月", 4)]
    "月3,火3" → [("月", 3), ("火", 3)]
    "月3,水1" → [("月", 3), ("水", 1)]
    "集中"    → [("集", 0)]
    """
    slot_str = slot_str.strip()
    if slot_str == "集中":
        return [("集", 0)]
    slots = []
    current_day = None
    for part in slot_str.split(","):
        part = part.strip()
        m = re.match(r'^([月火水木金土日])(\d+)$', part)
        if m:
            current_day = m.group(1)
            slots.append((current_day, int(m.group(2))))
        elif part.isdigit() and current_day:
            slots.append((current_day, int(part)))
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
        name = clean_name(parts[4].strip())
        instructor = parts[5].strip()
        slot_str = parts[6].strip()
        timetable_code = parts[7].strip()

        # 第3・第4クォーターおよび後期のみ（前期・第1Q・第2Q・年度はスキップ）
        if not (term.startswith("第3") or term.startswith("第4") or term == "後期"):
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

async def import_courses(courses: list[dict], also_courses: bool = False,
                         classification: str = "", faculty: str = ""):
    from sqlalchemy import select
    from database import AsyncSessionLocal, init_db
    from models import SyllabusCourse, CourseSlot, Course

    await init_db()

    tt_added = 0
    tt_skipped = 0
    c_added = 0
    c_skipped = 0

    async with AsyncSessionLocal() as session:
        for c in courses:
            # ── syllabus_courses / course_slots ──
            existing = (await session.execute(
                select(SyllabusCourse).where(SyllabusCourse.timetable_code == c["timetable_code"])
            )).scalar_one_or_none()
            if existing:
                tt_skipped += 1
            else:
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
                tt_added += 1

            # ── courses テーブル（LINE bot 用）──
            if also_courses:
                existing_c = (await session.execute(
                    select(Course).where(Course.name == c["name"])
                )).scalar_one_or_none()
                if existing_c:
                    if not existing_c.term:
                        existing_c.term = c["term"]
                    c_skipped += 1
                else:
                    dept_faculty = faculty or c["department"].split("　")[0].split(" ")[0]
                    session.add(Course(
                        name=c["name"],
                        instructor=c["instructor"],
                        classification=classification,
                        category="専門",
                        faculty=dept_faculty or None,
                        reading="",
                        term=c["term"],
                    ))
                    c_added += 1

        await session.commit()

    print(f"時間割DB: {tt_added}件追加, {tt_skipped}件スキップ（重複）")
    if also_courses:
        print(f"科目DB:  {c_added}件追加, {c_skipped}件スキップ（重複）")

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="シラバスデータファイルのパス")
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument("--dry-run", action="store_true", help="DBに書き込まず件数だけ表示")
    parser.add_argument("--also-courses", action="store_true",
                        help="courses テーブル（LINE bot 用）にも登録する")
    parser.add_argument("--classification", default="",
                        help="courses テーブルの分類名（例：共通専門科目）")
    parser.add_argument("--faculty", default="",
                        help="courses テーブルの学部名（例：経営学部）")
    args = parser.parse_args()

    load_env(args.env)

    courses = parse_file(args.file)
    print(f"パース結果: {len(courses)}件（第3・第4Q および後期）")

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

    asyncio.run(import_courses(
        courses,
        also_courses=args.also_courses,
        classification=args.classification,
        faculty=args.faculty,
    ))

if __name__ == "__main__":
    main()
