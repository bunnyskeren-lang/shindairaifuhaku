"""routers/admin/reviews.py の管理画面レビュー承認フローのE2Eテスト。

reviews.status(pending/approved/rejected)の3値遷移(承認/却下/復元/編集)と、
承認済み・却下タブのページネーションを実HTTPリクエスト経由で検証する。
"""
import pytest
from sqlalchemy import select

import routers.admin.reviews as admin_reviews
from core.config import ADMIN_COOKIE
from core.security import make_admin_token
from models import CourseSection, Instructor, Review, ReviewStatus, Subject


async def _seed_review(test_sessionmaker, *, status: str, content: str = "普通でした", course_name: str = "経営管理") -> int:
    async with test_sessionmaker() as session:
        subj = (await session.execute(select(Subject).where(Subject.name == course_name))).scalar_one_or_none()
        if subj is None:
            subj = Subject(name=course_name, faculty="経営学部", category="専門")
            session.add(subj)
            await session.flush()
            instr = Instructor(name="山田太郎")
            session.add(instr)
            await session.flush()
            cs = CourseSection(subject_id=subj.id, instructor_id=instr.id)
            session.add(cs)
            await session.flush()
        else:
            cs = (await session.execute(select(CourseSection).where(CourseSection.subject_id == subj.id))).scalars().first()
        review = Review(
            course_section_id=cs.id,
            content=content,
            rating=3,
            ease_rating="A",
            submitter_name="投稿太郎",
            status=status,
        )
        session.add(review)
        await session.commit()
        await session.refresh(review)
        return review.id


def _admin_client(http_client_factory, monkeypatch):
    client = http_client_factory(admin_reviews, monkeypatch)
    client.cookies.set(ADMIN_COOKIE, make_admin_token())
    return client


@pytest.mark.asyncio
async def test_reject_does_not_delete_but_sets_status(http_client_factory, monkeypatch, test_sessionmaker):
    review_id = await _seed_review(test_sessionmaker, status=ReviewStatus.PENDING)
    client = _admin_client(http_client_factory, monkeypatch)

    resp = await client.post(f"/admin/reviews/reject/{review_id}")
    assert resp.status_code == 303

    async with test_sessionmaker() as session:
        review = await session.get(Review, review_id)
        assert review is not None
        assert review.status == ReviewStatus.REJECTED


@pytest.mark.asyncio
async def test_restore_moves_rejected_back_to_pending(http_client_factory, monkeypatch, test_sessionmaker):
    review_id = await _seed_review(test_sessionmaker, status=ReviewStatus.REJECTED)
    client = _admin_client(http_client_factory, monkeypatch)

    resp = await client.post(f"/admin/reviews/restore/{review_id}")
    assert resp.status_code == 303

    async with test_sessionmaker() as session:
        review = await session.get(Review, review_id)
        assert review.status == ReviewStatus.PENDING


@pytest.mark.asyncio
async def test_restore_moves_approved_back_to_pending(http_client_factory, monkeypatch, test_sessionmaker):
    review_id = await _seed_review(test_sessionmaker, status=ReviewStatus.APPROVED)
    client = _admin_client(http_client_factory, monkeypatch)

    resp = await client.post(f"/admin/reviews/restore/{review_id}")
    assert resp.status_code == 303

    async with test_sessionmaker() as session:
        review = await session.get(Review, review_id)
        assert review.status == ReviewStatus.PENDING


@pytest.mark.asyncio
async def test_update_edits_content_without_changing_status(http_client_factory, monkeypatch, test_sessionmaker):
    review_id = await _seed_review(test_sessionmaker, status=ReviewStatus.APPROVED, content="元のコメント")
    client = _admin_client(http_client_factory, monkeypatch)

    resp = await client.post(f"/admin/reviews/update/{review_id}", data={
        "content": "編集後のコメント",
        "rating": "5",
    })
    assert resp.status_code == 303

    async with test_sessionmaker() as session:
        review = await session.get(Review, review_id)
        assert review.status == ReviewStatus.APPROVED
        assert review.content == "編集後のコメント"
        assert review.rating == 5


@pytest.mark.asyncio
async def test_update_edits_rejected_review_without_changing_status(http_client_factory, monkeypatch, test_sessionmaker):
    review_id = await _seed_review(test_sessionmaker, status=ReviewStatus.REJECTED, content="元のコメント")
    client = _admin_client(http_client_factory, monkeypatch)

    resp = await client.post(f"/admin/reviews/update/{review_id}", data={"content": "却下後に編集"})
    assert resp.status_code == 303

    async with test_sessionmaker() as session:
        review = await session.get(Review, review_id)
        assert review.status == ReviewStatus.REJECTED
        assert review.content == "却下後に編集"


@pytest.mark.asyncio
async def test_reassign_moves_review_to_existing_course_section(http_client_factory, monkeypatch, test_sessionmaker):
    review_id = await _seed_review(test_sessionmaker, status=ReviewStatus.APPROVED)
    async with test_sessionmaker() as session:
        # 間違ったレビューの投稿先とは別の、正しい科目×教員の組み合わせを事前に用意しておく
        correct_subj = Subject(name="国際経営論", faculty="経営学部", category="専門")
        session.add(correct_subj)
        await session.flush()
        correct_instr = Instructor(name="鈴木花子")
        session.add(correct_instr)
        await session.flush()
        correct_cs = CourseSection(subject_id=correct_subj.id, instructor_id=correct_instr.id)
        session.add(correct_cs)
        await session.commit()
        correct_subject_id = correct_subj.id
        correct_cs_id = correct_cs.id

    client = _admin_client(http_client_factory, monkeypatch)
    resp = await client.post(f"/admin/reviews/reassign/{review_id}", data={
        "subject_id": str(correct_subject_id),
        "instructor_name": "鈴木花子",
    })
    assert resp.status_code == 303

    async with test_sessionmaker() as session:
        review = await session.get(Review, review_id)
        assert review.course_section_id == correct_cs_id
        assert review.selected_instructor == "鈴木花子"


@pytest.mark.asyncio
async def test_reassign_creates_course_section_and_instructor_when_missing(http_client_factory, monkeypatch, test_sessionmaker):
    review_id = await _seed_review(test_sessionmaker, status=ReviewStatus.PENDING)
    async with test_sessionmaker() as session:
        target_subj = Subject(name="国際法概論", faculty="国際人間科学部", category="専門")
        session.add(target_subj)
        await session.commit()
        target_subject_id = target_subj.id

    client = _admin_client(http_client_factory, monkeypatch)
    resp = await client.post(f"/admin/reviews/reassign/{review_id}", data={
        "subject_id": str(target_subject_id),
        "instructor_name": "新任太郎",
    })
    assert resp.status_code == 303

    async with test_sessionmaker() as session:
        review = await session.get(Review, review_id)
        assert review.selected_instructor == "新任太郎"
        cs = await session.get(CourseSection, review.course_section_id)
        assert cs is not None
        assert cs.subject_id == target_subject_id
        instr = await session.get(Instructor, cs.instructor_id)
        assert instr.name == "新任太郎"


@pytest.mark.asyncio
async def test_admin_reviews_page_paginates_approved_and_rejected(http_client_factory, monkeypatch, test_sessionmaker):
    for i in range(3):
        await _seed_review(test_sessionmaker, status=ReviewStatus.APPROVED, content=f"承認{i}")
    for i in range(2):
        await _seed_review(test_sessionmaker, status=ReviewStatus.REJECTED, content=f"却下{i}")
    client = _admin_client(http_client_factory, monkeypatch)
    monkeypatch.setattr(admin_reviews, "_PAGE_SIZE", 2)

    resp = await client.get("/admin/reviews")
    assert resp.status_code == 200
    assert "全3件中" in resp.text
    assert "全2件中" in resp.text

    resp_page2 = await client.get("/admin/reviews?apage=2")
    assert resp_page2.status_code == 200
