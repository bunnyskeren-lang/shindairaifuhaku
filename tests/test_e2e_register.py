"""profile_api.py /api/register (会員登録)のAPI経由E2Eテスト。

初回登録時のレビュー閲覧権チケット付与（ウェルカムボーナス）と、既存プロフィールの
更新（再登録）時に二重付与しないことを実HTTPリクエスト経由で検証する。
"""
import asyncio

import pytest
from sqlalchemy import select

import core.funnel as funnel
import routers.profile_api as profile_api
from core.background_tasks import _background_tasks
from core.config import (
    REGISTRATION_WELCOME_UNLOCK_CREDITS,
    REVIEW_APPROVAL_UNLOCK_CREDITS_KYOYO,
    REVIEW_APPROVAL_UNLOCK_CREDITS_SENMON,
)
from models import FunnelEvent, UserProfile
from tests.conftest import patch_async_session_local

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

    # 成功時は PRG（Post/Redirect/Get）で 303 → GET /register/done
    resp = await client.post("/api/register", data=VALID_FORM)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/register/done"

    done = await client.get(resp.headers["location"])
    assert done.status_code == 200
    # チケット型カード: 枚数は大きな数字だけの<span>で、続く「枚」「プレゼント！」は別要素
    assert f'>{REGISTRATION_WELCOME_UNLOCK_CREDITS}</span><span class="text-3xl font-bold">枚</span>' in done.text
    assert "プレゼント！" in done.text

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
    assert first.status_code == 303

    updated_form = {**VALID_FORM, "name": "神戸次郎"}
    second = await client.post("/api/register", data=updated_form)
    assert second.status_code == 303
    # 完了画面の文面は初回登録と同一にする方針（2026-09-08、ユーザー指示）。
    # 二重付与しないことはDB上の unlock_credits で担保する。

    async with test_sessionmaker() as session:
        profile = await session.get(UserProfile, USER_ID)
        assert profile.name == "神戸次郎"
        assert profile.unlock_credits == REGISTRATION_WELCOME_UNLOCK_CREDITS


@pytest.mark.asyncio
async def test_register_same_nonce_is_idempotent(http_client_factory, monkeypatch, test_sessionmaker):
    """送信直後のアプリbg化でOS/webviewが保留POSTを再送する事象（2026-09-22、
    liff_auth_failed:IdToken expired. として顕在化）の回帰テスト。同じ register_nonce の
    2回目のPOSTは、1回目送信後にid_tokenが失効していても（=verify_liff_id_tokenが失敗する
    トークンでも）LINEログイン再検証を経由せず1回目の成功ページへ直行すること。"""
    _fake_verify(monkeypatch)
    _stub_link_rich_menu(monkeypatch)
    client = http_client_factory(profile_api, monkeypatch)

    form = {**VALID_FORM, "register_nonce": "nonce-abc-123"}
    first = await client.post("/api/register", data=form)
    assert first.status_code == 303

    # 2回目は期限切れ等で検証に失敗するトークンで再送されるケースを模す
    replay = {**form, "id_token": "expired-token"}
    second = await client.post("/api/register", data=replay)
    assert second.status_code == 303
    assert second.headers["location"] == "/register/done"

    async with test_sessionmaker() as session:
        profile = await session.get(UserProfile, USER_ID)
        assert profile is not None
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
    assert first.status_code == 303
    second = await client.post("/api/register", data={**VALID_FORM, "coop_jobsite_known": "はい"})
    assert second.status_code == 303

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
    assert resp.status_code == 303
    done = await client.get(resp.headers["location"])
    assert done.status_code == 200
    assert "「レビューを閲覧」から" in done.text
    # 「レビュー投稿でチケット獲得」ブロック: 教養+5枚・専門+3枚（科目名と枚数は別要素）
    assert "教養科目" in done.text and f"+{REVIEW_APPROVAL_UNLOCK_CREDITS_KYOYO}<span" in done.text
    assert "専門科目" in done.text and f"+{REVIEW_APPROVAL_UNLOCK_CREDITS_SENMON}<span" in done.text
    # レビュー投稿フォームへの自動遷移・戻り導線は廃止済み
    assert "course_id=" not in done.text
    assert "goToReviewForm" not in done.text


BROWSER_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Line/14.0.0"


async def _funnel_events(test_sessionmaker):
    while _background_tasks:
        await asyncio.gather(*list(_background_tasks))
    await asyncio.sleep(0)
    async with test_sessionmaker() as session:
        return [e.event for e in (await session.execute(select(FunnelEvent).order_by(FunnelEvent.id))).scalars()]


@pytest.mark.asyncio
async def test_register_new_user_records_funnel_event_but_reregistration_does_not(
    http_client_factory, monkeypatch, test_sessionmaker
):
    """新規の会員登録完了だけ funnel_events(register_done) に記録し、既存ユーザーの再登録
    （生協求人質問の埋め直し）は数えない。"""
    _fake_verify(monkeypatch)
    _stub_link_rich_menu(monkeypatch)
    client = http_client_factory(profile_api, monkeypatch)
    patch_async_session_local(monkeypatch, funnel, test_sessionmaker)
    headers = {"user-agent": BROWSER_UA}

    first = await client.post("/api/register", data=VALID_FORM, headers=headers)
    assert first.status_code == 303
    assert await _funnel_events(test_sessionmaker) == ["register_done"]

    second = await client.post("/api/register", data={**VALID_FORM, "name": "神戸次郎"}, headers=headers)
    assert second.status_code == 303
    assert await _funnel_events(test_sessionmaker) == ["register_done"]


@pytest.mark.asyncio
async def test_prefill_for_unregistered_user_returns_own_uid_for_register_link(
    http_client_factory, monkeypatch
):
    """未登録なら検証済みの本人のuidを返す（投稿フォームが登録画面リンクの?uid=に使う）。"""
    _fake_verify(monkeypatch)
    client = http_client_factory(profile_api, monkeypatch)

    resp = await client.post("/api/profile/prefill", json={"id_token": "valid-token"})
    assert resp.json() == {"found": False, "uid": USER_ID}
