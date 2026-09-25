"""登録までの漏斗の計測（core/funnel.py）と、管理画面の漏斗集計の回帰テスト。"""
import asyncio
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import core.funnel as funnel
import routers.admin.stats as admin_stats
import routers.pages as pages
from core import cache
from core.background_tasks import _background_tasks
from models import FunnelEvent, UserActivity, UserProfile
from tests.conftest import patch_async_session_local

BROWSER_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Line/14.0.0"


@pytest_asyncio.fixture
async def client(monkeypatch, test_sessionmaker):
    patch_async_session_local(monkeypatch, funnel, test_sessionmaker)

    async def _no_faculties():
        return []

    monkeypatch.setattr(cache, "get_faculty_order", _no_faculties)
    app = FastAPI()
    app.include_router(pages.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"user-agent": BROWSER_UA}) as c:
        yield c


async def _flush_background():
    """fire_and_forget で投げた計測INSERTの完了を待つ。"""
    while _background_tasks:
        await asyncio.gather(*list(_background_tasks))
    await asyncio.sleep(0)


async def _events(sessionmaker):
    async with sessionmaker() as s:
        return (await s.execute(select(FunnelEvent).order_by(FunnelEvent.id))).scalars().all()


# ── 純粋関数 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ua", [
    "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
    "Twitterbot/1.0",
    "facebookexternalhit/1.1;line-poker/1.0",
    "Slackbot-LinkExpanding 1.0",
    "python-httpx/0.27.0",
    "curl/8.4.0",
    "Googlebot/2.1",
    "",
    None,
])
def test_bot_user_agents_detected(ua):
    assert funnel.is_bot_user_agent(ua) is True


def test_real_browser_and_line_in_app_user_agents_not_bots():
    assert funnel.is_bot_user_agent(BROWSER_UA) is False
    assert funnel.is_bot_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ) is False


@pytest.mark.parametrize("raw,expected", [
    ("discord_0925", "discord_0925"),
    ("a-b_C9", "a-b_C9"),
    ("", ""),
    (None, ""),
    ("has space", ""),
    ("<script>", ""),
    ("x" * 41, ""),
])
def test_sanitize_source(raw, expected):
    assert funnel.sanitize_source(raw) == expected


# ── ページ表示の記録 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_page_view_is_recorded_with_visitor_cookie_and_source(client, test_sessionmaker):
    resp = await client.get("/register?src=discord_0925")
    assert resp.status_code == 200
    await _flush_background()

    events = await _events(test_sessionmaker)
    assert [(e.event, e.source) for e in events] == [("register_view", "discord_0925")]
    vid = resp.cookies.get(funnel.VISITOR_COOKIE)
    assert vid and len(vid) == 32
    assert events[0].visitor_id == vid


@pytest.mark.asyncio
async def test_returning_browser_keeps_same_visitor_id(client, test_sessionmaker):
    first = await client.get("/register")
    vid = first.cookies.get(funnel.VISITOR_COOKIE)
    await client.get("/liff/review")
    await _flush_background()

    events = await _events(test_sessionmaker)
    assert [e.event for e in events] == ["register_view", "liff_review_view"]
    assert {e.visitor_id for e in events} == {vid}


@pytest.mark.asyncio
async def test_bot_user_agent_is_not_recorded(client, test_sessionmaker):
    resp = await client.get("/join?src=discord_0925", headers={"user-agent": "Discordbot/2.0"})
    assert resp.status_code == 200
    await _flush_background()
    assert await _events(test_sessionmaker) == []


@pytest.mark.asyncio
async def test_join_page_is_counted_without_issuing_cookie(client, test_sessionmaker):
    """/join は Cache-Control: public の共有キャッシュされうる応答なので Set-Cookie しない。"""
    resp = await client.get("/join?src=discord_0925")
    assert resp.status_code == 200
    assert funnel.VISITOR_COOKIE not in resp.cookies
    await _flush_background()

    events = await _events(test_sessionmaker)
    assert [(e.event, e.source, e.visitor_id) for e in events] == [("join_view", "discord_0925", None)]


@pytest.mark.asyncio
async def test_invalid_src_is_stored_as_empty(client, test_sessionmaker):
    await client.get("/register?src=<script>alert(1)</script>")
    await _flush_background()
    events = await _events(test_sessionmaker)
    assert [e.source for e in events] == [""]


@pytest.mark.asyncio
async def test_tracking_is_dropped_silently_over_rate_limit(client, test_sessionmaker):
    """連打しても表示自体は成功し、計測の書き込みだけが上限（60秒30件）で頭打ちになる。"""
    for _ in range(35):
        assert (await client.get("/liff/review")).status_code == 200
    await _flush_background()
    assert len(await _events(test_sessionmaker)) == 30


@pytest.mark.asyncio
async def test_page_still_renders_when_insert_fails(monkeypatch, client):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(funnel, "AsyncSessionLocal", _boom)
    assert (await client.get("/liff/review")).status_code == 200
    await _flush_background()


# ── 管理画面の集計 ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_funnel_stats_counts_events_sources_and_db_rows(test_sessionmaker):
    async with test_sessionmaker() as s:
        s.add_all([
            FunnelEvent(event="join_view", source="discord_0925"),
            FunnelEvent(event="join_view", source="discord_0925"),
            FunnelEvent(event="register_view", visitor_id="a" * 32),
            FunnelEvent(event="register_view", visitor_id="a" * 32),
            FunnelEvent(event="register_view", visitor_id="b" * 32),
            FunnelEvent(event="register_done", visitor_id="a" * 32),
        ])
        s.add(UserProfile(line_user_id="U" + "1" * 32, name="テスト", student_id="1234567S"))
        await s.commit()

    async with test_sessionmaker() as s:
        stats = await admin_stats._funnel_stats(s)

    totals = {t["event"]: t for t in stats["totals"]}
    assert (totals["join_view"]["views"], totals["join_view"]["uniques"]) == (2, 0)
    assert (totals["register_view"]["views"], totals["register_view"]["uniques"]) == (3, 2)
    assert (totals["register_done"]["views"], totals["register_done"]["uniques"]) == (1, 1)
    assert totals["review_form_view"]["views"] == 0
    assert [t["event"] for t in stats["totals"]] == list(funnel.FUNNEL_EVENTS_IN_ORDER)

    assert stats["sources"] == [{"source": "discord_0925", "label": "友だち追加ページ", "views": 2}]

    today = stats["daily_rows"][0]
    assert today["join_view"] == 2
    assert today["register_view"] == 3
    assert today["register_done"] == 1
    assert today["profiles"] == 1
    assert len(stats["daily_rows"]) == admin_stats.FUNNEL_DAILY_DAYS


@pytest.mark.asyncio
async def test_admin_usage_stats_page_renders_funnel_section(http_client_factory, monkeypatch, test_sessionmaker):
    from core.config import ADMIN_COOKIE
    from core.security import make_admin_token

    async with test_sessionmaker() as s:
        s.add_all([
            FunnelEvent(event="register_view", source="discord_0925", visitor_id="a" * 32),
            FunnelEvent(event="register_done", visitor_id="a" * 32),
        ])
        await s.commit()

    client = http_client_factory(admin_stats, monkeypatch)
    client.cookies.set(ADMIN_COOKIE, make_admin_token())
    resp = await client.get("/admin/usage-stats")
    assert resp.status_code == 200
    assert "登録までの漏斗" in resp.text
    assert "登録画面" in resp.text
    assert "discord_0925" in resp.text  # 流入元別の表
    assert "LINE友だち追加者の内訳" in resp.text


# ── LINE友だち追加者の3グループ ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_page_records_line_user_id_from_uid_param(client, test_sessionmaker):
    uid = "U" + "a" * 32
    await client.get(f"/register?uid={uid}")
    await client.get("/register?uid=not-a-line-id")
    await _flush_background()
    events = await _events(test_sessionmaker)
    assert [e.line_user_id for e in events] == [uid, None]


def _at(hour: int) -> datetime:
    return datetime(2026, 9, 25, hour, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_friend_breakdown_splits_followers_into_three_groups(test_sessionmaker):
    u_registered = "U" + "1" * 32       # 友だち追加 → 登録画面を開いた → 登録した
    u_opened = "U" + "2" * 32           # 友だち追加 → 登録画面を開いた → 未登録
    u_never = "U" + "3" * 32            # 友だち追加（計測開始後）→ 開いていない
    u_unknown = "U" + "4" * 32          # 計測開始前に友だち追加、未登録、記録なし
    u_direct = "U" + "5" * 32           # 友だち追加の記録なしで直接登録
    u_stranger = "U" + "6" * 32         # 友だち追加の記録なし、開いたが未登録
    async with test_sessionmaker() as s:
        s.add_all([
            UserActivity(user_id=u_registered, action="[follow]", count=1, last_at=_at(10)),
            UserActivity(user_id=u_opened, action="[follow]", count=1, last_at=_at(10)),
            UserActivity(user_id=u_never, action="[follow]", count=1, last_at=_at(12)),
            UserActivity(user_id=u_unknown, action="[follow]", count=1, last_at=_at(8)),
            UserActivity(user_id=u_registered, action="教養", count=3, last_at=_at(11)),  # follow以外は無視される
            # 計測開始 = 最初のfunnel_events(10時)
            FunnelEvent(event="register_view", line_user_id=u_registered, created_at=_at(10)),
            FunnelEvent(event="register_view", line_user_id=u_opened, created_at=_at(11)),
            FunnelEvent(event="register_view", line_user_id=u_opened, created_at=_at(12)),
            FunnelEvent(event="register_view", line_user_id=u_stranger, created_at=_at(12)),
            UserProfile(line_user_id=u_registered, name="登録済み太郎", student_id="1234567S"),
            UserProfile(line_user_id=u_direct, name="直接登録花子", student_id="7654321S"),
        ])
        await s.commit()

    async with test_sessionmaker() as s:
        f = (await admin_stats._funnel_stats(s))["friends"]

    assert f["followers"] == 4
    assert (f["never_opened"], f["opened_unregistered"], f["registered_friends"]) == (1, 1, 1)
    assert f["unknown"] == 1
    assert f["registered_total"] == 2
    assert f["registered_without_follow"] == 1
    assert f["opened_not_friend_unregistered"] == 1
    assert [r["id"] for r in f["never_opened_rows"]] == [u_never[:7] + "…"]
    assert [(r["id"], r["opens"]) for r in f["opened_unregistered_rows"]] == [(u_opened[:7] + "…", 2)]


@pytest.mark.asyncio
async def test_friend_breakdown_without_any_funnel_events_marks_unregistered_as_unknown(test_sessionmaker):
    """計測開始前（funnel_eventsが空）に友だち追加して未登録の人は、「開いていない」と断定せず不明にする。"""
    async with test_sessionmaker() as s:
        s.add(UserActivity(user_id="U" + "7" * 32, action="[follow]", count=1, last_at=_at(9)))
        await s.commit()
    async with test_sessionmaker() as s:
        f = (await admin_stats._funnel_stats(s))["friends"]
    assert (f["never_opened"], f["opened_unregistered"], f["unknown"]) == (0, 0, 1)
    assert f["measure_start"] is None
