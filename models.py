from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, Integer, Numeric, BigInteger, func, UniqueConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, validates
from database import Base
from core.config import normalize_instructor_name, normalize_subject_name


class TimestampMixin:
    """created_at列(タイムゾーン付き、DB側でNOW()を既定値にする)を持つモデル共通のmixin。"""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MessageLog(TimestampMixin, Base):
    __tablename__ = "message_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)


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


class UserProfile(TimestampMixin, Base):
    __tablename__ = "user_profiles"

    line_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    student_id: Mapped[str] = mapped_column(String(20), nullable=False)
    faculty: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    grade: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    department: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # 大学メール({学籍番号を小文字化}@stu.kobe-u.ac.jp)のマジックリンク認証が完了した日時。
    # 初回のプロフィール作成時のみ検証し、以降の学籍番号変更を伴わない更新では再検証しない
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ErrorLog(TimestampMixin, Base):
    __tablename__ = "error_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    action: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    error_type: Mapped[str] = mapped_column(String(100), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    traceback: Mapped[str] = mapped_column(Text, nullable=False)


class UserActivity(Base):
    __tablename__ = "user_activity"
    __table_args__ = (UniqueConstraint("user_id", "action"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # user_idの単独indexは張らない。UniqueConstraint(user_id, action)の先頭列プレフィックスで代替できる
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
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


class PushSubscription(TimestampMixin, Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(100), nullable=False)


# ── 新スキーマ ────────────────────────────────────────────────────────────────


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint("name", "faculty", "department", name="uq_subjects_name_faculty_department"),
        # 教養教育院の分類絞り込み(classification==/LIKE)向け
        Index("ix_subjects_faculty_classification", "faculty", "classification"),
        # 管理画面(/admin/courses)のclassification単体GROUP BY・完全一致検索向け
        Index("ix_subjects_classification", "classification"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # name/facultyの単独indexは張らない。uq_subjects_name_faculty_department（name, faculty, department）と
    # ix_subjects_faculty_classification（facultyが先頭列）が既にある以上、単独indexは
    # 先頭列プレフィックスとして完全に重複し検索速度に寄与せず書き込みコストだけ増やす
    name: Mapped[str] = mapped_column(Text, nullable=False)
    reading: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    faculty: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 学部内で学科・専攻ごとに卒業要件が異なる場合の学科名（工学部5学科・理学部5学科・
    # 医学部保健学科4専攻等）。user_profilesと同じ「faculty列+department列」のペア形式。
    # 学科の区別が無い学部では空文字（subjects.readingと同じプレースホルダ方式、UNIQUE制約で
    # NULL同士を区別しないPostgresの挙動を避けるためNOT NULL）。
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
    # subject_idの単独indexは張らない。uq_course_sections_subject_instructor(subject_id, instructor_id)が
    # 先頭列としてsubject_id単体の検索もカバーするため（instructor_idは先頭列ではないので単独indexが必要）
    subject_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False)
    instructor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("instructors.id", ondelete="CASCADE"), nullable=False, index=True)


class Syllabus(TimestampMixin, Base):
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


class ReviewStatus:
    """Review.statusの取りうる値。CHECK制約はdatabase.py init_db()側で管理。"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PaymentRequestStatus:
    """PaymentRequest.statusの取りうる値。CHECK制約はdatabase.py init_db()側で管理。"""
    PENDING = "pending"
    PAID = "paid"
    REJECTED = "rejected"


class PaymentRequest(TimestampMixin, Base):
    """レビュー投稿報酬（1件10円、100円単位）の支払い申請。
    承認時に対象のreviews（古い順にamount/10件）へpayment_request_idを付与して予約し、
    二重申請・二重支払いを防ぐ（reviews側の紐付けが実質の「支払い済みフラグ」）。"""
    __tablename__ = "payment_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    student_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    paypay_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    # 'pending'(支払い待ち) / 'paid'(支払い済み) / 'rejected'(却下、予約したreviewsは解放)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=PaymentRequestStatus.PENDING)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Review(TimestampMixin, Base):
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
    # 'pending'(待機中) / 'approved'(承認) / 'rejected'(却下)。CHECK制約はdatabase.py init_db()側で管理。
    # 却下は物理削除ではなくstatus='rejected'として保持する（投稿レビューは削除しない方針）
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    # 支払い報酬の予約/支払い済み紐付け。NULL＝未払い（database.py init_db()でFK列を追加）
    payment_request_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("payment_requests.id", ondelete="SET NULL"), nullable=True, index=True)


class CourseSectionView(Base):
    __tablename__ = "course_section_views"

    course_section_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("course_sections.id", ondelete="CASCADE"), primary_key=True)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InquiryStatus:
    """Inquiry.statusの取りうる値。CHECK制約はdatabase.py init_db()側で管理。"""
    PENDING = "pending"
    HANDLED = "handled"


class Inquiry(TimestampMixin, Base):
    """お問い合わせ（質問・情報の誤りの指摘・新情報の追加提案・情報のアップデート・誤字脱字の
    指摘等）。フォーム送信時にそのまま作成する（メールアドレス認証は行わない）。"""
    __tablename__ = "inquiries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    # 'pending'(未対応) / 'handled'(対応済み)。CHECK制約はdatabase.py init_db()側で管理。
    status: Mapped[str] = mapped_column(Text, nullable=False, default=InquiryStatus.PENDING)
    handled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailVerification(TimestampMixin, Base):
    """レビュー投稿フォームで初めてUserProfileを作る際のメール認証待ち情報。
    大学メール宛のマジックリンクをクリックするまでUserProfile/Reviewの作成を保留し、
    payloadに投稿内容一式をJSON文字列で保持しておく（core/mail.py・routers/email_verify_api.py参照）。"""
    __tablename__ = "email_verifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    line_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
