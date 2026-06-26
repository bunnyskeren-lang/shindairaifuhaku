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
    from models import MessageLog, Course, PendingReview, UserProfile, UserActivity, ErrorLog, PushSubscription, CourseInstructor, ClassificationOrder, RichMenuTap, CourseView, SyllabusCourse, CourseSlot, UserCourse, TimetableProfile, CreditRequirement  # noqa: F401
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
        await conn.execute(text(
            "ALTER TABLE pending_reviews ADD COLUMN IF NOT EXISTS nickname VARCHAR(30)"
        ))
        await conn.execute(text(
            "ALTER TABLE pending_reviews ADD COLUMN IF NOT EXISTS academic_year INTEGER"
        ))
        await conn.execute(text(
            "ALTER TABLE pending_reviews ADD COLUMN IF NOT EXISTS student_id VARCHAR(20)"
        ))
        await conn.execute(text(
            "ALTER TABLE courses ADD COLUMN IF NOT EXISTS faculty VARCHAR(100)"
        ))
        await conn.execute(text(
            "ALTER TABLE syllabus_courses ADD COLUMN IF NOT EXISTS target_grades VARCHAR(20)"
        ))
        await conn.execute(text(
            "ALTER TABLE syllabus_courses ADD COLUMN IF NOT EXISTS subject_category VARCHAR(50)"
        ))
        await conn.execute(text(
            "ALTER TABLE syllabus_courses ADD COLUMN IF NOT EXISTS numbering_code VARCHAR(20)"
        ))
        await conn.execute(text(
            "ALTER TABLE classification_orders ADD COLUMN IF NOT EXISTS parent_group VARCHAR(100)"
        ))
        await conn.execute(text(
            "ALTER TABLE courses ADD COLUMN IF NOT EXISTS senmon_group VARCHAR(20)"
        ))
        await conn.execute(text(
            "ALTER TABLE credit_requirements ADD COLUMN IF NOT EXISTS note TEXT"
        ))
        defaults = [
            ("kyoyo_kei",   12, "人文科学系・自然科学系・社会科学系・総合科学系の4系に分類される総合教養科目が対象。"),
            ("kyoyo_kiban",  4, "基礎教養科目（情報リテラシー等）と情報科目を合算したもの。"),
            ("gaigo1",       4, "Academic English Communication / Literacy など英語科目が対象。"),
            ("gaigo2",       4, "ドイツ語・フランス語・中国語・韓国語・ロシア語など第二外国語が対象。"),
            ("kyotsu",       6, "全学部共通の専門基礎科目。成績表の「共通専門基礎科目」欄の合計。"),
            ("shonen",       1, "1年次必修の初年次セミナー（2単位）。必要単位数は1科目=2単位。"),
            ("senmon1",      6, "経営学基礎論・会計学基礎論・市場システム基礎論の3科目（各2単位・計6単位）。"),
            ("senmon2",     12, "経営管理・経営戦略・簿記・財務会計・マーケティングなど第2群の専門科目。"),
            ("global",       4, "英語で開講される専門科目・外国書講読・外国文献講義が対象。"),
            ("senmon3",      0, "第1・2群・グローバル以外の専門科目（人的資源管理・証券市場など）。PDFから自動計算。"),
        ]
        for cat_id, req, note in defaults:
            await conn.execute(text(
                "INSERT INTO credit_requirements (category_id, required_credits, note) "
                "VALUES (:cat, :req, :note) ON CONFLICT (category_id) DO NOTHING"
            ), {"cat": cat_id, "req": req, "note": note})
