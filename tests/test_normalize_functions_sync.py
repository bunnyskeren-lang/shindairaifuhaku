"""normalize_instructor_name/normalize_subject_name（担当教員名の空白除去・科目名のローマ数字
表記統一）は core/config.py・programing files/models.py の2箇所に手動で複製されている
（programing files/models.pyのコメントに明記、docs/SCHEMA_REVIEW.md参照）。片方だけ更新されると
科目名・教員名の正規化結果が本体アプリとスクリプト（import_syllabus.py等）で食い違い、
UNIQUE制約違反や表記ゆれの再発につながるため、2箇所の実装（関数本体+依存する変換テーブル）が
完全一致していることを機械的に検証する。programing files/models.pyはDATABASE_URL等の環境変数を
importするだけで要求するため、実行(import)はせずASTでソースを抽出して比較する。
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_TARGET_NAMES = {
    "normalize_instructor_name", "normalize_subject_name",
    "_HALF_TO_FULL_ROMAN", "_ROMAN_NUMERAL_RE",
}


def _extract_sources(path: Path, names: set[str]) -> dict[str, str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = {}
    for node in tree.body:
        node_name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node_name = node.name
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            node_name = node.targets[0].id
        if node_name in names:
            found[node_name] = ast.get_source_segment(src, node)
    return found


def test_normalize_functions_are_in_sync_across_2_files():
    core_src = _extract_sources(REPO_ROOT / "core" / "config.py", _TARGET_NAMES)
    assert set(core_src) == _TARGET_NAMES, f"core/config.pyに不足: {_TARGET_NAMES - set(core_src)}"

    scripts_src = _extract_sources(REPO_ROOT / "programing files" / "models.py", _TARGET_NAMES)
    assert set(scripts_src) == _TARGET_NAMES, (
        f"programing files/models.pyに不足: {_TARGET_NAMES - set(scripts_src)}"
    )

    for name in _TARGET_NAMES:
        assert core_src[name] == scripts_src[name], (
            f"{name}: core/config.py と programing files/models.py の実装が不一致\n"
            f"--- core/config.py ---\n{core_src[name]}\n"
            f"--- programing files/models.py ---\n{scripts_src[name]}"
        )
