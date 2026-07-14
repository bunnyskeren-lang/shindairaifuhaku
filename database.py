import json
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from core.db_ssl import make_ssl_context

_url = os.environ["DATABASE_URL"]
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _url.startswith("postgresql://") and "+asyncpg" not in _url:
    _url = _url.replace("postgresql://", "postgresql+asyncpg://", 1)

ssl_ctx = make_ssl_context()

engine = create_async_engine(
    _url,
    echo=False,
    connect_args={"ssl": ssl_ctx, "command_timeout": 30, "statement_cache_size": 0},
    # 修正理由: pool_pre_pingはチェックアウト毎にSELECT 1を1往復追加するため、
    # Render(Singapore)⇄Supabase(東京/大阪)間のようにDB往復のコストが高い構成では
    # クエリのレイテンシを実質2倍にしていた。pool_recycle=270で定期的に接続を
    # 更新しているため、古い接続を掴むリスクは残るがpre_pingほど高頻度ではない
    pool_recycle=270,
    # Supabase pooler側の上限に合わせて調整できるよう環境変数で上書き可能にする
    # （既定値はSupabase無料/Starterプランのpooler接続上限を踏まえた控えめな値）
    pool_size=int(os.environ.get("DB_POOL_SIZE", "10")),
    max_overflow=int(os.environ.get("DB_POOL_MAX_OVERFLOW", "20")),
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    from models import (  # noqa: F401
        MessageLog, UserProfile, UserActivity, ErrorLog,
        PushSubscription, DisplayOrder, RichMenuTap,
        CreditRequirement, UserSeisekiRaw,
        Subject, Instructor, CourseSection, Syllabus, Schedule, Review,
        CourseSectionView, UserSyllabus, SubjectCreditCategory, RequiredSubject,
        RegistrationCap, UserCustomCourse,
    )
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # classification_orders → display_orders(kind='classification') への移行
        # classification_ordersテーブルがまだ存在する場合のみ実行（移行後は削除済みで存在しない）
        table_exists = (await conn.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'classification_orders')"
        ))).scalar()
        if table_exists:
            await conn.execute(text(
                "ALTER TABLE classification_orders ADD COLUMN IF NOT EXISTS parent_group VARCHAR(100)"
            ))
            await conn.execute(text(
                "ALTER TABLE classification_orders ADD COLUMN IF NOT EXISTS faculty VARCHAR(100) NOT NULL DEFAULT ''"
            ))
            await conn.execute(text(
                "INSERT INTO display_orders (kind, name, faculty, sort_order, parent_group) "
                "SELECT 'classification', name, faculty, sort_order, parent_group FROM classification_orders "
                "ON CONFLICT (kind, name, faculty) DO NOTHING"
            ))
            await conn.execute(text("DROP TABLE classification_orders"))
        await conn.execute(text(
            "ALTER TABLE instructors ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0"
        ))
        # display_orders(kind='faculty') の初期シード（未登録の場合のみ）
        # core/config.py の FACULTIES と同じ11学部（database.py は core.config に依存させない）
        _default_faculties = [
            "文学部", "国際人間科学部", "法学部", "経済学部", "経営学部",
            "システム情報学部", "理学部", "医学部", "工学部", "農学部", "海洋政策科学部",
        ]
        for i, faculty_name in enumerate(_default_faculties):
            await conn.execute(text(
                "INSERT INTO display_orders (kind, name, faculty, sort_order) "
                "VALUES ('faculty', :name, '', :sort) "
                "ON CONFLICT (kind, name, faculty) DO NOTHING"
            ), {"name": faculty_name, "sort": i})
        await conn.execute(text(
            "ALTER TABLE credit_requirements ADD COLUMN IF NOT EXISTS note TEXT"
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
        # 学科別の卒業要件に対応するための列（NULL＝学部内の全学科共通の要件）
        await conn.execute(text(
            "ALTER TABLE credit_requirements ADD COLUMN IF NOT EXISTS department TEXT"
        ))
        # (cat_id, req, note, label, group_name, sort_order)
        # kyoyo_kei（人文系・自然系をまとめた1区分）は2026-07-11、jinbun/shizen/shakai/sougou
        # の4区分＋合算行（jinbun_shizen/kyoyo_all）に置き換え済み（このファイル末尾の
        # 「経営学部: 教養科目（人文系・自然系・社会系・総合系）」ブロック参照）
        defaults = [
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
        # sonota(その他必要と認める科目)の上限12単位は2026-07-11、required_creditsの代用ではなく
        # max_creditsで正しく表現するよう変更済み（このファイル後方の該当UPDATE文を参照）。
        # 旧: "UPDATE credit_requirements SET required_credits = 12 WHERE category_id='sonota' AND required_credits=0"
        # は毎起動ごとにmax_credits化した後のrequired_credits=0を12へ巻き戻してしまうバグがあったため削除。
        # display_orders(kind='credit_requirement_group') の初期シード
        # 各(faculty, group_name)の現行sort_order最小値を引き継ぐ（未登録の場合のみ）
        await conn.execute(text(
            "INSERT INTO display_orders (kind, name, faculty, sort_order) "
            "SELECT 'credit_requirement_group', group_name, faculty, MIN(sort_order) "
            "FROM credit_requirements WHERE group_name != '' "
            "GROUP BY faculty, group_name "
            "ON CONFLICT (kind, name, faculty) DO NOTHING"
        ))
        # インデックス追加
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
        # user_profiles / timetable_profiles 統合（学部・学年・学科を登録必須項目としてuser_profilesに集約）
        await conn.execute(text(
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS faculty TEXT"
        ))
        await conn.execute(text(
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS grade INTEGER"
        ))
        await conn.execute(text(
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS department TEXT"
        ))
        timetable_profiles_exists = (await conn.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'timetable_profiles')"
        ))).scalar()
        if timetable_profiles_exists:
            await conn.execute(text("""
                UPDATE user_profiles up SET faculty = tp.faculty, grade = tp.grade
                FROM timetable_profiles tp
                WHERE up.line_user_id = tp.line_user_id AND up.faculty IS NULL
            """))
            await conn.execute(text("DROP TABLE timetable_profiles"))
        await conn.execute(text(
            "ALTER TABLE push_subscriptions ADD COLUMN IF NOT EXISTS line_user_id VARCHAR(64)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_push_subscriptions_line_user_id ON push_subscriptions (line_user_id)"
        ))
        # UNIQUE制約（重複時は無視）
        # UNIQUE制約は内部的に同名のインデックスも作成するため、既存の場合の
        # エラーコードは duplicate_object(42710) ではなく duplicate_table(42P07)
        # になることがある（インデックスもrelationとして扱われるため）
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE user_profiles ADD CONSTRAINT uq_up_student_id UNIQUE (student_id);
            EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
            END $$
        """))
        # CHECK制約（重複時は無視）
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
        # subjects.reading（よみがな検索用カラム、新スキーマ移行後に追加）
        await conn.execute(text(
            "ALTER TABLE subjects ADD COLUMN IF NOT EXISTS reading TEXT"
        ))
        # syllabi.quarter → academic_term へのカラム名変更（「いつ開講か」のニュアンスを明確化）
        await conn.execute(text("""
            DO $$ BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'syllabi' AND column_name = 'quarter'
              ) THEN
                ALTER TABLE syllabi RENAME COLUMN quarter TO academic_term;
              END IF;
            END $$
        """))
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE syllabi RENAME CONSTRAINT uq_syllabi_section_year_quarter TO uq_syllabi_section_year_term;
            EXCEPTION WHEN undefined_object THEN NULL;
            END $$
        """))
        # subjects.hide_from_timetable（管理者がマイ時間割への表示を科目単位で止められるようにする）
        await conn.execute(text(
            "ALTER TABLE subjects ADD COLUMN IF NOT EXISTS hide_from_timetable BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        # 既存科目のうち reading 未設定のものをバックフィル
        result = await conn.execute(text("SELECT id, name FROM subjects WHERE reading IS NULL"))
        rows = result.fetchall()
        if rows:
            try:
                import pykakasi
                kks = pykakasi.kakasi()

                def _gen_reading(name: str) -> str:
                    converted = kks.convert(name)
                    hira = ''.join(item.get('hira', '') for item in converted)
                    roma = ''.join(item.get('hepburn', '') for item in converted)
                    return f"{hira} {roma}".lower().strip()

                for row in rows:
                    await conn.execute(
                        text("UPDATE subjects SET reading = :r WHERE id = :id"),
                        {"r": _gen_reading(row.name), "id": row.id},
                    )
            except Exception:
                pass
        # 開講区分: 旧termカラムの値をterm_typeへバックフィルしてからtermを削除（term_typeに一本化）
        # 修正理由: term='' (空文字) の行が424件存在し、そのままバックフィルすると
        # term_type用のCHECK制約(subjects_term_type_check)に違反してDO $$ブロックが例外を投げ、
        # init_db()全体がロールバックされていた（結果、gpaカラム追加など後続の全マイグレーションが
        # 毎回無効化されていた）。空文字はNULL相当として扱いバックフィル対象から除外する。
        await conn.execute(text("""
            DO $$ BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'subjects' AND column_name = 'term'
              ) THEN
                UPDATE subjects SET term_type = term WHERE term_type IS NULL AND term IS NOT NULL AND term <> '';
                ALTER TABLE subjects DROP COLUMN term;
              END IF;
            END $$
        """))
        # 未使用カラムの削除（DBクリーンアップ）
        await conn.execute(text(
            "ALTER TABLE course_sections DROP COLUMN IF EXISTS course_type"
        ))
        await conn.execute(text(
            "ALTER TABLE schedules DROP COLUMN IF EXISTS classroom"
        ))
        await conn.execute(text(
            "ALTER TABLE push_subscriptions DROP COLUMN IF EXISTS line_user_id"
        ))
        # subjects の UNIQUE制約は当初 name 単独だったが、「卒業研究」「国際関係論」等
        # 学部をまたいで同名の専門科目が実在するケースで別学部の科目が誤って同じ行に
        # 相乗りしてしまう問題があったため (name, faculty) の複合UNIQUEへ変更する。
        # dev→prod同期スクリプト（sync_db_to_prod.py）のON CONFLICT句も(name, faculty)に
        # 合わせて修正済み。既存データに重複(name, faculty)行が残っている環境では
        # unique_violationで追加自体をスキップする
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE subjects DROP CONSTRAINT uq_subjects_name;
            EXCEPTION WHEN undefined_object THEN NULL;
            END $$
        """))
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE subjects ADD CONSTRAINT uq_subjects_name_faculty UNIQUE (name, faculty);
            EXCEPTION WHEN duplicate_object OR duplicate_table OR unique_violation THEN NULL;
            END $$
        """))
        # GPAをlocalStorageだけでなくDBにも永続化する
        await conn.execute(text(
            "ALTER TABLE user_seiseki_raw ADD COLUMN IF NOT EXISTS gpa DOUBLE PRECISION"
        ))
        # 単位チェッカー: 複数区分の合計に対する合算制約（例:第2群+第3群+グローバル=55単位）と、
        # 取得単位数の上限（例:その他必要と認める科目=12単位まで）に対応する列
        await conn.execute(text(
            "ALTER TABLE credit_requirements ADD COLUMN IF NOT EXISTS combined_of JSONB"
        ))
        await conn.execute(text(
            "ALTER TABLE credit_requirements ADD COLUMN IF NOT EXISTS max_credits INTEGER"
        ))
        # sonota(その他必要と認める科目)はrequired_creditsを上限の代用にしていたが、
        # 学生便覧上は必要最低数ではなく上限（自由選択・12単位以内）のためmax_creditsへ移す
        await conn.execute(text("""
            UPDATE credit_requirements SET max_credits = required_credits, required_credits = 0
            WHERE category_id = 'sonota' AND max_credits IS NULL AND required_credits > 0
        """))
        # 経営学部: 学生便覧「経営学部規則 別表第2 履修要件」に明記された合算制約
        # （第2群+第3群+グローバル科目群=55単位以上、専門科目全体=98単位以上）を追加
        _keiei_combined = [
            ("senmon_55", "第2群＋第3群＋グローバル科目群 計", "専門科目", 85, 55,
             ["senmon2", "senmon3", "global"]),
            ("senmon_98", "初年次＋第1群＋第2群＋第3群＋グローバル科目群 計", "専門科目", 95, 98,
             ["shonen", "senmon1", "senmon2", "senmon3", "global"]),
        ]
        for cat_id, label, group_name, sort_order, req, members in _keiei_combined:
            await conn.execute(text(
                "INSERT INTO credit_requirements "
                "  (category_id, label, group_name, sort_order, required_credits, faculty, combined_of) "
                "VALUES (:cat, :label, :gname, :sort, :req, '経営学部', CAST(:members AS JSONB)) "
                "ON CONFLICT (category_id) DO UPDATE SET "
                "  label = EXCLUDED.label, group_name = EXCLUDED.group_name, sort_order = EXCLUDED.sort_order, "
                "  required_credits = EXCLUDED.required_credits, combined_of = EXCLUDED.combined_of"
            ), {
                "cat": cat_id, "label": label, "gname": group_name,
                "sort": sort_order, "req": req, "members": json.dumps(members),
            })
        # 経営学部: 教養科目（人文系・自然系・社会系・総合系）を成績表の新カリキュラム区分に合わせて
        # 4区分に分割し、「人文系+自然系=8単位以上」「4区分合計=12単位」の合算制約を追加。
        # 旧 kyoyo_kei(人文系・自然系をまとめた1区分)/syakaisogo(社会系・総合系)は
        # subject_credit_categoriesの参照が無いことを確認済みのため削除して置き換える
        await conn.execute(text(
            "DELETE FROM credit_requirements WHERE category_id IN ('kyoyo_kei', 'syakaisogo')"
        ))
        _keiei_kyoyo = [
            ("jinbun", "人文系", "教養科目", 10, 0, None),
            ("shizen", "自然系", "教養科目", 11, 0, None),
            ("shakai", "社会系", "教養科目", 12, 0, None),
            ("sougou", "総合系", "教養科目", 13, 0, None),
            ("jinbun_shizen", "人文系＋自然系 計", "教養科目", 14, 8, ["jinbun", "shizen"]),
            ("kyoyo_all", "人文系＋自然系＋社会系＋総合系 計", "教養科目", 16, 12,
             ["jinbun", "shizen", "shakai", "sougou"]),
        ]
        for cat_id, label, group_name, sort_order, req, members in _keiei_kyoyo:
            await conn.execute(text(
                "INSERT INTO credit_requirements "
                "  (category_id, label, group_name, sort_order, required_credits, faculty, combined_of) "
                "VALUES (:cat, :label, :gname, :sort, :req, '経営学部', CAST(:members AS JSONB)) "
                "ON CONFLICT (category_id) DO UPDATE SET "
                "  label = EXCLUDED.label, group_name = EXCLUDED.group_name, sort_order = EXCLUDED.sort_order, "
                "  required_credits = EXCLUDED.required_credits, combined_of = EXCLUDED.combined_of"
            ), {
                "cat": cat_id, "label": label, "gname": group_name,
                "sort": sort_order, "req": req,
                "members": json.dumps(members) if members else None,
            })
        # My時間割: ユーザーが登録科目ごとに教室名を自由入力できる欄
        await conn.execute(text(
            "ALTER TABLE user_syllabi ADD COLUMN IF NOT EXISTS classroom TEXT"
        ))
