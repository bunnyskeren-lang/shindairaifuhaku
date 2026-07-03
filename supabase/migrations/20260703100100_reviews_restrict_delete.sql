-- Migration: reviews_restrict_delete
-- Created: 2026-07-03
--
-- reviews.course_section_id は ON DELETE CASCADE で course_sections を参照しており、
-- 科目（subjects）や担当教員セクション（course_sections）を削除すると、
-- 承認済み・未承認を問わずレビューが DB レベルで自動的に巻き添え削除されていた。
--
-- アプリ側（routers/admin/courses.py の admin_courses_delete / delete_instructor）は
-- レビューが1件でもあれば削除をブロックするよう既に修正済みだが、
-- 直接 SQL 操作や将来のコード変更でアプリ側チェックを経由しなかった場合の保険として、
-- DB レベルでも CASCADE → RESTRICT に変更し、レビューが紐づく行の削除自体を
-- DB に拒否させる（CLAUDE.md「レビューを巻き添えで削除しない」ルールの多層防御）。
--
-- 実行前提: 制約名は 20260627120000_create_course_system_tables.sql で
-- 明示的な CONSTRAINT 名を付けずに定義したため、PostgreSQL のデフォルト命名規則
-- （<table>_<column>_fkey）である reviews_course_section_id_fkey を想定している。
-- 適用前に以下で実際の制約名を確認すること:
--   SELECT conname FROM pg_constraint
--   WHERE conrelid = 'reviews'::regclass AND contype = 'f';

ALTER TABLE reviews
    DROP CONSTRAINT IF EXISTS reviews_course_section_id_fkey;

ALTER TABLE reviews
    ADD CONSTRAINT reviews_course_section_id_fkey
    FOREIGN KEY (course_section_id) REFERENCES course_sections(id) ON DELETE RESTRICT;
