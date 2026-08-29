import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, Response

from core import cache
from core.activity_log import save_error_log
from core.config import (
    APP_URL, EMAIL_VERIFICATION_ENABLED, FACULTY_DEPARTMENTS, IS_DEV,
    LIFF_ID, REGISTER_LIFF_ID, REVIEW_APPROVAL_UNLOCK_CREDITS, REVIEW_FORM_URL, REVIEW_LIFF_ID,
    make_email_verify_url,
)
from core.templates import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, uid: str = Query(default="")):
    # 修正理由: 以前はここでクライアント指定の uid をそのままDB照会し、氏名・
    # 学籍番号をテンプレートに埋め込んでいたため、任意のuidを指定するだけで
    # 他人の個人情報が閲覧できるIDOR/PII漏洩になっていた。プリフィルは
    # LIFF ID token検証済みの /api/profile/prefill 経由でJSから行う。
    response = templates.TemplateResponse(
        "form_index.html",
        {
            "request": request,
            "uid": uid,
            "liff_id": REVIEW_LIFF_ID,
            "register_liff_id": REGISTER_LIFF_ID,
            "IS_DEV": IS_DEV,
            "email_verification_enabled": EMAIL_VERIFICATION_ENABLED,
        },
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email_gate(request: Request):
    """レビュー投稿フォームを開く前段のメール認証ページ。氏名・学籍番号のみを集め、
    大学メール宛のマジックリンクで本人確認する（/api/email/request参照）。"""
    response = templates.TemplateResponse(
        "form_email_gate.html",
        {
            "request": request,
            "liff_id": REVIEW_LIFF_ID,
            "IS_DEV": IS_DEV,
        },
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, uid: str = Query(default="")):
    # 修正理由: 以前はここでクライアント指定の uid をそのままDB照会し、氏名・
    # 学籍番号・学部・学年・学科をテンプレートに埋め込んでいたため、任意の
    # uidを指定するだけで他人の個人情報が閲覧できるIDOR/PII漏洩になっていた。
    # プリフィルはLIFF ID token検証済みの /api/profile/prefill 経由でJSから行う。
    response = templates.TemplateResponse(
        "form_register.html",
        {
            "request": request,
            "uid": uid,
            "faculties": await cache.get_faculty_order(),
            "faculty_departments_json": json.dumps(FACULTY_DEPARTMENTS, ensure_ascii=False),
            "liff_id": REGISTER_LIFF_ID,
            "IS_DEV": IS_DEV,
        },
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@router.get("/liff/review", response_class=HTMLResponse)
async def liff_review(request: Request):
    return templates.TemplateResponse(
        "liff/review_redirect.html",
        {
            "request": request,
            "liff_id": REVIEW_LIFF_ID,
            "base_url": APP_URL,
            "redirect_path": "/",
        },
    )


@router.get("/liff/review/verify-email", response_class=HTMLResponse)
async def liff_review_verify_email(request: Request):
    # REVIEW_LIFF_IDのLINE Developersコンソール側エンドポイントURLが/liff/reviewの
    # ため、make_email_verify_url()が生成する https://liff.line.me/{REVIEW_LIFF_ID}/verify-email
    # は実際には /liff/review/verify-email に展開される。このパスで/verify-emailへ
    # 中継する(2026-08-29、この不整合により404になっていた不具合の修正)。
    return templates.TemplateResponse(
        "liff/review_redirect.html",
        {
            "request": request,
            "liff_id": REVIEW_LIFF_ID,
            "base_url": APP_URL,
            "redirect_path": "/verify-email",
        },
    )


@router.get("/coop", response_class=HTMLResponse)
async def coop_redirect(request: Request):
    return templates.TemplateResponse("coop_redirect.html", {"request": request})


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request):
    return templates.TemplateResponse("terms.html", {"request": request})


@router.get("/sw.js")
async def service_worker():
    js = """
self.addEventListener('push', function(e) {
  const d = e.data ? e.data.json() : {};
  e.waitUntil(self.registration.showNotification(d.title || '新着レビュー', {
    body: d.body || '',
    icon: 'https://cdn-icons-png.flaticon.com/512/1041/1041916.png',
    badge: 'https://cdn-icons-png.flaticon.com/512/1041/1041916.png',
  }));
});
self.addEventListener('notificationclick', function(e) {
  e.notification.close();
  e.waitUntil(clients.openWindow('/admin/courses'));
});
""".strip()
    return Response(content=js, media_type="application/javascript")


@router.get("/liff/course", response_class=HTMLResponse)
async def liff_course(request: Request):
    try:
        return templates.TemplateResponse("liff/course.html", {
            "request": request,
            "liff_id": LIFF_ID,
            "register_liff_id": REGISTER_LIFF_ID,
            "review_form_url": REVIEW_FORM_URL,
            "base_url": APP_URL,
            "IS_DEV": IS_DEV,
            "unlock_reward": REVIEW_APPROVAL_UNLOCK_CREDITS,
            "email_verification_enabled": EMAIL_VERIFICATION_ENABLED,
            "email_verify_url": make_email_verify_url(),
        })
    except Exception as exc:
        await save_error_log(exc, action="liff_course")
        raise
