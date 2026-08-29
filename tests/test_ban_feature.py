"""虚偽レビュー対策のBAN機能(a02dddb, 2026-08-29)のE2Eテスト。

管理画面からのBAN/解除操作がban_status_cacheへ即時反映されること、および
BAN中のユーザーが各書き込み系エンドポイント(レビュー投稿・レビュー閲覧解除・
プロフィール編集)で拒否されることを実HTTPリクエスト経由で検証する
(2026-08-29技術的負債監査で「テストが皆無」と指摘され追加)。
"""
from datetime import datetime, timezone

import pytest

import routers.admin.users_errors as admin_users
import routers.liff_api as liff_api
import routers.profile_api as profile_api
import routers.review_submit_api as review_submit_api
from core import moderation
from core.config import ADMIN_COOKIE
from core.security import make_admin_token
from models import CourseSection, Instructor, Subject, UserProfile

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


async def _seed_profile(test_sessionmaker, uid: str, *, banned: bool, complete: bool = True) -> None:
    async with test_sessionmaker() as session:
        session.add(UserProfile(
            line_user_id=uid,
            name="太郎",
            student_id="2345678S",
            faculty="経営学部" if complete else None,
            department="経営学科" if complete else None,
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
        "comment": "とても勉強になりました",
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
    })
    assert resp.status_code == 200
