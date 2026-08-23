from datetime import datetime
from typing import Optional
import re as _re
from sqlalchemy import String, Text, DateTime, Integer, Numeric, BigInteger, func, UniqueConstraint, ForeignKey
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


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
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    term_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    credits: Mapped[Optional[float]] = mapped_column(Numeric(3, 1), nullable=True)

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
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
