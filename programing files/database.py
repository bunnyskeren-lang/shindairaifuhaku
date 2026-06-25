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
    connect_args={"ssl": ssl_ctx, "command_timeout": 30},
    pool_pre_ping=True,
    pool_recycle=270,
    pool_size=10,
    max_overflow=20,
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    from models import MessageLog, Course, PendingReview, UserPreference, UserProfile, UserActivity, ErrorLog, PushSubscription, CourseInstructor, SyllabusCourse, CourseSlot, UserCourse, TimetableProfile, CreditRequirement  # noqa: F401
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "ALTER TABLE courses ADD COLUMN IF NOT EXISTS reading VARCHAR(400) NOT NULL DEFAULT ''"
        ))
        await conn.execute(text(
            "ALTER TABLE courses ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0"
        ))
        await conn.execute(text(
            "ALTER TABLE courses ADD COLUMN IF NOT EXISTS term VARCHAR(20) NOT NULL DEFAULT ''"
        ))
        await conn.execute(text(
            "ALTER TABLE courses ADD COLUMN IF NOT EXISTS credits SMALLINT NOT NULL DEFAULT 0"
        ))
        await conn.execute(text(
            "ALTER TABLE courses ADD COLUMN IF NOT EXISTS faculty VARCHAR(100)"
        ))
        await conn.execute(text(
            "ALTER TABLE courses ADD COLUMN IF NOT EXISTS senmon_group VARCHAR(20)"
        ))
        await conn.execute(text(
            "ALTER TABLE syllabus_courses ADD COLUMN IF NOT EXISTS target_grades VARCHAR(20)"
        ))
        await conn.execute(text(
            "ALTER TABLE syllabus_courses ADD COLUMN IF NOT EXISTS subject_category VARCHAR(50)"
        ))
        # credit_requirements の初期データ投入（未登録の場合のみ）
        defaults = [
            ("kyoyo_kei", 12), ("kyoyo_kiban", 4), ("gaigo1", 4), ("gaigo2", 4),
            ("kyotsu", 6), ("shonen", 1), ("senmon1", 6), ("senmon2", 12),
            ("global", 4), ("senmon3", 0),
        ]
        for cat_id, req in defaults:
            await conn.execute(text(
                "INSERT INTO credit_requirements (category_id, required_credits) "
                "VALUES (:cat, :req) ON CONFLICT (category_id) DO NOTHING"
            ), {"cat": cat_id, "req": req})
