"""メール認証(なりすまし防止)のE2Eテスト。

投稿フォームを開く前段のゲート(/api/email/request)→マジックリンク(/api/email/verify)→
通常の投稿(/submit)という一連の流れと、異常系(期限切れ・使用済み・不正トークン・
重複学籍番号・ゲートを迂回した直接投稿)を実HTTPリクエスト経由で検証する。
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import routers.email_verify_api as email_verify_api
import routers.profile_api as profile_api
import routers.review_submit_api as review_submit_api
from models import CourseSection, EmailVerification, Instructor, Review, Subject, UserProfile

UID = "U65326572657669657765723100000000"


def _fake_verify(monkeypatch, user_id: str = UID):
    async def _verify(id_token, request=None):
        return user_id if id_token == "valid-token" else None
    monkeypatch.setattr(review_submit_api, "verify_liff_id_token", _verify)
    monkeypatch.setattr(email_verify_api, "verify_liff_id_token", _verify)
    monkeypatch.setattr(profile_api, "verify_liff_id_token", _verify)


def _stub_push_notification(monkeypatch):
    async def _noop(**kwargs):
        return None
    monkeypatch.setattr(review_submit_api, "send_push_notification", _noop)


def _capture_mail(monkeypatch):
    captured = {}

    async def _fake_send(to_email, verify_url, user_id=None):
        captured["to_email"] = to_email
        captured["verify_url"] = verify_url
        return True
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


REQUEST_FORM = {"id_token": "valid-token", "reg_name": "神戸太郎", "student_id": "2345678S"}

SUBMIT_FORM = {
    "course_name": "経営管理",
    "rating": "4",
    "ease_rating": "A",
    "comment": "とても勉強になりました",
    "id_token": "valid-token",
    "student_id": "2345678S",
    "academic_year": "2026",
}


def _extract_token(verify_url: str) -> str:
    return verify_url.split("token=", 1)[1]


@pytest.mark.asyncio
async def test_request_creates_pending_verification_without_profile(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    captured = _capture_mail(monkeypatch)
    client = http_client_factory(email_verify_api, monkeypatch)

    resp = await client.post("/api/email/request", data=REQUEST_FORM)
    assert resp.status_code == 200
    assert captured["to_email"] == "2345678s@stu.kobe-u.ac.jp"

    async with test_sessionmaker() as session:
        assert await session.get(UserProfile, UID) is None
        pending = (await session.execute(select(EmailVerification))).scalars().one()
        assert pending.student_id == "2345678S"
        assert pending.consumed_at is None


@pytest.mark.asyncio
async def test_request_shows_error_when_mail_send_fails(http_client_factory, monkeypatch, test_sessionmaker):
    """Brevo API障害等でsend_verification_email()がFalseを返した場合、握りつぶして
    「送信しました」画面を返さず、エラー画面を返すことを固定する(2026-08-29修正)。"""
    _fake_verify(monkeypatch)

    async def _fake_send_failure(to_email, verify_url, user_id=None):
        return False
    monkeypatch.setattr(email_verify_api, "send_verification_email", _fake_send_failure)
    client = http_client_factory(email_verify_api, monkeypatch)

    resp = await client.post("/api/email/request", data=REQUEST_FORM)
    assert resp.status_code == 400
    assert "送信に失敗" in resp.text


@pytest.mark.asyncio
async def test_request_rejects_student_id_taken_by_another_account(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _capture_mail(monkeypatch)
    async with test_sessionmaker() as session:
        session.add(UserProfile(line_user_id="U6578697374696e677573657200000000", name="既存", student_id="2345678S"))
        await session.commit()
    client = http_client_factory(email_verify_api, monkeypatch)

    resp = await client.post("/api/email/request", data=REQUEST_FORM)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_verify_get_shows_confirm_page_without_consuming_token(http_client_factory, monkeypatch, test_sessionmaker):
    """GETはトークンを消費しない(確認画面を表示するだけ)。実際の確定はPOSTで行う。

    修正理由: 大学メールのセキュリティシステムと思われる自動アクセスが、ユーザー本人が
    タップする前にGETでこのURLへアクセスし、ワンタイムトークンを消費してしまう事故が
    実際に発生した(2026-08-29)。GETでは副作用を起こさないことをここで固定する。"""
    _fake_verify(monkeypatch)
    captured = _capture_mail(monkeypatch)
    request_client = http_client_factory(email_verify_api, monkeypatch)
    await request_client.post("/api/email/request", data=REQUEST_FORM)

    verify_client = http_client_factory(email_verify_api, monkeypatch)
    token = _extract_token(captured["verify_url"])
    get_resp = await verify_client.get(f"/api/email/verify?token={token}")
    assert get_resp.status_code == 200

    async with test_sessionmaker() as session:
        assert await session.get(UserProfile, UID) is None
        pending = (await session.execute(select(EmailVerification))).scalars().one()
        assert pending.consumed_at is None

    post_resp = await verify_client.post("/api/email/verify", data={"token": token})
    assert post_resp.status_code == 200

    async with test_sessionmaker() as session:
        profile = await session.get(UserProfile, UID)
        assert profile is not None
        assert profile.name == "神戸太郎"
        assert profile.email_verified_at is not None
        assert (await session.execute(select(Review))).scalars().first() is None


@pytest.mark.asyncio
async def test_verify_then_submit_rejected_until_registration_completed(http_client_factory, monkeypatch, test_sessionmaker):
    """メール認証は会員登録の一部（本人確認ステップ）であり、それ単体では会員登録
    完了とみなさない。学部・学科未入力のままでは/submitは拒否される。"""
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    captured = _capture_mail(monkeypatch)
    await _seed_course(test_sessionmaker)

    request_client = http_client_factory(email_verify_api, monkeypatch)
    await request_client.post("/api/email/request", data=REQUEST_FORM)
    verify_client = http_client_factory(email_verify_api, monkeypatch)
    token = _extract_token(captured["verify_url"])
    await verify_client.post("/api/email/verify", data={"token": token})

    submit_client = http_client_factory(review_submit_api, monkeypatch)
    resp = await submit_client.post("/submit", data=SUBMIT_FORM)
    assert resp.status_code == 400

    async with test_sessionmaker() as session:
        assert (await session.execute(select(Review))).scalars().first() is None
        profile = await session.get(UserProfile, UID)
        assert profile is not None
        assert profile.faculty is None


@pytest.mark.asyncio
async def test_verify_then_register_then_submit_creates_review(http_client_factory, monkeypatch, test_sessionmaker):
    """メール認証→/api/register（学部・学科入力）で会員登録が完了して初めて/submitが通る。"""
    async def _noop_unlink(user_id, rich_menu_id=None):
        return None
    monkeypatch.setattr(profile_api.line_client, "link_rich_menu", _noop_unlink)

    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    captured = _capture_mail(monkeypatch)
    await _seed_course(test_sessionmaker)

    request_client = http_client_factory(email_verify_api, monkeypatch)
    await request_client.post("/api/email/request", data=REQUEST_FORM)
    verify_client = http_client_factory(email_verify_api, monkeypatch)
    token = _extract_token(captured["verify_url"])
    await verify_client.post("/api/email/verify", data={"token": token})

    register_client = http_client_factory(profile_api, monkeypatch)
    resp = await register_client.post("/api/register", data={
        "id_token": "valid-token", "name": "神戸太郎", "student_id": "2345678S",
        "faculty": "経営学部", "department": "経営学科",
    })
    assert resp.status_code == 200

    submit_client = http_client_factory(review_submit_api, monkeypatch)
    resp = await submit_client.post("/submit", data=SUBMIT_FORM)
    assert resp.status_code == 200

    async with test_sessionmaker() as session:
        review = (await session.execute(select(Review))).scalars().one()
        assert review.content == "とても勉強になりました"
        assert review.status == "pending"
        profile = await session.get(UserProfile, UID)
        assert profile.faculty == "経営学部"
        assert profile.email_verified_at is not None


@pytest.mark.asyncio
async def test_submit_without_any_profile_rejected(http_client_factory, monkeypatch, test_sessionmaker):
    """/verify-email ゲートを経由せず直接/submitを叩く迂回策への防御を確認する。"""
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    resp = await client.post("/submit", data=SUBMIT_FORM)
    assert resp.status_code == 400

    async with test_sessionmaker() as session:
        assert (await session.execute(select(Review))).scalars().first() is None
        assert await session.get(UserProfile, UID) is None


@pytest.mark.asyncio
async def test_fully_registered_profile_can_submit_regardless_of_email_verification(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    async with test_sessionmaker() as session:
        session.add(UserProfile(line_user_id=UID, name="神戸太郎", student_id="2345678S", faculty="経営学部", department="経営学科"))
        await session.commit()
    client = http_client_factory(review_submit_api, monkeypatch)

    resp = await client.post("/submit", data=SUBMIT_FORM)
    assert resp.status_code == 200

    async with test_sessionmaker() as session:
        review = (await session.execute(select(Review))).scalars().one()
        assert review.content == "とても勉強になりました"


@pytest.mark.asyncio
async def test_verify_replays_consumed_token_as_success(http_client_factory, monkeypatch, test_sessionmaker):
    """二重タップや同じメールリンクの再アクセスで既に消費済みのトークンに再度アクセスしても、
    本人確認自体は完了しているので200(完了画面)を返す。他人による使い回し等、本人確認が
    完了していないケースのエラーはtest_verify_rejects_expired_tokenやid_token検証失敗系で担保する。"""
    _fake_verify(monkeypatch)
    captured = _capture_mail(monkeypatch)
    request_client = http_client_factory(email_verify_api, monkeypatch)
    await request_client.post("/api/email/request", data=REQUEST_FORM)

    verify_client = http_client_factory(email_verify_api, monkeypatch)
    token = _extract_token(captured["verify_url"])
    resp1 = await verify_client.post("/api/email/verify", data={"token": token})
    assert resp1.status_code == 200
    resp2 = await verify_client.post("/api/email/verify", data={"token": token})
    assert resp2.status_code == 200
    resp3 = await verify_client.get(f"/api/email/verify?token={token}")
    assert resp3.status_code == 200


@pytest.mark.asyncio
async def test_verify_rejects_expired_token(http_client_factory, monkeypatch, test_sessionmaker):
    verify_client = http_client_factory(email_verify_api, monkeypatch)
    async with test_sessionmaker() as session:
        session.add(EmailVerification(
            line_user_id=UID,
            student_id="2345678S",
            token_hash=email_verify_api._hash_token("expired-token"),
            payload='{"name": "神戸太郎"}',
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
async def test_resend_reissues_token_for_pending_verification(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    captured = _capture_mail(monkeypatch)
    request_client = http_client_factory(email_verify_api, monkeypatch)
    await request_client.post("/api/email/request", data=REQUEST_FORM)
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


@pytest.mark.asyncio
async def test_resend_reports_failure_when_mail_send_fails(http_client_factory, monkeypatch, test_sessionmaker):
    """再送信時もsend_verification_email()の結果をそのまま{"ok": ...}へ反映することを固定する
    (2026-08-29修正、以前は送信失敗時も常にok:Trueを返しフロント側の失敗表示が出せなかった)。"""
    _fake_verify(monkeypatch)
    captured = _capture_mail(monkeypatch)
    request_client = http_client_factory(email_verify_api, monkeypatch)
    await request_client.post("/api/email/request", data=REQUEST_FORM)
    assert captured

    async def _fake_send_failure(to_email, verify_url, user_id=None):
        return False
    monkeypatch.setattr(email_verify_api, "send_verification_email", _fake_send_failure)
    resend_client = http_client_factory(email_verify_api, monkeypatch)
    resp = await resend_client.post("/api/email/resend", json={"id_token": "valid-token"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
