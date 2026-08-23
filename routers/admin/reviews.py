from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select

from core import cache
from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, Review, Subject

router = APIRouter()


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
                    Review.status == "pending",
                )
            )
            await session.commit()
    return RedirectResponse("/admin/courses", status_code=303)


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
    )


@router.get("/admin/reviews", response_class=HTMLResponse)
async def admin_reviews(request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        pending_rows = (await session.execute(
            select(Review, Subject.name.label("subj_name"))
            .join(CourseSection, CourseSection.id == Review.course_section_id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .where(Review.status == "pending")
            .order_by(Review.created_at.desc())
        )).all()
        approved_rows = (await session.execute(
            select(Review, Subject.name.label("subj_name"))
            .join(CourseSection, CourseSection.id == Review.course_section_id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .where(Review.status == "approved")
            .order_by(Review.created_at.desc())
            .limit(50)
        )).all()
        rejected_rows = (await session.execute(
            select(Review, Subject.name.label("subj_name"))
            .join(CourseSection, CourseSection.id == Review.course_section_id)
            .join(Subject, Subject.id == CourseSection.subject_id)
            .where(Review.status == "rejected")
            .order_by(Review.created_at.desc())
            .limit(50)
        )).all()
    pending = [_make_review_ns(r, n) for r, n in pending_rows]
    approved = [_make_review_ns(r, n) for r, n in approved_rows]
    rejected = [_make_review_ns(r, n) for r, n in rejected_rows]
    return templates.TemplateResponse("admin/reviews.html", {
        "request": request,
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
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
    # content/rating等はフォームから送られてきた場合のみ上書きする
    # （courses.html の簡易承認ボタンはこれらを送らないため、その場合は既存の内容のまま承認する）
    async with AsyncSessionLocal() as session:
        review = await session.get(Review, review_id)
        if review:
            if content is not None:
                review.content = content.strip() or None
            if rating is not None:
                review.rating = rating
            if ease_rating is not None:
                review.ease_rating = ease_rating or None
            if grading_method is not None:
                review.grading_method = grading_method.strip() or None
            if selected_instructor is not None:
                review.selected_instructor = selected_instructor.strip() or None
            if nickname is not None:
                review.nickname = nickname.strip() or None
            review.status = "approved"
            await session.commit()
    cache.invalidate_review_cache()
    return RedirectResponse("/admin/reviews", status_code=303)


@router.post("/admin/reviews/reject/{review_id}")
async def admin_review_reject(review_id: int, _: str = Depends(check_admin)):
    # 投稿レビューは削除しない方針のため、物理削除ではなくstatus='rejected'にする
    async with AsyncSessionLocal() as session:
        review = await session.get(Review, review_id)
        if review:
            review.status = "rejected"
            await session.commit()
    cache.invalidate_review_cache()
    return RedirectResponse("/admin/reviews", status_code=303)


@router.post("/admin/reviews/restore/{review_id}")
async def admin_review_restore(review_id: int, _: str = Depends(check_admin)):
    # 却下済みレビューを待機中に戻す
    async with AsyncSessionLocal() as session:
        review = await session.get(Review, review_id)
        if review:
            review.status = "pending"
            await session.commit()
    cache.invalidate_review_cache()
    return RedirectResponse("/admin/reviews", status_code=303)
