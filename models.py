from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, Integer, Float, Boolean, Numeric, BigInteger, func, UniqueConstraint, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, validates
from database import Base
from core.config import normalize_instructor_name, normalize_subject_name


class MessageLog(Base):
    __tablename__ = "message_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DisplayOrder(Base):
    """汎用の表示順マスタ。kindで対象種別(classification/faculty/credit_requirement_group等)を区別する。"""
    __tablename__ = "display_orders"
    __table_args__ = (UniqueConstraint("kind", "name", "faculty", name="uq_display_orders_kind_name_faculty"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_group: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default=None)
    faculty: Mapped[str] = mapped_column(String(100), nullable=False, server_default="", default="")


class UserProfile(Base):
    __tablename__ = "user_profiles"

    line_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    student_id: Mapped[str] = mapped_column(String(20), nullable=False)
    faculty: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    grade: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    department: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # マイ時間割の共有リンクに埋め込む世代番号。「共有を停止する」でインクリメントすると
    # それ以前に発行済みのリンク（友達に送信済みのものを含む）が一括で無効になる
    share_token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


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


class RichMenuTap(Base):
    __tablename__ = "richmenu_taps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    button: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    tapped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CreditRequirement(Base):
    __tablename__ = "credit_requirements"

    category_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    group_name: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    faculty: Mapped[str] = mapped_column(String(100), nullable=False, server_default="経営学部", default="経営学部")
    department: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    # 複数区分の合計に対する合算制約を表す行の場合、対象category_idのリスト（例: 第2群+第3群+グローバル=55単位）
    # NULLなら通常の区分（自区分の取得単位のみで判定）
    combined_of: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=None)
    # 取得単位数がこれを超えても卒業要件には超過分を算入しない上限（例: その他必要と認める科目=12単位）
    # NULLなら上限なし
    max_credits: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)


class UserSeisekiRaw(Base):
    __tablename__ = "user_seiseki_raw"

    line_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    gpa: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── 新スキーマ ────────────────────────────────────────────────────────────────


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint("name", "faculty", "department", name="uq_subjects_name_faculty_department"),
        # マイ時間割の科目選択(/api/timetable/slots)向け: faculty==X, department.in_(...), category==専門
        Index("ix_subjects_faculty_department_category", "faculty", "department", "category"),
        # 教養教育院の分類絞り込み(classification==/LIKE)向け
        Index("ix_subjects_faculty_classification", "faculty", "classification"),
        # 管理画面(/admin/courses)のclassification単体GROUP BY・完全一致検索向け
        Index("ix_subjects_classification", "classification"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    reading: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    faculty: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)
    # 学部内で学科・専攻ごとに卒業要件が異なる場合の学科名（工学部5学科・理学部5学科・
    # 医学部保健学科4専攻等）。credit_requirements/registration_caps/required_subjects/
    # user_profilesと同じ「faculty列+department列」のペア形式。学科の区別が無い学部では
    # 空文字（subjects.readingと同じプレースホルダ方式、UNIQUE制約でNULL同士を区別しないPostgresの
    # 挙動を避けるためNOT NULL）。
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
    """シラバスURLはtimetable_code+department（course_sections経由でSubject.faculty/departmentを
    参照、core.config.syllabus_department_key()で再構成）から毎回core.config.make_syllabus_url()で
    動的生成する（列としては持たない）。年度が変わりtimetable_codeが変われば自動で
    追従するため、course_sections.syllabus_urlのような値の陳腐化が起きない。"""
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


class CourseSectionView(Base):
    __tablename__ = "course_section_views"

    course_section_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("course_sections.id", ondelete="CASCADE"), primary_key=True)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserSyllabus(Base):
    __tablename__ = "user_syllabi"
    __table_args__ = (UniqueConstraint("line_user_id", "syllabus_id", name="uq_user_syllabi"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    line_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    syllabus_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("syllabi.id", ondelete="CASCADE"), nullable=False, index=True)
    classroom: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UserCustomCourse(Base):
    """ユーザーがマイ時間割で手動追加した個人用の科目（シラバスマスタに存在しない科目）。
    line_user_idの本人にのみ表示され、他ユーザーの科目一覧には表示されない。
    classificationはcredit_requirements.category_idと一致させ、単位チェッカーの取得単位数に
    creditsを加算する（routers/seiseki_api.pyのapi_seiseki_credits参照）。"""
    __tablename__ = "user_custom_courses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    line_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    instructor: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    classification: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    credits: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    day_of_week: Mapped[str] = mapped_column(Text, nullable=False)
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RequiredSubject(Base):
    """学部・学科・学年ごとの必修科目マスタ。時間割登録時に自動でuser_syllabiへ登録する対象を管理する。
    student_id_parity は学籍番号末尾1桁の偶奇でクラスが分かれる科目（機械工学科の実習/製図等）向けの絞り込み。
    NULLなら全員が対象。"""
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


class SubjectCreditCategory(Base):
    __tablename__ = "subject_credit_categories"
    __table_args__ = (UniqueConstraint("subject_id", "category_id", name="uq_subject_credit_categories"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True)
    category_id: Mapped[str] = mapped_column(String(50), ForeignKey("credit_requirements.category_id"), nullable=False, index=True)
    credits: Mapped[float] = mapped_column(Numeric(3, 1), nullable=False, default=2.0)
