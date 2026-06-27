-- Migration: create_unified_course_system
-- Created: 2026-06-27
--
-- 新テーブル群（既存 LINE bot テーブルを統合・正規化）:
--   subjects, instructors, course_sections, syllabi,
--   schedules, reviews, course_section_views, user_syllabi,
--   subject_credit_categories
--
-- 以下の既存テーブルは変更なし（このファイルでは触らない）:
--   classification_orders, credit_requirements,
--   user_profiles, user_activity, message_logs, error_logs,
--   push_subscriptions, richmenu_taps, user_seiseki_raw,
--   user_preferences, timetable_profiles


-- ============================================================
-- subjects（科目マスター）
-- 既存 courses から: name / reading / faculty / classification /
--                    category / senmon_group / sort_order
-- ============================================================
CREATE TABLE subjects (
    id                bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name              text    NOT NULL,
    reading           text,                               -- よみがな（かな検索用）
    faculty           text,                               -- 学部
    classification_id bigint  REFERENCES classification_orders(id) ON DELETE SET NULL,
    category          text,                               -- 専門 / 教養
    senmon_group      text,                               -- 第1群 / 第2群 等
    sort_order        integer NOT NULL DEFAULT 0
);

CREATE INDEX ix_subjects_name              ON subjects (name);
CREATE INDEX ix_subjects_classification_id ON subjects (classification_id);
CREATE INDEX ix_subjects_faculty           ON subjects (faculty);


-- ============================================================
-- instructors（教員マスター）
-- 既存 course_instructors のテキスト埋め込みを独立テーブルに昇格
-- ============================================================
CREATE TABLE instructors (
    id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text   NOT NULL
);

CREATE INDEX ix_instructors_name ON instructors (name);


-- ============================================================
-- course_sections（開講セクション: 科目 × 教員）
-- 既存 courses + course_instructors の関係を正規化
-- ============================================================
CREATE TABLE course_sections (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_id    bigint NOT NULL REFERENCES subjects(id)    ON DELETE CASCADE,
    instructor_id bigint NOT NULL REFERENCES instructors(id) ON DELETE CASCADE,
    credits       numeric(3, 1),
    course_type   text,                                  -- 開講区分（専門 / 教養 / 共通基礎 等）
    term_type     text CHECK (term_type IN ('通年', 'セメスター', 'クオーター', '集中')),
    syllabus_url  text,                                  -- シラバスURL（教員×科目単位）
    CONSTRAINT uq_course_sections_subject_instructor UNIQUE (subject_id, instructor_id)
);

CREATE INDEX ix_course_sections_subject_id    ON course_sections (subject_id);
CREATE INDEX ix_course_sections_instructor_id ON course_sections (instructor_id);


-- ============================================================
-- syllabi（シラバス）
-- 既存 syllabus_courses の属性を統合
-- ※ シラバス本文（content）は不要。URLは course_sections.syllabus_url で管理。
-- ============================================================
CREATE TABLE syllabi (
    id                bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    course_section_id bigint      NOT NULL REFERENCES course_sections(id) ON DELETE CASCADE,
    year              integer     NOT NULL,               -- 年度（例: 2026）
    quarter           text        NOT NULL,               -- クオーター（例: "spring", "q1"）
    timetable_code    text,                               -- 時間割コード（例: 3B185）
    target_grades     text,                               -- 対象学年（例: "1・2・3・4年"）
    subject_category  text,                               -- 科目区分（人文科学系 等）
    numbering_code    text,                               -- ナンバリングコード
    department        text,                               -- 開講学部
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_syllabi_section_year_quarter UNIQUE (course_section_id, year, quarter)
);

CREATE INDEX ix_syllabi_course_section_id ON syllabi (course_section_id);
CREATE INDEX ix_syllabi_year              ON syllabi (year);
CREATE INDEX ix_syllabi_timetable_code    ON syllabi (timetable_code);


-- ============================================================
-- schedules（開講スケジュール）
-- 既存 course_slots の period を統合、classroom を新規追加
-- ※ 1つの授業が複数コマを持てるよう 1:多 設計（月曜3限＋木曜3限 等）
-- ============================================================
CREATE TABLE schedules (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    syllabus_id bigint      NOT NULL REFERENCES syllabi(id) ON DELETE CASCADE,
    day_of_week text        NOT NULL,                    -- 曜日（月 / 火 / 水 / 木 / 金 / 土）
    period      integer,                                 -- 時限（1〜6 等）
    classroom   text,                                    -- 教室名
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_schedules_syllabus_day_period UNIQUE (syllabus_id, day_of_week, period)
);

CREATE INDEX ix_schedules_syllabus_id ON schedules (syllabus_id);
CREATE INDEX ix_schedules_day_period  ON schedules (day_of_week, period);


-- ============================================================
-- reviews（レビュー）
-- 既存 pending_reviews の全属性を統合・正規化
-- ============================================================
CREATE TABLE reviews (
    id                bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    course_section_id bigint      NOT NULL REFERENCES course_sections(id) ON DELETE CASCADE,
    content           text,                              -- レビュー本文
    rating            integer     CHECK (rating BETWEEN 1 AND 5),
    ease_rating       text        CHECK (ease_rating IN ('SS', 'S', 'A', 'B', 'C')),
    grading_method    text,                              -- 評価方法の説明
    submitter_name    text,                              -- 投稿者本名（管理者確認用）
    nickname          text,                              -- 表示用ニックネーム
    student_id        text,                              -- 学籍番号
    academic_year     integer,                           -- 受講時の学年
    is_approved       boolean     NOT NULL DEFAULT false,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_reviews_course_section_id  ON reviews (course_section_id);
CREATE INDEX ix_reviews_is_approved        ON reviews (is_approved);
CREATE INDEX ix_reviews_course_approved    ON reviews (course_section_id, is_approved);
CREATE INDEX ix_reviews_created_at         ON reviews (created_at);


-- ============================================================
-- course_section_views（科目閲覧数）
-- 既存 course_views の正規化版
-- （course_name の非正規化カラムを廃止し、FK のみで参照）
-- ============================================================
CREATE TABLE course_section_views (
    course_section_id bigint      PRIMARY KEY REFERENCES course_sections(id) ON DELETE CASCADE,
    view_count        integer     NOT NULL DEFAULT 0,
    last_viewed_at    timestamptz NOT NULL
);


-- ============================================================
-- user_syllabi（ユーザー時間割登録）
-- 既存 user_courses の正規化版（FK先を syllabi に変更）
-- ============================================================
CREATE TABLE user_syllabi (
    id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    line_user_id text        NOT NULL,                   -- LINE ユーザーID
    syllabus_id  bigint      NOT NULL REFERENCES syllabi(id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_user_syllabi UNIQUE (line_user_id, syllabus_id)
);

CREATE INDEX ix_user_syllabi_line_user_id ON user_syllabi (line_user_id);
CREATE INDEX ix_user_syllabi_syllabus_id  ON user_syllabi (syllabus_id);


-- ============================================================
-- subject_credit_categories（科目単位カテゴリ紐付け）
-- 既存 category_courses の正規化版（course_name テキスト → subject_id FK）
-- ============================================================
CREATE TABLE subject_credit_categories (
    id          bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_id  bigint       NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    category_id text         NOT NULL REFERENCES credit_requirements(category_id),
    credits     numeric(3,1) NOT NULL DEFAULT 2.0,
    CONSTRAINT uq_subject_credit_categories UNIQUE (subject_id, category_id)
);

CREATE INDEX ix_scc_subject_id  ON subject_credit_categories (subject_id);
CREATE INDEX ix_scc_category_id ON subject_credit_categories (category_id);


-- ============================================================
-- RLS 有効化（ポリシーは別途設定）
-- ============================================================
ALTER TABLE subjects                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE instructors               ENABLE ROW LEVEL SECURITY;
ALTER TABLE course_sections           ENABLE ROW LEVEL SECURITY;
ALTER TABLE syllabi                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE schedules                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE reviews                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE course_section_views      ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_syllabi              ENABLE ROW LEVEL SECURITY;
ALTER TABLE subject_credit_categories ENABLE ROW LEVEL SECURITY;
