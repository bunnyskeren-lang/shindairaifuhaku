import asyncio
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from core.activity_log import save_error_log
from core.config import APP_URL, EMAIL_VERIFICATION_TTL_MINUTES, student_email
from core.liff_auth import verify_liff_id_token
from core.mail import send_verification_email
from core.push import send_push_notification
from core.rate_limit import rate_limiter
from core.templates import templates
from database import AsyncSessionLocal
from models import EmailVerification, Review, ReviewStatus, UserProfile

router = APIRouter()

# 修正理由: 再送信の連打によるメール送信サービスへの負荷・迷惑メール化を防ぐため、10分あたり3回までに制限する
_resend_rate_limit = rate_limiter(max_requests=3, window_seconds=600)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_pending_verification(session, line_user_id: str, sid: str, payload: dict) -> str:
    """未検証の投稿一式をemail_verificationsに保存し、平文トークンを返す(呼び出し側でメール送信する)。"""
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
        profile = await session.get(UserProfile, ev.line_user_id)
        now = datetime.now(timezone.utc)
        if profile is None:
            profile = UserProfile(
                line_user_id=ev.line_user_id,
                name=payload["name"],
                student_id=ev.student_id,
                email_verified_at=now,
            )
            session.add(profile)
        else:
            profile.email_verified_at = now

        review = Review(
            course_section_id=payload["course_section_id"],
            submitter_name=payload["name"],
            content=payload["content"],
            rating=payload["rating"],
            ease_rating=payload["ease_rating"],
            grading_method=payload["grading_method"],
            selected_instructor=payload["selected_instructor"],
            nickname=payload["nickname"],
            academic_year=payload["academic_year"],
            student_id=ev.student_id,
            status=ReviewStatus.PENDING,
        )
        session.add(review)
        ev.consumed_at = now

        try:
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await save_error_log(exc, user_id=ev.line_user_id, action="email_verify_finalize")
            return _err("投稿の確定に失敗しました。もう一度お試しください")

        review_count = (await session.execute(
            select(func.count(Review.id)).where(Review.student_id == ev.student_id)
        )).scalar_one()
        course_id = payload["subject_id"]
        course_name = payload["course_name"]
        rating = payload["rating"]
        ease_rating = payload["ease_rating"]
        content = payload["content"]
        uid = ev.line_user_id

    async def _notify() -> None:
        try:
            await send_push_notification(
                course_name=course_name, rating=rating, ease_rating=ease_rating, comment=content,
            )
        except Exception as exc:
            await save_error_log(exc, user_id=uid, action="submit_push_notification")

    asyncio.create_task(_notify())

    return templates.TemplateResponse(
        "form_success.html", {
            "request": request,
            "course_name": course_name,
            "course_id": course_id,
            "review_count": review_count,
            "base_url": APP_URL,
        }
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
