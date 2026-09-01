from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select

from core import cache
from core.config import REVIEW_APPROVAL_UNLOCK_CREDITS, normalize_instructor_name
from core.grading_method import build_grading_method_from_edit_text
from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, Instructor, Review, ReviewStatus, Subject, UserProfile

router = APIRouter()

_PAGE_SIZE = 50


@router.post("/admin/reviews/cleanup")
async def admin_reviews_cleanup(_: str = Depends(check_admin)):
    # 待機中の孤立レビュー（subject が削除済み）を削除。承認済み・却下済みは削除しない。
    async with AsyncSessionLocal() as session:
        orphan_cs_ids = (await session.execute(
            select(CourseSection.id).where(
                ~CourseSection.subject_id.in_(select(Subject.id))
            )
        )).scalars().all()
        if orphan_cs_ids:
            await session.execute(
                delete(Review).where(
                    Review.course_section_id.in_(orphan_cs_ids),
                    Review.status == ReviewStatus.PENDING,
                )
            )
            await session.commit()
    return RedirectResponse("/admin/courses", status_code=303)


def _apply_review_edits(
    review: Review,
    content: Optional[str],
    rating: Optional[int],
    ease_rating: Optional[str],
    grading_method: Optional[str],
    selected_instructor: Optional[str],
    nickname: Optional[str],
) -> None:
    # 各フィールドはフォームから送られてきた場合のみ上書きする
    # （courses.html の簡易承認ボタンはこれらを送らないため、その場合は既存の内容のまま状態だけ変える）
    if content is not None:
        review.content = content.strip() or None
    if rating is not None:
        review.rating = rating
    if ease_rating is not None:
        review.ease_rating = ease_rating or None
    if grading_method is not None:
        # 管理画面のtextareaは1行1項目の「ラベル: テキスト」編集用フォーマット
        # （core/grading_method.py参照）。保存はJSON配列文字列で行う
        review.grading_method = build_grading_method_from_edit_text(grading_method)
    if selected_instructor is not None:
        review.selected_instructor = selected_instructor.strip() or None
    if nickname is not None:
        review.nickname = nickname.strip() or None


def _make_review_ns(rev: Review, course_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=rev.id,
        course_name=course_name,
        comment=rev.content,
        content=rev.content,
        rating=rev.rating,
        ease_rating=rev.ease_rating,
        grading_method=rev.grading_method,
        status=rev.status,
        selected_instructor=rev.selected_instructor,
        created_at=rev.created_at,
        submitter_name=rev.submitter_name,
        nickname=rev.nickname,
        academic_year=rev.academic_year,
        student_id=rev.student_id,
        paid=rev.payment_request_id is not None,
    )


@router.get("/admin/reviews", response_class=HTMLResponse)
async def admin_reviews(
    request: Request,
    _: str = Depends(check_admin),
    apage: int = Query(default=1, ge=1),
    rpage: int = Query(default=1, ge=1),
):
    variant_labels = await cache.get_variant_full_label_map_cached()

    async with AsyncSessionLocal() as session:
        # reviews.student_id（フォーム手入力のテキスト）とuser_profiles.student_idの
        # 完全一致でのみ会員登録日時に紐づく。一致しない（会員登録日時が特定できない）
        # レビューは末尾にまとめ、その中ではレビュー投稿日時が新しい順に並べる。
        pending_rows = (await session.execute(
            select(Review, Subject.name.label("subj_name"))
            .join(CourseSection, CourseSection.id == Review.course_section_id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .outerjoin(UserProfile, UserProfile.student_id == Review.student_id)
            .where(Review.status == ReviewStatus.PENDING)
            .order_by(UserProfile.created_at.desc().nulls_last(), Review.created_at.desc())
        )).all()

        approved_total = (await session.execute(
            select(func.count(Review.id)).where(Review.status == ReviewStatus.APPROVED)
        )).scalar_one()
        approved_rows = (await session.execute(
            select(Review, Subject.name.label("subj_name"))
            .join(CourseSection, CourseSection.id == Review.course_section_id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .outerjoin(UserProfile, UserProfile.student_id == Review.student_id)
            .where(Review.status == ReviewStatus.APPROVED)
            .order_by(UserProfile.created_at.desc().nulls_last(), Review.created_at.desc())
            .offset((apage - 1) * _PAGE_SIZE).limit(_PAGE_SIZE)
        )).all()

        rejected_total = (await session.execute(
            select(func.count(Review.id)).where(Review.status == ReviewStatus.REJECTED)
        )).scalar_one()
        rejected_rows = (await session.execute(
            select(Review, Subject.name.label("subj_name"))
            .join(CourseSection, CourseSection.id == Review.course_section_id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .outerjoin(UserProfile, UserProfile.student_id == Review.student_id)
            .where(Review.status == ReviewStatus.REJECTED)
            .order_by(UserProfile.created_at.desc().nulls_last(), Review.created_at.desc())
            .offset((rpage - 1) * _PAGE_SIZE).limit(_PAGE_SIZE)
        )).all()
    # 語尾バリアント違いの科目（力学基礎1/力学基礎2等）は管理画面上も
    # 「力学基礎(1/2)」のようにグループ名でまとめて表示する（個々のSubjectのままだと
    # 教員が同じでも別科目に投稿されたように見えてしまうため）
    pending = [_make_review_ns(r, variant_labels.get(n, n)) for r, n in pending_rows]
    approved = [_make_review_ns(r, variant_labels.get(n, n)) for r, n in approved_rows]
    rejected = [_make_review_ns(r, variant_labels.get(n, n)) for r, n in rejected_rows]
    return templates.TemplateResponse("admin/reviews.html", {
        "request": request,
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "apage": apage,
        "atotal": approved_total,
        "atotal_pages": max(1, (approved_total + _PAGE_SIZE - 1) // _PAGE_SIZE),
        "rpage": rpage,
        "rtotal": rejected_total,
        "rtotal_pages": max(1, (rejected_total + _PAGE_SIZE - 1) // _PAGE_SIZE),
    })


@router.post("/admin/reviews/approve/{review_id}")
async def admin_review_approve(
    review_id: int,
    content: Optional[str] = Form(None),
    rating: Optional[int] = Form(None),
    ease_rating: Optional[str] = Form(None),
    grading_method: Optional[str] = Form(None),
    selected_instructor: Optional[str] = Form(None),
    nickname: Optional[str] = Form(None),
    _: str = Depends(check_admin),
):
    async with AsyncSessionLocal() as session:
        review = await session.get(Review, review_id)
        if review:
            _apply_review_edits(review, content, rating, ease_rating, grading_method, selected_instructor, nickname)
            review.status = ReviewStatus.APPROVED
            # レビュー閲覧権チケットの付与。credit_granted_atで一度きりに限定し、
            # 却下→復元→再承認のようなステータス往復があっても二重付与しない
            if review.credit_granted_at is None and review.student_id:
                profile = (await session.execute(
                    select(UserProfile).where(UserProfile.student_id == review.student_id)
                )).scalar_one_or_none()
                if profile:
                    profile.unlock_credits += REVIEW_APPROVAL_UNLOCK_CREDITS
                review.credit_granted_at = datetime.now(timezone.utc)
            await session.commit()
    cache.invalidate_review_cache()
    return RedirectResponse("/admin/reviews", status_code=303)


@router.post("/admin/reviews/update/{review_id}")
async def admin_review_update(
    review_id: int,
    content: Optional[str] = Form(None),
    rating: Optional[int] = Form(None),
    ease_rating: Optional[str] = Form(None),
    grading_method: Optional[str] = Form(None),
    selected_instructor: Optional[str] = Form(None),
    nickname: Optional[str] = Form(None),
    _: str = Depends(check_admin),
):
    # 承認済み・却下済みレビューの内容を編集する（statusは変更しない）
    async with AsyncSessionLocal() as session:
        review = await session.get(Review, review_id)
        if review:
            _apply_review_edits(review, content, rating, ease_rating, grading_method, selected_instructor, nickname)
            await session.commit()
    cache.invalidate_review_cache()
    return RedirectResponse("/admin/reviews", status_code=303)


async def _get_or_create_course_section(session, subject_id: int, instructor_name: str) -> CourseSection:
    name = normalize_instructor_name(instructor_name)
    instructor = (await session.execute(
        select(Instructor).where(Instructor.name == name)
    )).scalar_one_or_none()
    if instructor is None:
        instructor = Instructor(name=name)
        session.add(instructor)
        await session.flush()
    cs = (await session.execute(
        select(CourseSection).where(
            CourseSection.subject_id == subject_id,
            CourseSection.instructor_id == instructor.id,
        )
    )).scalar_one_or_none()
    if cs is None:
        cs = CourseSection(subject_id=subject_id, instructor_id=instructor.id)
        session.add(cs)
        await session.flush()
    return cs


@router.post("/admin/reviews/reassign/{review_id}")
async def admin_review_reassign(
    review_id: int,
    subject_id: int = Form(...),
    instructor_name: str = Form(...),
    _: str = Depends(check_admin),
):
    # 誤った科目・担当教員でレビューが投稿された場合に、管理者が正しい組み合わせへ
    # 付け替える（お問い合わせ対応用）。course_section_id自体を丸ごと差し替えるため、
    # 対応する組み合わせのcourse_sectionが無ければ新規作成する（教員追加と同じ扱い）
    name = instructor_name.strip()
    if not name:
        return RedirectResponse("/admin/reviews", status_code=303)
    async with AsyncSessionLocal() as session:
        review = await session.get(Review, review_id)
        subject = await session.get(Subject, subject_id)
        if review and subject:
            cs = await _get_or_create_course_section(session, subject_id, name)
            review.course_section_id = cs.id
            review.selected_instructor = normalize_instructor_name(name)
            await session.commit()
    cache.invalidate_review_cache()
    cache.invalidate_courses_cache()
    cache.invalidate_full_pairs_cache()
    return RedirectResponse("/admin/reviews", status_code=303)


@router.post("/admin/reviews/reject/{review_id}")
async def admin_review_reject(review_id: int, _: str = Depends(check_admin)):
    # 投稿レビューは削除しない方針のため、物理削除ではなくstatus='rejected'にする
    # 支払い済み（payment_request_id紐付き）のレビューは帳簿保護のため状態変更不可
    async with AsyncSessionLocal() as session:
        review = await session.get(Review, review_id)
        if review and review.payment_request_id is None:
            review.status = ReviewStatus.REJECTED
            await session.commit()
    cache.invalidate_review_cache()
    return RedirectResponse("/admin/reviews", status_code=303)


@router.post("/admin/reviews/restore/{review_id}")
async def admin_review_restore(review_id: int, _: str = Depends(check_admin)):
    # 承認済み・却下済みレビューを待機中に戻す
    # 支払い済み（payment_request_id紐付き）のレビューは帳簿保護のため状態変更不可
    async with AsyncSessionLocal() as session:
        review = await session.get(Review, review_id)
        if review and review.payment_request_id is None:
            review.status = ReviewStatus.PENDING
            await session.commit()
    cache.invalidate_review_cache()
    return RedirectResponse("/admin/reviews", status_code=303)
