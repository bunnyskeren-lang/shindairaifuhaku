-- Migration: drop_subjects_classification_id
-- Created: 2026-07-02
--
-- subjects.classification_id（classification_orders への FK）は
-- 導入以来どのコードからも書き込まれておらず常に NULL のまま残っていた。
-- 分類の親子関係は subjects.classification（テキスト）と
-- classification_orders.name の文字列一致のみで運用されているため、
-- 未使用の classification_id カラムを削除する。

ALTER TABLE subjects
    DROP COLUMN IF EXISTS classification_id;
