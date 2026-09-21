import asyncio
import time
import traceback as _traceback
from datetime import datetime, timedelta, UTC

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.background_tasks import fire_and_forget
from database import AsyncSessionLocal
from models import DebugLog, ErrorLog, LiffAuthEvent, MessageLog, UserActivity, UserProfile

# メッセージログが「大西英恋」本人以外により動いた（受信方向のログが記録された）ときに
# 管理者端末へ通知するための照合名。未登録ユーザー（user_profiles未登録）も対象に含める。
_OWNER_NAME = "大西英恋"

_LOG_RETENTION_DAYS = 30

# DB接続枯渇等の障害時に短時間で大量のエラーが発生すると、エラー1件ごとにPush通知が
# 飛んで管理者端末に通知が殺到してしまうため、Push通知だけをクールダウンで間引く
# （DBへのErrorLog保存自体は間引かず、全件記録する）。
# クールダウンは push_cooldown_key 単位で独立させる: 本物のエラー("error")と、error_logs へ
# 相乗りしている想定内テレメトリ（submit_duplicate 等）が同じ枠を共有すると、良性テレメトリの
# バーストが本物の障害Push通知を最大5分マスクしてしまうため。
_ERROR_PUSH_COOLDOWN_SECONDS = 300
_last_push_at: dict[str, float] = {}


async def save_error_log(
    exc: Exception,
    user_id: str | None = None,
    action: str | None = None,
    notify: bool = True,
    push_cooldown_key: str = "error",
):
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

    now = time.monotonic()
    if now - _last_push_at.get(push_cooldown_key, 0.0) < _ERROR_PUSH_COOLDOWN_SECONDS:
        return
    _last_push_at[push_cooldown_key] = now

    async def _notify() -> None:
        try:
            # circular import回避のため遅延import（core.push が core.activity_log.save_error_log を使うため）
            from core.push import send_error_push_notification
            await send_error_push_notification(action, type(exc).__name__, str(exc))
        except Exception as push_exc:
            # ここでsave_error_logを呼ぶと無限ループになるためprintのみに留める
            print(f"[error_push_notify_failed] {type(push_exc).__name__}: {push_exc}", flush=True)

    fire_and_forget(_notify())


async def save_debug_log(
    action: str,
    user_id: str | None = None,
    status: str = "ok",
    duration_ms: float | None = None,
    detail: str | None = None,
) -> None:
    """バグ調査用の動作ログ（DebugLog）を保存する。エラーの有無に関わらず毎回呼ばれる想定
    （line_bot/handler.py `_log_reply_timing()`参照）ため、save_error_logと同様に例外を
    握りつぶしてprintのみに留め、呼び出し元の処理を絶対に止めない。"""
    try:
        async with AsyncSessionLocal() as session:
            session.add(DebugLog(
                user_id=user_id,
                action=action[:200],
                status=status,
                duration_ms=int(duration_ms) if duration_ms is not None else None,
                detail=detail[:500] if detail else None,
            ))
            await session.commit()
    except Exception as log_exc:
        print(f"[debug_log_failed] {type(log_exc).__name__}: {log_exc}", flush=True)


async def save_log_bg(user_id: str, direction: str, message: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            session.add(MessageLog(user_id=user_id, direction=direction, message=message))
            if direction == "in":
                now = datetime.now(UTC)
                stmt = (
                    pg_insert(UserActivity)
                    .values(user_id=user_id, action=message[:200], count=1, last_at=now)
                    .on_conflict_do_update(
                        index_elements=["user_id", "action"],
                        set_={"count": UserActivity.count + 1, "last_at": now},
                    )
                )
                await session.execute(stmt)

                profile = await session.get(UserProfile, user_id)
                other_user_name = profile.name if profile else None
                if other_user_name != _OWNER_NAME:
                    fire_and_forget(_notify_other_user_activity(other_user_name, message))
            await session.commit()
    except Exception as exc:
        await save_error_log(exc, user_id=user_id, action=f"save_log_{direction}")


async def _notify_other_user_activity(name: str | None, message: str) -> None:
    try:
        # circular import回避のため遅延import（core.push が core.activity_log.save_error_log を使うため）
        from core.push import send_other_user_activity_push_notification
        await send_other_user_activity_push_notification(name, message)
    except Exception as push_exc:
        # ここでsave_error_logを呼ぶと無限ループになるためprintのみに留める
        print(f"[other_user_activity_push_notify_failed] {type(push_exc).__name__}: {push_exc}", flush=True)


async def cleanup_old_logs():
    """message_logs / error_logs / debug_logs / liff_auth_eventsの古い行を削除する
    （Supabase Freeプランのストレージ上限対策。いずれもreviews等と異なり永続保存が前提のデータではない）。"""
    try:
        cutoff = datetime.now(UTC) - timedelta(days=_LOG_RETENTION_DAYS)
        async with AsyncSessionLocal() as session:
            await session.execute(delete(MessageLog).where(MessageLog.created_at < cutoff))
            await session.execute(delete(ErrorLog).where(ErrorLog.created_at < cutoff))
            await session.execute(delete(DebugLog).where(DebugLog.created_at < cutoff))
            await session.execute(delete(LiffAuthEvent).where(LiffAuthEvent.created_at < cutoff))
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
