"""/liff/review・/liff/review/verify-email のLIFF中継ページの回帰テスト。

bdf9004でREVIEW_LIFF_IDのLINE Developersコンソール側エンドポイントURL(/liff/review)と
make_email_verify_url()が生成するURLの前提がズレて404になっていたバグを修正した際、
再発を検知するテストが追加されていなかったため補う。
"""
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


@pytest.mark.asyncio
async def test_liff_review_verify_email_redirects_to_verify_email_path(client):
    """make_email_verify_url()が生成する https://liff.line.me/{REVIEW_LIFF_ID}/verify-email は
    REVIEW_LIFF_IDのエンドポイントURL(/liff/review)の後ろに/verify-emailが付与された形で
    このルートに展開される。このルート自体が存在し、正しいredirect_pathを返すことを固定する。"""
    resp = await client.get("/liff/review/verify-email")
    assert resp.status_code == 200
    assert 'const REDIRECT_PATH = "/verify-email";' in resp.text
    assert f'const LIFF_ID = "{REVIEW_LIFF_ID}";' in resp.text
