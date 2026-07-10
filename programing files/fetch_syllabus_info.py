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
    "G": "20",  # 教養科目の一部で使われるコード（Uと同じpath）
    "Z": "14",  # 海洋政策科学部
    "E": "05",  # 経済学部
    "H": "13",  # 国際人間科学部
    "A": "10",  # 農学部
    "L": "01",  # 文学部
    "J": "04",  # 法学部
    "M": "0801",  # 医学部医学科
}

# 工学部は学科ごとにpathが分かれるが、時間割コードの2文字目は学科をまたいで「T」「N」を
# 共有しており数字部分の範囲でしか判別できない（import_syllabus.pyのENGINEERING_RANGESと
# 同じ対応表。学科を追加する際は両方を更新すること）
ENGINEERING_RANGES: list[tuple[int, int, str]] = [
    (0, 99, "0921"),      # 工学部建築学科
    (100, 149, "0922"),   # 工学部市民工学科
    (150, 199, "0923"),   # 工学部電気電子工学科
    (200, 249, "0924"),   # 工学部機械工学科
    (250, 299, "0925"),   # 工学部応用化学科
]
ENGINEERING_LETTERS = {"T", "N"}


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
    letter = code[1].upper()
    if letter in ENGINEERING_LETTERS:
        digits = code[2:]
        if not digits.isdigit():
            return None
        num = int(digits)
        for lo, hi, path in ENGINEERING_RANGES:
            if lo <= num <= hi:
                return SYLLABUS_BASE.format(path=path, code=code)
        return None
    path = FACULTY_PATH.get(letter)
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


def parse_credits(html_text: str) -> str:
    """単位数を返す（例："2.0"）。
    実際のHTML: <td class="gaibu-syllabus-kihon" align="center">単位数</th><td>2.0</td>
    （サイト側の閉じタグ不整合で </td> ではなく </th> になっているため、
    ラベル直後の </td> には固定せず次の <td> を拾う）"""
    m = re.search(r'単位数.*?<td[^>]*>(.*?)</td>', html_text, re.DOTALL)
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
            q = q.where(Syllabus.target_grades.is_(None))
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


async def run_credits(dry_run: bool = False, force: bool = False):
    """単位数(subjects.credits)を補完する。
    前期のみ開講の科目はsyllabiレコードを持たない（import_syllabus.pyの仕様）ため、
    Syllabus経由ではなくcourse_sections.syllabus_url経由で辿る。"""
    from sqlalchemy import select
    from database import AsyncSessionLocal, init_db
    from models import Subject, CourseSection

    await init_db()

    async with AsyncSessionLocal() as session:
        q = select(Subject)
        if not force:
            q = q.where(Subject.credits.is_(None))
        subjects = (await session.execute(q)).scalars().all()

    print(f"単位数取得対象: {len(subjects)}件")
    counts = {"updated": 0, "skipped": 0, "not_found": 0}

    async def process_subject(session, subj):
        cs = (await session.execute(
            select(CourseSection).where(
                CourseSection.subject_id == subj.id,
                CourseSection.syllabus_url.isnot(None),
            ).limit(1)
        )).scalar_one_or_none()
        if cs is None:
            counts["skipped"] += 1
            return

        html_text = fetch_html(cs.syllabus_url)
        if not html_text:
            counts["not_found"] += 1
            return

        credits_raw = parse_credits(html_text)

        if dry_run:
            print(f"  {subj.name}: credits={credits_raw!r}")
        elif credits_raw:
            try:
                s = await session.get(Subject, subj.id)
                s.credits = float(credits_raw)
                counts["updated"] += 1
            except ValueError:
                pass
        time.sleep(0.3)  # サーバー負荷軽減

    # 長時間の一括処理でSupabase側のアイドルタイムアウト等により接続が切れても
    # 全体が失敗しないよう、一定件数ごとに新しいセッションへ切り替えてコミットする
    BATCH_SIZE = 40
    for batch_start in range(0, len(subjects), BATCH_SIZE):
        batch = subjects[batch_start:batch_start + BATCH_SIZE]
        for attempt in range(3):
            try:
                async with AsyncSessionLocal() as session:
                    for subj in batch:
                        await process_subject(session, subj)
                    if not dry_run:
                        await session.commit()
                break
            except Exception as e:
                if attempt == 2:
                    print(f"  [バッチ失敗] {batch_start}-{batch_start + len(batch)}: {e!r}")
                    raise
                print(f"  [バッチ再試行 {attempt + 1}/3] {batch_start}-{batch_start + len(batch)}: {e!r}")
        print(f"  進捗: {min(batch_start + BATCH_SIZE, len(subjects))}/{len(subjects)}")

    print(f"単位数補完完了: 更新={counts['updated']}, スキップ={counts['skipped']}(セクション未登録), 404={counts['not_found']}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="既取得分も上書き")
    args = parser.parse_args()
    load_env(args.env)

    async def _main():
        # run()とrun_credits()を同一イベントループ内で実行する。
        # asyncio.run()を2回に分けるとSQLAlchemyの非同期エンジンが保持する
        # コネクションプールが前のイベントループに紐づいたままになり、
        # 2回目の呼び出しで「Event loop is closed」になるため。
        await run(args.dry_run, args.force)
        await run_credits(args.dry_run, args.force)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
