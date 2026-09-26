"""dev→本番同期スクリプトが団体管理のテーブルに触れないことの回帰テスト（2026-09-26）。

groups / group_payouts、および団体に紐づく user_profiles.group_id / reviews.group_id は
本番へ絶対に反映しない。同期対象を増やす際に紛れ込まないよう、スクリプトのSQL・列名を検査する。
"""
import re
from pathlib import Path

SYNC_SCRIPTS = [
    Path(__file__).resolve().parent.parent / "programing files" / "sync_db_to_prod.py",
]

FORBIDDEN = ("group_payouts", "group_id", "FROM groups", "INTO groups", "UPDATE groups", "JOIN groups")


def _code_only(text: str) -> str:
    """先頭のdocstringと#コメントを除いたコード部分（説明文中の言及は許可するため）。"""
    text = re.sub(r'^""".*?"""', "", text, count=1, flags=re.S)
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_sync_script_never_references_group_tables():
    for path in SYNC_SCRIPTS:
        code = _code_only(path.read_text(encoding="utf-8"))
        for word in FORBIDDEN:
            # display_orders.parent_group は別物なので、テーブル名・group_id列のみを検査する
            assert word not in code, f"{path.name} が {word} に触れている"
