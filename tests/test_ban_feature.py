"""虚偽レビュー対策のBAN機能(a02dddb, 2026-08-29)のE2Eテスト。

管理画面からのBAN/解除操作がban_status_cacheへ即時反映されること、および
BAN中のユーザーが各書き込み系エンドポイント(レビュー投稿・レビュー閲覧解除・
プロフィール編集)で拒否されることを実HTTPリクエスト経由で検証する
(2026-08-29技術的負債監査で「テストが皆無」と指摘され追加)。
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

import routers.admin.users_errors as admin_users
import routers.liff_api as liff_api
import routers.payment_api as payment_api
import routers.profile_api as profile_api
import routers.review_submit_api as review_submit_api
from core import moderation
from core.config import ADMIN_COOKIE
from core.security import make_admin_token
from models import CourseSection, Instructor, PaymentRequest, Review, ReviewStatus, Subject, UserProfile

BANNED_UID = "U" + "1" * 32
OTHER_UID = "U" + "2" * 32


def _fake_verify(monkeypatch, modules, user_id: str = BANNED_UID):
    async def _verify(id_token, request=None):
        return user_id if id_token == "valid-token" else None
    for m in modules:
        monkeypatch.setattr(m, "verify_liff_id_token", _verify)


def _admin_client(http_client_factory, monkeypatch):
    # http_client_factory()はrouter_module自身に加えてcore.cacheのAsyncSessionLocalも
    # テストDBへ差し替えるため、core.moderation.is_banned()がcore.cache経由で読むDBも
    # 追加設定なしに揃う
    client = http_client_factory(admin_users, monkeypatch)
    client.cookies.set(ADMIN_COOKIE, make_admin_token())
    return client


async def _seed_profile(
    test_sessionmaker, uid: str, *, banned: bool, complete: bool = True, payment_limit: int = 0
) -> None:
    async with test_sessionmaker() as session:
        session.add(UserProfile(
            line_user_id=uid,
            name="太郎",
            student_id="2345678S",
            faculty="経営学部" if complete else None,
            department="経営学科" if complete else None,
            coop_jobsite_known="はい" if complete else None,
            payment_limit=payment_limit,
            banned_at=datetime.now(timezone.utc) if banned else None,
            ban_reason="虚偽投稿" if banned else None,
        ))
        await session.commit()


async def _seed_course(test_sessionmaker, name="経営管理", instructor="山田太郎") -> int:
    async with test_sessionmaker() as session:
        subj = Subject(name=name, faculty="経営学部", category="専門")
        session.add(subj)
        await session.flush()
        instr = Instructor(name=instructor)
        session.add(instr)
        await session.flush()
        cs = CourseSection(subject_id=subj.id, instructor_id=instr.id)
        session.add(cs)
        await session.commit()
        return subj.id


@pytest.mark.asyncio
async def test_admin_ban_sets_banned_at_and_invalidates_cache(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_profile(test_sessionmaker, BANNED_UID, banned=False)
    client = _admin_client(http_client_factory, monkeypatch)

    assert await moderation.is_banned(BANNED_UID) is False

    resp = await client.post(f"/admin/users/ban/{BANNED_UID}", data={"reason": "虚偽投稿"})
    assert resp.status_code == 303

    async with test_sessionmaker() as session:
        profile = await session.get(UserProfile, BANNED_UID)
        assert profile.banned_at is not None
        assert profile.ban_reason == "虚偽投稿"

    assert await moderation.is_banned(BANNED_UID) is True


@pytest.mark.asyncio
async def test_admin_unban_clears_banned_at(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_profile(test_sessionmaker, BANNED_UID, banned=True)
    client = _admin_client(http_client_factory, monkeypatch)

    assert await moderation.is_banned(BANNED_UID) is True

    resp = await client.post(f"/admin/users/unban/{BANNED_UID}", data={})
    assert resp.status_code == 303

    async with test_sessionmaker() as session:
        profile = await session.get(UserProfile, BANNED_UID)
        assert profile.banned_at is None
        assert profile.ban_reason is None

    assert await moderation.is_banned(BANNED_UID) is False


@pytest.mark.asyncio
async def test_banned_user_cannot_submit_review(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_profile(test_sessionmaker, BANNED_UID, banned=True)
    await _seed_course(test_sessionmaker)
    _fake_verify(monkeypatch, [review_submit_api])
    client = http_client_factory(review_submit_api, monkeypatch)

    resp = await client.post("/submit", data={
        "course_name": "経営管理",
        "rating": "4",
        "ease_rating": "A",
        # コメントは MIN_COMMENT_LEN(30) 文字以上ないと長さチェックで先に弾かれるため、
        # BANチェックまで到達する十分な長さの本文にする
        "comment": "とても勉強になりました。予習と復習をきちんとやれば単位は取りやすい印象でした。",
        "id_token": "valid-token",
        "student_id": "2345678S",
        "academic_year": "2026",
    })
    assert resp.status_code == 400
    assert "利用を停止" in resp.text


@pytest.mark.asyncio
async def test_banned_user_cannot_view_course_reviews(http_client_factory, monkeypatch, test_sessionmaker):
    """BANはunlock/submitだけでなく、リッチメニュー経由のレビュー閲覧そのものも封じること
    (2026-08-29、閲覧だけは素通りしていた不備の修正)を検証する。"""
    await _seed_profile(test_sessionmaker, BANNED_UID, banned=True)
    course_id = await _seed_course(test_sessionmaker)
    _fake_verify(monkeypatch, [liff_api])
    client = http_client_factory(liff_api, monkeypatch)

    resp = await client.get(f"/api/course/{course_id}", params={"id_token": "valid-token"})
    assert resp.status_code == 403
    assert "利用を停止" in resp.text


@pytest.mark.asyncio
async def test_non_banned_user_can_view_course_reviews(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_profile(test_sessionmaker, OTHER_UID, banned=False)
    course_id = await _seed_course(test_sessionmaker)
    _fake_verify(monkeypatch, [liff_api], user_id=OTHER_UID)
    client = http_client_factory(liff_api, monkeypatch)

    resp = await client.get(f"/api/course/{course_id}", params={"id_token": "valid-token"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_banned_user_cannot_unlock_course(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_profile(test_sessionmaker, BANNED_UID, banned=True)
    course_id = await _seed_course(test_sessionmaker)
    _fake_verify(monkeypatch, [liff_api])
    client = http_client_factory(liff_api, monkeypatch)

    resp = await client.post(f"/api/course/{course_id}/unlock", json={"id_token": "valid-token"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_banned_user_cannot_edit_profile(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_profile(test_sessionmaker, BANNED_UID, banned=True)
    _fake_verify(monkeypatch, [profile_api])
    client = http_client_factory(profile_api, monkeypatch)

    resp = await client.post("/api/register", data={
        "id_token": "valid-token",
        "name": "太郎",
        "student_id": "2345678S",
        "faculty": "経営学部",
        "department": "",
    })
    assert resp.status_code == 400
    assert "利用を停止" in resp.text


@pytest.mark.asyncio
async def test_prefill_reports_banned_flag_for_review_form_gating(http_client_factory, monkeypatch, test_sessionmaker):
    """form_index.html(レビュー投稿フォーム)は/api/profile/prefillのbannedフラグを見て
    フォームをオーバーレイでブロックする。student_id等の他フィールドは変えず、フラグだけ
    追加すること(contact.htmlは同じレスポンスを見るがbannedを見ずBAN中でも学籍番号表示を
    続けるため、既存フィールドを欠落させると壊れる)。"""
    await _seed_profile(test_sessionmaker, BANNED_UID, banned=True)
    _fake_verify(monkeypatch, [profile_api])
    client = http_client_factory(profile_api, monkeypatch)

    resp = await client.post("/api/profile/prefill", json={"id_token": "valid-token"})
    data = resp.json()
    assert data["found"] is True
    assert data["banned"] is True
    assert data["student_id"] == "2345678S"


@pytest.mark.asyncio
async def test_prefill_reports_not_banned_for_normal_user(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_profile(test_sessionmaker, OTHER_UID, banned=False)
    _fake_verify(monkeypatch, [profile_api], user_id=OTHER_UID)
    client = http_client_factory(profile_api, monkeypatch)

    resp = await client.post("/api/profile/prefill", json={"id_token": "valid-token"})
    data = resp.json()
    assert data["found"] is True
    assert data["banned"] is False


@pytest.mark.asyncio
async def test_banned_student_cannot_submit_payment_request(http_client_factory, monkeypatch, test_sessionmaker):
    """支払い申請フォームはLINE識別子を持たず学籍番号のみで動くため、
    user_profiles.student_id経由でBAN状態を判定する(2026-08-30、支払い申請だけ
    BANチェックが漏れていた不備の修正)。"""
    await _seed_profile(test_sessionmaker, BANNED_UID, banned=True)
    course_id = await _seed_course(test_sessionmaker)
    async with test_sessionmaker() as session:
        cs_id = (await session.execute(
            select(CourseSection.id).where(CourseSection.subject_id == course_id)
        )).scalars().first()
        session.add(Review(
            course_section_id=cs_id, student_id="2345678S",
            status=ReviewStatus.APPROVED, rating=4, ease_rating="A",
        ))
        await session.commit()

    client = http_client_factory(payment_api, monkeypatch)
    resp = await client.post("/payment/apply/submit", data={
        "name": "太郎", "student_id": "2345678S",
        "paypay_display_name": "タロウ", "paypay_id": "taro123",
        "email": "taro@example.com", "amount": "200",
    })
    assert resp.status_code == 400
    assert "利用を停止" in resp.text

    async with test_sessionmaker() as session:
        count = (await session.execute(select(func.count()).select_from(PaymentRequest))).scalar_one()
        assert count == 0


@pytest.mark.asyncio
async def test_banned_student_gets_ineligible_for_payment(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_profile(test_sessionmaker, BANNED_UID, banned=True)
    client = http_client_factory(payment_api, monkeypatch)

    resp = await client.get("/api/payment/eligible", params={"student_id": "2345678S"})
    assert resp.json() == {"valid": False}


@pytest.mark.asyncio
async def test_non_banned_student_can_submit_payment_request(http_client_factory, monkeypatch, test_sessionmaker):
    # 2026-09-07以降、申請額は管理画面で設定したpayment_limit（円）そのもので、
    # 承認済みレビューの件数とは一切連動しない
    await _seed_profile(test_sessionmaker, OTHER_UID, banned=False, payment_limit=200)

    client = http_client_factory(payment_api, monkeypatch)
    resp = await client.post("/payment/apply/submit", data={
        "name": "花子", "student_id": "2345678S",
        "paypay_display_name": "ハナコ", "paypay_id": "hanako123",
        "email": "hanako@example.com",
    })
    # 成功は PRG（Post/Redirect/Get）で /payment/apply/done へ 303
    assert resp.status_code == 303
    assert resp.headers["location"] == "/payment/apply/done"

    async with test_sessionmaker() as session:
        pr = (await session.execute(select(PaymentRequest))).scalars().first()
        assert pr is not None and pr.amount == 200
        assert pr.paypay_display_name == "ハナコ"
        assert pr.email == "hanako@example.com"


@pytest.mark.asyncio
async def test_student_without_payment_limit_cannot_submit(http_client_factory, monkeypatch, test_sessionmaker):
    """payment_limitが0（既定）のユーザーは申請フォームから申請できない。"""
    await _seed_profile(test_sessionmaker, OTHER_UID, banned=False, payment_limit=0)

    client = http_client_factory(payment_api, monkeypatch)
    resp = await client.post("/payment/apply/submit", data={
        "name": "花子", "student_id": "2345678S",
        "paypay_display_name": "ハナコ", "paypay_id": "hanako123",
        "email": "hanako@example.com",
    })
    assert resp.status_code == 400

    async with test_sessionmaker() as session:
        count = (await session.execute(select(func.count()).select_from(PaymentRequest))).scalar_one()
        assert count == 0


@pytest.mark.asyncio
async def test_payment_apply_duplicate_submit_shows_already_sent(http_client_factory, monkeypatch, test_sessionmaker):
    """本人は「申請する」を1回押しただけでも、OS/webviewの保留POST再送で2回届くことがある。
    同じ submit_nonce の2回目はエラー画面（送信できませんでした）ではなく
    『既に送信済みです』（PRGで /payment/apply/done?dup=1）へ流し、DB上も1件に収束する。"""
    await _seed_profile(test_sessionmaker, OTHER_UID, banned=False, payment_limit=200)
    client = http_client_factory(payment_api, monkeypatch)
    payload = {
        "name": "花子", "student_id": "2345678S",
        "paypay_display_name": "ハナコ", "paypay_id": "hanako123",
        "email": "hanako@example.com", "submit_nonce": "nonce-abc-123",
    }

    resp1 = await client.post("/payment/apply/submit", data=payload)
    assert resp1.status_code == 303
    assert resp1.headers["location"] == "/payment/apply/done"

    resp2 = await client.post("/payment/apply/submit", data=payload)
    assert resp2.status_code == 303
    assert resp2.headers["location"] == "/payment/apply/done?dup=1"

    async with test_sessionmaker() as session:
        count = (await session.execute(select(func.count()).select_from(PaymentRequest))).scalar_one()
        assert count == 1

    done = await client.get("/payment/apply/done", params={"dup": "1"})
    assert done.status_code == 200
    assert "既に送信済みです" in done.text


@pytest.mark.asyncio
async def test_payment_apply_second_pending_without_nonce_shows_already_sent(http_client_factory, monkeypatch, test_sessionmaker):
    """nonce が一致しない二重送信（古いキャッシュのフォーム等）でも、既に支払い待ちの
    申請があれば『既に送信済みです』へ寄せる（エラー画面にしない）。"""
    await _seed_profile(test_sessionmaker, OTHER_UID, banned=False, payment_limit=200)
    client = http_client_factory(payment_api, monkeypatch)
    base = {
        "name": "花子", "student_id": "2345678S",
        "paypay_display_name": "ハナコ", "paypay_id": "hanako123",
        "email": "hanako@example.com",
    }

    resp1 = await client.post("/payment/apply/submit", data={**base, "submit_nonce": "n1"})
    assert resp1.status_code == 303
    assert resp1.headers["location"] == "/payment/apply/done"

    resp2 = await client.post("/payment/apply/submit", data={**base, "submit_nonce": "n2"})
    assert resp2.status_code == 303
    assert resp2.headers["location"] == "/payment/apply/done?dup=1"

    async with test_sessionmaker() as session:
        count = (await session.execute(select(func.count()).select_from(PaymentRequest))).scalar_one()
        assert count == 1


@pytest.mark.asyncio
async def test_payment_apply_rejects_bad_email(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_profile(test_sessionmaker, OTHER_UID, banned=False, payment_limit=200)
    client = http_client_factory(payment_api, monkeypatch)
    resp = await client.post("/payment/apply/submit", data={
        "name": "花子", "student_id": "2345678S",
        "paypay_display_name": "ハナコ", "paypay_id": "hanako123",
        "email": "not-an-email",
    })
    assert resp.status_code == 400
    assert "メールアドレス" in resp.text
    async with test_sessionmaker() as session:
        count = (await session.execute(select(func.count()).select_from(PaymentRequest))).scalar_one()
        assert count == 0


@pytest.mark.asyncio
async def test_non_banned_user_unaffected(http_client_factory, monkeypatch, test_sessionmaker):
    """BANチェックの追加が、通常ユーザーの正常系を巻き込んでいないことを確認する。"""
    await _seed_profile(test_sessionmaker, OTHER_UID, banned=False, complete=True)
    _fake_verify(monkeypatch, [profile_api], user_id=OTHER_UID)
    client = http_client_factory(profile_api, monkeypatch)

    resp = await client.post("/api/register", data={
        "id_token": "valid-token",
        "name": "花子",
        "student_id": "2345678S",
        "faculty": "経営学部",
        "department": "経営学科",
        "coop_jobsite_known": "はい",
    })
    assert resp.status_code == 200
