"""
農学部 単位チェッカー: 専門科目区分(kyotsu/senmon_only)のnoteに対象科目一覧を追記する
使い方:
  python -X utf8 update_nogaku_credit_notes.py [--env dev|prod]

機械工学科の教養科目区分で先行導入した「対象科目：〜」形式のnote
（[[project_credit_checker_note_subject_breakdown]]）を、農学部の共通専門基礎科目・
専門科目区分にも適用する。seed_nogaku_subject_categories.pyで投入済みの
subject_credit_categoriesから対象科目名を集計し、note列に追記する
（既存の注記があれば1行目に残し、対象科目一覧は追加の行として付与する）。
"""
import os
import sys
from pathlib import Path

COURSE_PREFIXES = ["seisan", "keizai", "doubutsu", "shokubutsu", "seimeika", "kinou"]
CATEGORY_SUFFIXES = ["kyotsu", "senmon_only"]


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


async def run():
    from sqlalchemy import select
    from database import AsyncSessionLocal
    from models import CreditRequirement, Subject, SubjectCreditCategory

    async with AsyncSessionLocal() as session:
        for prefix in COURSE_PREFIXES:
            for suffix in CATEGORY_SUFFIXES:
                cat_id = f"{prefix}_{suffix}"
                req = await session.get(CreditRequirement, cat_id)
                if not req:
                    print(f"skip (not found): {cat_id}")
                    continue
                names = (await session.execute(
                    select(Subject.name)
                    .join(SubjectCreditCategory, SubjectCreditCategory.subject_id == Subject.id)
                    .where(SubjectCreditCategory.category_id == cat_id)
                    .order_by(Subject.name)
                )).scalars().all()
                if not names:
                    print(f"skip (no subjects): {cat_id}")
                    continue
                subject_line = "対象科目：" + "、".join(names)
                existing = (req.note or "").strip()
                # 既存の注記（生産環境工学コースの特例注記等）があれば1行目として残す
                req.note = f"{existing}\n{subject_line}" if existing else subject_line
                print(f"{cat_id}: {len(names)}件")
        await session.commit()


def main():
    import argparse
    import asyncio
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    args = parser.parse_args()

    load_env(args.env)
    if args.env == "prod":
        confirm = input("本番DBに投入します。よろしいですか？ (yes/no): ")
        if confirm.strip().lower() != "yes":
            print("中止しました")
            return
    asyncio.run(run())


if __name__ == "__main__":
    main()
