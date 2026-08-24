"""profile_api.py /api/register (会員登録)のAPI経由E2Eテスト。

初回登録時のレビュー閲覧権チケット付与（ウェルカムボーナス）と、既存プロフィールの
更新（再登録）時に二重付与しないことを実HTTPリクエスト経由で検証する。
"""
import pytest

import routers.profile_api as profile_api
from core.config import REGISTRATION_WELCOME_UNLOCK_CREDITS
from models import UserProfile

USER_ID = "U11111111111111111111111111111111"


def _fake_verify(monkeypatch, user_id: str = USER_ID):
    async def _verify(id_token, request=None):
        return user_id if id_token == "valid-token" else None
    monkeypatch.setattr(profile_api, "verify_liff_id_token", _verify)


def _stub_unlink_rich_menu(monkeypatch):
    async def _noop(user_id):
        return None
    monkeypatch.setattr(profile_api.line_client, "unlink_rich_menu", _noop)


VALID_FORM = {
    "id_token": "valid-token",
    "name": "神戸太郎",
    "student_id": "2345678S",
    "faculty": "経営学部",
    "department": "経営学科",
}


@pytest.mark.asyncio
async def test_register_new_user_grants_welcome_credits(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _stub_unlink_rich_menu(monkeypatch)
    client = http_client_factory(profile_api, monkeypatch)

    resp = await client.post("/api/register", data=VALID_FORM)
    assert resp.status_code == 200
    assert f"{REGISTRATION_WELCOME_UNLOCK_CREDITS}枚プレゼント" in resp.text

    async with test_sessionmaker() as session:
        profile = await session.get(UserProfile, USER_ID)
        assert profile is not None
        assert profile.unlock_credits == REGISTRATION_WELCOME_UNLOCK_CREDITS


@pytest.mark.asyncio
async def test_register_existing_user_does_not_double_grant_credits(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _stub_unlink_rich_menu(monkeypatch)
    client = http_client_factory(profile_api, monkeypatch)

    first = await client.post("/api/register", data=VALID_FORM)
    assert first.status_code == 200

    updated_form = {**VALID_FORM, "name": "神戸次郎"}
    second = await client.post("/api/register", data=updated_form)
    assert second.status_code == 200
    assert "プレゼント" not in second.text

    async with test_sessionmaker() as session:
        profile = await session.get(UserProfile, USER_ID)
        assert profile.name == "神戸次郎"
        assert profile.unlock_credits == REGISTRATION_WELCOME_UNLOCK_CREDITS
