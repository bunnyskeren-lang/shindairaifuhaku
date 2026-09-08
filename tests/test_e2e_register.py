"""profile_api.py /api/register (会員登録)のAPI経由E2Eテスト。

初回登録時のレビュー閲覧権チケット付与（ウェルカムボーナス）と、既存プロフィールの
更新（再登録）時に二重付与しないことを実HTTPリクエスト経由で検証する。
"""
import pytest

import routers.profile_api as profile_api
from core.config import (
    REGISTRATION_WELCOME_UNLOCK_CREDITS,
    REVIEW_APPROVAL_UNLOCK_CREDITS_KYOYO,
    REVIEW_APPROVAL_UNLOCK_CREDITS_SENMON,
)
from models import UserProfile

USER_ID = "U11111111111111111111111111111111"


def _fake_verify(monkeypatch, user_id: str = USER_ID):
    async def _verify(id_token, request=None):
        return user_id if id_token == "valid-token" else None
    monkeypatch.setattr(profile_api, "verify_liff_id_token", _verify)


def _stub_link_rich_menu(monkeypatch):
    async def _noop(user_id, rich_menu_id=None):
        return None
    monkeypatch.setattr(profile_api.line_client, "link_rich_menu", _noop)


VALID_FORM = {
    "id_token": "valid-token",
    "name": "神戸太郎",
    "student_id": "2345678S",
    "faculty": "経営学部",
    "department": "経営学科",
    "coop_jobsite_known": "はい",
}


@pytest.mark.asyncio
async def test_register_new_user_grants_welcome_credits(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _stub_link_rich_menu(monkeypatch)
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
    _stub_link_rich_menu(monkeypatch)
    client = http_client_factory(profile_api, monkeypatch)

    first = await client.post("/api/register", data=VALID_FORM)
    assert first.status_code == 200

    updated_form = {**VALID_FORM, "name": "神戸次郎"}
    second = await client.post("/api/register", data=updated_form)
    assert second.status_code == 200
    # 完了画面の文面は初回登録と同一にする方針（2026-09-08、ユーザー指示）。
    # 二重付与しないことはDB上の unlock_credits で担保する。

    async with test_sessionmaker() as session:
        profile = await session.get(UserProfile, USER_ID)
        assert profile.name == "神戸次郎"
        assert profile.unlock_credits == REGISTRATION_WELCOME_UNLOCK_CREDITS


@pytest.mark.asyncio
async def test_register_missing_faculty_field_shows_friendly_error(http_client_factory, monkeypatch, test_sessionmaker):
    """54a0821の回帰テスト。/api/registerのForm引数が全てForm(...)(必須)だった頃は、
    POSTボディにfaculty自体が含まれないと生の{"detail":[...]}バリデーションエラーが
    そのまま表示されていた。Form("")化により、既存の日本語エラーメッセージ分岐へ
    流れることを固定する。"""
    _fake_verify(monkeypatch)
    _stub_link_rich_menu(monkeypatch)
    client = http_client_factory(profile_api, monkeypatch)

    form = {k: v for k, v in VALID_FORM.items() if k != "faculty"}
    resp = await client.post("/api/register", data=form)
    assert resp.status_code == 400
    assert "detail" not in resp.text
    assert "学部を選択してください" in resp.text


@pytest.mark.asyncio
async def test_register_missing_department_field_shows_friendly_error(http_client_factory, monkeypatch, test_sessionmaker):
    """54a0821の回帰テスト。departmentがPOSTボディに含まれない場合も同様。"""
    _fake_verify(monkeypatch)
    _stub_link_rich_menu(monkeypatch)
    client = http_client_factory(profile_api, monkeypatch)

    form = {k: v for k, v in VALID_FORM.items() if k != "department"}
    resp = await client.post("/api/register", data=form)
    assert resp.status_code == 400
    assert "detail" not in resp.text
    assert "学科を選択してください" in resp.text


@pytest.mark.asyncio
async def test_register_missing_coop_jobsite_known_shows_friendly_error(http_client_factory, monkeypatch, test_sessionmaker):
    """2026-09-07追加の必須項目。未選択（POSTボディに無い／空文字）だと400で誘導文言を返し、
    生のバリデーションエラーを出さないこと。"""
    _fake_verify(monkeypatch)
    _stub_link_rich_menu(monkeypatch)
    client = http_client_factory(profile_api, monkeypatch)

    form = {k: v for k, v in VALID_FORM.items() if k != "coop_jobsite_known"}
    resp = await client.post("/api/register", data=form)
    assert resp.status_code == 400
    assert "detail" not in resp.text
    assert "神大生協が運営するアルバイト求人サイト" in resp.text

    bad = {**VALID_FORM, "coop_jobsite_known": "たぶん"}
    resp2 = await client.post("/api/register", data=bad)
    assert resp2.status_code == 400
    assert "神大生協が運営するアルバイト求人サイト" in resp2.text


@pytest.mark.asyncio
async def test_register_existing_user_recorded_coop_jobsite_answer(http_client_factory, monkeypatch, test_sessionmaker):
    """必須化前に登録済みのユーザー（coop_jobsite_known=NULL）が再登録すると、その回答が
    保存されること（ON CONFLICT DO UPDATE の set_ 対象に含めているため）。"""
    _fake_verify(monkeypatch)
    _stub_link_rich_menu(monkeypatch)
    client = http_client_factory(profile_api, monkeypatch)

    first = await client.post("/api/register", data={**VALID_FORM, "coop_jobsite_known": "いいえ"})
    assert first.status_code == 200
    second = await client.post("/api/register", data={**VALID_FORM, "coop_jobsite_known": "はい"})
    assert second.status_code == 200

    async with test_sessionmaker() as session:
        profile = await session.get(UserProfile, USER_ID)
        assert profile.coop_jobsite_known == "はい"


@pytest.mark.asyncio
async def test_register_new_user_sees_review_view_guidance(http_client_factory, monkeypatch, test_sessionmaker):
    """会員登録完了画面は、レビュー投稿フォームへ誘導せず「レビューを閲覧」の案内と
    チケット付与枚数の注記（教養/専門）を表示する（2026-09-07変更）。"""
    _fake_verify(monkeypatch)
    _stub_link_rich_menu(monkeypatch)
    client = http_client_factory(profile_api, monkeypatch)

    resp = await client.post("/api/register", data=VALID_FORM)
    assert resp.status_code == 200
    assert "「レビューを閲覧」から" in resp.text
    assert f"教養：{REVIEW_APPROVAL_UNLOCK_CREDITS_KYOYO}枚" in resp.text
    assert f"専門：{REVIEW_APPROVAL_UNLOCK_CREDITS_SENMON}枚" in resp.text
    # レビュー投稿フォームへの自動遷移・戻り導線は廃止済み
    assert "course_id=" not in resp.text
    assert "goToReviewForm" not in resp.text
