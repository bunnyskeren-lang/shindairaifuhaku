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
# ENABLE_SSL_VERIFY=1 で証明書検証を有効化できる（デフォルト無効: Supabase pooler との互換性のため）
if os.environ.get("ENABLE_SSL_VERIFY", "").lower() not in ("1", "true", "yes"):
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
    from models import (  # noqa: F401
        MessageLog, Course, PendingReview, UserProfile, UserActivity, ErrorLog,
        PushSubscription, CourseInstructor, ClassificationOrder, RichMenuTap,
        CourseView, SyllabusCourse, CourseSlot, UserCourse, TimetableProfile,
        CreditRequirement, CategoryCourse, UserSeisekiRaw,
        Subject, Instructor, CourseSection, Syllabus, Schedule, Review,
        CourseSectionView, UserSyllabus, SubjectCreditCategory,
    )
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
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_course_slots_day_period ON course_slots (day_of_week, period)"
        ))
        await conn.execute(text(
            "ALTER TABLE credit_requirements ADD COLUMN IF NOT EXISTS label VARCHAR(100) NOT NULL DEFAULT ''"
        ))
        await conn.execute(text(
            "ALTER TABLE credit_requirements ADD COLUMN IF NOT EXISTS group_name VARCHAR(50) NOT NULL DEFAULT ''"
        ))
        await conn.execute(text(
            "ALTER TABLE credit_requirements ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0"
        ))
        # (cat_id, req, note, label, group_name, sort_order)
        defaults = [
            ("kyoyo_kei",   12, "人文科学系・自然科学系・社会科学系・総合科学系の4系に分類される総合教養科目が対象。", "系科目（人文・自然・社会・総合）", "教養科目", 10),
            ("kyoyo_kiban",  4, "基礎教養科目（情報リテラシー等）と情報科目を合算したもの。",                       "基盤系",                          "教養科目", 20),
            ("gaigo1",       4, "Academic English Communication / Literacy など英語科目が対象。",                  "外国語第1",                       "教養科目", 30),
            ("gaigo2",       4, "ドイツ語・フランス語・中国語・韓国語・ロシア語など第二外国語が対象。",             "外国語第2",                       "教養科目", 40),
            ("kyotsu",       6, "全学部共通の専門基礎科目。成績表の「共通専門基礎科目」欄の合計。",                 "共通専門基礎科目",                "共通専門",  50),
            ("shonen",       1, "1年次必修の初年次セミナー（2単位）。必要単位数は1科目=2単位。",                    "初年次セミナー",                  "専門科目", 60),
            ("senmon1",      6, "経営学基礎論・会計学基礎論・市場システム基礎論の3科目（各2単位・計6単位）。",     "第1群科目",                       "専門科目", 70),
            ("senmon2",     12, "経営管理・経営戦略・簿記・財務会計・マーケティングなど第2群の専門科目。",          "第2群科目",                       "専門科目", 80),
            ("global",       4, "英語で開講される専門科目・外国書講読・外国文献講義が対象。",                      "グローバル科目群",                "専門科目", 90),
            ("senmon3",      0, "第1・2群・グローバル以外の専門科目（人的資源管理・証券市場など）。PDFから自動計算。", "第3群・その他",                  "専門科目", 100),
            ("kanren",       0, "", "関連科目",                          "", 110),
            ("sonota",      12, "", "その他必要と認める科目",            "", 120),
        ]
        for cat_id, req, note, label, group_name, sort_order in defaults:
            await conn.execute(text(
                "INSERT INTO credit_requirements (category_id, required_credits, note, label, group_name, sort_order) "
                "VALUES (:cat, :req, :note, :label, :gname, :sort) "
                "ON CONFLICT (category_id) DO UPDATE SET "
                "  label = EXCLUDED.label, group_name = EXCLUDED.group_name, sort_order = EXCLUDED.sort_order "
                "WHERE credit_requirements.label = ''"
            ), {"cat": cat_id, "req": req, "note": note, "label": label, "gname": group_name, "sort": sort_order})
        await conn.execute(text(
            "UPDATE credit_requirements SET required_credits = 12 "
            "WHERE category_id = 'sonota' AND required_credits = 0"
        ))
        await conn.execute(text(
            "INSERT INTO courses (name, classification, category, reading, term, credits, faculty, sort_order) "
            "SELECT '研究指導', '第3群科目', '専門', 'けんきゅうしどう', '通年', 8, '経営学部', 0 "
            "WHERE NOT EXISTS (SELECT 1 FROM courses WHERE name = '研究指導')"
        ))
        await conn.execute(text(
            "UPDATE courses SET classification = '第3群科目', senmon_group = '第3群' "
            "WHERE name = '研究指導' AND (classification = '専門科目' OR senmon_group IS NULL)"
        ))
        await conn.execute(text(
            "INSERT INTO courses (name, classification, category, reading, term, credits, faculty, sort_order) "
            "SELECT '初年次セミナー', '初年次セミナー', '専門', 'しょねんじせみなー', '第1クォーター', 2, '経営学部', 0 "
            "WHERE NOT EXISTS (SELECT 1 FROM courses WHERE name = '初年次セミナー')"
        ))
        # ── スキーマ改善マイグレーション ───────────────────────────────────────────
        # インデックス追加
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_pr_course_name ON pending_reviews (course_name)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_pr_is_approved ON pending_reviews (is_approved)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_pr_course_name_approved ON pending_reviews (course_name, is_approved)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_message_logs_user_id ON message_logs (user_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_message_logs_created_at ON message_logs (created_at)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_error_logs_created_at ON error_logs (created_at)"
        ))
        # 新規カラム追加
        await conn.execute(text(
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ"
        ))
        await conn.execute(text(
            "ALTER TABLE push_subscriptions ADD COLUMN IF NOT EXISTS line_user_id VARCHAR(64)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_push_subscriptions_line_user_id ON push_subscriptions (line_user_id)"
        ))
        # UNIQUE制約（重複時は無視）
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE course_instructors ADD CONSTRAINT uq_ci_course_id_name UNIQUE (course_id, name);
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE course_slots ADD CONSTRAINT uq_cs_slot UNIQUE (syllabus_course_id, day_of_week, period);
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE user_profiles ADD CONSTRAINT uq_up_student_id UNIQUE (student_id);
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        # FK制約（重複時は無視）
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE course_instructors ADD CONSTRAINT fk_ci_course_id
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE course_slots ADD CONSTRAINT fk_cs_syllabus_course_id
                FOREIGN KEY (syllabus_course_id) REFERENCES syllabus_courses(id) ON DELETE CASCADE;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE user_courses ADD CONSTRAINT fk_uc_syllabus_course_id
                FOREIGN KEY (syllabus_course_id) REFERENCES syllabus_courses(id) ON DELETE CASCADE;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE course_views ADD CONSTRAINT fk_cv_course_id
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE category_courses ADD CONSTRAINT fk_cc_category_id
                FOREIGN KEY (category_id) REFERENCES credit_requirements(category_id);
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        # CHECK制約（重複時は無視）
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE pending_reviews ADD CONSTRAINT chk_pr_rating CHECK (rating BETWEEN 1 AND 5);
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE pending_reviews ADD CONSTRAINT chk_pr_ease_rating
                CHECK (ease_rating IN ('SS', 'S', 'A', 'B', 'C'));
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE message_logs ADD CONSTRAINT chk_ml_direction CHECK (direction IN ('in', 'out'));
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        # user_seiseki_raw.raw_json を TEXT→JSONB に変換（まだ TEXT の場合のみ）
        await conn.execute(text("""
            DO $$ BEGIN
              IF (SELECT data_type FROM information_schema.columns
                  WHERE table_name='user_seiseki_raw' AND column_name='raw_json') = 'text' THEN
                ALTER TABLE user_seiseki_raw ALTER COLUMN raw_json TYPE JSONB USING raw_json::jsonb;
              END IF;
            END $$
        """))
        # updated_at 自動更新トリガー
        await conn.execute(text("""
            CREATE OR REPLACE FUNCTION fn_set_updated_at()
            RETURNS TRIGGER AS $$
            BEGIN
              NEW.updated_at = NOW();
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        await conn.execute(text("""
            CREATE OR REPLACE TRIGGER trg_user_seiseki_raw_updated_at
            BEFORE UPDATE ON user_seiseki_raw
            FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()
        """))
        await conn.execute(text("""
            CREATE OR REPLACE TRIGGER trg_user_profiles_updated_at
            BEFORE UPDATE ON user_profiles
            FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at()
        """))
