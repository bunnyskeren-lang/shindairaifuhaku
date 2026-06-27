"""
未分類（classification が空）かつ category='教養' の科目を一括削除するスクリプト。
PendingReview は削除しない（course_name は文字列参照のため影響なし）。

実行例:
  python cleanup_kyoyo_unclassified.py --env dev          # 確認のみ（dry-run）
  python cleanup_kyoyo_unclassified.py --env dev --execute # 実際に削除
"""
import asyncio
import argparse
import os
from dotenv import load_dotenv

parser = argparse.ArgumentParser()
parser.add_argument("--env", choices=["dev", "prod"], required=True)
parser.add_argument("--execute", action="store_true", help="実際に削除する（指定なしはdry-run）")
args = parser.parse_args()

env_file = ".env.dev" if args.env == "dev" else ".env"
load_dotenv(env_file)

from sqlalchemy import select, delete, or_
from database import AsyncSessionLocal
from models import Course


async def main():
    async with AsyncSessionLocal() as session:
        stmt = select(Course).where(
            or_(Course.classification.is_(None), Course.classification == ""),
            Course.category == "教養",
        ).order_by(Course.name)
        rows = (await session.execute(stmt)).scalars().all()

        if not rows:
            print("対象科目なし。")
            return

        print(f"対象科目 {len(rows)} 件:")
        for c in rows:
            print(f"  [{c.id}] {c.name}  instructor={c.instructor or '(なし)'}  term={c.term or '(なし)'}")

        if not args.execute:
            print("\n--execute を付けると実際に削除されます。")
            return

        ids = [c.id for c in rows]
        await session.execute(delete(Course).where(Course.id.in_(ids)))
        await session.commit()
        print(f"\n{len(ids)} 件削除しました。")


asyncio.run(main())
