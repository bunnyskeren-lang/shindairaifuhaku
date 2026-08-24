"""EMAIL_VERIFICATION_ENABLED=True時の/submit→/api/email/verifyのE2Eテスト。

新規ユーザーの初回投稿はメール認証待ち(email_verifications)になり、UserProfile/Reviewは
マジックリンクを踏むまで作成されないこと、既存ユーザーは再認証不要なこと、
不正/期限切れ/再送信の異常系を実HTTPリクエスト経由で検証する。
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import routers.email_verify_api as email_verify_api
import routers.review_submit_api as review_submit_api
from models import CourseSection, EmailVerification, Instructor, Review, Subject, UserProfile


def _fake_verify(monkeypatch, user_id: str = "U65326572657669657765723100000000"):
    async def _verify(id_token, request=None):
        return user_id if id_token == "valid-token" else None
    monkeypatch.setattr(review_submit_api, "verify_liff_id_token", _verify)
    monkeypatch.setattr(email_verify_api, "verify_liff_id_token", _verify)


def _stub_push_notification(monkeypatch):
    async def _noop(**kwargs):
        return None
    monkeypatch.setattr(review_submit_api, "send_push_notification", _noop)
    monkeypatch.setattr(email_verify_api, "send_push_notification", _noop)


def _capture_mail(monkeypatch):
    captured = {}

    async def _fake_send(to_email, verify_url, user_id=None):
        captured["to_email"] = to_email
        captured["verify_url"] = verify_url
        return True
    monkeypatch.setattr(review_submit_api, "send_verification_email", _fake_send)
    monkeypatch.setattr(email_verify_api, "send_verification_email", _fake_send)
    return captured


async def _seed_course(test_sessionmaker, name="経営管理", instructor="山田太郎"):
    async with test_sessionmaker() as session:
        subj = Subject(name=name, faculty="経営学部", category="専門")
        session.add(subj)
        await session.flush()
        instr = Instructor(name=instructor)
        session.add(instr)
        await session.flush()
        session.add(CourseSection(subject_id=subj.id, instructor_id=instr.id))
        await session.commit()


VALID_FORM = {
    "course_name": "経営管理",
    "rating": "4",
    "ease_rating": "A",
    "comment": "とても勉強になりました",
    "id_token": "valid-token",
    "reg_name": "神戸太郎",
    "student_id": "2345678S",
    "academic_year": "2026",
}


def _extract_token(verify_url: str) -> str:
    return verify_url.split("token=", 1)[1]


@pytest.mark.asyncio
async def test_new_user_submit_holds_review_until_email_verified(http_client_factory, monkeypatch, test_sessionmaker):
    monkeypatch.setattr(review_submit_api, "EMAIL_VERIFICATION_ENABLED", True)
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    captured = _capture_mail(monkeypatch)
    await _seed_course(test_sessionmaker)
    submit_client = http_client_factory(review_submit_api, monkeypatch)

    resp = await submit_client.post("/submit", data=VALID_FORM)
    assert resp.status_code == 200
    assert "email" in resp.text or "確認" in resp.text
    assert captured["to_email"] == "2345678s@stu.kobe-u.ac.jp"

    async with test_sessionmaker() as session:
        assert (await session.execute(select(Review))).scalars().first() is None
        assert await session.get(UserProfile, "U65326572657669657765723100000000") is None
        pending = (await session.execute(select(EmailVerification))).scalars().one()
        assert pending.student_id == "2345678S"
        assert pending.consumed_at is None

    verify_client = http_client_factory(email_verify_api, monkeypatch)
    token = _extract_token(captured["verify_url"])
    resp2 = await verify_client.get(f"/api/email/verify?token={token}")
    assert resp2.status_code == 200

    async with test_sessionmaker() as session:
        review = (await session.execute(select(Review))).scalars().one()
        assert review.content == "とても勉強になりました"
        assert review.status == "pending"
        profile = await session.get(UserProfile, "U65326572657669657765723100000000")
        assert profile is not None
        assert profile.email_verified_at is not None
        pending = (await session.execute(select(EmailVerification))).scalars().one()
        assert pending.consumed_at is not None


@pytest.mark.asyncio
async def test_verify_rejects_already_consumed_token(http_client_factory, monkeypatch, test_sessionmaker):
    monkeypatch.setattr(review_submit_api, "EMAIL_VERIFICATION_ENABLED", True)
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    captured = _capture_mail(monkeypatch)
    await _seed_course(test_sessionmaker)
    submit_client = http_client_factory(review_submit_api, monkeypatch)
    await submit_client.post("/submit", data=VALID_FORM)

    verify_client = http_client_factory(email_verify_api, monkeypatch)
    token = _extract_token(captured["verify_url"])
    resp1 = await verify_client.get(f"/api/email/verify?token={token}")
    assert resp1.status_code == 200
    resp2 = await verify_client.get(f"/api/email/verify?token={token}")
    assert resp2.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_expired_token(http_client_factory, monkeypatch, test_sessionmaker):
    verify_client = http_client_factory(email_verify_api, monkeypatch)
    async with test_sessionmaker() as session:
        session.add(EmailVerification(
            line_user_id="U65326572657669657765723100000000",
            student_id="2345678S",
            token_hash=email_verify_api._hash_token("expired-token"),
            payload='{"name": "x", "course_section_id": 1, "subject_id": 1, "course_name": "x", '
                    '"content": "x", "rating": 3, "ease_rating": "A", "grading_method": null, '
                    '"selected_instructor": null, "nickname": null, "academic_year": 2026}',
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        ))
        await session.commit()

    resp = await verify_client.get("/api/email/verify?token=expired-token")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_verify_rejects_unknown_token(http_client_factory, monkeypatch):
    verify_client = http_client_factory(email_verify_api, monkeypatch)
    resp = await verify_client.get("/api/email/verify?token=does-not-exist")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_existing_user_submit_skips_email_verification_even_when_enabled(http_client_factory, monkeypatch, test_sessionmaker):
    monkeypatch.setattr(review_submit_api, "EMAIL_VERIFICATION_ENABLED", True)
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    captured = _capture_mail(monkeypatch)
    await _seed_course(test_sessionmaker)
    async with test_sessionmaker() as session:
        session.add(UserProfile(
            line_user_id="U65326572657669657765723100000000", name="神戸太郎", student_id="2345678S",
        ))
        await session.commit()
    client = http_client_factory(review_submit_api, monkeypatch)

    resp = await client.post("/submit", data=VALID_FORM)
    assert resp.status_code == 200
    assert captured == {}

    async with test_sessionmaker() as session:
        review = (await session.execute(select(Review))).scalars().one()
        assert review.content == "とても勉強になりました"


@pytest.mark.asyncio
async def test_resend_reissues_token_for_pending_verification(http_client_factory, monkeypatch, test_sessionmaker):
    monkeypatch.setattr(review_submit_api, "EMAIL_VERIFICATION_ENABLED", True)
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    captured = _capture_mail(monkeypatch)
    await _seed_course(test_sessionmaker)
    submit_client = http_client_factory(review_submit_api, monkeypatch)
    await submit_client.post("/submit", data=VALID_FORM)
    first_token = _extract_token(captured["verify_url"])

    resend_client = http_client_factory(email_verify_api, monkeypatch)
    resp = await resend_client.post("/api/email/resend", json={"id_token": "valid-token"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    second_token = _extract_token(captured["verify_url"])
    assert second_token != first_token

    verify_client = http_client_factory(email_verify_api, monkeypatch)
    resp_old = await verify_client.get(f"/api/email/verify?token={first_token}")
    assert resp_old.status_code == 400
    resp_new = await verify_client.get(f"/api/email/verify?token={second_token}")
    assert resp_new.status_code == 200
