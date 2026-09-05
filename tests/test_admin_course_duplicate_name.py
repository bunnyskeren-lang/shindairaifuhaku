"""管理画面の科目編集で「全く同じ科目名を別分類に登録する」フローのテスト。

2026-09-05: 科目名・学部・学科が完全に一致する別科目が既に存在する場合、確認なしでは
保存させず（IntegrityErrorではなく明示的なduplicate_nameエラーを返す）、確認後
（force_duplicate）は保存した上で既存科目の承認済みレビューを複製して見せる
（買取対象からは常に除外する）仕様を検証する。
"""
import pytest
from sqlalchemy import select

import routers.admin.courses as admin_courses
from core.config import ADMIN_COOKIE
from core.security import make_admin_token
from models import CourseSection, Instructor, Review, ReviewStatus, Subject


def _admin_client(http_client_factory, monkeypatch):
    client = http_client_factory(admin_courses, monkeypatch)
    client.cookies.set(ADMIN_COOKIE, make_admin_token())
    return client


async def _seed(test_sessionmaker):
    """既存科目(分類「旧分類」)に承認済みレビュー1件、編集対象の科目(別名・別分類)を作成する。"""
    async with test_sessionmaker() as session:
        existing = Subject(name="経済学入門", faculty="経済学部", department="", classification="旧分類", category="専門")
        session.add(existing)
        await session.flush()
        instr = Instructor(name="佐藤教授")
        session.add(instr)
        await session.flush()
        cs = CourseSection(subject_id=existing.id, instructor_id=instr.id)
        session.add(cs)
        await session.flush()
        review = Review(
            course_section_id=cs.id,
            content="分かりやすい授業でした",
            rating=5,
            ease_rating="A",
            submitter_name="投稿花子",
            student_id="1234567X",
            status=ReviewStatus.APPROVED,
        )
        session.add(review)

        target = Subject(name="経済学入門（旧名）", faculty="経済学部", department="", classification="新分類", category="専門")
        session.add(target)
        await session.commit()
        await session.refresh(existing)
        await session.refresh(target)
        await session.refresh(review)
        return existing.id, target.id, review.id


@pytest.mark.asyncio
async def test_duplicate_name_requires_confirmation(http_client_factory, monkeypatch, test_sessionmaker):
    _, target_id, _ = await _seed(test_sessionmaker)
    client = _admin_client(http_client_factory, monkeypatch)

    resp = await client.post(
        f"/admin/courses/update/{target_id}",
        headers={"X-Requested-With": "XMLHttpRequest"},
        data={
            "name": "経済学入門", "classification": "新分類", "category": "専門",
            "faculty": "経済学部", "department": "",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "duplicate_name"
    assert "旧分類" in body["message"]

    async with test_sessionmaker() as session:
        target = await session.get(Subject, target_id)
        assert target.name == "経済学入門（旧名）"  # 確認前なので保存されていない


@pytest.mark.asyncio
async def test_duplicate_name_force_copies_approved_reviews(http_client_factory, monkeypatch, test_sessionmaker):
    _, target_id, review_id = await _seed(test_sessionmaker)
    client = _admin_client(http_client_factory, monkeypatch)

    resp = await client.post(
        f"/admin/courses/update/{target_id}",
        headers={"X-Requested-With": "XMLHttpRequest"},
        data={
            "name": "経済学入門", "classification": "新分類", "category": "専門",
            "faculty": "経済学部", "department": "", "force_duplicate": "1",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    async with test_sessionmaker() as session:
        target = await session.get(Subject, target_id)
        assert target.name == "経済学入門"
        assert target.classification == "新分類"

        target_cs = (await session.execute(
            select(CourseSection).where(CourseSection.subject_id == target_id)
        )).scalars().all()
        assert len(target_cs) == 1
        copied = (await session.execute(
            select(Review).where(Review.course_section_id == target_cs[0].id)
        )).scalars().first()
        assert copied is not None
        assert copied.copied_from_review_id == review_id
        assert copied.content == "分かりやすい授業でした"
        assert copied.status == ReviewStatus.APPROVED
        assert copied.payment_request_id is None
        assert copied.credit_granted_at is None

        # 元のレビューは影響を受けない
        original = await session.get(Review, review_id)
        assert original.copied_from_review_id is None


@pytest.mark.asyncio
async def test_duplicate_name_force_is_idempotent(http_client_factory, monkeypatch, test_sessionmaker):
    """同じ確認操作を誤って2回送っても、コピーが重複して増えないこと。"""
    _, target_id, review_id = await _seed(test_sessionmaker)
    client = _admin_client(http_client_factory, monkeypatch)

    form = {
        "name": "経済学入門", "classification": "新分類", "category": "専門",
        "faculty": "経済学部", "department": "", "force_duplicate": "1",
    }
    for _ in range(2):
        resp = await client.post(
            f"/admin/courses/update/{target_id}",
            headers={"X-Requested-With": "XMLHttpRequest"},
            data=form,
        )
        assert resp.json() == {"ok": True}

    async with test_sessionmaker() as session:
        target_cs = (await session.execute(
            select(CourseSection).where(CourseSection.subject_id == target_id)
        )).scalars().all()
        copies = (await session.execute(
            select(Review).where(
                Review.course_section_id.in_([c.id for c in target_cs]),
                Review.copied_from_review_id == review_id,
            )
        )).scalars().all()
        assert len(copies) == 1
