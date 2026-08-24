import asyncio

from core import cache
from core.activity_log import save_error_log
from line_bot.flex_builders import prewarm_flex_cache


async def prewarm_caches() -> None:
    await asyncio.sleep(0.5)
    try:
        await cache.warm_query_caches()
    except Exception as e:
        print(f"Prewarm failed: {e}", flush=True)
        await save_error_log(e, action="prewarm_query_caches")
    try:
        await prewarm_flex_cache()
    except Exception as e:
        print(f"Prewarm flex cache failed: {e}", flush=True)
        await save_error_log(e, action="prewarm_flex_cache")
    print("Cache pre-warm complete", flush=True)
