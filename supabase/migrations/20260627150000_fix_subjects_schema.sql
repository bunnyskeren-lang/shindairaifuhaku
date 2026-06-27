-- Migration: fix_subjects_schema
-- Created: 2026-06-27
--
-- term / term_type / credits は「科目固有・永続不変」なので
-- course_sections から subjects に移動する。
-- instructors.name に UNIQUE 制約を追加してデータ整合性を担保する。


-- ============================================================
-- subjects に term / term_type / credits を追加
-- ============================================================
ALTER TABLE subjects
    ADD COLUMN IF NOT EXISTS term      text,
    ADD COLUMN IF NOT EXISTS term_type text
        CHECK (term_type IN ('通年', 'セメスター', 'クオーター', '集中')),
    ADD COLUMN IF NOT EXISTS credits   numeric(3,1);


-- ============================================================
-- course_sections から term_type / credits を削除
-- ============================================================
ALTER TABLE course_sections
    DROP COLUMN IF EXISTS term_type,
    DROP COLUMN IF EXISTS credits;


-- ============================================================
-- instructors.name に UNIQUE 制約を追加
-- ============================================================
DO $$ BEGIN
    ALTER TABLE instructors ADD CONSTRAINT uq_instructors_name UNIQUE (name);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
