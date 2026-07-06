-- Migration: rename_syllabi_quarter_to_academic_term
-- Created: 2026-07-06
--
-- syllabi.quarter は「クオーター」という名前だが、実際の値は
-- 第1〜第4クォーターだけでなく「後期」「集中」も入るため、
-- 「いつ開講されるか」を表す名前として academic_term にリネームする。
--
-- 実行前提: 制約名は 20260627120000_create_course_system_tables.sql で
-- 定義した uq_syllabi_section_year_quarter を想定している。

ALTER TABLE syllabi
    RENAME COLUMN quarter TO academic_term;

ALTER TABLE syllabi
    RENAME CONSTRAINT uq_syllabi_section_year_quarter TO uq_syllabi_section_year_term;
