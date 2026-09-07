from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select

from core.config import BAN_MESSAGE_TEXT, IS_DEV, STUDENT_ID_RE, normalize_student_id
from core.rate_limit import rate_limiter
from core.templates import templates
from database import AsyncSessionLocal
from models import PaymentRequest, PaymentRequestStatus, UserProfile

router = APIRouter()

# 修正理由: 学籍番号を総当たりして他人の支払い上限額を探れてしまわないよう、
# 照会APIもレビュー投稿(/submit)と同水準でIPアドレス単位に制限する
_eligible_rate_limit = rate_limiter(max_requests=20, window_seconds=60)
_apply_rate_limit = rate_limiter(max_requests=3, window_seconds=60)


async def _payment_limit(session, sid: str) -> int:
    """この学籍番号に対して管理画面（/admin/users）で設定された支払い上限額（円）。
    2026-09-07以降、支払い申請フォームの申請額はこの値そのものになり、
    承認済みレビューの件数とは一切連動しない。
    同一学籍番号のプロフィールが複数ある場合は最大値を採用する。"""
    val = (await session.execute(
        select(func.max(UserProfile.payment_limit)).where(UserProfile.student_id == sid)
    )).scalar()
    return int(val or 0)


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
        amount = await _payment_limit(session, sid)
    return JSONResponse({"valid": True, "amount": amount})


@router.post("/payment/apply/submit")
async def payment_apply_submit(
    request: Request,
    name: str = Form(default=""),
    student_id: str = Form(default=""),
    paypay_id: str = Form(default=""),
    _rl: None = Depends(_apply_rate_limit),
):
    # 修正理由: name/student_id/paypay_idをFastAPIのForm(...)必須指定にしていたため、
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

        # 申請金額はユーザー入力を受け付けず、管理画面（/admin/users）で設定された
        # 支払い上限額をそのまま使う（承認済みレビューの件数とは一切連動しない）
        amount_val = await _payment_limit(session, sid)
        if amount_val <= 0:
            return _error(
                "現在、この学籍番号でお受け取りいただける金額が設定されていません。"
                "学籍番号を投稿時と同じもので入力しているかご確認ください"
            )

        payment_request = PaymentRequest(
            name=name,
            student_id=sid,
            paypay_id=paypay,
            amount=amount_val,
            status=PaymentRequestStatus.PENDING,
        )
        session.add(payment_request)
        await session.commit()

    return templates.TemplateResponse(
        "payment_apply_success.html", {"request": request, "amount": amount_val}
    )
