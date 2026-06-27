-- Migration: add_faculty_to_shared_tables
-- Created: 2026-06-27
--
-- 複数学部対応のため classification_orders と credit_requirements に
-- faculty カラムを追加する。
--
-- 【classification_orders】
--   UNIQUE(name) → UNIQUE(name, faculty) に変更。
--   異なる学部が同じ分類名（例: "第1群科目"）を持てるようになる。
--
-- 【credit_requirements】
--   faculty カラムを追加（表示・絞り込み用）。
--   category_id は引き続きグローバル一意の PK として維持する。
--   他学部のカテゴリを追加する際は衝突を避けるため
--   学部識別子をプレフィックスとして付けること。
--   例: 理学部なら "rika_senmon1"、文学部なら "bun_kyoyo" など。


-- ============================================================
-- classification_orders
-- ============================================================

-- faculty カラム追加（既存行はすべて経営学部として設定）
ALTER TABLE classification_orders
    ADD COLUMN IF NOT EXISTS faculty text NOT NULL DEFAULT '経営学部';

-- name 単独の UNIQUE 制約を削除
ALTER TABLE classification_orders
    DROP CONSTRAINT IF EXISTS classification_orders_name_key;

-- (name, faculty) の複合 UNIQUE 制約を追加
ALTER TABLE classification_orders
    ADD CONSTRAINT uq_classification_orders_name_faculty UNIQUE (name, faculty);

CREATE INDEX IF NOT EXISTS ix_classification_orders_faculty
    ON classification_orders (faculty);


-- ============================================================
-- credit_requirements
-- ============================================================

-- faculty カラム追加（既存行はすべて経営学部として設定）
ALTER TABLE credit_requirements
    ADD COLUMN IF NOT EXISTS faculty text NOT NULL DEFAULT '経営学部';

CREATE INDEX IF NOT EXISTS ix_credit_requirements_faculty
    ON credit_requirements (faculty);
