import secrets as py_secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core import cache
from core.config import ADMIN_COOKIE, ADMIN_PASSWORD
from core.rate_limit import rate_limiter
from core.security import make_admin_token
from core.templates import templates
from database import AsyncSessionLocal
from models import AdminSession

router = APIRouter()

# 修正理由: パスワード総当たり攻撃を防ぐため、IPアドレス単位で1分あたり5回までに制限する
_login_rate_limit = rate_limiter(max_requests=5, window_seconds=60)


@router.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, next: str = "/admin"):
    return templates.TemplateResponse("admin/login.html", {"request": request, "next": next, "error": False})


@router.post("/admin/login")
async def admin_login(
    request: Request,
    password: str = Form(...),
    next: str = Form(default="/admin"),
    _rl: None = Depends(_login_rate_limit),
):
    if not py_secrets.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
        return templates.TemplateResponse("admin/login.html", {"request": request, "next": next, "error": True})
    safe_next = next if (next.startswith("/admin") and ".." not in next) else "/admin"
    response = RedirectResponse(safe_next, status_code=303)
    response.set_cookie(ADMIN_COOKIE, make_admin_token(), httponly=True, samesite="strict", secure=True)
    return response


@router.post("/admin/logout")
async def admin_logout():
    # Cookie削除だけでは署名的に有効な旧トークンがコピーされていた場合TTL(4時間)いっぱい
    # 使えてしまうため、サーバー側でもこの時刻以前に発行された全トークンを一括失効させる
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        row = await session.get(AdminSession, 1)
        if row:
            row.revoked_before = now
        else:
            session.add(AdminSession(id=1, revoked_before=now))
        await session.commit()
    cache.invalidate_admin_revoke_cache()
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE)
    return response
