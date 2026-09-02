"""レビュー閲覧UX改善(2026-09-02)のE2Eテスト。

/api/course/{id}が返す評価分布(rating_distribution/ease_distribution)と、
閲覧者本人の投稿を示すis_mineフラグが正しく計算されることを検証する。
"""
import pytest

import routers.liff_api as liff_api
from models import CourseSection, Instructor, Review, ReviewStatus, Subject, SubjectUnlock, UserProfile

UID = "U" + "3" * 32


def _fake_verify(monkeypatch, user_id: str = UID):
    async def _verify(id_token, request=None):
        return user_id if id_token == "valid-token" else None
    monkeypatch.setattr(liff_api, "verify_liff_id_token", _verify)


async def _seed_course_with_reviews(test_sessionmaker, *, my_student_id: str):
    async with test_sessionmaker() as session:
        subj = Subject(name="ミクロ経済学", faculty="経営学部", category="教養")
        session.add(subj)
        await session.flush()
        instr = Instructor(name="鈴木一郎")
        session.add(instr)
        await session.flush()
        cs = CourseSection(subject_id=subj.id, instructor_id=instr.id)
        session.add(cs)
        await session.flush()

        session.add(UserProfile(
            line_user_id=UID, name="太郎", student_id=my_student_id,
            faculty="経営学部", department="経営学科",
        ))
        session.add(SubjectUnlock(line_user_id=UID, subject_id=subj.id))

        # 自分の投稿(rating=5, ease=SS) + 他人の投稿2件(rating=3/ease=A, rating=3/ease=None)
        session.add(Review(
            course_section_id=cs.id, student_id=my_student_id,
            status=ReviewStatus.APPROVED, rating=5, ease_rating="SS",
            selected_instructor="鈴木一郎", academic_year=2026,
        ))
        session.add(Review(
            course_section_id=cs.id, student_id="9999999X",
            status=ReviewStatus.APPROVED, rating=3, ease_rating="A",
            selected_instructor="鈴木一郎", academic_year=2025,
        ))
        session.add(Review(
            course_section_id=cs.id, student_id="8888888X",
            status=ReviewStatus.APPROVED, rating=3, ease_rating=None,
            selected_instructor="鈴木一郎", academic_year=2024,
        ))
        await session.commit()
        return subj.id


@pytest.mark.asyncio
async def test_course_api_returns_rating_and_ease_distribution(http_client_factory, monkeypatch, test_sessionmaker):
    course_id = await _seed_course_with_reviews(test_sessionmaker, my_student_id="2345678S")
    _fake_verify(monkeypatch)
    client = http_client_factory(liff_api, monkeypatch)

    resp = await client.get(f"/api/course/{course_id}", params={"id_token": "valid-token"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["locked"] is False
    assert data["review_count"] == 3
    assert data["rating_distribution"] == {"5": 1, "3": 2}
    assert data["ease_distribution"] == {"SS": 1, "A": 1}
    # avg_rating = (5+3+3)/3
    assert data["avg_rating"] == pytest.approx(11 / 3)


@pytest.mark.asyncio
async def test_course_api_flags_own_review_as_mine(http_client_factory, monkeypatch, test_sessionmaker):
    course_id = await _seed_course_with_reviews(test_sessionmaker, my_student_id="2345678S")
    _fake_verify(monkeypatch)
    client = http_client_factory(liff_api, monkeypatch)

    resp = await client.get(f"/api/course/{course_id}", params={"id_token": "valid-token"})
    data = resp.json()

    mine = [r for r in data["reviews"] if r["is_mine"]]
    others = [r for r in data["reviews"] if not r["is_mine"]]
    assert len(mine) == 1
    assert mine[0]["rating"] == 5
    assert len(others) == 2


@pytest.mark.asyncio
async def test_course_api_no_mine_flag_without_login(http_client_factory, monkeypatch, test_sessionmaker):
    """id_token無し(uid確定なし)の場合はis_mineが常にfalseであること。"""
    course_id = await _seed_course_with_reviews(test_sessionmaker, my_student_id="2345678S")
    # SubjectUnlockはUID紐づけなので、未ログインでは施錠されlockedになるはず
    client = http_client_factory(liff_api, monkeypatch)

    resp = await client.get(f"/api/course/{course_id}")
    data = resp.json()
    assert data["locked"] is True
    assert data["reviews"] == []
