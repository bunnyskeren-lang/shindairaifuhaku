import asyncio
import re as _re

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from core.activity_log import save_error_log
from core.config import BAN_MESSAGE_TEXT, IS_DEV, STUDENT_ID_RE, normalize_student_id
from core.push import send_payment_request_push_notification
from core.rate_limit import rate_limiter
from core.templates import templates
from database import AsyncSessionLocal
from models import PaymentRequest, PaymentRequestStatus, UserProfile

router = APIRouter()

# 連絡先メールの形式チェック。routers/contact_api.py と同じゆるい判定
_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

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


async def _prior_amount_by_nonce(session, nonce: str):
    """同じ submit_nonce の申請が既にあれば amount を返す（無ければ None）。
    OS/webview がバックグラウンド復帰時に保留POSTを再送しても同じ nonce が載るため、
    2回目以降はこれで検知して新規INSERTせず「受付済み」画面へ流す。"""
    return (await session.execute(
        select(PaymentRequest.amount).where(PaymentRequest.submit_nonce == nonce)
    )).scalars().first()


def _done_redirect(amount: int, *, dup: bool = False):
    """PRG: 申請成功・再送検知いずれも GET /payment/apply/done へ 303 で流す。
    申請金額はURLに載せず短命Cookieで渡す（reviews の成功画面と同じ方式。
    URL直打ちで任意金額の受付画面を出せてしまうのを防ぐ）。"""
    resp = RedirectResponse(
        url="/payment/apply/done" + ("?dup=1" if dup else ""), status_code=303
    )
    resp.set_cookie(
        "kobe_payment_amount", str(int(amount or 0)),
        max_age=120, httponly=True, samesite="lax", path="/payment/apply/done",
    )
    return resp


@router.get("/payment/apply", response_class=HTMLResponse)
async def payment_apply_page(request: Request):
    response = templates.TemplateResponse(
        "payment_apply.html", {"request": request, "IS_DEV": IS_DEV}
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@router.get("/payment/apply/done", response_class=HTMLResponse)
async def payment_apply_done(request: Request, dup: int = Query(default=0)):
    """申請完了（PRG）の表示専用ページ。POST再送で走っても無害なGET。
    dup=1 は「本人は1回しか押していないのに二重送信になった／既に申請済み」の再送検知で、
    エラーではなく『既に送信済みです』として見せる。"""
    try:
        amount = int(request.cookies.get("kobe_payment_amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    resp = templates.TemplateResponse(
        "payment_apply_success.html",
        {"request": request, "amount": amount, "dup": bool(dup)},
    )
    resp.delete_cookie("kobe_payment_amount", path="/payment/apply/done")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


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
    paypay_display_name: str = Form(default=""),
    paypay_id: str = Form(default=""),
    email: str = Form(default=""),
    submit_nonce: str = Form(default=""),
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

    display_name = paypay_display_name.strip()[:50]
    if not display_name:
        return _error("PayPayの表示名を入力してください")

    paypay = paypay_id.strip()[:50]
    if not paypay:
        return _error("PayPay IDを入力してください")

    mail = email.strip()[:200]
    if not _EMAIL_RE.match(mail):
        return _error("メールアドレスの形式が正しくありません")

    nonce = submit_nonce.strip()[:64] or None

    async with AsyncSessionLocal() as session:
        # 再送POST（本人は「申請する」を1回押しただけなのにOS/webviewがPOSTを再送した）は
        # 同じ nonce を載せてくる。1回目が既にあるならエラー画面を出さず「既に送信済みです」へ。
        if nonce:
            prior_amount = await _prior_amount_by_nonce(session, nonce)
            if prior_amount is not None:
                return _done_redirect(prior_amount, dup=True)

        if await _is_banned_student(session, sid):
            return _error(BAN_MESSAGE_TEXT)

        existing_pending = (await session.execute(
            select(PaymentRequest.amount).where(
                PaymentRequest.student_id == sid,
                PaymentRequest.status == PaymentRequestStatus.PENDING,
            )
        )).scalars().first()
        if existing_pending is not None:
            # nonce が一致しない二重送信（古いキャッシュのフォーム等）もここで
            # 「既に送信済みです（処理待ち）」へ寄せる
            return _done_redirect(existing_pending, dup=True)

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
            paypay_display_name=display_name,
            paypay_id=paypay,
            email=mail,
            amount=amount_val,
            status=PaymentRequestStatus.PENDING,
            submit_nonce=nonce,
        )
        session.add(payment_request)
        try:
            await session.commit()
        except IntegrityError:
            # ほぼ同時に届いた再送POSTが UNIQUE(submit_nonce) で衝突。1回目が勝っているので
            # rollback して「既に送信済みです」へ流す。
            await session.rollback()
            if nonce:
                prior_amount = await _prior_amount_by_nonce(session, nonce)
                if prior_amount is not None:
                    return _done_redirect(prior_amount, dup=True)
            return _error("既に送信済みです。処理をお待ちください")

    # 新規申請がDBに入ったときだけ管理者へプッシュ通知する（再送検知・処理待ちの
    # 二重送信は上で return 済みなのでここには来ない）。お問い合わせと同様に
    # レスポンスを待たせずバックグラウンドで送る
    async def _notify() -> None:
        try:
            await send_payment_request_push_notification(name, sid, amount_val)
        except Exception as exc:
            await save_error_log(exc, action="payment_push_notification")

    asyncio.create_task(_notify())

    return _done_redirect(amount_val)
