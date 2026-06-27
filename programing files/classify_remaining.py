"""
残り49件の未分類科目を分類するスクリプト。
シラバスデータに基づき、経営学部生履修不可/第3群/第2群/グローバルを設定。
"""
import asyncio
import argparse
import os
from pathlib import Path
from collections import defaultdict

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

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

# (classification, senmon_group)
COURSE_MAP = {
    # ── 経営学部生履修不可（高度教養科目で「履修不可」明記） ────────────────
    'エッセンシャル経営学（高度教養科目）':                          ('経営学部生履修不可', None),
    'ゲーム理論（高度教養科目）':                                    ('経営学部生履修不可', None),
    'マーケティング・マネジメント（高度教養科目）':                   ('経営学部生履修不可', None),
    'リスク・マネジメント（高度教養科目）':                           ('経営学部生履修不可', None),
    '国際交通（高度教養科目）':                                      ('経営学部生履修不可', None),
    '市場システム特殊講義（産業組織）（高度教養科目）':               ('経営学部生履修不可', None),
    '人的資源管理（高度教養科目）':                                  ('経営学部生履修不可', None),
    '監査論（高度教養科目）':                                        ('経営学部生履修不可', None),
    '流通システム（高度教養科目）':                                  ('経営学部生履修不可', None),
    '社会環境会計（高度教養科目）':                                  ('経営学部生履修不可', None),
    '会計学特殊講義（財務諸表分析）（高度教養科目）':                 ('経営学部生履修不可', None),
    '工業経営（高度教養科目）':                                      ('経営学部生履修不可', None),
    '顧客関係管理（高度教養科目）':                                  ('経営学部生履修不可', None),
    '高度教養セミナー経営学部（グローバル企業へのタックス・コンサルティング）': ('経営学部生履修不可', None),
    '高度教養セミナー経営学部（ソーシャルビジネスプランニング）':      ('経営学部生履修不可', None),
    '高度教養セミナー経営学部（リーダーシップ開発入門）':             ('経営学部生履修不可', None),

    # ── 第3群科目（経営学部生可の高度教養科目を含む） ────────────────────────
    '市場システム特殊講義（産業組織）':                              ('第3群科目', '第3群'),
    # 以下は高度教養科目区分だが経営学部生も履修可（条件付き）：追加済み（上部）
    '高度教養セミナー経営学部（ビジネスリーダーとの議論と対話）':     ('第3群科目', '第3群'),
    # 高度教養科目だが経営学部生も可（トップマネジメント講座系）
    '実践　顧客基点のデジタルトランスフォーメーション（電通デジタル寄附講義）（高度教養科目）': ('第3群科目', '第3群'),
    '事業承継と中堅・中小企業のM&A(日本M＆Aセンター寄附講義）（高度教養科目）':                    ('第3群科目', '第3群'),
    # 通常の第3群専門科目
    'アントレプレナーシップ':                                        ('第3群科目', '第3群'),
    'インターンシップA':                                             ('第3群科目', '第3群'),
    'インターンシップB':                                             ('第3群科目', '第3群'),
    'フィールド調査実習A':                                           ('第3群科目', '第3群'),
    'フィールド調査実習B':                                           ('第3群科目', '第3群'),
    'テクノロジー・マネジメント':                                    ('第3群科目', '第3群'),
    'リーダーシップ論':                                              ('第3群科目', '第3群'),
    '中小企業論':                                                    ('第3群科目', '第3群'),
    '人的資源管理論':                                                ('第3群科目', '第3群'),
    '個人と組織のパーパス発見セミナー':                              ('第3群科目', '第3群'),
    '商法（会社法）':                                                ('第3群科目', '第3群'),
    '多国籍企業論':                                                  ('第3群科目', '第3群'),
    '意思決定論':                                                    ('第3群科目', '第3群'),
    '流通システム論':                                                ('第3群科目', '第3群'),
    '消費者行動論':                                                  ('第3群科目', '第3群'),
    '産業組織論':                                                    ('第3群科目', '第3群'),
    '税務会計論':                                                    ('第3群科目', '第3群'),
    '経営倫理':                                                      ('第3群科目', '第3群'),
    '経営科学の基礎':                                                ('第3群科目', '第3群'),
    '証券論':                                                        ('第3群科目', '第3群'),
    '起業指導A':                                                     ('第3群科目', '第3群'),
    '金融論':                                                        ('第3群科目', '第3群'),

    # ── 第2群科目 ─────────────────────────────────────────────────────────
    'マクロ経済学':                                                  ('第2群科目', '第2群'),
    'ミクロ経済学':                                                  ('第2群科目', '第2群'),

    # ── グローバル科目群 ───────────────────────────────────────────────────
    'グローバル・スタートアップ・セミナー':                          ('グローバル科目群', 'グローバル'),
    'グローバル経営インターンシップA':                               ('グローバル科目群', 'グローバル'),
    'グローバル経営インターンシップB':                               ('グローバル科目群', 'グローバル'),
    'ビジネス・リーダーシップ・セミナーA':                           ('グローバル科目群', 'グローバル'),
    'ファイナンシャル・アカウンティング':                            ('グローバル科目群', 'グローバル'),
}


async def main():
    from sqlalchemy import select
    from database import AsyncSessionLocal, init_db
    from models import Course

    await init_db()

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Course).where(Course.name.in_(list(COURSE_MAP.keys())))
        )).scalars().all()

    found_names = {c.name for c in rows}
    missing = set(COURSE_MAP.keys()) - found_names
    if missing:
        print(f"⚠ DBに見つからない科目: {len(missing)} 件")
        for n in sorted(missing):
            print(f"  - {n}")

    print(f"\n更新対象: {len(rows)} 件")

    if args.dry_run:
        by_cls: dict[str, list] = defaultdict(list)
        for c in sorted(rows, key=lambda x: x.name):
            new_cls, new_grp = COURSE_MAP[c.name]
            by_cls[new_cls].append(c.name)
        for cls_name, names in sorted(by_cls.items()):
            print(f"\n【{cls_name}】{len(names)} 件")
            for n in sorted(names):
                print(f"  - {n}")
        print("\n[dry-run] 変更は行いません。")
        return

    async with AsyncSessionLocal() as session:
        ids = [c.id for c in rows]
        db_rows = (await session.execute(
            select(Course).where(Course.id.in_(ids))
        )).scalars().all()
        for c in db_rows:
            new_cls, new_grp = COURSE_MAP[c.name]
            c.classification = new_cls
            c.senmon_group = new_grp
        await session.commit()

    by_cls2: dict[str, list] = defaultdict(list)
    for c in rows:
        new_cls, _ = COURSE_MAP[c.name]
        by_cls2[new_cls].append(c.name)

    print(f"\n✓ {len(rows)} 件を更新しました。")
    for cls_name, names in sorted(by_cls2.items()):
        print(f"  【{cls_name}】{len(names)} 件")


asyncio.run(main())
