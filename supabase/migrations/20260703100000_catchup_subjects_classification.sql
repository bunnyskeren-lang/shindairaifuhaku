-- Migration: catchup_subjects_classification
-- Created: 2026-07-03
--
-- subjects.classification（分類名の非正規化テキスト、classification_orders.name と
-- 文字列一致で参照）は、アプリ全体（管理画面・LINE Bot・programing files 配下スクリプト）
-- で常時使われている列だが、2026-06-27 のスキーマ作成マイグレーション
-- （20260627120000_create_course_system_tables.sql）には含まれておらず、
-- マイグレーション履歴を介さず DB に直接追加された形跡がある。
--
-- 既に本番/dev の実DBには存在している前提で IF NOT EXISTS を使い、
-- 将来 migrations だけから新規に環境を再現した場合にも同じスキーマになるよう
-- 履歴を追いつかせる（キャッチアップ）ためのマイグレーション。
-- 既存DBに対しては実質的な変更なし（列もインデックスも既に存在する想定）。

ALTER TABLE subjects
    ADD COLUMN IF NOT EXISTS classification text;

CREATE INDEX IF NOT EXISTS ix_subjects_classification
    ON subjects (classification);
