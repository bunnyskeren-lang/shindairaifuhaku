import os
import ssl
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

_url = os.environ["DATABASE_URL"]
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _url.startswith("postgresql://") and "+asyncpg" not in _url:
    _url = _url.replace("postgresql://", "postgresql+asyncpg://", 1)

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

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
    from models import MessageLog, Course, PendingReview, UserPreference, UserProfile, UserActivity, ErrorLog, PushSubscription, CourseInstructor, ClassificationOrder  # noqa: F401
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "ALTER TABLE courses ADD COLUMN IF NOT EXISTS reading VARCHAR(400) NOT NULL DEFAULT ''"
        ))
        await conn.execute(text(
            "ALTER TABLE course_instructors ADD COLUMN IF NOT EXISTS url VARCHAR(500)"
        ))
        await conn.execute(text(
            "ALTER TABLE pending_reviews ADD COLUMN IF NOT EXISTS selected_instructor VARCHAR(100)"
        ))
        await conn.execute(text(
            "ALTER TABLE courses ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0"
        ))
