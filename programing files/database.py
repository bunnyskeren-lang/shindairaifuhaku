import os
import ssl
from pathlib import Path
from uuid import uuid4
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

_url = os.environ["DATABASE_URL"]
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _url.startswith("postgresql://") and "+asyncpg" not in _url:
    _url = _url.replace("postgresql://", "postgresql+asyncpg://", 1)

# SupabaseのTransaction pooler（port 6543）はPgBouncerのtransactionモードで動作し、
# バックエンド接続が別クライアントと使い回されるため、asyncpgの連番prepared statement名
# （__asyncpg_stmt_1__等）が衝突しDuplicatePreparedStatementErrorになる
# （ルートのdatabase.pyと同じ対処、2026-08-31に判明した既知の非互換性）。
_is_pgbouncer_transaction_mode = ":6543/" in _url


def _make_ssl_context() -> ssl.SSLContext:
    """ルートのcore/db_ssl.pyと同じくSupabaseのCA証明書をpinningして検証する。
    DISABLE_SSL_VERIFY=1 で（緊急時の切り戻し用に）検証を無効化できる。"""
    if os.environ.get("DISABLE_SSL_VERIFY", "").lower() in ("1", "true", "yes"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    ca_path = Path(__file__).resolve().parent.parent / "certs" / "supabase-ca.crt"
    return ssl.create_default_context(cafile=str(ca_path))


ssl_ctx = _make_ssl_context()

_connect_args = {"ssl": ssl_ctx, "command_timeout": 30, "statement_cache_size": 0}
if _is_pgbouncer_transaction_mode:
    _connect_args["prepared_statement_name_func"] = lambda: f"__asyncpg_{uuid4()}__"

_engine_kwargs: dict = {"echo": False, "connect_args": _connect_args}
if _is_pgbouncer_transaction_mode:
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs.update(pool_pre_ping=True, pool_recycle=270, pool_size=5, max_overflow=10)

engine = create_async_engine(_url, **_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    from models import (  # noqa: F401
        MessageLog, DisplayOrder, UserProfile,
        Subject, Instructor, CourseSection, Syllabus, Review,
    )
    from sqlalchemy import text
    async with engine.begin() as conn:
        # ルートのdatabase.py init_db()と同じロックキー(727001)で直列化する。
        # Web本体の再デプロイとこのスクリプトの実行が重なると、同じDBに対する
        # create_all()同士が低確率でデッドロックしうるため
        # (pg_advisory_xact_lockはCOMMIT/ROLLBACKで自動解放されるため、PgBouncerの
        # transactionモードpooler経由でも安全にセッションをまたがず使える)
        await conn.execute(text("SELECT pg_advisory_xact_lock(727001)"))
        await conn.run_sync(Base.metadata.create_all)
