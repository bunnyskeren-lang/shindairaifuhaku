"""
経営学部専門科目のナンバリングコードをシラバスから取得し、
syllabi.numbering_code に保存 → subjects.classification を群名に更新する。

実行:
  python -X utf8 update_senmon_classification.py --env dev
  python -X utf8 update_senmon_classification.py --env dev --dry-run
"""
import argparse
import asyncio
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

SYLLABUS_BASE = "https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026/06/data/2026_{code}.html"

# ナンバリングコード末尾3桁 → subjects.classification
_NC_SUFFIX_TO_CLS: dict[str, str] = {
    "100": "第1群科目",
    "101": "第1群科目",
    "103": "第3群科目",
    "202": "第2群科目",
    "203": "グローバル科目群",
    "204": "グローバル科目群",
    "300": "第3群科目",
    "303": "グローバル科目群",
    "400": "研究指導・卒業論文",
    "403": "研究指導・卒業論文",
}


def load_env(env: str):
    env_file = Path(__file__).parent / (".env.dev" if env == "dev" else ".env")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def fetch_html(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


def parse_numbering_code(html: str) -> str | None:
    m = re.search(r'ナンバリングコード.*?([A-Z]\d[A-Z]{2}\d{3})', html, re.DOTALL)
    return m.group(1) if m else None


def nc_to_classification(nc: str) -> str | None:
    suffix = nc[-3:] if nc and len(nc) >= 3 else ""
    return _NC_SUFFIX_TO_CLS.get(suffix)


async def run(env: str, dry_run: bool):
    from sqlalchemy import select
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from database import AsyncSessionLocal, init_db
    from models import Syllabus, CourseSection, Subject

    await init_db()

    # 経営学部の syllabi を取得（timetable_code の2文字目が B）
    async with AsyncSessionLocal() as s:
        sc_rows = (await s.execute(
            select(Syllabus).where(Syllabus.timetable_code.like("_B%"))
        )).scalars().all()
    print(f"経営学部 syllabi: {len(sc_rows)} 件")

    fetched = 0
    failed = 0
    for i, sc in enumerate(sc_rows):
        url = SYLLABUS_BASE.format(code=sc.timetable_code)
        html = fetch_html(url)
        nc = parse_numbering_code(html) if html else None
        if not nc:
            failed += 1
            continue
        if dry_run:
            print(f"  {sc.timetable_code}: {nc} → {nc_to_classification(nc)}")
        else:
            async with AsyncSessionLocal() as s:
                row = await s.get(Syllabus, sc.id)
                row.numbering_code = nc
                await s.commit()
        fetched += 1
        if (i + 1) % 20 == 0:
            print(f"  進捗 {i+1}/{len(sc_rows)}")
        time.sleep(0.3)

    print(f"ナンバリングコード取得: 成功={fetched}, 失敗={failed}")

    if dry_run:
        print("[dry-run] subjects.classification は更新しません")
        return

    # subjects.classification を更新
    # syllabi → course_sections → subjects の JOIN で科目名を取得
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(Subject.name, Syllabus.numbering_code)
            .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .where(Syllabus.timetable_code.like("_B%"))
            .where(Syllabus.numbering_code.isnot(None))
        )).all()

    nc_by_name: dict[str, str] = {}
    for name, nc in rows:
        cls = nc_to_classification(nc)
        if cls:
            nc_by_name[name] = cls

    async with AsyncSessionLocal() as s:
        subjects = (await s.execute(
            select(Subject).where(Subject.faculty == "経営学部")
        )).scalars().all()

        updated = 0
        for subj in subjects:
            new_cls = nc_by_name.get(subj.name)
            if new_cls and subj.classification != new_cls:
                subj.classification = new_cls
                updated += 1
        await s.commit()

    print(f"subjects.classification 更新: {updated} 件")
    print("完了")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(run(args.env, args.dry_run))


if __name__ == "__main__":
    main()
