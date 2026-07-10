-- Migration: subjects_unique_name_faculty
-- Created: 2026-07-10
--
-- subjects.name の UNIQUE制約を (name, faculty) の複合UNIQUEに変更する。
-- 「卒業研究」「国際関係論」等、学部をまたいで同名の専門科目が実在するケースで、
-- 別学部の科目が誤って同じ subjects 行に相乗りしてしまう不具合があったため。
-- dev→prod同期スクリプト（programing files/sync_db_to_prod.py）のON CONFLICT句も
-- (name, faculty) に合わせて修正済み。

ALTER TABLE subjects DROP CONSTRAINT IF EXISTS uq_subjects_name;
ALTER TABLE subjects ADD CONSTRAINT uq_subjects_name_faculty UNIQUE (name, faculty);
