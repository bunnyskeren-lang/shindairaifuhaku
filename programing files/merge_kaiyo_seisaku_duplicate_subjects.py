"""
海洋政策科学部のシラバスインポートで、括弧内の担当教員名・対象学年等が
科目名に含まれていたために別科目として作成されてしまった重複subjectsを統合する。

対象パターン（括弧を除いたときに同一名称になるもの）:
  海洋ガバナンス特殊講義-1〜4、海技士総合ゼミ、物理学実験、材料加工・機械製図、化学実験

「物理学実験」は他学部の既存科目(id=244)と名称が一致するため、そちらへ統合する
（uq_subjects_nameのUNIQUE制約により同名科目は共存できない）。

使い方:
  python -X utf8 merge_kaiyo_seisaku_duplicate_subjects.py --env dev
"""
import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PATTERNS = ['海洋ガバナンス特殊講義', '海技士総合ゼミ', '物理学実験', '材料加工・機械製図', '化学実験']


def strip_paren(name: str) -> str:
    return re.sub(r'[（(][^）(]*[）)]$', '', name).strip()


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


async def merge():
    from sqlalchemy import select
    from database import AsyncSessionLocal
    from models import Subject, CourseSection

    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(Subject).where(Subject.faculty == '海洋政策科学部'))).scalars().all()
        groups: dict[str, list[Subject]] = {}
        for subj in rows:
            if any(subj.name.startswith(p) for p in PATTERNS):
                groups.setdefault(strip_paren(subj.name), []).append(subj)

        total_merged = 0
        total_deleted = 0
        for stripped, subs in sorted(groups.items()):
            if len(subs) < 2:
                continue
            subs.sort(key=lambda x: x.id)
            ids_in_group = {x.id for x in subs}
            existing_external = (await s.execute(
                select(Subject).where(Subject.name == stripped, Subject.id.notin_(ids_in_group))
            )).scalar_one_or_none()

            if existing_external is not None:
                canonical, others = existing_external, subs
            else:
                canonical, others = subs[0], subs[1:]

            for other in others:
                cs_list = (await s.execute(
                    select(CourseSection).where(CourseSection.subject_id == other.id)
                )).scalars().all()
                for cs in cs_list:
                    cs.subject_id = canonical.id
                    total_merged += 1
                await s.delete(other)
                total_deleted += 1

            if canonical.name != stripped:
                canonical.name = stripped

            print(f'{stripped}: canonical_id={canonical.id}(external={existing_external is not None}), merged={len(others)}')

        await s.commit()
        print(f'--- CourseSection付け替え: {total_merged}件, Subject削除: {total_deleted}件 ---')


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    args = parser.parse_args()
    load_env(args.env)
    if args.env == "prod":
        confirm = input("本番DBに実行します。よろしいですか？ (yes/no): ")
        if confirm.strip().lower() != "yes":
            print("中止しました")
            return
    asyncio.run(merge())


if __name__ == "__main__":
    main()
