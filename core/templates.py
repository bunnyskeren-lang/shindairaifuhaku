from fastapi.templating import Jinja2Templates

from core.config import IS_DEV, JST, VAPID_PUBLIC_KEY


def _to_jst(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST).strftime("%m/%d %H:%M")


templates = Jinja2Templates(directory="templates")
templates.env.filters["jst"] = _to_jst
templates.env.globals["VAPID_PUBLIC_KEY"] = VAPID_PUBLIC_KEY
templates.env.globals["IS_DEV"] = IS_DEV
