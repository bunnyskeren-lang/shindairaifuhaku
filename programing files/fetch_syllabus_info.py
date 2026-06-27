"""
シラバスページから対象年次・科目分類を取得してDBに保存するスクリプト
使い方:
  python -X utf8 fetch_syllabus_info.py --env dev
  python -X utf8 fetch_syllabus_info.py --env dev --dry-run
  python -X utf8 fetch_syllabus_info.py --env dev --force   # 既取得分も上書き
"""
import asyncio
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

SYLLABUS_BASE = "https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026/{path}/data/2026_{code}.html"

# 時間割コードの2文字目 → URL パス番号（学部が増えたらここに追加）
FACULTY_PATH: dict[str, str] = {
    "U": "20",  # 教養科目（教養教育院）
    "B": "06",  # 経営学部
    "X": "15",  # システム情報学部
}


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


def make_syllabus_url(code: str) -> str | None:
    if len(code) < 2:
        return None
    path = FACULTY_PATH.get(code[1].upper())
    if not path:
        return None
    return SYLLABUS_BASE.format(path=path, code=code)


def fetch_html(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


def parse_target_grades(html_text: str) -> str:
    """開講年次を "1,2,3,4" 形式で返す"""
    # 実際のHTML: <td class="...">開講年次</td><td width="300">1 ･ 2 ･ 3 ･ 4 年</td>
    m = re.search(r'開講年次</td>\s*<td[^>]*>(.*?)</td>', html_text, re.DOTALL)
    if not m:
        m = re.search(r'開講年次.*?<td[^>]*>(.*?)</td>', html_text, re.DOTALL)
    if not m:
        return ""
    raw = re.sub(r'<[^>]+>', '', m.group(1)).strip()
    nums = [int(n) for n in re.findall(r'[1-4]', raw)]
    if not nums:
        return ""
    if any(c in raw for c in "～〜~－-"):
        nums = list(range(min(nums), max(nums) + 1))
    return ",".join(str(n) for n in sorted(set(nums)))


def parse_subject_category(html_text: str) -> str:
    """科目分類を返す（例：教養科目、専門科目）"""
    # 実際のHTML: <td class="...">科目分類</td><td width="300">教養科目</td>
    m = re.search(r'科目分類</td>\s*<td[^>]*>(.*?)</td>', html_text, re.DOTALL)
    if not m:
        m = re.search(r'科目分類.*?<td[^>]*>(.*?)</td>', html_text, re.DOTALL)
    if not m:
        return ""
    return re.sub(r'<[^>]+>', '', m.group(1)).strip()


async def run(dry_run: bool = False, force: bool = False):
    from sqlalchemy import select
    from database import AsyncSessionLocal, init_db
    from models import Syllabus

    await init_db()

    async with AsyncSessionLocal() as session:
        q = select(Syllabus).where(Syllabus.timetable_code.isnot(None))
        if not force:
            q = q.where(Syllabus.target_grades == None)
        courses = (await session.execute(q)).scalars().all()

    print(f"対象コース: {len(courses)}件")
    updated = skipped = not_found = 0

    async with AsyncSessionLocal() as session:
        for i, c in enumerate(courses):
            url = make_syllabus_url(c.timetable_code)
            if not url:
                skipped += 1
                continue

            html_text = fetch_html(url)
            if not html_text:
                not_found += 1
                if i < 10 or not_found <= 5:
                    print(f"  404: {c.timetable_code}")
                continue

            grades = parse_target_grades(html_text)
            category = parse_subject_category(html_text)

            if dry_run:
                print(f"  {c.timetable_code}: grades={grades!r}, category={category!r}")
            else:
                sc = await session.get(Syllabus, c.id)
                sc.target_grades = grades or None
                sc.subject_category = category or None
                updated += 1

            if (i + 1) % 20 == 0:
                print(f"  進捗: {i+1}/{len(courses)}")
                if not dry_run:
                    await session.commit()
            time.sleep(0.3)  # サーバー負荷軽減

        if not dry_run:
            await session.commit()

    print(f"完了: 更新={updated}, スキップ={skipped}(未対応学部), 404={not_found}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="既取得分も上書き")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(run(args.dry_run, args.force))


if __name__ == "__main__":
    main()
