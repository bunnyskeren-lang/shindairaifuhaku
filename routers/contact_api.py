import re as _re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from core.config import IS_DEV
from core.rate_limit import rate_limiter
from core.templates import templates
from database import AsyncSessionLocal
from models import Inquiry, InquiryStatus

router = APIRouter()

# 修正理由: いたずら連投を防ぐため、IPアドレス単位で1分あたり3回まで（レビュー投稿と同水準）に制限する
_submit_rate_limit = rate_limiter(max_requests=3, window_seconds=60)

_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CATEGORIES = ("質問", "情報の誤りのご指摘", "新しい情報の追加提案", "情報のアップデート", "誤字脱字のご指摘", "その他")


@router.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    response = templates.TemplateResponse(
        "contact.html", {"request": request, "IS_DEV": IS_DEV, "categories": _CATEGORIES}
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@router.post("/contact/submit")
async def contact_submit(
    request: Request,
    category: str = Form(default=""),
    content: str = Form(default=""),
    email: str = Form(default=""),
    _rl: None = Depends(_submit_rate_limit),
):
    def _error(msg: str):
        return templates.TemplateResponse(
            "form_error.html", {"request": request, "message": msg}, status_code=400
        )

    cat = category.strip()
    if cat not in _CATEGORIES:
        return _error("お問い合わせの種類を選択してください")

    body = content.strip()[:2000]
    if not body:
        return _error("お問い合わせ内容を入力してください")

    to_email = email.strip()[:200]
    if to_email and not _EMAIL_RE.match(to_email):
        return _error("メールアドレスの形式が正しくありません")

    async with AsyncSessionLocal() as session:
        session.add(Inquiry(
            category=cat,
            content=body,
            email=to_email or None,
            status=InquiryStatus.PENDING,
        ))
        await session.commit()

    return templates.TemplateResponse("contact_success.html", {"request": request})
