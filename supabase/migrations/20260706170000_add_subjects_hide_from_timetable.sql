-- Migration: add_subjects_hide_from_timetable
-- Created: 2026-07-06
--
-- 管理者が特定の科目をマイ時間割（LIFF）の表示から個別に外せるようにするための列。
-- 科目マスタ自体は消さず、時間割表示だけを抑制する（reviews等の既存データには影響しない）。

ALTER TABLE subjects
    ADD COLUMN IF NOT EXISTS hide_from_timetable BOOLEAN NOT NULL DEFAULT FALSE;
