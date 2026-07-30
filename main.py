import asyncio
import time
import traceback as _traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse as _JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from core import backup, liff_auth, line_client, prewarm, rate_limit
from core.activity_log import log_cleanup_loop, save_error_log
from database import engine, init_db
from routers import (
    health, liff_api, pages, profile_api, review_submit_api, richmenu, webhook,
)
from routers.admin import (
    auth as admin_auth,
    binran_discrepancies as admin_binran_discrepancies,
    classifications as admin_classifications,
    courses as admin_courses,
    dashboard as admin_dashboard,
    instructors as admin_instructors,
    reviews as admin_reviews,
    stats as admin_stats,
    users_errors as admin_users_errors,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
        print("DB OK", flush=True)
        asyncio.create_task(prewarm.prewarm_caches())
    except Exception as e:
        _traceback.print_exc()
        print(f"DB ERROR: {e}", flush=True)
        await save_error_log(e, action="init_db")
        await engine.dispose()
        print("Engine disposed and reset after startup error", flush=True)
    await line_client.startup()
    await liff_auth.startup()
    ping_task = asyncio.create_task(line_client.self_ping())
    backup_task = asyncio.create_task(backup.backup_loop())
    cleanup_task = asyncio.create_task(log_cleanup_loop())
    rate_limit_cleanup_task = asyncio.create_task(rate_limit.rate_limit_cleanup_loop())
    yield
    ping_task.cancel()
    backup_task.cancel()
    cleanup_task.cancel()
    rate_limit_cleanup_task.cancel()
    for task in (ping_task, backup_task, cleanup_task, rate_limit_cleanup_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await line_client.shutdown()
    await liff_auth.shutdown()


app = FastAPI(lifespan=lifespan)

# 修正理由: リクエストボディサイズの上限が一切なく、JSON受信エンドポイントに
# 巨大なペイロードを送りつけるとメモリ枯渇DoSになり得た。
_MAX_BODY_BYTES = 2 * 1024 * 1024
_BODY_LIMIT_EXEMPT_PATHS = set()


# Starlette の BaseHTTPMiddleware は各リクエストで追加タスク生成+anyioストリーム経由の
# 中継が発生し全リクエストに一定のオーバーヘッドが乗るため、素のASGIミドルウェアとして実装する。
class _BodyTooLargeError(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in _BODY_LIMIT_EXEMPT_PATHS:
            return await self.app(scope, receive, send)
        content_length = next(
            (v for k, v in scope["headers"] if k == b"content-length"), None
        )
        if content_length is not None:
            try:
                if int(content_length) > _MAX_BODY_BYTES:
                    response = _JSONResponse(status_code=413, content={"detail": "リクエストが大きすぎます"})
                    return await response(scope, receive, send)
            except ValueError:
                pass

        # Content-Length ヘッダーが無い(chunked encoding等)リクエストはここまでのチェックを
        # 素通りするため、実際に受信したボディの累計バイト数もreceive()をラップして監視する。
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body") or b"")
                if received > _MAX_BODY_BYTES:
                    raise _BodyTooLargeError()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLargeError:
            response = _JSONResponse(status_code=413, content={"detail": "リクエストが大きすぎます"})
            await response(scope, receive, send)


# 修正理由: リクエストごとの処理時間・ステータスコードを記録する仕組みが
# 皆無で、障害調査やボトルネック検出ができなかった。Renderの標準stdoutログに
# 1行で出力する（既存コードもprint()ベースのため踏襲、外部依存を増やさない）。
_SLOW_REQUEST_MS = 3000


class RequestTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        method = scope["method"]
        path = scope["path"]
        start = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            print(f"[access] {method} {path} 500 {duration_ms:.0f}ms", flush=True)
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        marker = " SLOW" if duration_ms >= _SLOW_REQUEST_MS else ""
        print(f"[access] {method} {path} {status_holder['status']} {duration_ms:.0f}ms{marker}", flush=True)


app.add_middleware(RequestTimingMiddleware)
app.add_middleware(BodySizeLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://liff.line.me", "https://access.line.me"],
    allow_origin_regex=r"https://.*\.line\.me",
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)
# 修正理由: JSONレスポンス（/api/preload等）が無圧縮のままだった。モバイル回線での
# 転送時間短縮のため、他の全ミドルウェアより外側（最終的なレスポンスバイト列）で圧縮する。
app.add_middleware(GZipMiddleware, minimum_size=500)


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
app.include_router(profile_api.router)
app.include_router(review_submit_api.router)

app.include_router(admin_auth.router)
app.include_router(admin_dashboard.router)
app.include_router(admin_courses.router)
app.include_router(admin_instructors.router)
app.include_router(admin_classifications.router)
app.include_router(admin_reviews.router)
app.include_router(admin_users_errors.router)
app.include_router(admin_stats.router)
app.include_router(admin_binran_discrepancies.router)
