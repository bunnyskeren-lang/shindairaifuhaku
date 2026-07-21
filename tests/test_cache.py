import time

import pytest

from core import cache


@pytest.mark.asyncio
async def test_get_credit_countable_filter_returns_none_without_faculty():
    # facultyが無ければDBに触れず即Noneを返す（session=Noneでも安全に動く）
    result = await cache.get_credit_countable_filter(None, None, None)
    assert result is None


@pytest.mark.asyncio
async def test_get_credit_countable_filter_cache_hit_skips_rebuild():
    cache.invalidate_credit_countable_filter_cache()
    cache._credit_countable_filter_cache[("経営学部", "")] = "SENTINEL"
    cache._credit_countable_filter_cache_at = time.monotonic()
    try:
        # session=NoneでもキャッシュヒットすればDBに触れないため例外にならない
        result = await cache.get_credit_countable_filter(None, "経営学部", "")
        assert result == "SENTINEL"
    finally:
        cache.invalidate_credit_countable_filter_cache()


def test_invalidate_credit_countable_filter_cache_clears_state():
    cache._credit_countable_filter_cache[("x", "y")] = "z"
    cache._credit_countable_filter_cache_at = time.monotonic()
    cache.invalidate_credit_countable_filter_cache()
    assert cache._credit_countable_filter_cache == {}
    assert cache._credit_countable_filter_cache_at == 0.0


@pytest.mark.asyncio
async def test_get_credit_countable_filter_cache_expires_after_ttl():
    cache.invalidate_credit_countable_filter_cache()
    cache._credit_countable_filter_cache[("経営学部", "")] = "STALE"
    cache._credit_countable_filter_cache_at = time.monotonic() - cache._CREDIT_COUNTABLE_FILTER_TTL - 1
    try:
        # TTL切れなのでキャッシュ全体がクリアされ、facultyが無いのですぐNoneを返す(DB不要)
        result = await cache.get_credit_countable_filter(None, None, None)
        assert result is None
        # 古いキーはTTL切れで消え、新しい呼び出し分のキーだけが残る
        assert ("経営学部", "") not in cache._credit_countable_filter_cache
    finally:
        cache.invalidate_credit_countable_filter_cache()
