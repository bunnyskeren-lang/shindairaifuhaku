import os
import ssl
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

_url = os.environ["DATABASE_URL"]
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _url.startswith("postgresql://") and "+asyncpg" not in _url:
    _url = _url.replace("postgresql://", "postgresql+asyncpg://", 1)


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

engine = create_async_engine(
    _url,
    echo=False,
    connect_args={"ssl": ssl_ctx, "command_timeout": 30, "statement_cache_size": 0},
    pool_pre_ping=True,
    pool_recycle=270,
    pool_size=5,
    max_overflow=10,
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    from models import (  # noqa: F401
        MessageLog, DisplayOrder, UserProfile,
        CreditRequirement, UserSeisekiRaw,
        Subject, Instructor, CourseSection, Syllabus, Schedule, Review,
        SubjectCreditCategory,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
