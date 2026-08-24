from fastapi.templating import Jinja2Templates

from core.config import IS_DEV, JST, VAPID_PUBLIC_KEY
from core.grading_method import (
    format_grading_method_for_edit,
    format_grading_method_summary,
    parse_grading_method,
)


def _to_jst(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST).strftime("%m/%d %H:%M")


templates = Jinja2Templates(directory="templates")
templates.env.filters["jst"] = _to_jst
templates.env.filters["grading_parts"] = parse_grading_method
templates.env.filters["grading_edit_text"] = format_grading_method_for_edit
templates.env.filters["grading_summary"] = format_grading_method_summary
templates.env.globals["VAPID_PUBLIC_KEY"] = VAPID_PUBLIC_KEY
templates.env.globals["IS_DEV"] = IS_DEV
