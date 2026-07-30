"""
シラバスページから単位数・経営学部専門科目の群を取得してDBに保存するスクリプト
使い方:
  python -X utf8 fetch_syllabus_info.py --env dev
  python -X utf8 fetch_syllabus_info.py --env dev --dry-run
  python -X utf8 fetch_syllabus_info.py --env dev --force   # 既取得分も上書き
"""
import asyncio
import re
import time
import urllib.request
import urllib.error

from _env import load_env

SYLLABUS_BASE = "https://kym22-web.ofc.kobe-u.ac.jp/kobe_syllabus/2026/{path}/data/2026_{code}.html"

REQUEST_INTERVAL_SECONDS = 0.3  # サーバー負荷軽減

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
}

# 工学部は学科ごとにpathが分かれるが、時間割コードの2文字目は学科をまたいで「T」「N」を
# 共有しており数字部分の範囲でしか判別できない
ENGINEERING_RANGES: list[tuple[int, int, str]] = [
    (0, 99, "0921"),      # 工学部建築学科
    (100, 149, "0922"),   # 工学部市民工学科
    (150, 199, "0923"),   # 工学部電気電子工学科
    (200, 249, "0924"),   # 工学部機械工学科
    (250, 299, "0925"),   # 工学部応用化学科
]
ENGINEERING_LETTERS = {"T", "N"}

# 医学部は学科によって時間割コードの3文字目（Mの次）にさらに1文字付く場合と、
# 数字がそのまま続くが番号帯で学科が異なる場合がある
MEDICINE_SUBLETTERS: dict[str, str] = {
    "B": "0803",  # 医学部医療創成工学科
}
MEDICINE_RANGES: list[tuple[int, int, str]] = [
    (0, 399, "080201"),  # 医学部保健学科看護学専攻（暫定上限。他専攻データ確認後に調整）
    (900, 999, "0801"),  # 医学部医学科
]

# 保健学科の専攻同士は数字部分の範囲が重なりうるため所属名を優先する
DEPARTMENT_PATH_OVERRIDE: dict[str, str] = {
    "医学部保健学科看護学専攻": "080201",
    "医学部保健学科検査技術科学専攻": "080202",
    "医学部保健学科理学療法学専攻": "080203",
    "医学部保健学科作業療法学専攻": "080204",
    "理学部数学科": "0701",
    "理学部物理学科": "0702",
    "理学部化学科": "0703",
    "理学部生物学科": "0704",
    "理学部惑星学科": "0707",
    "工学部": "09",  # 工学部の全学科共通科目（所属列が学科名を含まず「工学部」のみ）
}


def make_syllabus_url(code: str, department: str = "") -> str | None:
    if len(code) < 2:
        return None
    if department in DEPARTMENT_PATH_OVERRIDE:
        return SYLLABUS_BASE.format(path=DEPARTMENT_PATH_OVERRIDE[department], code=code)
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
    if letter == "M":
        if len(code) >= 3 and code[2].isalpha():
            path = MEDICINE_SUBLETTERS.get(code[2].upper())
            if not path:
                return None
            return SYLLABUS_BASE.format(path=path, code=code)
        digits = code[2:]
        if not digits.isdigit():
            return None
        num = int(digits)
        for lo, hi, path in MEDICINE_RANGES:
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


def parse_numbering_code(html_text: str) -> str | None:
    """科目ナンバリングコード（例: B1BB202）を返す"""
    m = re.search(r'ナンバリングコード.*?([A-Z]\d[A-Z]{2}\d{3})', html_text, re.DOTALL)
    return m.group(1) if m else None


# ナンバリングコード末尾3桁 → 経営学部専門科目の群（経営学部規則 別表第2・科目ナンバリング一覧表より）
# 100(初年次セミナー)は科目名の「初年次セミナー」判定で別途拾えるためここでは対象外。
# 203/303はグローバル科目群と①〜⑥を除く第3群科目の両方で使われ番号だけでは判別不能、
# 204は他学部生のみ履修を許可された科目で本学部生の卒業要件区分には該当しないため、
# いずれも自動確定せず管理画面での目視レビューに委ねる。
_KEIEI_NC_SUFFIX_TO_CLASSIFICATION: dict[str, str] = {
    "101": "第1群科目",
    "202": "第2群科目",
    "103": "第3群科目",  # 会計プロフェッショナル育成プログラム授業科目
    "300": "第3群科目",  # 経営学入門演習
    "400": "第3群科目",  # 研究指導（第3群科目に含む）
    "403": "第3群科目",  # 卒業論文・上級科目（第3群科目に含む）
}


def parse_credits(html_text: str) -> str:
    """単位数を返す（例："2.0"）。
    実際のHTML: <td class="gaibu-syllabus-kihon" align="center">単位数</th><td>2.0</td>
    （サイト側の閉じタグ不整合で </td> ではなく </th> になっているため、
    ラベル直後の </td> には固定せず次の <td> を拾う）"""
    m = re.search(r'単位数.*?<td[^>]*>(.*?)</td>', html_text, re.DOTALL)
    if not m:
        return ""
    return re.sub(r'<[^>]+>', '', m.group(1)).strip()


async def run_credits(dry_run: bool = False, force: bool = False):
    """単位数(subjects.credits)を補完する。
    シラバスURLはsyllabi.timetable_code/departmentから毎回動的生成する（syllabiレコードを
    持たない科目はスキップし、subjects.creditsはNULLのまま残る。手入力での補完対象）。"""
    from sqlalchemy import select
    from database import AsyncSessionLocal, init_db
    from models import Subject, CourseSection, Syllabus

    await init_db()

    async with AsyncSessionLocal() as session:
        q = select(Subject)
        if not force:
            # credits=0.0は旧スキーマ移行時の誤データ（実シラバスでは1.0/2.0）が入り込む
            # ことがあり、NULLだけを対象にすると永遠に再取得されないため0も対象に含める
            # （2026-07-18、経営学部53件+教養1件で発覚・修正済み）
            from sqlalchemy import or_
            q = q.where(or_(Subject.credits.is_(None), Subject.credits == 0))
        subjects = (await session.execute(q)).scalars().all()

    print(f"単位数取得対象: {len(subjects)}件")
    counts = {"updated": 0, "skipped": 0, "not_found": 0}

    async def process_subject(session, subj):
        syl = (await session.execute(
            select(Syllabus)
            .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
            .where(
                CourseSection.subject_id == subj.id,
                Syllabus.timetable_code.isnot(None),
            ).limit(1)
        )).scalar_one_or_none()
        url = make_syllabus_url(syl.timetable_code, f"{subj.faculty or ''}{subj.department or ''}") if syl else None
        if not url:
            counts["skipped"] += 1
            return

        html_text = fetch_html(url)
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
        time.sleep(REQUEST_INTERVAL_SECONDS)

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


async def run_senmon_classification(dry_run: bool = False, force: bool = False):
    """経営学部専門科目の群(第1群/第2群/第3群/グローバル)をシラバスのナンバリングコードから
    自動判定し、classification に反映する。シラバスURLはrun_credits()と同様に
    syllabi.timetable_code/departmentから毎回動的生成する（syllabiを持たない科目はスキップ）。
    「初年次セミナー」は下の名前フィルタで対象外にする。
    203/303/204等ナンバリングだけでは群を確定できない科目、ページ取得に失敗した科目は
    自動更新せず「要レビュー」として一覧表示するのみに留める。"""
    from sqlalchemy import select
    from database import AsyncSessionLocal, init_db
    from models import Subject, CourseSection, Syllabus

    await init_db()

    async with AsyncSessionLocal() as session:
        q = select(Subject).where(Subject.faculty.like("%経営学部%"))
        if not force:
            q = q.where(Subject.classification == "経営学部専門科目")
        subjects = (await session.execute(q)).scalars().all()
    subjects = [s for s in subjects if "初年次セミナー" not in s.name]

    print(f"群判定対象: {len(subjects)}件")
    counts = {"updated": 0, "review": 0, "skipped": 0, "not_found": 0}
    review_list: list[str] = []

    async def process_subject(session, subj):
        syl = (await session.execute(
            select(Syllabus)
            .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
            .where(
                CourseSection.subject_id == subj.id,
                Syllabus.timetable_code.isnot(None),
            ).limit(1)
        )).scalar_one_or_none()
        url = make_syllabus_url(syl.timetable_code, f"{subj.faculty or ''}{subj.department or ''}") if syl else None
        if not url:
            counts["skipped"] += 1
            return

        html_text = fetch_html(url)
        if not html_text:
            counts["not_found"] += 1
            review_list.append(f"{subj.name}（404）")
            return

        nc = parse_numbering_code(html_text)
        suffix = nc[-3:] if nc and len(nc) >= 3 else ""
        new_cls = _KEIEI_NC_SUFFIX_TO_CLASSIFICATION.get(suffix)

        if dry_run:
            print(f"  {subj.name}: numbering_code={nc!r} → {new_cls!r}")
        elif new_cls:
            s = await session.get(Subject, subj.id)
            s.classification = new_cls
            counts["updated"] += 1
        else:
            counts["review"] += 1
            review_list.append(f"{subj.name}（コード: {nc or '取得不可'}）")
        time.sleep(REQUEST_INTERVAL_SECONDS)

    BATCH_SIZE = 20
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

    print(f"群判定完了: 更新={counts['updated']}, 要レビュー={counts['review']}, "
          f"スキップ={counts['skipped']}(セクション未登録), 404={counts['not_found']}")
    if review_list:
        print("要レビュー一覧（/admin/keiei で手動判定してください）:")
        for name in review_list:
            print(f"  - {name}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="既取得分も上書き")
    args = parser.parse_args()
    load_env(args.env)

    async def _main():
        # run_credits()・run_senmon_classificationを同一イベントループ内で実行する。
        # asyncio.run()を分けるとSQLAlchemyの非同期エンジンが保持する
        # コネクションプールが前のイベントループに紐づいたままになり、
        # 2回目以降の呼び出しで「Event loop is closed」になるため。
        await run_credits(args.dry_run, args.force)
        await run_senmon_classification(args.dry_run, args.force)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
