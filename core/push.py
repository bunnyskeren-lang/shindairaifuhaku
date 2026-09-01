import asyncio
import json as _json

from sqlalchemy import delete as _sa_delete, select

from core.activity_log import save_error_log
from core.config import VAPID_EMAIL, VAPID_PRIVATE_KEY
from database import AsyncSessionLocal
from models import PushSubscription


async def _send_to_subscribers(title: str, body: str, url: str = "/admin/courses") -> None:
    if not VAPID_PRIVATE_KEY:
        return
    from pywebpush import webpush, WebPushException
    async with AsyncSessionLocal() as session:
        subs = (await session.execute(select(PushSubscription))).scalars().all()
    if not subs:
        return
    payload = _json.dumps({"title": title, "body": body, "url": url})

    async def _send_one(sub) -> int | None:
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{VAPID_EMAIL}"},
            )
        except WebPushException as e:
            if e.response is not None and e.response.status_code == 410:
                return sub.id
            await save_error_log(e, action="push_notification")
        except Exception as e:
            await save_error_log(e, action="push_notification")
        return None

    results = await asyncio.gather(*(_send_one(sub) for sub in subs))
    expired_ids = [r for r in results if r is not None]
    if expired_ids:
        async with AsyncSessionLocal() as session:
            await session.execute(_sa_delete(PushSubscription).where(PushSubscription.id.in_(expired_ids)))
            await session.commit()


async def send_push_notification(course_name: str, rating: int, ease_rating: str, comment: str):
    stars = "★" * rating + "☆" * (5 - rating)
    await _send_to_subscribers(
        title=f"📝 新着レビュー: {course_name}",
        body=f"{stars}  楽単: {ease_rating}\n{comment[:80]}",
        url="/admin/courses",
    )


async def send_registration_push_notification(name: str, faculty: str, department: str | None):
    dept = f" {department}" if department else ""
    await _send_to_subscribers(
        title="👤 新規会員登録",
        body=f"{name}（{faculty}{dept}）",
        url="/admin/users",
    )


async def send_inquiry_push_notification(category: str, content: str):
    await _send_to_subscribers(
        title=f"📩 新着お問い合わせ: {category}",
        body=content[:80],
        url="/admin/inquiries",
    )
