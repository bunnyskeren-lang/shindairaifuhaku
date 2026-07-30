"""シラバスURL生成用の対応表(FACULTY_PATH等)は core/config.py・
programing files/fetch_syllabus_info.py の2箇所に手動で複製されている
(core/config.pyのコメントに明記)。新学部追加時に1箇所でも更新漏れがあると
本番でリンク切れになるまで気づけないため、2箇所の値が完全一致していることを
機械的に検証する。
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# core/config.py の変数名 → fetch_syllabus_info.pyの変数名
_TABLE_NAMES = {
    "_SYLLABUS_FACULTY_PATH": "FACULTY_PATH",
    "_ENGINEERING_RANGES": "ENGINEERING_RANGES",
    "_MEDICINE_SUBLETTERS": "MEDICINE_SUBLETTERS",
    "_MEDICINE_RANGES": "MEDICINE_RANGES",
    "_DEPARTMENT_PATH_OVERRIDE": "DEPARTMENT_PATH_OVERRIDE",
}


def _extract_python_literals(path: Path, names: set[str]) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in names
        ):
            found[node.targets[0].id] = ast.literal_eval(node.value)
        # 型アノテーション付き代入(fetch_syllabus_info.py側)にも対応
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in names
            and node.value is not None
        ):
            found[node.target.id] = ast.literal_eval(node.value)
    return found


def _normalize(value):
    """タプルのリスト(ENGINEERING_RANGES等)を比較可能な形に揃える。"""
    if isinstance(value, list):
        return [tuple(v) if isinstance(v, (list, tuple)) else v for v in value]
    return value


def test_syllabus_path_tables_are_in_sync_across_2_files():
    py_names = set(_TABLE_NAMES.keys())
    py_tables = _extract_python_literals(REPO_ROOT / "core" / "config.py", py_names)
    assert set(py_tables) == py_names, f"core/config.pyに不足: {py_names - set(py_tables)}"

    script_names = set(_TABLE_NAMES.values())
    script_tables = _extract_python_literals(
        REPO_ROOT / "programing files" / "fetch_syllabus_info.py", script_names
    )
    assert set(script_tables) == script_names, (
        f"fetch_syllabus_info.pyに不足: {script_names - set(script_tables)}"
    )

    for py_name, script_name in _TABLE_NAMES.items():
        py_val = _normalize(py_tables[py_name])
        script_val = _normalize(script_tables[script_name])
        assert py_val == script_val, (
            f"{py_name}: core/config.py と fetch_syllabus_info.py の値が不一致 "
            f"({py_val} != {script_val})"
        )
