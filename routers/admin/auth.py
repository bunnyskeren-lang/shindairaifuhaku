import secrets as py_secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.config import ADMIN_COOKIE, ADMIN_PASSWORD
from core.rate_limit import rate_limiter
from core.security import make_admin_token
from core.templates import templates

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
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE)
    return response
