import asyncio
import traceback as _traceback
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import AsyncSessionLocal
from models import ErrorLog, MessageLog, UserActivity

_LOG_RETENTION_DAYS = 30


async def save_error_log(exc: Exception, user_id: str | None = None, action: str | None = None, notify: bool = True):
    try:
        # exc.__traceback__から明示的に組み立てる。asyncio.Task.add_done_callback等、
        # 元のexceptブロックを抜けた後に呼ばれる場合はtraceback.format_exc()だと
        # 例外コンテキストが失われ無意味な文字列になるため。
        tb = "".join(_traceback.format_exception(type(exc), exc, exc.__traceback__))
        async with AsyncSessionLocal() as session:
            session.add(ErrorLog(
                user_id=user_id,
                action=action[:200] if action else None,
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
                traceback=tb[:4000],
            ))
            await session.commit()
    except Exception as log_exc:
        # DB書き込み自体が失敗した場合でも、Renderの標準ログには残す
        # （ここでのraiseは呼び出し元の処理を止めてしまうため行わない）
        print(f"[error_log_failed] {type(log_exc).__name__}: {log_exc} (original: {type(exc).__name__}: {exc})", flush=True)
        return

    if not notify:
        return

    async def _notify() -> None:
        try:
            # circular import回避のため遅延import（core.push が core.activity_log.save_error_log を使うため）
            from core.push import send_error_push_notification
            await send_error_push_notification(action, type(exc).__name__, str(exc))
        except Exception as push_exc:
            # ここでsave_error_logを呼ぶと無限ループになるためprintのみに留める
            print(f"[error_push_notify_failed] {type(push_exc).__name__}: {push_exc}", flush=True)

    asyncio.create_task(_notify())


async def save_log_bg(user_id: str, direction: str, message: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            session.add(MessageLog(user_id=user_id, direction=direction, message=message))
            if direction == "in":
                now = datetime.now(timezone.utc)
                stmt = (
                    pg_insert(UserActivity)
                    .values(user_id=user_id, action=message[:200], count=1, last_at=now)
                    .on_conflict_do_update(
                        index_elements=["user_id", "action"],
                        set_={"count": UserActivity.count + 1, "last_at": now},
                    )
                )
                await session.execute(stmt)
            await session.commit()
    except Exception as exc:
        await save_error_log(exc, user_id=user_id, action=f"save_log_{direction}")


async def cleanup_old_logs():
    """message_logs / error_logsの古い行を削除する（Supabase Freeプランの
    ストレージ上限対策。両テーブルともreviews等と異なり永続保存が前提の
    データではない）。"""
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=_LOG_RETENTION_DAYS)
        async with AsyncSessionLocal() as session:
            await session.execute(delete(MessageLog).where(MessageLog.created_at < cutoff))
            await session.execute(delete(ErrorLog).where(ErrorLog.created_at < cutoff))
            await session.commit()
    except Exception as exc:
        await save_error_log(exc, action="cleanup")


async def log_cleanup_loop() -> None:
    """1日に1回cleanup_old_logs()を実行する。着信イベント頻度に依存する
    確率トリガーだと低トラフィック期間に掃除が走らない懸念があったため、
    backup_loopと同じ固定間隔ループ方式に変更した。"""
    await asyncio.sleep(60)
    while True:
        await cleanup_old_logs()
        await asyncio.sleep(24 * 3600)
