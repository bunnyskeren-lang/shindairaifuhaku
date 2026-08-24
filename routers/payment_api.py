from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select

from core.config import IS_DEV, STUDENT_ID_RE
from core.rate_limit import rate_limiter
from core.templates import templates
from database import AsyncSessionLocal
from models import PaymentRequest, PaymentRequestStatus, Review, ReviewStatus

router = APIRouter()

# 修正理由: 学籍番号を総当たりして他人の未払いレビュー件数を探れてしまわないよう、
# 照会APIもレビュー投稿(/submit)と同水準でIPアドレス単位に制限する
_eligible_rate_limit = rate_limiter(max_requests=20, window_seconds=60)
_apply_rate_limit = rate_limiter(max_requests=3, window_seconds=60)

_UNIT_YEN = 500
_YEN_PER_REVIEW = 10


async def _unpaid_count(session, sid: str) -> int:
    return (await session.execute(
        select(func.count(Review.id)).where(
            Review.student_id == sid,
            Review.status == ReviewStatus.APPROVED,
            Review.payment_request_id.is_(None),
        )
    )).scalar_one()


@router.get("/payment/apply", response_class=HTMLResponse)
async def payment_apply_page(request: Request):
    response = templates.TemplateResponse(
        "payment_apply.html", {"request": request, "IS_DEV": IS_DEV}
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@router.get("/api/payment/eligible")
async def payment_eligible(
    student_id: str = Query(default=""),
    _rl: None = Depends(_eligible_rate_limit),
):
    sid = student_id.strip().upper()
    if not STUDENT_ID_RE.match(sid):
        return JSONResponse({"valid": False})
    async with AsyncSessionLocal() as session:
        count = await _unpaid_count(session, sid)
    max_amount = (count // (_UNIT_YEN // _YEN_PER_REVIEW)) * _UNIT_YEN
    return JSONResponse({"valid": True, "count": count, "max_amount": max_amount})


@router.post("/payment/apply/submit")
async def payment_apply_submit(
    request: Request,
    name: str = Form(default=""),
    student_id: str = Form(default=""),
    paypay_id: str = Form(default=""),
    amount: str = Form(default=""),
    _rl: None = Depends(_apply_rate_limit),
):
    # 修正理由: name/student_id/paypay_id/amountをFastAPIのForm(...)必須指定にしていたため、
    # 未入力や欠落時に本来表示したかったform_error.html（日本語の案内）より先に
    # FastAPI標準の生JSON 422エラーが返っていた。review_submit_api.pyと同様、
    # Form側は常に受理してから本関数内で検証しエラーページへ誘導する
    def _error(msg: str):
        return templates.TemplateResponse(
            "form_error.html", {"request": request, "message": msg}, status_code=400
        )

    name = name.strip()[:50]
    if not name:
        return _error("お名前を入力してください")

    sid = student_id.strip().upper()
    if not STUDENT_ID_RE.match(sid):
        return _error("学籍番号の形式が正しくありません（例：2345678S、医学部は2345678MM）")

    paypay = paypay_id.strip()[:50]
    if not paypay:
        return _error("PayPay IDを入力してください")

    try:
        amount_val = int(amount)
    except ValueError:
        return _error("申請金額を正しく入力してください")

    if amount_val <= 0 or amount_val % _UNIT_YEN != 0:
        return _error(f"申請金額は{_UNIT_YEN}円単位で入力してください")

    required_reviews = amount_val // _YEN_PER_REVIEW

    async with AsyncSessionLocal() as session:
        existing_pending = (await session.execute(
            select(PaymentRequest.id).where(
                PaymentRequest.student_id == sid,
                PaymentRequest.status == PaymentRequestStatus.PENDING,
            )
        )).scalars().first()
        if existing_pending is not None:
            return _error("既に支払い待ちの申請があります。処理をお待ちください")

        unpaid_count = await _unpaid_count(session, sid)
        if required_reviews > unpaid_count:
            max_amount = (unpaid_count // (_UNIT_YEN // _YEN_PER_REVIEW)) * _UNIT_YEN
            return _error(f"承認済み（未払い）レビューが不足しています。現在申請できる金額は{max_amount}円です")

        target_ids = (await session.execute(
            select(Review.id).where(
                Review.student_id == sid,
                Review.status == ReviewStatus.APPROVED,
                Review.payment_request_id.is_(None),
            ).order_by(Review.created_at.asc()).limit(required_reviews)
        )).scalars().all()

        payment_request = PaymentRequest(
            name=name,
            student_id=sid,
            paypay_id=paypay,
            amount=amount_val,
            status=PaymentRequestStatus.PENDING,
        )
        session.add(payment_request)
        await session.flush()

        await session.execute(
            Review.__table__.update()
            .where(Review.id.in_(target_ids))
            .values(payment_request_id=payment_request.id)
        )
        await session.commit()

    return templates.TemplateResponse(
        "payment_apply_success.html", {"request": request, "amount": amount_val}
    )
