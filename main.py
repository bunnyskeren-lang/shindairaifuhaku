import asyncio
import traceback as _traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse as _JSONResponse

from core import cache, line_client, prewarm
from core.activity_log import save_error_log
from database import engine, init_db
from routers import health, liff_api, pages, richmenu, seiseki_api, timetable_api, webhook
from routers.admin import (
    auth as admin_auth,
    courses as admin_courses,
    credit_requirements as admin_credit_requirements,
    dashboard as admin_dashboard,
    reviews as admin_reviews,
    stats as admin_stats,
    sync as admin_sync,
    timetable_check as admin_timetable_check,
    users_errors as admin_users_errors,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
        print("DB OK", flush=True)
        asyncio.create_task(cache.reload_senmon_cache())
        asyncio.create_task(prewarm.prewarm_caches())
    except Exception as e:
        _traceback.print_exc()
        print(f"DB ERROR: {e}", flush=True)
        await engine.dispose()
        print("Engine disposed and reset after startup error", flush=True)
    await line_client.startup()
    ping_task = asyncio.create_task(line_client.self_ping())
    yield
    ping_task.cancel()
    try:
        await ping_task
    except asyncio.CancelledError:
        pass
    await line_client.shutdown()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://liff.line.me", "https://access.line.me"],
    allow_origin_regex=r"https://.*\.line\.me",
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    await save_error_log(exc, action=f"validation:{request.method} {request.url.path}")
    return _JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code >= 500:
        await save_error_log(exc, action=f"HTTP{exc.status_code} {request.method} {request.url.path}")
    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    await save_error_log(exc, action=f"{request.method} {request.url.path}")
    return _JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


# ── ルーター登録 ────────────────────────────────────────────────
app.include_router(webhook.router)
app.include_router(health.router)
app.include_router(pages.router)
app.include_router(richmenu.router)
app.include_router(liff_api.router)
app.include_router(timetable_api.router)
app.include_router(seiseki_api.router)

app.include_router(admin_auth.router)
app.include_router(admin_dashboard.router)
app.include_router(admin_courses.router)
app.include_router(admin_reviews.router)
app.include_router(admin_users_errors.router)
app.include_router(admin_stats.router)
app.include_router(admin_timetable_check.router)
app.include_router(admin_credit_requirements.router)
app.include_router(admin_sync.router)
