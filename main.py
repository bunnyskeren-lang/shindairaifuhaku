import asyncio
import traceback as _traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse as _JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core import backup, cache, liff_auth, line_client, prewarm
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
    await liff_auth.startup()
    ping_task = asyncio.create_task(line_client.self_ping())
    backup_task = asyncio.create_task(backup.backup_loop())
    yield
    ping_task.cancel()
    backup_task.cancel()
    try:
        await ping_task
    except asyncio.CancelledError:
        pass
    try:
        await backup_task
    except asyncio.CancelledError:
        pass
    await line_client.shutdown()
    await liff_auth.shutdown()


app = FastAPI(lifespan=lifespan)

# 修正理由: リクエストボディサイズの上限が一切なく、JSON受信エンドポイント
# （/api/timetable/profile、/api/seiseki/save_raw 等）に巨大なペイロードを
# 送りつけるとメモリ枯渇DoSになり得た。/api/parse_seiseki はPDFアップロード用に
# 既存の10MB上限（ハンドラ内でファイル読み込み後にチェック）があるため除外する。
_MAX_BODY_BYTES = 2 * 1024 * 1024
_BODY_LIMIT_EXEMPT_PATHS = {"/api/parse_seiseki"}


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path not in _BODY_LIMIT_EXEMPT_PATHS:
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > _MAX_BODY_BYTES:
                        return _JSONResponse(status_code=413, content={"detail": "リクエストが大きすぎます"})
                except ValueError:
                    pass
        return await call_next(request)


app.add_middleware(BodySizeLimitMiddleware)

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
