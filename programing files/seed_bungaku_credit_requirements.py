"""
文学部 単位チェッカー・CAP制・必修科目自動登録 初期データ投入スクリプト
使い方:
  python -X utf8 seed_bungaku_credit_requirements.py [--env dev|prod]

学生便覧2026（docs/学生便覧2026/binran_2026_bungaku.pdf 別表第1・別表第2・第6条・第7条）に基づき、
文学部（人文学科1学科のみ、学科分岐なし）の卒業要件（credit_requirements）、
履修登録上限単位数（registration_caps、54単位）、必修科目（required_subjects）を投入する。

必修科目は「別表第2の必要修得単位数と、別表第1の授業科目名が1対1で確定できる科目」のみ登録対象とした
（基盤系4科目のみ）。以下は便覧だけでは学生ごとの対象が確定できないため対象外:
  - 初年次セミナー: DBに該当科目が未登録（時間割コード不明、別途調査要）
  - Academic English（外国語第Ⅰ）8科目: 1科目あたり28〜30の並行クラス（教員別）が存在し、
    学生がどのクラスに割り当てられるか判定する情報がDBに無い
  - 外国語第Ⅱ（独仏中露）: 学生本人が言語を選択するため確定できない
  - グローバル人文学英語科目: 5科目からの選択科目のため確定できない
  - 卒業論文: 指導教員ごとに別科目（43名分）のため、学生の配属先が分からないと確定できない

冪等: 既存のcategory_idがあれば値を更新、無ければ追加する。registration_caps/required_subjectsも
一意制約で既存行を検索し、無ければ追加する。
"""
import asyncio
import os
import sys
from pathlib import Path

FACULTY = "文学部"
DEPARTMENT = "人文学科"
YEAR = 2026
CAP_CREDITS = 54  # 文学部規則第7条

GAIGO2_NOTE = "ドイツ語，フランス語，中国語，ロシア語のうちから1つの言語を選択のこと。"
JINBUN_SOUGOU_NOTE = "哲学，心理学Ａ，心理学Ｂ，倫理学を除く教養科目人文系・総合系の科目から0～4単位。"
SOTSURON_KANREN_NOTE = "専修ごとに「専修別専門科目履修に関する内規」で指定された科目から42単位。便覧だけでは専修別の内訳が確定できないため合計値のみ登録。"
JIYU_NOTE = "教養科目総合系の「外国語セミナー」「多言語セミナー」は計4単位まで、教養科目外国語系・他学部専門科目は4単位まで算入可。"
SHONEN_NOTE = "DBに該当科目（初年次セミナー）が未登録のため必修科目自動登録の対象外（別途時間割コードの調査が必要）。"

ROWS = [
    # 教養科目
    dict(category_id="bungaku_kiban", label="基盤系", group_name="教養科目",
         sort_order=10, required_credits=4),
    dict(category_id="bungaku_jinbun", label="人文系", group_name="教養科目",
         sort_order=20, required_credits=0, note=JINBUN_SOUGOU_NOTE),
    dict(category_id="bungaku_shakai", label="社会系", group_name="教養科目",
         sort_order=21, required_credits=0),
    dict(category_id="bungaku_shizen", label="自然系", group_name="教養科目",
         sort_order=22, required_credits=0),
    dict(category_id="bungaku_sougou", label="総合系", group_name="教養科目",
         sort_order=23, required_credits=0, note=JINBUN_SOUGOU_NOTE),
    dict(category_id="bungaku_shakai_shizen", label="社会系＋自然系 計", group_name="教養科目",
         sort_order=24, required_credits=8,
         combined_of=["bungaku_shakai", "bungaku_shizen"]),
    dict(category_id="bungaku_kyoyo_all", label="人文系＋社会系＋自然系＋総合系 計", group_name="教養科目",
         sort_order=25, required_credits=12,
         combined_of=["bungaku_jinbun", "bungaku_shakai", "bungaku_shizen", "bungaku_sougou"]),
    dict(category_id="bungaku_gaigo1", label="外国語第Ⅰ", group_name="教養科目",
         sort_order=30, required_credits=4),
    dict(category_id="bungaku_gaigo2", label="外国語第Ⅱ", group_name="教養科目",
         sort_order=40, required_credits=4, note=GAIGO2_NOTE),
    # 専門科目
    dict(category_id="bungaku_shonen", label="初年次セミナー", group_name="専門科目",
         sort_order=50, required_credits=1, note=SHONEN_NOTE),
    dict(category_id="bungaku_kiso_gaigo", label="基礎科目（外国語）", group_name="専門科目",
         sort_order=51, required_credits=1,
         note="外国語第Ⅰで選択した独・仏・中・露いずれかの言語科目。"),
    dict(category_id="bungaku_kiso_sonota", label="基礎科目（その他）", group_name="専門科目",
         sort_order=52, required_credits=10),
    dict(category_id="bungaku_global_eng", label="グローバル人文学英語科目", group_name="専門科目",
         sort_order=53, required_credits=2),
    dict(category_id="bungaku_sotsuron_kanren", label="卒業論文関連科目", group_name="専門科目",
         sort_order=54, required_credits=42, note=SOTSURON_KANREN_NOTE),
    dict(category_id="bungaku_sotsuron", label="卒業論文", group_name="専門科目",
         sort_order=55, required_credits=10),
    dict(category_id="bungaku_jiyu", label="自由選択科目", group_name="専門科目",
         sort_order=56, required_credits=35, note=JIYU_NOTE),
    dict(category_id="bungaku_senmon_only", label="専門科目 計", group_name="専門科目",
         sort_order=100, required_credits=101,
         combined_of=[
             "bungaku_shonen", "bungaku_kiso_gaigo", "bungaku_kiso_sonota",
             "bungaku_global_eng", "bungaku_sotsuron_kanren", "bungaku_sotsuron", "bungaku_jiyu",
         ]),
]

# 必修科目自動登録（1年次、基盤系4科目のみ。DB上のsubject_id・faculty='教養教育院'で確認済み）
REQUIRED_SUBJECTS = [
    dict(grade=1, subject_id=5, note="教養とは何か（基盤系）"),
    dict(grade=1, subject_id=6, note="多言語と多文化の世界（基盤系）"),
    dict(grade=1, subject_id=7, note="情報基礎（基盤系）"),
    dict(grade=1, subject_id=2, note="データサイエンス基礎学（基盤系）"),
]


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
    from models import CreditRequirement, RegistrationCap, RequiredSubject, Subject

    async with AsyncSessionLocal() as session:
        added, updated = 0, 0
        for row in ROWS:
            row = dict(row, faculty=FACULTY, department=DEPARTMENT)
            existing = await session.get(CreditRequirement, row["category_id"])
            if existing:
                for k, v in row.items():
                    setattr(existing, k, v)
                updated += 1
            else:
                session.add(CreditRequirement(**row))
                added += 1
        await session.commit()
        print(f"credit_requirements: {added}件追加 / {updated}件更新")

        cap_existing = (await session.execute(
            select(RegistrationCap).where(
                RegistrationCap.faculty == FACULTY,
                RegistrationCap.department.is_(None),
                RegistrationCap.year == YEAR,
            )
        )).scalar_one_or_none()
        if cap_existing:
            cap_existing.max_credits = CAP_CREDITS
            print(f"registration_caps: 既存行を更新（{FACULTY}/{YEAR}年度={CAP_CREDITS}単位）")
        else:
            session.add(RegistrationCap(faculty=FACULTY, department=None, year=YEAR, max_credits=CAP_CREDITS))
            print(f"registration_caps: 新規追加（{FACULTY}/{YEAR}年度={CAP_CREDITS}単位）")
        await session.commit()

        rs_added, rs_updated, rs_skipped = 0, 0, 0
        for rs in REQUIRED_SUBJECTS:
            subj = await session.get(Subject, rs["subject_id"])
            if not subj:
                print(f"WARNING: subject_id={rs['subject_id']} が見つかりません。スキップします", file=sys.stderr)
                rs_skipped += 1
                continue
            existing_rs = (await session.execute(
                select(RequiredSubject).where(
                    RequiredSubject.faculty == FACULTY,
                    RequiredSubject.department == DEPARTMENT,
                    RequiredSubject.grade == rs["grade"],
                    RequiredSubject.subject_id == rs["subject_id"],
                )
            )).scalar_one_or_none()
            if existing_rs:
                existing_rs.note = rs.get("note")
                rs_updated += 1
            else:
                session.add(RequiredSubject(
                    faculty=FACULTY, department=DEPARTMENT, grade=rs["grade"],
                    subject_id=rs["subject_id"], note=rs.get("note"),
                ))
                rs_added += 1
        await session.commit()
        print(f"required_subjects: {rs_added}件追加 / {rs_updated}件更新 / {rs_skipped}件スキップ")


def main():
    import argparse
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
