-- Migration: add_credit_requirements_department
-- Created: 2026-07-06
--
-- 単位チェッカー（credit_requirements）を学科別の卒業要件にも対応させるための列。
-- NULL の場合は学部内の全学科共通の要件として扱う（既存の経営学部・システム情報学部の行はNULLのまま）。

ALTER TABLE credit_requirements
    ADD COLUMN IF NOT EXISTS department TEXT;
