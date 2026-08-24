"""Review.grading_method（成績評価方法）の構造化パース。

2026-08-25以前は「形式:講義・演習 / 出席:たまにあり / 評価:定期試験(60%)」のような
独自区切り文字列（' / '・':'・'・'・括弧の4種類が入れ子）で保存していた。区切り文字に
ユーザー自由記述の補足欄がエスケープなしで埋め込まれるため、補足欄に '/' や ':' を
含む文字を書かれると表示側の素朴な文字列分割（templates/liff/course.html・
templates/admin/reviews.html の2箇所で独立に再実装されていた）が壊れる不具合があった。

2026-08-25以降は JSON配列 [{"label": "...", "text": "..."}, ...] で保存する
（label=""は見出し無しの単独テキスト行）。過去に投稿済みのレビューは投稿削除禁止方針
（CLAUDE.md）のため旧形式のまま残り続けるので、parse_grading_method() はJSONとして
読めない場合に旧形式へフォールパースする。
"""
import json


def parse_grading_method(raw: str | None) -> list[dict[str, str]]:
    """保存済みのgrading_methodを表示用の[{"label", "text"}, ...]へ変換する。
    新形式(JSON配列)・旧形式(区切り文字列)のどちらでも読める。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, list):
        result = []
        for item in data:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            result.append({"label": str(item.get("label", "")).strip(), "text": text})
        return result
    # 旧形式（' / '区切り、'label:text'）へのフォールバック
    result = []
    for part in raw.split(" / "):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            label, _, text = part.partition(":")
            result.append({"label": label.strip(), "text": text.strip()})
        else:
            result.append({"label": "", "text": part})
    return result


def format_grading_method_summary(raw: str | None) -> str:
    """管理画面の一覧テーブルなど、狭いセルに1行で収める簡易表示用。"""
    parts = parse_grading_method(raw)
    if not parts:
        return "―"
    return " / ".join(f"{p['label']}:{p['text']}" if p["label"] else p["text"] for p in parts)


def format_grading_method_for_edit(raw: str | None) -> str:
    """管理画面の編集textarea向けに、パース結果を1行1項目の「ラベル: テキスト」形式へ変換する。"""
    lines = []
    for part in parse_grading_method(raw):
        lines.append(f"{part['label']}: {part['text']}" if part["label"] else part["text"])
    return "\n".join(lines)


def build_grading_method_from_edit_text(text: str) -> str | None:
    """管理画面の編集textarea（1行1項目の「ラベル: テキスト」形式）から、
    保存用のJSON文字列を組み立てる。空行は無視する。全行が空ならNoneを返す。"""
    parts = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            label, _, value = line.partition(":")
            parts.append({"label": label.strip(), "text": value.strip()})
        else:
            parts.append({"label": "", "text": line})
    if not parts:
        return None
    return json.dumps(parts, ensure_ascii=False)
