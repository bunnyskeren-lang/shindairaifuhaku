from datetime import datetime
from typing import Optional
import re as _re
from sqlalchemy import String, Text, DateTime, Integer, Float, Boolean, Numeric, BigInteger, func, UniqueConstraint, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, validates
from database import Base


def normalize_instructor_name(name: str) -> str:
    if not name:
        return name
    return name.replace(' ', '').replace('　', '')


# core/config.pyのnormalize_subject_nameと同じロジック（scripts専用のmodels.pyのため複製）。
# 半角ローマ数字表記（I, II, III...）をDB上の表記（全角ローマ数字）へ統一する。
_HALF_TO_FULL_ROMAN = {
    'IX': 'Ⅸ', 'IV': 'Ⅳ', 'VIII': 'Ⅷ', 'VII': 'Ⅶ', 'VI': 'Ⅵ',
    'III': 'Ⅲ', 'II': 'Ⅱ', 'I': 'Ⅰ', 'V': 'Ⅴ', 'X': 'Ⅹ',
}
_ROMAN_NUMERAL_RE = _re.compile(r'(?<![A-Za-z0-9])(IX|IV|VIII|VII|VI|III|II|I|V|X)(?![A-Za-z0-9])')


def normalize_subject_name(name: str) -> str:
    if not name:
        return name
    return _ROMAN_NUMERAL_RE.sub(lambda m: _HALF_TO_FULL_ROMAN[m.group(1)], name)


class MessageLog(Base):
    __tablename__ = "message_logs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DisplayOrder(Base):
    __tablename__ = "display_orders"
    __table_args__ = (UniqueConstraint("kind", "name", "faculty", name="uq_display_orders_kind_name_faculty"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_group: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    faculty: Mapped[str] = mapped_column(String(100), nullable=False, server_default="", default="")


class UserProfile(Base):
    __tablename__ = "user_profiles"
    line_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    student_id: Mapped[str] = mapped_column(String(20), nullable=False)
    faculty: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    grade: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    department: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # マイ時間割の共有リンクに埋め込む世代番号（ルートmodels.py参照）
    share_token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CreditRequirement(Base):
    __tablename__ = "credit_requirements"
    category_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    group_name: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    faculty: Mapped[str] = mapped_column(String(100), nullable=False, server_default="経営学部", default="経営学部")
    department: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    combined_of: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    max_credits: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class RegistrationCap(Base):
    """学部・学科・年度ごとの履修登録上限単位数（CAP制）。departmentがNULLの行はその学部の学科共通値として扱う。"""
    __tablename__ = "registration_caps"
    __table_args__ = (UniqueConstraint("faculty", "department", "year", name="uq_registration_caps"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    faculty: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    department: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    max_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RequiredSubject(Base):
    """学部・学科・学年ごとの必修科目マスタ。時間割登録時に自動でuser_syllabiへ登録する対象を管理する。"""
    __tablename__ = "required_subjects"
    __table_args__ = (UniqueConstraint("faculty", "department", "grade", "subject_id", name="uq_required_subjects"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    faculty: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    department: Mapped[str] = mapped_column(Text, nullable=False)
    grade: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id_parity: Mapped[Optional[str]] = mapped_column(String(4), nullable=True, default=None)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UserSeisekiRaw(Base):
    __tablename__ = "user_seiseki_raw"
    line_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    gpa: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("name", "faculty", "department", name="uq_subjects_name_faculty_department"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    reading: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    faculty: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)
    department: Mapped[str] = mapped_column(Text, nullable=False, server_default="", default="")
    classification: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    senmon_group: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    term_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    credits: Mapped[Optional[float]] = mapped_column(Numeric(3, 1), nullable=True)
    hide_from_timetable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)

    @validates("name")
    def _normalize_name(self, key, value):
        return normalize_subject_name(value)


class Instructor(Base):
    __tablename__ = "instructors"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, index=True, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)

    @validates("name")
    def _normalize_name(self, key, value):
        return normalize_instructor_name(value)


class CourseSection(Base):
    __tablename__ = "course_sections"
    __table_args__ = (UniqueConstraint("subject_id", "instructor_id", name="uq_course_sections_subject_instructor"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True)
    instructor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instructors.id", ondelete="CASCADE"), nullable=False, index=True)


class Syllabus(Base):
    # シラバスURLはtimetable_code+department（Subject.faculty/department経由）から毎回make_syllabus_url()で動的生成する（列としては持たない）
    __tablename__ = "syllabi"
    __table_args__ = (UniqueConstraint("course_section_id", "year", "academic_term", name="uq_syllabi_section_year_term"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    course_section_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("course_sections.id", ondelete="CASCADE"), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    academic_term: Mapped[str] = mapped_column(Text, nullable=False)
    timetable_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)
    target_grades: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    subject_category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (UniqueConstraint("syllabus_id", "day_of_week", "period", name="uq_schedules_syllabus_day_period"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    syllabus_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("syllabi.id", ondelete="CASCADE"), nullable=False, index=True)
    day_of_week: Mapped[str] = mapped_column(Text, nullable=False)
    period: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # ルートmodels.pyと同じくRESTRICT。レビューは科目削除の巻き添えで消してはならない（データ保護ルール）
    course_section_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("course_sections.id", ondelete="RESTRICT"), nullable=False, index=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ease_rating: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    grading_method: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    submitter_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    nickname: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    student_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    academic_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    selected_instructor: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SubjectCreditCategory(Base):
    __tablename__ = "subject_credit_categories"
    __table_args__ = (UniqueConstraint("subject_id", "category_id", name="uq_subject_credit_categories"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True)
    category_id: Mapped[str] = mapped_column(String(50), ForeignKey("credit_requirements.category_id"), nullable=False, index=True)
    credits: Mapped[float] = mapped_column(Numeric(3, 1), nullable=False, default=2.0)
