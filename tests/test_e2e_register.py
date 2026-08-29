"""profile_api.py /api/register (会員登録)のAPI経由E2Eテスト。

初回登録時のレビュー閲覧権チケット付与（ウェルカムボーナス）と、既存プロフィールの
更新（再登録）時に二重付与しないことを実HTTPリクエスト経由で検証する。
"""
import pytest

import routers.profile_api as profile_api
from core.config import REGISTRATION_WELCOME_UNLOCK_CREDITS, WELCOME_PROMO_SUBJECT_ID
from models import Subject, UserProfile

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


@pytest.mark.asyncio
async def test_register_new_user_blocked_when_email_verification_enabled(http_client_factory, monkeypatch, test_sessionmaker):
    """EMAIL_VERIFICATION_ENABLED時、/verify-emailを経由せずUserProfileを新規作成できてしまう
    抜け道(LINE友だち追加時の登録案内が/registerへ直接誘導していた)を防ぐガードの検証。"""
    _fake_verify(monkeypatch)
    _stub_unlink_rich_menu(monkeypatch)
    monkeypatch.setattr(profile_api, "EMAIL_VERIFICATION_ENABLED", True)
    client = http_client_factory(profile_api, monkeypatch)

    resp = await client.post("/api/register", data=VALID_FORM)
    assert resp.status_code == 400
    assert "メールアドレス認証" in resp.text

    async with test_sessionmaker() as session:
        profile = await session.get(UserProfile, USER_ID)
        assert profile is None


@pytest.mark.asyncio
async def test_register_existing_user_allowed_when_email_verification_enabled(http_client_factory, monkeypatch, test_sessionmaker):
    """メール認証済み(=UserProfileが既に存在する)ユーザーの本登録(学部学科入力)は
    EMAIL_VERIFICATION_ENABLED時も引き続き許可される。"""
    _fake_verify(monkeypatch)
    _stub_unlink_rich_menu(monkeypatch)
    async with test_sessionmaker() as session:
        session.add(UserProfile(line_user_id=USER_ID, name="神戸太郎", student_id="2345678S"))
        await session.commit()
    monkeypatch.setattr(profile_api, "EMAIL_VERIFICATION_ENABLED", True)
    client = http_client_factory(profile_api, monkeypatch)

    resp = await client.post("/api/register", data=VALID_FORM)
    assert resp.status_code == 200

    async with test_sessionmaker() as session:
        profile = await session.get(UserProfile, USER_ID)
        assert profile.faculty == "経営学部"
        assert profile.department == "経営学科"


@pytest.mark.asyncio
async def test_register_missing_faculty_field_shows_friendly_error(http_client_factory, monkeypatch, test_sessionmaker):
    """54a0821の回帰テスト。/api/registerのForm引数が全てForm(...)(必須)だった頃は、
    POSTボディにfaculty自体が含まれないと生の{"detail":[...]}バリデーションエラーが
    そのまま表示されていた。Form("")化により、既存の日本語エラーメッセージ分岐へ
    流れることを固定する。"""
    _fake_verify(monkeypatch)
    _stub_unlink_rich_menu(monkeypatch)
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
    _stub_unlink_rich_menu(monkeypatch)
    client = http_client_factory(profile_api, monkeypatch)

    form = {k: v for k, v in VALID_FORM.items() if k != "department"}
    resp = await client.post("/api/register", data=form)
    assert resp.status_code == 400
    assert "detail" not in resp.text
    assert "学科を選択してください" in resp.text


@pytest.mark.asyncio
async def test_register_new_user_sees_promo_course_link(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _stub_unlink_rich_menu(monkeypatch)
    async with test_sessionmaker() as session:
        session.add(Subject(id=WELCOME_PROMO_SUBJECT_ID, name="データサイエンス基礎学", faculty="教養教育院"))
        await session.commit()
    client = http_client_factory(profile_api, monkeypatch)

    resp = await client.post("/api/register", data=VALID_FORM)
    assert resp.status_code == 200
    assert "データサイエンス基礎学" in resp.text
    assert f"course_id={WELCOME_PROMO_SUBJECT_ID}" in resp.text
