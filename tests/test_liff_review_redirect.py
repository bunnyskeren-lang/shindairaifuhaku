"""/liff/review のLIFF中継ページの回帰テスト。"""
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import routers.pages as pages
from core.config import REVIEW_LIFF_ID


@pytest_asyncio.fixture
async def client():
    app = FastAPI()
    app.include_router(pages.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_liff_review_redirects_to_root(client):
    resp = await client.get("/liff/review")
    assert resp.status_code == 200
    assert 'const REDIRECT_PATH = "/";' in resp.text
    assert f'const LIFF_ID = "{REVIEW_LIFF_ID}";' in resp.text
