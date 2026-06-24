from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, Integer, Float, Boolean, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class MessageLog(Base):
    __tablename__ = "message_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    instructor: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    classification: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(20), nullable=False, server_default="専門")
    syllabus_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    reading: Mapped[str] = mapped_column(String(400), nullable=False, server_default="", default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    term: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    credits: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    faculty: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default=None)


class ClassificationOrder(Base):
    __tablename__ = "classification_orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PendingReview(Base):
    __tablename__ = "pending_reviews"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    submitter_name: Mapped[str] = mapped_column(String(100), nullable=False)
    course_name: Mapped[str] = mapped_column(String(200), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    ease_rating: Mapped[str] = mapped_column(String(10), nullable=False)
    grading_method: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    selected_instructor: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    nickname: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    academic_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    student_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )



class UserProfile(Base):
    __tablename__ = "user_profiles"

    line_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    student_id: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ErrorLog(Base):
    __tablename__ = "error_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    action: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    error_type: Mapped[str] = mapped_column(String(100), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    traceback: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserActivity(Base):
    __tablename__ = "user_activity"
    __table_args__ = (UniqueConstraint("user_id", "action"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CourseInstructor(Base):
    __tablename__ = "course_instructors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class RichMenuTap(Base):
    __tablename__ = "richmenu_taps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    button: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    tapped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CourseView(Base):
    __tablename__ = "course_views"

    course_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_name: Mapped[str] = mapped_column(String(200), nullable=False)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SyllabusCourse(Base):
    __tablename__ = "syllabus_courses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    term: Mapped[str] = mapped_column(String(20), nullable=False)
    department: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    instructor: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    timetable_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    target_grades: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    subject_category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)


class TimetableProfile(Base):
    __tablename__ = "timetable_profiles"

    line_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    faculty: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    grade: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class CourseSlot(Base):
    __tablename__ = "course_slots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    syllabus_course_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    day_of_week: Mapped[str] = mapped_column(String(2), nullable=False)
    period: Mapped[int] = mapped_column(Integer, nullable=False)


class UserCourse(Base):
    __tablename__ = "user_courses"
    __table_args__ = (UniqueConstraint("line_user_id", "syllabus_course_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    line_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    syllabus_course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
