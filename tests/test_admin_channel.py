"""ゲスト用bot / 本番botのチャンネル切替（source列・管理画面の絞り込み・両チャンネル利用者の表示）のテスト。

ゲスト用と本番は同じLINEプロバイダー配下で同一人物が同じユーザーIDになるため、ユーザーIDではなく
ログの source 列（core.config.CHANNEL）で区別する。ユーザー一覧では is_guest と user_activity.source
から「両方使っている人」を判定して本番の管理画面に出す。
"""
from datetime import UTC, datetime

import pytest

import routers.admin.users_errors as users_errors
from core.config import ADMIN_COOKIE
from core.security import make_admin_token
from models import ErrorLog, UserActivity, UserProfile


def _client(http_client_factory, monkeypatch, channel=None):
    client = http_client_factory(users_errors, monkeypatch)
    client.cookies.set(ADMIN_COOKIE, make_admin_token())
    if channel:
        client.cookies.set("admin_channel", channel)
    return client


async def _seed_errors(sm):
    async with sm() as s:
        s.add_all([
            ErrorLog(action="a", error_type="E", error_message="本番側のエラー", traceback="t", source="main"),
            ErrorLog(action="b", error_type="E", error_message="ゲスト側のエラー", traceback="t", source="guest"),
        ])
        await s.commit()


@pytest.mark.asyncio
async def test_errors_default_channel_is_the_services_own_channel(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_errors(test_sessionmaker)
    resp = await _client(http_client_factory, monkeypatch).get("/admin/errors")
    assert "本番側のエラー" in resp.text
    assert "ゲスト側のエラー" not in resp.text


@pytest.mark.asyncio
async def test_errors_cookie_switches_channel(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_errors(test_sessionmaker)
    resp = await _client(http_client_factory, monkeypatch, "guest").get("/admin/errors")
    assert "ゲスト側のエラー" in resp.text
    assert "本番側のエラー" not in resp.text


@pytest.mark.asyncio
async def test_errors_all_channels_shows_both_with_badges(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_errors(test_sessionmaker)
    resp = await _client(http_client_factory, monkeypatch, "all").get("/admin/errors")
    assert "本番側のエラー" in resp.text and "ゲスト側のエラー" in resp.text
    assert 'ch-badge guest' in resp.text and 'ch-badge main' in resp.text


@pytest.mark.asyncio
async def test_invalid_channel_falls_back_to_default(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_errors(test_sessionmaker)
    resp = await _client(http_client_factory, monkeypatch, "bogus").get("/admin/errors")
    assert "本番側のエラー" in resp.text
    assert "ゲスト側のエラー" not in resp.text


def _profile(uid, name, sid, is_guest):
    return UserProfile(line_user_id=uid, name=name, student_id=sid, faculty="経営学部",
                       department="", coop_jobsite_known="はい", is_guest=is_guest)


@pytest.mark.asyncio
async def test_users_page_separates_channels_and_flags_users_in_both(
    http_client_factory, monkeypatch, test_sessionmaker
):
    now = datetime.now(UTC)
    async with test_sessionmaker() as s:
        s.add_all([
            _profile("U" + "1" * 32, "本番だけ太郎", "1111111A", False),
            _profile("U" + "2" * 32, "ゲストだけ花子", "9999999A", True),
            _profile("U" + "3" * 32, "両方使う次郎", "3333333A", True),   # ゲストで登録
            _profile("U" + "4" * 32, "本番登録でゲストも触った三郎", "4444444A", False),
        ])
        s.add_all([
            UserActivity(user_id="U" + "3" * 32, action="x", count=1, last_at=now, source="main"),
            UserActivity(user_id="U" + "4" * 32, action="x", count=1, last_at=now, source="guest"),
        ])
        await s.commit()

    main = (await _client(http_client_factory, monkeypatch).get("/admin/users")).text
    assert "本番だけ太郎" in main
    assert "ゲストだけ花子" not in main
    assert "両方使う次郎" in main and "本番登録でゲストも触った三郎" in main
    assert "の<u>両方</u>を使っているユーザーが 2 人" in main

    guest = (await _client(http_client_factory, monkeypatch, "guest").get("/admin/users")).text
    assert "ゲストだけ花子" in guest and "両方使う次郎" in guest
    assert "本番だけ太郎" not in guest

    both = (await _client(http_client_factory, monkeypatch).get("/admin/users?view=both")).text
    assert "両方使う次郎" in both and "本番登録でゲストも触った三郎" in both
    assert "本番だけ太郎" not in both and "ゲストだけ花子" not in both


@pytest.mark.asyncio
async def test_funnel_stats_and_taps_and_views_split_by_channel(test_sessionmaker):
    """友だち追加ページ等の計測(funnel_events.channel)・リッチメニュータップ・科目閲覧数も
    チャンネル別に集計できる（guest指定でmainの行が混ざらない）。"""
    import routers.admin.stats as admin_stats
    from models import FunnelEvent

    async with test_sessionmaker() as s:
        s.add_all([
            FunnelEvent(event="join_view", channel="main"),
            FunnelEvent(event="join_view", channel="main"),
            FunnelEvent(event="join_view", channel="guest"),
        ])
        await s.commit()
    async with test_sessionmaker() as s:
        def views(stats):
            return {t["event"]: t["views"] for t in stats["totals"]}["join_view"]
        assert views(await admin_stats._funnel_stats(s, "main")) == 2
        assert views(await admin_stats._funnel_stats(s, "guest")) == 1
        assert views(await admin_stats._funnel_stats(s, "all")) == 3
