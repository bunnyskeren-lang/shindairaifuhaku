"""/liff/review のLIFF中継ページの回帰テスト。"""
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import routers.pages as pages
from core.config import REVIEW_FORM_PATH, REVIEW_LIFF_ID


@pytest_asyncio.fixture
async def client():
    app = FastAPI()
    app.include_router(pages.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_liff_review_redirects_to_form_path(client):
    # 旧URL "/" は締め切り案内に切り替えたため、中継ページはフォーム実体の
    # REVIEW_FORM_PATH（/post-review）へ転送する
    resp = await client.get("/liff/review")
    assert resp.status_code == 200
    assert f'const REDIRECT_PATH = "{REVIEW_FORM_PATH}";' in resp.text
    assert f'const LIFF_ID = "{REVIEW_LIFF_ID}";' in resp.text


@pytest.mark.asyncio
async def test_root_shows_closed_notice(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "締め切りました" in resp.text


@pytest.mark.asyncio
async def test_form_path_serves_review_form(client):
    resp = await client.get(REVIEW_FORM_PATH)
    assert resp.status_code == 200
    assert "授業レビューを投稿" in resp.text
