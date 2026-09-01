from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select

from core.config import BAN_MESSAGE_TEXT, IS_DEV, STUDENT_ID_RE, normalize_student_id
from core.rate_limit import rate_limiter
from core.templates import templates
from database import AsyncSessionLocal
from models import PaymentRequest, PaymentRequestStatus, Review, ReviewStatus, UserProfile

router = APIRouter()

# 修正理由: 学籍番号を総当たりして他人の未払いレビュー件数を探れてしまわないよう、
# 照会APIもレビュー投稿(/submit)と同水準でIPアドレス単位に制限する
_eligible_rate_limit = rate_limiter(max_requests=20, window_seconds=60)
_apply_rate_limit = rate_limiter(max_requests=3, window_seconds=60)

_UNIT_YEN = 100
_YEN_PER_REVIEW = 100
# 1回の申請あたりの上限額。超過分は未申請のまま残り、次回以降の申請に繰り越される
_MAX_APPLY_AMOUNT = 1000
_MAX_REVIEWS_PER_APPLY = _MAX_APPLY_AMOUNT // _YEN_PER_REVIEW


async def _unpaid_count(session, sid: str) -> int:
    return (await session.execute(
        select(func.count(Review.id)).where(
            Review.student_id == sid,
            Review.status == ReviewStatus.APPROVED,
            Review.payment_request_id.is_(None),
        )
    )).scalar_one()


async def _submitted_count(session, sid: str) -> int:
    """未払い（過去の支払い申請に紐づいていない）レビューの総投稿数。
    status問わず（pending/approved/rejected）カウントする。"""
    return (await session.execute(
        select(func.count(Review.id)).where(
            Review.student_id == sid,
            Review.payment_request_id.is_(None),
        )
    )).scalar_one()


async def _is_banned_student(session, sid: str) -> bool:
    """このフォームはLINE識別子を持たず学籍番号のみで動くため、user_profiles.student_idで
    引いてBAN状態を判定する（2026-08-30、支払い申請だけBANチェックが無かった漏れを修正）。"""
    rows = (await session.execute(
        select(UserProfile.banned_at).where(UserProfile.student_id == sid)
    )).scalars().all()
    return any(b is not None for b in rows)


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
    sid = normalize_student_id(student_id)
    if not STUDENT_ID_RE.match(sid):
        return JSONResponse({"valid": False})
    async with AsyncSessionLocal() as session:
        if await _is_banned_student(session, sid):
            return JSONResponse({"valid": False})
        count = await _unpaid_count(session, sid)
        submitted_count = await _submitted_count(session, sid)
    max_amount = min(
        (count // (_UNIT_YEN // _YEN_PER_REVIEW)) * _UNIT_YEN,
        _MAX_APPLY_AMOUNT,
    )
    return JSONResponse({
        "valid": True,
        "count": count,
        "submitted_count": submitted_count,
        "max_amount": max_amount,
    })


@router.post("/payment/apply/submit")
async def payment_apply_submit(
    request: Request,
    name: str = Form(default=""),
    student_id: str = Form(default=""),
    paypay_id: str = Form(default=""),
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

    sid = normalize_student_id(student_id)
    if not STUDENT_ID_RE.match(sid):
        return _error("学籍番号の形式が正しくありません（例：2345678S、医学部は2345678MM）")

    paypay = paypay_id.strip()[:50]
    if not paypay:
        return _error("PayPay IDを入力してください")

    async with AsyncSessionLocal() as session:
        if await _is_banned_student(session, sid):
            return _error(BAN_MESSAGE_TEXT)

        existing_pending = (await session.execute(
            select(PaymentRequest.id).where(
                PaymentRequest.student_id == sid,
                PaymentRequest.status == PaymentRequestStatus.PENDING,
            )
        )).scalars().first()
        if existing_pending is not None:
            return _error("既に支払い待ちの申請があります。処理をお待ちください")

        # 申請金額はユーザー入力を受け付けず、承認済み（未申請）レビュー数から自動算出する。
        # ただし1回の申請あたり_MAX_APPLY_AMOUNTを上限とし、超過分は未申請のまま次回に繰り越す
        unpaid_count = await _unpaid_count(session, sid)
        if unpaid_count == 0:
            return _error("承認済み（未申請）のレビューが見つかりませんでした")

        apply_count = min(unpaid_count, _MAX_REVIEWS_PER_APPLY)
        amount_val = apply_count * _YEN_PER_REVIEW

        target_ids = (await session.execute(
            select(Review.id).where(
                Review.student_id == sid,
                Review.status == ReviewStatus.APPROVED,
                Review.payment_request_id.is_(None),
            ).order_by(Review.created_at.asc()).limit(apply_count)
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
