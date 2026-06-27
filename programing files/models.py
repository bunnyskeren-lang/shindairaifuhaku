from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, Integer, Boolean, Numeric, BigInteger, func, UniqueConstraint, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class MessageLog(Base):
    __tablename__ = "message_logs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ClassificationOrder(Base):
    __tablename__ = "classification_orders"
    __table_args__ = (UniqueConstraint("name", "faculty", name="uq_classification_orders_name_faculty"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_group: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    faculty: Mapped[str] = mapped_column(String(100), nullable=False, server_default="経営学部", default="経営学部")


class UserProfile(Base):
    __tablename__ = "user_profiles"
    line_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    student_id: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TimetableProfile(Base):
    __tablename__ = "timetable_profiles"
    line_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    faculty: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    grade: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class CreditRequirement(Base):
    __tablename__ = "credit_requirements"
    category_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    group_name: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    faculty: Mapped[str] = mapped_column(String(100), nullable=False, server_default="経営学部", default="経営学部")


class UserSeisekiRaw(Base):
    __tablename__ = "user_seiseki_raw"
    line_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Subject(Base):
    __tablename__ = "subjects"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    reading: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    faculty: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)
    classification_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("classification_orders.id", ondelete="SET NULL"), nullable=True, index=True)
    classification: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    senmon_group: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    term: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    term_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    credits: Mapped[Optional[float]] = mapped_column(Numeric(3, 1), nullable=True)


class Instructor(Base):
    __tablename__ = "instructors"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, index=True, unique=True)


class CourseSection(Base):
    __tablename__ = "course_sections"
    __table_args__ = (UniqueConstraint("subject_id", "instructor_id", name="uq_course_sections_subject_instructor"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True)
    instructor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instructors.id", ondelete="CASCADE"), nullable=False, index=True)
    course_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    syllabus_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Syllabus(Base):
    __tablename__ = "syllabi"
    __table_args__ = (UniqueConstraint("course_section_id", "year", "quarter", name="uq_syllabi_section_year_quarter"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    course_section_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("course_sections.id", ondelete="CASCADE"), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter: Mapped[str] = mapped_column(Text, nullable=False)
    timetable_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)
    target_grades: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    subject_category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    numbering_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    department: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (UniqueConstraint("syllabus_id", "day_of_week", "period", name="uq_schedules_syllabus_day_period"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    syllabus_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("syllabi.id", ondelete="CASCADE"), nullable=False, index=True)
    day_of_week: Mapped[str] = mapped_column(Text, nullable=False)
    period: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    classroom: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    course_section_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("course_sections.id", ondelete="CASCADE"), nullable=False, index=True)
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
