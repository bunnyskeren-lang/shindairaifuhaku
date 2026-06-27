"""
「経営学部専門科目」「未分類」グループの科目を経営学部グループに移動し、
シラバスのナンバリングコードから群（classification）を判定して更新する。

実行:
  python -X utf8 move_to_keiei.py --env dev --dry-run  # 確認のみ
  python -X utf8 move_to_keiei.py --env dev            # 実行（Web取得あり）
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path
from collections import defaultdict
from sqlalchemy import and_

parser = argparse.ArgumentParser()
parser.add_argument("--env", choices=["dev", "prod"], required=True)
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()

env_file = Path(__file__).parent / (".env.dev" if args.env == "dev" else ".env")
for line in env_file.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── _classify_senmon ロジック（main.py から移植・拡張版）────────────────
_SENMON1 = {'経営学基礎論', '会計学基礎論', '市場システム基礎論'}

_SENMON2_EXACT = {
    '経営管理', '経営戦略', '経営史', '経営数学', '経営統計',
    'コーポレートファイナンス', '財務会計', '管理会計',
    'マーケティング', '金融システム', '交通論',
    '経済学', '統計学', '民法', '数学Ⅰ', '数学Ⅱ',
    '経営学入門', '会計学', '国際経営', '経営組織', '財務管理',
    '生産管理', '経営情報', 'ビジネス法',
    # courses テーブルでは「xxx論」「xxx（yyy）」形式になっているものを追加
    'マーケティング論', '財務会計論', '管理会計論', '経営戦略論',
    '国際経営論', '経営組織論', '財務管理論', '生産管理論', '経営情報論',
    'ビジネス法Ｉ', 'ビジネス法Ⅱ',
    '数学Ⅰ（線形代数）', '数学Ⅱ（微分積分）', '民法（財産法）',
}
_SENMON2_PREFIX = ('簿記', '数学（')

_GLOBAL_PREFIXES = (
    'Academic Reading and Writing', 'International Business',
    'International Management', 'Introduction to Finance',
    'Introduction to Marketing', 'Introduction to Management',
    'Introduction to Accounting', 'Business Presentation',
    'Business Strategy', 'Business Leadership',
    'Advanced Financial', 'Advanced Study',
    'Portfolio Management', 'Portfolio Theory',
    'Entrepreneurial', 'Capstone',
    'Overview of Corporate', 'Foundations of Securities',
    'Managerial Accounting', 'Organization Theory',
    'Marketing Management', 'Corporate Finance',
    'Operations Management', 'Statistics for Business',
    'Sustainability Management', 'Innovation and',
    'Supply Chain', 'Brand Management', 'Mergers and',
    'Human Resource Management', 'Global ',
    ' Finansial', 'Financial Accounting',
    '外国文献講義', '外国書講読',
)


def _classify(name: str) -> tuple[str, str]:
    """(classification, senmon_group) を返す。判定不能は ('', '')。"""
    if '初年次セミナー' in name:
        return ('第1群科目', '初年次')
    if name in _SENMON1:
        return ('第1群科目', '第1群')
    if name in _SENMON2_EXACT or any(name.startswith(p) for p in _SENMON2_PREFIX):
        return ('第2群科目', '第2群')
    if any(name.startswith(p) for p in _GLOBAL_PREFIXES):
        return ('グローバル科目群', 'グローバル')
    return ('', '')


async def main():
    from sqlalchemy import select, or_
    from database import AsyncSessionLocal, init_db
    from models import Course

    await init_db()

    # ── Step 1: 対象科目を特定 ──────────────────────────────
    # 「経営学部専門科目」グループ OR 未分類かつ faculty=経営学部 の専門科目のみ
    async with AsyncSessionLocal() as session:
        targets = (await session.execute(
            select(Course).where(
                or_(
                    Course.classification == "経営学部専門科目",
                    and_(
                        or_(Course.classification.is_(None), Course.classification == ""),
                        Course.faculty == "経営学部",
                    ),
                )
            ).order_by(Course.name)
        )).scalars().all()

    print(f"\n対象科目: {len(targets)} 件")
    for c in targets:
        print(f"  [{c.id}] {c.name}  cls={c.classification or '(空)'}  faculty={c.faculty or '(空)'}  category={c.category}")

    if not targets:
        print("対象なし。終了。")
        return

    if args.dry_run:
        print("\n[dry-run] 変更は行いません。")
        return

    # ── Step 2: faculty='経営学部' をセット ────────────────
    print("\n--- faculty='経営学部' をセット ---")
    async with AsyncSessionLocal() as session:
        ids = [c.id for c in targets]
        rows = (await session.execute(
            select(Course).where(Course.id.in_(ids))
        )).scalars().all()
        for c in rows:
            c.faculty = "経営学部"
        await session.commit()
    print(f"{len(ids)} 件に faculty='経営学部' をセットしました。")

    # ── Step 3: _classify ロジックで分類 ─────────────────────
    print("\n--- 分類ロジックで courses.classification を更新 ---")

    classified = []
    unclassified = []

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Course).where(Course.id.in_(ids))
        )).scalars().all()

        for c in rows:
            new_cls, new_grp = _classify(c.name)
            if new_cls:
                old_cls = c.classification
                c.classification = new_cls
                if new_grp and not c.senmon_group:
                    c.senmon_group = new_grp
                classified.append((c.name, old_cls, new_cls, new_grp))
            else:
                unclassified.append(c.name)

        await session.commit()

    print(f"\n自動分類完了: {len(classified)} 件")
    by_cls: dict[str, list] = defaultdict(list)
    for name, _, new_cls, _ in classified:
        by_cls[new_cls].append(name)
    for cls_name, names in sorted(by_cls.items()):
        print(f"  【{cls_name}】{len(names)} 件")
        for n in names:
            print(f"    - {n}")

    print(f"\n【要確認】自動判定できなかった科目: {len(unclassified)} 件")
    if unclassified:
        for name in sorted(unclassified):
            print(f"  - {name}")
        print("\n上記の科目の群（第1群科目／第2群科目／第3群科目／グローバル科目群）を教えてください。")
    else:
        print("すべて自動判定できました。")


asyncio.run(main())
