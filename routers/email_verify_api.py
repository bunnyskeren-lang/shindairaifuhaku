import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select

from core.activity_log import save_error_log
from core.config import (
    APP_URL, EMAIL_VERIFICATION_TTL_MINUTES, LINE_USER_ID_RE, REVIEW_LIFF_ID,
    STUDENT_ID_RE, make_register_url, student_email,
)
from core.liff_auth import verify_liff_id_token
from core.mail import send_verification_email
from core.rate_limit import rate_limiter
from core.templates import templates
from database import AsyncSessionLocal
from models import EmailVerification, UserProfile

router = APIRouter()

# 修正理由: メール認証依頼の連打を防ぐため、IPアドレス単位で1分あたり5回までに制限する
_request_rate_limit = rate_limiter(max_requests=5, window_seconds=60)
# 修正理由: 再送信の連打によるメール送信サービスへの負荷・迷惑メール化を防ぐため、10分あたり3回までに制限する
_resend_rate_limit = rate_limiter(max_requests=3, window_seconds=600)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_pending_verification(session, line_user_id: str, sid: str, payload: dict) -> str:
    """未検証の登録情報をemail_verificationsに保存し、平文トークンを返す(呼び出し側でメール送信する)。"""
    token = secrets.token_urlsafe(32)
    session.add(EmailVerification(
        line_user_id=line_user_id,
        student_id=sid,
        token_hash=_hash_token(token),
        payload=json.dumps(payload, ensure_ascii=False),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=EMAIL_VERIFICATION_TTL_MINUTES),
    ))
    await session.commit()
    return token


@router.post("/api/email/request")
async def request_email_verification(
    request: Request,
    id_token: str = Form(...),
    reg_name: str = Form(...),
    student_id: str = Form(...),
    _rl: None = Depends(_request_rate_limit),
):
    """レビュー投稿フォームを開く前段のゲート(/verify-email)からの送信を受け、
    大学メール宛のマジックリンクを発行する。UserProfileはまだ作らない。"""
    def _err(msg: str):
        return templates.TemplateResponse(
            "form_error.html", {"request": request, "message": msg}, status_code=400
        )

    name = reg_name.strip()[:100]
    if not name:
        return _err("お名前を入力してください")
    sid = student_id.strip().upper()
    if not STUDENT_ID_RE.match(sid):
        return _err("学籍番号の形式が正しくありません（例：2345678S、医学部は2345678MM）")

    uid = await verify_liff_id_token(id_token, request)
    if not uid or not LINE_USER_ID_RE.match(uid):
        return _err("LINEログインの確認に失敗しました。LINEアプリから開き直してください")

    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(UserProfile).where(UserProfile.line_user_id == uid)
        )).scalar_one_or_none()
        if existing is not None:
            # 既にプロフィールがある(通常はここに来ない。ゲートはプロフィール未作成時のみ表示される)
            return templates.TemplateResponse(
                "form_email_verified.html",
                {"request": request, "liff_id": REVIEW_LIFF_ID, "register_url": make_register_url(uid)},
            )
        taken = (await session.execute(
            select(UserProfile.line_user_id).where(UserProfile.student_id == sid)
        )).scalars().first()
        if taken is not None and taken != uid:
            return _err("この学籍番号はすでに別のアカウントで登録されています")

        token = await create_pending_verification(session, uid, sid, {"name": name})

    to_email = student_email(sid)
    await send_verification_email(
        to_email, f"{APP_URL}/api/email/verify?token={token}", user_id=uid,
    )
    return templates.TemplateResponse(
        "form_email_sent.html", {"request": request, "email": to_email, "liff_id": REVIEW_LIFF_ID}
    )


@router.get("/api/email/verify")
async def verify_email(token: str, request: Request):
    def _err(msg: str):
        return templates.TemplateResponse(
            "form_error.html", {"request": request, "message": msg}, status_code=400
        )

    token_hash = _hash_token(token)
    async with AsyncSessionLocal() as session:
        ev = (await session.execute(
            select(EmailVerification).where(EmailVerification.token_hash == token_hash)
        )).scalar_one_or_none()
        if not ev:
            return _err("認証リンクが無効です。お手数ですが投稿フォームからやり直してください")
        if ev.consumed_at is not None:
            return _err("このリンクはすでに使用されています")
        expires_at = ev.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            return _err("認証リンクの有効期限が切れています。お手数ですが投稿フォームからもう一度お試しください")

        taken = (await session.execute(
            select(UserProfile.line_user_id).where(UserProfile.student_id == ev.student_id)
        )).scalars().first()
        if taken is not None and taken != ev.line_user_id:
            return _err("この学籍番号はすでに別のアカウントで登録されています")

        payload = json.loads(ev.payload)
        now = datetime.now(timezone.utc)
        profile = await session.get(UserProfile, ev.line_user_id)
        if profile is None:
            session.add(UserProfile(
                line_user_id=ev.line_user_id,
                name=payload["name"],
                student_id=ev.student_id,
                email_verified_at=now,
            ))
        else:
            profile.email_verified_at = now
        ev.consumed_at = now

        try:
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await save_error_log(exc, user_id=ev.line_user_id, action="email_verify_finalize")
            return _err("認証の確定に失敗しました。もう一度お試しください")

    # 修正理由: メール認証は会員登録の一部（本人確認ステップ）に位置づけたため、ここで作成する
    # UserProfileはまだ氏名・学籍番号のみで学部・学科が未入力(=is_profile_complete()はFalse)。
    # レビュー投稿にはfaculty/departmentまで揃った会員登録の完了が必須なため、
    # 認証完了後は/registerに誘導して会員登録を完了させる（学籍番号は不変なので再入力不要）
    return templates.TemplateResponse(
        "form_email_verified.html",
        {"request": request, "liff_id": REVIEW_LIFF_ID, "register_url": make_register_url(ev.line_user_id)},
    )


@router.post("/api/email/resend")
async def resend_verification(request: Request, _rl: None = Depends(_resend_rate_limit)):
    body = await request.json()
    uid = await verify_liff_id_token((body.get("id_token") or "").strip(), request)
    if not uid:
        return {"ok": False}
    async with AsyncSessionLocal() as session:
        ev = (await session.execute(
            select(EmailVerification)
            .where(EmailVerification.line_user_id == uid, EmailVerification.consumed_at.is_(None))
            .order_by(EmailVerification.created_at.desc())
        )).scalars().first()
        if not ev:
            return {"ok": False}
        new_token = secrets.token_urlsafe(32)
        ev.token_hash = _hash_token(new_token)
        ev.expires_at = datetime.now(timezone.utc) + timedelta(minutes=EMAIL_VERIFICATION_TTL_MINUTES)
        sid = ev.student_id
        await session.commit()

    await send_verification_email(
        student_email(sid), f"{APP_URL}/api/email/verify?token={new_token}", user_id=uid,
    )
    return {"ok": True}
