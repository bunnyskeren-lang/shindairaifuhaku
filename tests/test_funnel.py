"""登録までの漏斗の計測（core/funnel.py）と、管理画面の漏斗集計の回帰テスト。"""
import asyncio

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
from models import FunnelEvent, UserProfile
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
