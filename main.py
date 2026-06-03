
async def get_user_max_reviews(session: AsyncSession, user_id: str) -> int:
    pref = (await session.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )).scalar_one_or_none()
    return pref.max_reviews if pref else MAX_REVIEWS


async def get_course_flex(session: AsyncSession, course: Course, user_id: str) -> FlexMessage:
    agg = (await session.execute(
        select(func.avg(PendingReview.rating), func.count(PendingReview.id))
        .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
    )).first()
    avg_rating = float(agg[0]) if agg and agg[0] else None

    ease_rows = (await session.execute(
        select(PendingReview.ease_rating, func.count(PendingReview.id))
        .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
        .group_by(PendingReview.ease_rating)
    )).all()
    top_ease = None
    if ease_rows:
        top_ease = sorted(ease_rows, key=lambda r: EASE_ORDER.get(r[0], 99))[0][0]

    grading_row = (await session.execute(
        select(PendingReview.grading_method, func.count(PendingReview.id))
        .where(
            PendingReview.course_name == course.name,
            PendingReview.is_approved == True,
            PendingReview.grading_method.isnot(None),
        )
        .group_by(PendingReview.grading_method)
        .order_by(func.count(PendingReview.id).desc())
        .limit(1)
    )).first()
    top_grading_method = grading_row[0] if grading_row else None

    limit = await get_user_max_reviews(session, user_id)
    comments = (await session.execute(
        select(PendingReview.comment)
        .where(PendingReview.course_name == course.name, PendingReview.is_approved == True)
        .order_by(PendingReview.created_at.desc())
        .limit(limit)
    )).scalars().all()

    url = f"{REVIEW_FORM_URL}?uid={user_id}" if user_id else REVIEW_FORM_URL
    bubble = make_course_bubble(
        course.name, course.instructor, course.classification,
        avg_rating, top_ease, list(comments),
        grading_method=top_grading_method,
