import re
from pathlib import Path

CANDIDATES_FILE = (
    Path(__file__).resolve().parent.parent
    / "docs" / "学生便覧2026" / "IMPLEMENTATION_CANDIDATES.md"
)

_STATUS_RE = re.compile(r"→\s*(.+)$")


def parse_candidates() -> list[dict]:
    """IMPLEMENTATION_CANDIDATES.md（学生便覧ver2 vs DBの齟齬記録）をパースする。"""
    if not CANDIDATES_FILE.exists():
        return []
    lines = CANDIDATES_FILE.read_text(encoding="utf-8").splitlines()

    candidates: list[dict] = []
    faculty = None
    current: dict | None = None

    def _flush():
        if current is None:
            return
        body = current.pop("body_lines")
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
        current["body"] = "\n".join(body)
        candidates.append(current)

    for line in lines:
        if line.startswith("## "):
            _flush()
            current = None
            faculty = line[3:].strip()
            continue
        if line.startswith("### "):
            _flush()
            title = line[4:].strip()
            m = _STATUS_RE.search(title)
            status = m.group(1).strip() if m else "未対応"
            current = {"faculty": faculty, "title": title, "status": status, "body_lines": []}
            continue
        if current is not None:
            if line.startswith("# ") or line.strip() == "---":
                continue
            current["body_lines"].append(line)
    _flush()
    return candidates
