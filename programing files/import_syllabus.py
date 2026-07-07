"""
シラバスデータインポートスクリプト
使い方:
  python -X utf8 import_syllabus.py <データファイル.txt> [--env dev|prod]
  python -X utf8 import_syllabus.py <データファイル.txt> --env dev --also-courses --classification 共通専門科目 --faculty 経営学部

データファイルはシラバスサイトからコピペしたテキストをそのまま保存したもの。
第3・第4クォーターおよび後期の科目のみインポートします。
"""
import asyncio
import os
import re
import sys
from pathlib import Path

_LOG_PATH = Path(__file__).parent / "import_debug.log"

def _log(msg: str) -> None:
    print(msg)
    with open(_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

SYLLABUS_BASE = "https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026/{path}/data/2026_{code}.html"
FACULTY_PATH: dict[str, str] = {
    "U": "20",
    "B": "06",
    "X": "15",  # システム情報学部
}

def make_syllabus_url(code: str) -> str | None:
    if len(code) < 2:
        return None
    path = FACULTY_PATH.get(code[1].upper())
    if not path:
        return None
    return SYLLABUS_BASE.format(path=path, code=code)

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

_FULLWIDTH_ALNUM = str.maketrans({
    chr(c): chr(c - 0xFEE0)
    for c in list(range(0xFF10, 0xFF1A)) + list(range(0xFF21, 0xFF3B)) + list(range(0xFF41, 0xFF5B))
})

def normalize_alnum(name: str) -> str:
    """全角英数字（Ａ-Ｚ, a-z, ０-９）のみ半角化する。括弧等の全角記号はそのまま残す。"""
    return name.translate(_FULLWIDTH_ALNUM)

_PAREN_F2H = str.maketrans("（）", "()")
_PAREN_H2F = str.maketrans("()", "（）")

def clean_name(name: str) -> str:
    name = re.sub(r'\((?:副|主)：[^)]+\)', '', name)
    name = re.sub(r'【[^】]*】', '', name)
    return name.strip()

def parse_slots(slot_str: str) -> list[tuple[str, int]]:
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

def _is_timetable_term(term: str) -> bool:
    return term.startswith("第3") or term.startswith("第4") or term in ("後期", "集中")


def parse_file(filepath: str) -> list[dict]:
    text = Path(filepath).read_text(encoding="utf-8", errors="ignore")
    courses = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 8:
            parts = re.split(r' {2,}', line.strip())
        if len(parts) < 8:
            continue
        if not parts[0].strip().isdigit():
            continue
        year_str = parts[1].strip()
        if not year_str.isdigit():
            continue
        year = int(year_str)
        term = parts[2].strip()
        department = parts[3].strip()
        name = normalize_alnum(clean_name(parts[4].strip()))
        instructor = parts[5].strip()
        slot_str = parts[6].strip()
        timetable_code = parts[7].strip()
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
                         classification: str = "", faculty: str = "",
                         auto_create: bool = True):
    from sqlalchemy import select
    from database import AsyncSessionLocal, init_db
    from models import Subject, Instructor, CourseSection, Syllabus, Schedule

    await init_db()

    tt_added = 0
    tt_skipped = 0
    tt_unmatched = 0
    c_added = 0

    async with AsyncSessionLocal() as session:
        for c in courses:
            is_tt = _is_timetable_term(c["term"])

            # ── subjects テーブル（LINE bot 用）──
            subj = (await session.execute(
                select(Subject).where(Subject.name == c["name"])
            )).scalar_one_or_none()

            if subj is None:
                # 括弧の全角/半角表記ゆれを吸収して再検索（表示名は変更しない）
                for alt_name in {
                    c["name"].translate(_PAREN_F2H),
                    c["name"].translate(_PAREN_H2F),
                } - {c["name"]}:
                    subj = (await session.execute(
                        select(Subject).where(Subject.name == alt_name)
                    )).scalar_one_or_none()
                    if subj is not None:
                        break

            if subj is None:
                if also_courses:
                    dept_faculty = faculty or c["department"].split("　")[0].split(" ")[0]
                    subj = Subject(
                        name=c["name"],
                        classification=classification or None,
                        category="専門",
                        faculty=dept_faculty or None,
                        reading="",
                        term=c["term"],
                        credits=None,
                    )
                    session.add(subj)
                    await session.flush()
                    c_added += 1
                elif not is_tt:
                    continue
                elif not auto_create:
                    tt_unmatched += 1
                    continue
                else:
                    # 時間割用だが科目が未登録 → subject を仮登録
                    subj = Subject(
                        name=c["name"],
                        classification=classification or None,
                        category="専門",
                        faculty=faculty or None,
                        reading="",
                    )
                    session.add(subj)
                    await session.flush()

            if not is_tt:
                continue

            # Instructor を find-or-create（日本語名は空白を除去して統一する）
            instructor_name = c["instructor"]
            if any('぀' <= ch <= '鿿' for ch in instructor_name):
                instructor_name = instructor_name.replace(' ', '').replace('　', '')
            instr = (await session.execute(
                select(Instructor).where(Instructor.name == instructor_name)
            )).scalar_one_or_none()
            if instr is None:
                instr = Instructor(name=instructor_name)
                session.add(instr)
                await session.flush()

            # CourseSection を find-or-create（既存の場合、syllabus_url が未設定なら補完する）
            # ※ syllabus 重複チェックより先に行う。既にSyllabusが登録済みでも
            #   URLだけ未設定というケース（レビュー投稿由来のセクション等）を補完するため。
            cs = (await session.execute(
                select(CourseSection).where(
                    CourseSection.subject_id == subj.id,
                    CourseSection.instructor_id == instr.id,
                )
            )).scalar_one_or_none()
            if cs is None:
                cs = CourseSection(
                    subject_id=subj.id,
                    instructor_id=instr.id,
                    syllabus_url=make_syllabus_url(c["timetable_code"]),
                )
                session.add(cs)
                await session.flush()
            elif not cs.syllabus_url:
                cs.syllabus_url = make_syllabus_url(c["timetable_code"])

            # ── syllabi / schedules ──
            # timetable_code 重複チェック
            existing_syl = (await session.execute(
                select(Syllabus).where(Syllabus.timetable_code == c["timetable_code"])
            )).scalar_one_or_none()
            if existing_syl:
                tt_skipped += 1
                _log(f"  [重複スキップ:コード既存] {c['name']} / {c['instructor']} / {c['term']} / コード:{c['timetable_code']}")
                continue

            # 同一セクション×同一クォーターは1件のみ保持可能（DB制約）。
            # 同じ科目×同じ教員が同クォーターに複数コマ持つ場合は先勝ちでスキップする
            existing_term_syl = (await session.execute(
                select(Syllabus).where(
                    Syllabus.course_section_id == cs.id,
                    Syllabus.year == c["year"],
                    Syllabus.academic_term == c["term"],
                )
            )).scalar_one_or_none()
            if existing_term_syl is not None:
                tt_skipped += 1
                _log(f"  [重複スキップ:同一セクション] {c['name']} / {c['instructor']} / {c['term']} / 既存コード:{existing_term_syl.timetable_code} 今回:{c['timetable_code']}")
                continue

            syl = Syllabus(
                course_section_id=cs.id,
                year=c["year"],
                academic_term=c["term"],
                timetable_code=c["timetable_code"],
                department=c["department"],
            )
            session.add(syl)
            await session.flush()

            for day, period in c["slots"]:
                session.add(Schedule(
                    syllabus_id=syl.id,
                    day_of_week=day,
                    period=period,
                ))
            tt_added += 1

        await session.commit()

    _log(f"時間割DB: {tt_added}件追加, {tt_skipped}件スキップ（重複）")
    if not auto_create and tt_unmatched:
        _log(f"科目DBに未登録のためスキップ: {tt_unmatched}件")
    if also_courses:
        print(f"科目DB:  {c_added}件追加")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="シラバスデータファイルのパス")
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument("--dry-run", action="store_true", help="DBに書き込まず件数だけ表示")
    parser.add_argument("--also-courses", action="store_true",
                        help="subjects テーブル（LINE bot 用）にも登録する")
    parser.add_argument("--classification", default="",
                        help="subjects テーブルの分類名（例：共通専門科目）")
    parser.add_argument("--faculty", default="",
                        help="subjects テーブルの学部名（例：経営学部）")
    parser.add_argument("--no-auto-create", action="store_true",
                        help="科目名がsubjectsに未登録の場合、仮登録せずスキップする")
    args = parser.parse_args()

    load_env(args.env)

    courses = parse_file(args.file)
    tt_courses = [c for c in courses if _is_timetable_term(c["term"])]
    print(f"パース結果: {len(courses)}件全学期 / うち時間割対象（第3・第4Q・後期・集中）: {len(tt_courses)}件")

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
        auto_create=not args.no_auto_create,
    ))

if __name__ == "__main__":
    main()
