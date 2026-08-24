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
        Subject, Instructor, CourseSection, Syllabus, Review,
        CourseSectionView, EmailVerification, PaymentRequest,
        Inquiry, SubjectUnlock,
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
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS department TEXT"
        ))
        timetable_profiles_exists = (await conn.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'timetable_profiles')"
        ))).scalar()
        if timetable_profiles_exists:
            await conn.execute(text("""
                UPDATE user_profiles up SET faculty = tp.faculty
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
        # 既存科目のうち reading 未設定のものをバックフィル
        # 修正理由: programing files/import_syllabus.pyがSubject新規作成時にreading=""を
        # プレースホルダとして入れており、"IS NULL"だけだとこれらが永久にヒットせず
        # よみがな検索・LINE bot科目一覧の50音行分割の両方が効かないままになっていた。
        result = await conn.execute(text("SELECT id, name FROM subjects WHERE reading IS NULL OR reading = ''"))
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

                # 修正理由: 対象がIS NULLだけでなく空文字も含むよう広げたことで対象件数が
                # 数千件規模に増えた。1件ずつUPDATEするとDB往復が積み重なり起動が
                # 数分単位で遅延するため、VALUES一括UPDATEでラウンドトリップをまとめる。
                _BATCH = 500
                for i in range(0, len(rows), _BATCH):
                    batch = rows[i:i + _BATCH]
                    # 修正理由: ":id0::integer" のようにbind paramに直接"::"キャストを
                    # 続けるとSQLAlchemyのtext()バインド解析と衝突して構文エラーになるため
                    # CAST(...)を使う
                    values_sql = ", ".join(
                        f"(CAST(:id{j} AS INTEGER), CAST(:r{j} AS TEXT))" for j in range(len(batch))
                    )
                    params = {}
                    for j, row in enumerate(batch):
                        params[f"id{j}"] = row.id
                        params[f"r{j}"] = _gen_reading(row.name)
                    await conn.execute(text(
                        f"UPDATE subjects SET reading = v.r FROM (VALUES {values_sql}) AS v(id, r) "
                        f"WHERE subjects.id = v.id"
                    ), params)
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
        # subjects.department: 学部内で学科・専攻ごとに卒業要件が異なる場合の学科名を持つ列。
        # user_profilesと同じ「faculty列+department列」のペア形式に揃える。従来はfacultyに学部名+学科名を
        # 連結した複合文字列（例:「工学部建築学科」）を入れる場当たり的な運用だった。
        # syllabi.departmentは実データの94%がsubjects.facultyと完全一致する重複列で、
        # 差分197件（法学部/経営学部/経済学部の「　昼間主コース」接尾辞）も全科目に一律
        # 付いていて情報として無意味なノイズだったため、本カラム新設と引き換えに廃止する
        # （syllabi.department自体のDROPはfile末尾を参照。全読み取り箇所の切り替え後に実施）。
        await conn.execute(text(
            "ALTER TABLE subjects ADD COLUMN IF NOT EXISTS department TEXT NOT NULL DEFAULT ''"
        ))
        # 旧(name, faculty)制約を先に外しておく。分割後は複数の複合faculty由来の行が
        # 同じ(name, faculty=学部名のみ)になり得るため、新しい3列制約を張るまでの間
        # 一時的に旧制約が残っているとバックフィル中のUPDATEが重複エラーで失敗する
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE subjects DROP CONSTRAINT uq_subjects_name_faculty;
            EXCEPTION WHEN undefined_object THEN NULL;
            END $$
        """))
        # 既存の複合faculty値を分割するワンショットのバックフィル（dev/本番の実データで確認済みの
        # 16パターン限定）。新規インポートはprograming files/import_syllabus.pyが最初から
        # faculty/departmentを分離して書き込むため、このUPDATEは既存データの移行のみが目的
        _faculty_department_split = [
            ("医学部保健学科作業療法学専攻", "医学部", "保健学科作業療法学専攻"),
            ("医学部保健学科検査技術科学専攻", "医学部", "保健学科検査技術科学専攻"),
            ("医学部保健学科理学療法学専攻", "医学部", "保健学科理学療法学専攻"),
            ("医学部保健学科看護学専攻", "医学部", "保健学科看護学専攻"),
            ("医学部医学科", "医学部", "医学科"),
            ("医学部医療創成工学科", "医学部", "医療創成工学科"),
            ("工学部市民工学科", "工学部", "市民工学科"),
            ("工学部建築学科", "工学部", "建築学科"),
            ("工学部応用化学科", "工学部", "応用化学科"),
            ("工学部機械工学科", "工学部", "機械工学科"),
            ("工学部電気電子工学科", "工学部", "電気電子工学科"),
            ("理学部化学科", "理学部", "化学科"),
            ("理学部惑星学科", "理学部", "惑星学科"),
            ("理学部数学科", "理学部", "数学科"),
            ("理学部物理学科", "理学部", "物理学科"),
            ("理学部生物学科", "理学部", "生物学科"),
        ]
        for composite, pure_faculty, dept in _faculty_department_split:
            await conn.execute(text(
                "UPDATE subjects SET faculty = :fac, department = :dept "
                "WHERE faculty = :composite AND department = ''"
            ), {"fac": pure_faculty, "dept": dept, "composite": composite})
        # 新しい3列UNIQUE制約を追加
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE subjects ADD CONSTRAINT uq_subjects_name_faculty_department UNIQUE (name, faculty, department);
            EXCEPTION WHEN duplicate_object OR duplicate_table OR unique_violation THEN NULL;
            END $$
        """))
        # course_sections.syllabus_url は年度をまたぐURL変更を表現できず陳腐化する問題があったため廃止。
        # syllabi.timetable_code + Subject.faculty/department（course_sections経由、
        # core.config.syllabus_department_key()で再構成）から毎回動的生成する方式に統一する
        # （値はいずれも既存カラムから100%導出可能なため、バックフィル不要でそのままDROPしてよい）
        await conn.execute(text(
            "ALTER TABLE course_sections DROP COLUMN IF EXISTS syllabus_url"
        ))
        # syllabi.numbering_code は定義のみでどこからも書き込まれておらず、常にNULLの死んだカラムだった。
        # 経営学部専門科目の群判定（fetch_syllabus_info.py）はシラバスHTMLからその場でパースするだけで、
        # この列を経由しないためDROPしてよい
        await conn.execute(text(
            "ALTER TABLE syllabi DROP COLUMN IF EXISTS numbering_code"
        ))
        # syllabi.department は実データの94%がsubjects.faculty（新設のsubjects.department分離後は
        # faculty+department）と完全一致する重複列だったため廃止。全読み取り箇所は
        # core.config.syllabus_department_key()経由でSubject.faculty/departmentから
        # 再構成する方式に切り替え済み
        await conn.execute(text(
            "ALTER TABLE syllabi DROP COLUMN IF EXISTS department"
        ))
        # faculty=='教養教育院' AND classification=='...' / LIKE '教養(%' の絞り込み向け
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_subjects_faculty_classification "
            "ON subjects (faculty, classification)"
        ))
        # 管理画面（/admin/courses）のclassification単体GROUP BY・完全一致検索向け
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_subjects_classification ON subjects (classification)"
        ))
        # 冗長インデックスの削除: 以下はいずれも同テーブルの複合UNIQUE制約/複合INDEXの先頭列と
        # 完全に重複しており、複合indexが先頭列だけの検索（等値・IN）にもそのまま使えるため
        # 検索速度には一切寄与せず、INSERT/UPDATE/DELETE時のB-tree更新コストだけを増やしていた。
        #   ix_subjects_name              → uq_subjects_name_faculty_department (name, faculty, department)
        #   ix_subjects_faculty           → ix_subjects_faculty_classification
        #   ix_course_sections_subject_id → uq_course_sections_subject_instructor (subject_id, instructor_id)
        #   ix_user_activity_user_id      → UniqueConstraint (user_id, action)
        for _redundant_index in (
            "ix_subjects_name", "ix_subjects_faculty", "ix_course_sections_subject_id",
            "ix_user_activity_user_id",
        ):
            await conn.execute(text(f"DROP INDEX IF EXISTS {_redundant_index}"))

        # ── 2026-07-30 大規模リニューアル: My時間割・単位チェッカー・CAP制・
        # 必修科目自動登録機能を全廃止。関連テーブル・列をDROPする ──────────────
        for _dropped_table in (
            "user_syllabi", "user_custom_courses", "required_subjects",
            "registration_caps", "credit_requirements", "subject_credit_categories",
            "user_seiseki_raw", "schedules",
        ):
            await conn.execute(text(f'DROP TABLE IF EXISTS "{_dropped_table}" CASCADE'))
        await conn.execute(text(
            "ALTER TABLE subjects DROP COLUMN IF EXISTS hide_from_timetable"
        ))
        await conn.execute(text(
            "ALTER TABLE subjects DROP COLUMN IF EXISTS senmon_group"
        ))
        await conn.execute(text(
            "ALTER TABLE user_profiles DROP COLUMN IF EXISTS share_token_version"
        ))
        await conn.execute(text(
            "ALTER TABLE syllabi DROP COLUMN IF EXISTS target_grades"
        ))
        await conn.execute(text(
            "ALTER TABLE syllabi DROP COLUMN IF EXISTS subject_category"
        ))

        # ── 2026-08-23: reviews.is_approved(Boolean) → status(pending/approved/rejected)への移行 ──
        # 従来「却下」は即物理削除だったが、投稿レビューは削除しない方針(CLAUDE.md)に合わせ、
        # 却下もstatus='rejected'として保持し管理画面から復元・承認できるようにする
        await conn.execute(text(
            "ALTER TABLE reviews ADD COLUMN IF NOT EXISTS status TEXT"
        ))
        await conn.execute(text("""
            DO $$ BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'reviews' AND column_name = 'is_approved'
              ) THEN
                UPDATE reviews SET status = CASE WHEN is_approved THEN 'approved' ELSE 'pending' END
                WHERE status IS NULL;
                ALTER TABLE reviews DROP COLUMN is_approved;
              END IF;
            END $$
        """))
        await conn.execute(text(
            "UPDATE reviews SET status = 'pending' WHERE status IS NULL"
        ))
        await conn.execute(text(
            "ALTER TABLE reviews ALTER COLUMN status SET NOT NULL"
        ))
        await conn.execute(text(
            "ALTER TABLE reviews ALTER COLUMN status SET DEFAULT 'pending'"
        ))
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE reviews ADD CONSTRAINT chk_reviews_status CHECK (status IN ('pending', 'approved', 'rejected'));
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))

        # ── 2026-08-24: レビュー投稿フォームのメールアドレス認証（なりすまし防止） ──
        # LINEアカウント×学籍番号の組み合わせを初めて登録する際、大学メール
        # ({学籍番号を小文字化}@stu.kobe-u.ac.jp)宛のマジックリンクで本人確認する。
        # email_verificationsテーブル自体はcreate_all()で新規作成されるため、ここでは
        # 既存のuser_profilesへのカラム追加のみ行う。
        await conn.execute(text(
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ"
        ))

        # ── 2026-08-24: レビュー投稿報酬（1件10円、100円単位でPayPay払い）の支払い管理 ──
        # payment_requestsテーブル自体はcreate_all()で新規作成されるため、ここではCHECK制約と
        # 既存のreviewsテーブルへの紐付け列追加のみ行う
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE payment_requests ADD CONSTRAINT chk_payment_requests_status CHECK (status IN ('pending', 'paid', 'rejected'));
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        await conn.execute(text(
            "ALTER TABLE reviews ADD COLUMN IF NOT EXISTS payment_request_id BIGINT"
        ))
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE reviews ADD CONSTRAINT fk_reviews_payment_request
                FOREIGN KEY (payment_request_id) REFERENCES payment_requests(id) ON DELETE SET NULL;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))

        # ── 2026-08-24: お問い合わせフォーム（質問・情報の誤り指摘・新情報の追加提案等） ──
        # inquiriesテーブル自体はcreate_all()で新規作成されるため、ここではCHECK制約の追加のみ行う
        await conn.execute(text("""
            DO $$ BEGIN
              ALTER TABLE inquiries ADD CONSTRAINT chk_inquiries_status CHECK (status IN ('pending', 'handled'));
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """))
        # メールアドレスを必須化（当初は任意項目だったため、モデル変更に合わせて既存NULL行を
        # 空文字に寄せてからNOT NULL制約を追加する。公開直後の機能でテストデータのみのため実害なし）
        await conn.execute(text(
            "UPDATE inquiries SET email = '' WHERE email IS NULL"
        ))
        await conn.execute(text(
            "ALTER TABLE inquiries ALTER COLUMN email SET NOT NULL"
        ))

        # ── 2026-08-24: レビュー閲覧の鍵システム ──
        # デフォルトでは他人のレビューは閲覧できず、自分のレビューが1件承認されるたびに
        # 任意の科目3件分の閲覧権（チケット、REVIEW_APPROVAL_UNLOCK_CREDITS）が付与される。
        # subject_unlocksテーブル自体は
        # create_all()で新規作成されるため、ここでは既存テーブルへのカラム追加のみ行う。
        await conn.execute(text(
            "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS unlock_credits INTEGER NOT NULL DEFAULT 0"
        ))
        await conn.execute(text(
            "ALTER TABLE reviews ADD COLUMN IF NOT EXISTS credit_granted_at TIMESTAMPTZ"
        ))

        # ── 2026-08-24: 会員登録の学年入力を廃止 ──
        # 学年を使う機能（旧・単位チェッカー等）は既に全廃止済みで、実質未使用の項目だったため
        # 登録フォームから削除。既存ユーザーの値もユーザーの了承のもと削除する
        await conn.execute(text(
            "ALTER TABLE user_profiles DROP COLUMN IF EXISTS grade"
        ))

        # ── 2026-08-25: お問い合わせフォームに学籍番号を追加 ──
        # 会員登録済みユーザーのみ送信可能にするため必須化。既存行（公開直後の
        # テストデータのみで実害なし）は空文字のまま残す
        await conn.execute(text(
            "ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS student_id VARCHAR(20) NOT NULL DEFAULT ''"
        ))

        # ── 2026-08-25: reviews.grading_methodを旧区切り文字列からJSON配列形式へ移行 ──
        # parse_grading_method()（core/grading_method.py）は表示時に旧形式へフォールバックする
        # ため機能上は必須ではないが、フォールバック分岐を恒久的に残さないよう既存データも
        # 新形式へ揃える（ユーザーの了承のもと実施。content/rating/status等は一切変更しない）。
        # 既にJSON配列の行はparse時にlist判定でスキップするため、毎起動時に実行しても安全
        result = await conn.execute(text(
            "SELECT id, grading_method FROM reviews WHERE grading_method IS NOT NULL AND grading_method != ''"
        ))
        gm_rows = result.fetchall()
        if gm_rows:
            import json as _json

            from core.grading_method import parse_grading_method

            to_update = []
            for row in gm_rows:
                try:
                    already = _json.loads(row.grading_method)
                except (ValueError, TypeError):
                    already = None
                if isinstance(already, list):
                    continue
                parts = parse_grading_method(row.grading_method)
                if parts:
                    to_update.append((row.id, _json.dumps(parts, ensure_ascii=False)))
            if to_update:
                _BATCH = 500
                for i in range(0, len(to_update), _BATCH):
                    batch = to_update[i:i + _BATCH]
                    values_sql = ", ".join(
                        f"(CAST(:id{j} AS BIGINT), CAST(:g{j} AS TEXT))" for j in range(len(batch))
                    )
                    params = {}
                    for j, (rid, gm) in enumerate(batch):
                        params[f"id{j}"] = rid
                        params[f"g{j}"] = gm
                    await conn.execute(text(
                        f"UPDATE reviews SET grading_method = v.g FROM (VALUES {values_sql}) AS v(id, g) "
                        f"WHERE reviews.id = v.id"
                    ), params)
