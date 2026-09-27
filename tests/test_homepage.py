"""/hp（ホームページ）の回帰テスト：配信できることと、団体への支払額が config と連動していること。"""
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import routers.pages as pages
from core.config import (
    GROUP_CONTRIBUTOR_BONUS_AMOUNT,
    GROUP_CONTRIBUTOR_BONUS_UNIT,
    GROUP_REVIEW_PAYOUT_KYOYO,
    GROUP_REVIEW_PAYOUT_SENMON,
)


@pytest_asyncio.fixture
async def client():
    app = FastAPI()
    app.include_router(pages.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_homepage_served_with_amounts_from_config(client):
    resp = await client.get("/hp")
    assert resp.status_code == 200
    assert "[[" not in resp.text  # 置換漏れのプレースホルダが残っていない
    assert f"1件 {GROUP_REVIEW_PAYOUT_KYOYO}円" in resp.text
    assert f"専門科目は{GROUP_REVIEW_PAYOUT_SENMON}円" in resp.text
    assert f"{GROUP_CONTRIBUTOR_BONUS_UNIT}人ごとに +{GROUP_CONTRIBUTOR_BONUS_AMOUNT}円" in resp.text
    ex10 = 10 * 3 * GROUP_REVIEW_PAYOUT_KYOYO + (10 // GROUP_CONTRIBUTOR_BONUS_UNIT) * GROUP_CONTRIBUTOR_BONUS_AMOUNT
    assert f"{ex10:,}<small>" in resp.text
