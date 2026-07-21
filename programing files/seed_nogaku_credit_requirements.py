"""
農学部 単位チェッカー・CAP制 初期データ投入スクリプト
使い方:
  python -X utf8 seed_nogaku_credit_requirements.py [--env dev|prod]

学生便覧2026（docs/学生便覧2026/binran_2026_nogaku_kisoku.pdf 別表第2・第6条）に基づき、
農学部が2年次から分かれる6コースそれぞれの卒業要件（credit_requirements）と、
履修登録上限単位数（registration_caps、54単位・全コース共通）を投入する。

冪等: 既存のcategory_idがあれば値を更新、無ければ追加する。registration_capsは
(faculty, department, year)の一意制約で既存行を検索し、無ければ追加する。
"""
import asyncio

from _env import load_env

FACULTY = "農学部"
YEAR = 2026
CAP_CREDITS = 54  # 学部規則第6条

# コースごとの必要単位数（別表第2）。kyoyo_all=人文系＋社会系＋自然系＋総合系の合計必要単位数、
# kyotsu=共通専門基礎科目、senmon=専門科目（共通専門基礎科目を除く）
COURSES = {
    "seisan": {
        "name": "生産環境工学コース",
        "kyoyo_all": 10, "kyotsu": 15, "senmon": 88,
        "senmon_note": (
            "生産環境工学コースは便覧別表第2で「専門科目については別に定める指定科目から"
            "修得すること」とされており、必修・選択の内訳が便覧だけでは確定できないため、"
            "合計値（88単位）のみ登録しています。詳細はdocs/学生便覧2026/nogaku/"
            "NOGAKU_CREDIT_CHECKER_TODO.md参照。"
        ),
    },
    "keizai":     {"name": "食料環境経済学コース",   "kyoyo_all": 12, "kyotsu": 16, "senmon": 85},
    "doubutsu":   {"name": "応用動物学コース",       "kyoyo_all": 12, "kyotsu": 18, "senmon": 83},
    "shokubutsu": {"name": "応用植物学コース",       "kyoyo_all": 12, "kyotsu": 14, "senmon": 87},
    "seimeika":   {"name": "応用生命化学コース",     "kyoyo_all": 10, "kyotsu": 8,  "senmon": 95},
    "kinou":      {"name": "応用機能生物学コース",   "kyoyo_all": 12, "kyotsu": 10, "senmon": 91},
}

KYOYO_ALL_NOTE = "教養科目（人文系・社会系）の必要修得単位数を超えて修得した単位数を含む"
GAIGO2_NOTE = "ドイツ語，フランス語，中国語及びロシア語のうちから1つの言語を選択のこと。"


def build_rows(prefix: str, course: dict) -> list[dict]:
    name = course["name"]
    return [
        dict(category_id=f"{prefix}_kiban", label="基盤系", group_name="教養科目",
             sort_order=10, required_credits=4, faculty=FACULTY, department=name),
        dict(category_id=f"{prefix}_jinbun", label="人文系", group_name="教養科目",
             sort_order=20, required_credits=0, faculty=FACULTY, department=name),
        dict(category_id=f"{prefix}_shakai", label="社会系", group_name="教養科目",
             sort_order=21, required_credits=0, faculty=FACULTY, department=name),
        dict(category_id=f"{prefix}_shizen", label="自然系", group_name="教養科目",
             sort_order=22, required_credits=0, faculty=FACULTY, department=name),
        dict(category_id=f"{prefix}_sougou", label="総合系", group_name="教養科目",
             sort_order=23, required_credits=0, faculty=FACULTY, department=name),
        dict(category_id=f"{prefix}_jinbun_shakai", label="人文系＋社会系 計", group_name="教養科目",
             sort_order=24, required_credits=8, faculty=FACULTY, department=name,
             combined_of=[f"{prefix}_jinbun", f"{prefix}_shakai"]),
        dict(category_id=f"{prefix}_kyoyo_all", label="人文系＋社会系＋自然系＋総合系 計", group_name="教養科目",
             sort_order=25, required_credits=course["kyoyo_all"], faculty=FACULTY, department=name,
             combined_of=[f"{prefix}_jinbun", f"{prefix}_shakai", f"{prefix}_shizen", f"{prefix}_sougou"],
             note=KYOYO_ALL_NOTE),
        dict(category_id=f"{prefix}_gaigo1", label="外国語第Ⅰ", group_name="教養科目",
             sort_order=30, required_credits=4, faculty=FACULTY, department=name),
        dict(category_id=f"{prefix}_gaigo2", label="外国語第Ⅱ", group_name="教養科目",
             sort_order=40, required_credits=4, faculty=FACULTY, department=name, note=GAIGO2_NOTE),
        dict(category_id=f"{prefix}_kenko", label="健康・スポーツ科学系", group_name="教養科目",
             sort_order=45, required_credits=1, faculty=FACULTY, department=name),
        dict(category_id=f"{prefix}_kyotsu", label="共通専門基礎科目", group_name="専門科目",
             sort_order=50, required_credits=course["kyotsu"], faculty=FACULTY, department=name),
        dict(category_id=f"{prefix}_senmon_only", label="専門科目（共通専門基礎科目を除く）", group_name="専門科目",
             sort_order=100, required_credits=course["senmon"], faculty=FACULTY, department=name,
             note=course.get("senmon_note")),
    ]


async def run():
    from sqlalchemy import select
    from database import AsyncSessionLocal
    from models import CreditRequirement, RegistrationCap

    async with AsyncSessionLocal() as session:
        added, updated = 0, 0
        for prefix, course in COURSES.items():
            for row in build_rows(prefix, course):
                existing = await session.get(CreditRequirement, row["category_id"])
                if existing:
                    for k, v in row.items():
                        # note未指定(None)の行を上書きすると、update_nogaku_credit_notes.pyが
                        # 追記した対象科目一覧などの既存注記が再実行のたびに消えてしまうため保持する
                        if k == "note" and v is None:
                            continue
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
