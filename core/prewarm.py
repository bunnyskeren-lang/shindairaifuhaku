import asyncio

from core import cache
from core.activity_log import save_error_log
from line_bot.flex_builders import prewarm_flex_cache


# 全キャッシュのTTL(1時間)が切れる前に作り直す間隔。切れてから最初に開いた1人が
# 再構築待ち(実測2〜7秒)を負担しないよう、TTLより少し短くする。
_REWARM_INTERVAL_SEC = 50 * 60


async def rewarm_caches() -> None:
    """既に温まっているキャッシュを、古い値を返し続けたままバックグラウンドで作り直す。
    科目・レビュー更新による無効化後（core.cache.register_rewarm_hook）と、TTL切れ前の
    定期実行（rewarm_loop）から呼ばれる。メニュー/科目一覧のFlexは
    force_list_rebuildで既存キャッシュを無視して作り直す。"""
    from line_bot.handler import prewarm_menu_caches
    await cache.refresh_query_caches()
    token = cache.force_list_rebuild.set(True)
    try:
        await prewarm_menu_caches()
    finally:
        cache.force_list_rebuild.reset(token)


async def rewarm_loop() -> None:
    while True:
        await asyncio.sleep(_REWARM_INTERVAL_SEC)
        try:
            await rewarm_caches()
        except Exception as e:
            print(f"Periodic rewarm failed: {e}", flush=True)
            await save_error_log(e, action="periodic_rewarm")


async def prewarm_caches() -> None:
    cache.register_rewarm_hook(rewarm_caches)
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
    try:
        # circular import 回避のため遅延 import（line_bot.handler は core を広く import する）
        from line_bot.handler import prewarm_menu_caches
        await prewarm_menu_caches()
    except Exception as e:
        print(f"Prewarm menu cache failed: {e}", flush=True)
        await save_error_log(e, action="prewarm_menu_caches")
    print("Cache pre-warm complete", flush=True)
