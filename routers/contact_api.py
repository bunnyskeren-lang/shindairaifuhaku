import re as _re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from core.config import CONTACT_LIFF_ID, IS_DEV
from core.liff_auth import verify_liff_id_token
from core.rate_limit import rate_limiter
from core.templates import templates
from database import AsyncSessionLocal
from models import Inquiry, InquiryStatus, UserProfile

router = APIRouter()

# 修正理由: いたずら連投を防ぐため、IPアドレス単位で1分あたり3回まで（レビュー投稿と同水準）に制限する
_submit_rate_limit = rate_limiter(max_requests=3, window_seconds=60)

_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CATEGORIES = ("質問", "情報の誤りのご指摘", "新しい情報の追加提案", "情報のアップデート", "誤字脱字のご指摘", "メールが送られてこない", "その他")


@router.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    response = templates.TemplateResponse(
        "contact.html",
        {
            "request": request,
            "IS_DEV": IS_DEV,
            "categories": _CATEGORIES,
            "liff_id": CONTACT_LIFF_ID,
        },
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@router.post("/contact/submit")
async def contact_submit(
    request: Request,
    category: str = Form(default=""),
    content: str = Form(default=""),
    email: str = Form(default=""),
    id_token: str = Form(default=""),
    _rl: None = Depends(_submit_rate_limit),
):
    def _error(msg: str):
        return templates.TemplateResponse(
            "form_error.html", {"request": request, "message": msg}, status_code=400
        )

    # 修正理由: お問い合わせは会員登録(メール認証)が未完了でも送信できる必要があるため、
    # LIFF ID tokenでのLINEログインのみを必須とする。プロフィールがあれば学籍番号を添える
    # (クライアント指定の値は信用せず、あればLIFF ID token検証済みの本人のものを使う)
    uid = await verify_liff_id_token(id_token.strip(), request)
    if not uid:
        return _error("LINEログインの確認に失敗しました。LINEアプリから開き直してください")
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, uid)
        sid = profile.student_id if profile else ""

        cat = category.strip()
        if cat not in _CATEGORIES:
            return _error("お問い合わせの種類を選択してください")

        body = content.strip()[:2000]
        if not body:
            return _error("お問い合わせ内容を入力してください")

        to_email = email.strip()[:200]
        if not _EMAIL_RE.match(to_email):
            return _error("メールアドレスの形式が正しくありません")

        session.add(Inquiry(
            category=cat,
            content=body,
            email=to_email,
            student_id=sid,
            status=InquiryStatus.PENDING,
        ))
        await session.commit()

    return templates.TemplateResponse("contact_success.html", {"request": request})
