"""レスポンスを待たせない「発火して忘れる」バックグラウンドタスクの共通ヘルパー。

asyncio.create_task()の戻り値を誰も保持しないと、タスクがGC対象になり
実行途中で消える場合がある（CPythonの弱参照実装に起因、公式ドキュメントでも
明示的に注意喚起されている既知の落とし穴）。モジュールレベルのsetで参照を
保持し、完了時に自動でdiscardする。
"""
import asyncio
from collections.abc import Coroutine
from typing import Any

_background_tasks: set[asyncio.Task] = set()


def fire_and_forget(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task
