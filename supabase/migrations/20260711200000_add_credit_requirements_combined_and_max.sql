-- Migration: add_credit_requirements_combined_and_max
-- Created: 2026-07-11
--
-- 単位チェッカー（credit_requirements）に、複数区分の合計に対する合算制約
-- （例: 経営学部「第2群+第3群+グローバル科目群=55単位以上」「専門科目全体=98単位以上」）と、
-- 取得単位数の上限（例: 経営学部「その他必要と認める科目=12単位まで」）を表現するための列。
-- どちらもNULLなら従来通りの単純な区分（自区分の取得単位のみで判定）として扱われる。

ALTER TABLE credit_requirements
    ADD COLUMN IF NOT EXISTS combined_of JSONB,
    ADD COLUMN IF NOT EXISTS max_credits INTEGER;

-- sonota(その他必要と認める科目)はrequired_creditsを上限の代用にしていたが、
-- 学生便覧上は必要最低数ではなく上限（自由選択・12単位以内）のためmax_creditsへ移す
UPDATE credit_requirements SET max_credits = required_credits, required_credits = 0
WHERE category_id = 'sonota' AND max_credits IS NULL AND required_credits > 0;
