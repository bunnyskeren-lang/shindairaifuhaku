import asyncio

from core import cache
from line_bot.flex_builders import prewarm_flex_cache


async def prewarm_caches() -> None:
    await asyncio.sleep(0.5)
    try:
        await cache.warm_query_caches()
    except Exception as e:
        print(f"Prewarm failed: {e}", flush=True)
    try:
        await prewarm_flex_cache()
    except Exception as e:
        print(f"Prewarm flex cache failed: {e}", flush=True)
    print("Cache pre-warm complete", flush=True)
