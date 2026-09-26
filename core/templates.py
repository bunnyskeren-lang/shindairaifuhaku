import json as _json
from pathlib import Path

from fastapi.templating import Jinja2Templates

from core.config import IS_DEV, JST, VAPID_PUBLIC_KEY
from core.grading_method import (
    format_grading_method_for_edit,
    format_grading_method_summary,
    parse_grading_method,
)
from datetime import UTC


def _to_jst(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(JST).strftime("%m/%d %H:%M")


def _from_json(s):
    """文字列をJSONとしてパースし、失敗したらNoneを返す（管理画面のテレメトリ表示用）。"""
    try:
        return _json.loads(s)
    except (TypeError, ValueError):
        return None


class _Templates(Jinja2Templates):
    """旧書式 TemplateResponse(name, {"request": request, ...}) を新書式
    TemplateResponse(request, name, context) へ変換する。Starlette側で旧書式が
    非推奨（DeprecationWarning）になったため、呼び出し32箇所を個別に書き換える代わりに
    ここ1箇所で吸収する。新書式で呼んでもそのまま動く。"""

    def TemplateResponse(self, *args, **kwargs):
        if args and isinstance(args[0], str):
            name, *rest = args
            context = rest[0] if rest else kwargs.pop("context", {})
            rest = rest[1:]
            return super().TemplateResponse(context["request"], name, context, *rest, **kwargs)
        return super().TemplateResponse(*args, **kwargs)


templates = _Templates(directory="templates")
templates.env.filters["jst"] = _to_jst
templates.env.filters["fromjson"] = _from_json
templates.env.filters["grading_parts"] = parse_grading_method
templates.env.filters["grading_edit_text"] = format_grading_method_for_edit
templates.env.filters["grading_summary"] = format_grading_method_summary
templates.env.globals["VAPID_PUBLIC_KEY"] = VAPID_PUBLIC_KEY
templates.env.globals["IS_DEV"] = IS_DEV


def _static_version(filename: str) -> str:
    """静的ファイルの更新時刻。?v=に付けて、更新後に古いキャッシュが使われないようにする。"""
    try:
        return str(int((Path("static") / filename).stat().st_mtime))
    except OSError:
        return "0"


templates.env.globals["static_version"] = _static_version
