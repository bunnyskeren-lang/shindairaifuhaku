"""review_submit_api.py /submit (レビュー投稿)のAPI経由E2Eテスト。

フォーム投稿→バリデーション→初回プロフィール自動作成→レビュー保存という
一連のフローと、主要な異常系(不正評価値・学籍番号形式・認証失敗・重複学籍番号)を
実HTTPリクエスト経由で検証する。
"""
import pytest
from sqlalchemy import select

import routers.review_submit_api as review_submit_api
from models import CourseSection, Instructor, Review, Subject, UserProfile


def _fake_verify(monkeypatch, user_id: str = "U65326572657669657765723100000000"):
    async def _verify(id_token, request=None):
        return user_id if id_token == "valid-token" else None
    monkeypatch.setattr(review_submit_api, "verify_liff_id_token", _verify)


def _stub_push_notification(monkeypatch):
    async def _noop(**kwargs):
        return None
    monkeypatch.setattr(review_submit_api, "send_push_notification", _noop)


async def _seed_course(test_sessionmaker, name="経営管理", instructor="山田太郎"):
    async with test_sessionmaker() as session:
        subj = Subject(name=name, faculty="経営学部", category="専門")
        session.add(subj)
        await session.flush()
        instr = Instructor(name=instructor)
        session.add(instr)
        await session.flush()
        session.add(CourseSection(subject_id=subj.id, instructor_id=instr.id))
        await session.commit()


VALID_FORM = {
    "course_name": "経営管理",
    "rating": "4",
    "ease_rating": "A",
    "comment": "とても勉強になりました",
    "id_token": "valid-token",
    "reg_name": "神戸太郎",
    "student_id": "2345678S",
    "academic_year": "2026",
}


@pytest.mark.asyncio
async def test_submit_creates_review_and_profile_for_new_user(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    resp = await client.post("/submit", data=VALID_FORM)
    assert resp.status_code == 200

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 1
        assert reviews[0].content == "とても勉強になりました"
        assert reviews[0].is_approved is False

        profile = await session.get(UserProfile, "U65326572657669657765723100000000")
        assert profile is not None
        assert profile.student_id == "2345678S"


@pytest.mark.asyncio
async def test_submit_unauthenticated_returns_400_with_error_page(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    form = dict(VALID_FORM, id_token="invalid-token")
    resp = await client.post("/submit", data=form)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_submit_nonexistent_course_returns_400(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    # 科目を一切登録しない
    client = http_client_factory(review_submit_api, monkeypatch)

    resp = await client.post("/submit", data=VALID_FORM)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_submit_malformed_student_id_returns_400(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    form = dict(VALID_FORM, student_id="invalid-id")
    resp = await client.post("/submit", data=form)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_submit_duplicate_student_id_different_account_returns_400(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch, user_id="U6e657775736572320000000000000000")
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    async with test_sessionmaker() as session:
        session.add(UserProfile(line_user_id="U6578697374696e677573657200000000", name="既存ユーザー", student_id="2345678S"))
        await session.commit()
    client = http_client_factory(review_submit_api, monkeypatch)

    # 別のLINEアカウント(U6e657775736572320000000000000000)が同じ学籍番号で新規投稿しようとするケース
    resp = await client.post("/submit", data=VALID_FORM)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_submit_empty_comment_returns_400(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    form = dict(VALID_FORM, comment="   ")
    resp = await client.post("/submit", data=form)
    assert resp.status_code == 400


# ── 境界値 ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_rating_boundary_values_accepted(http_client_factory, monkeypatch, test_sessionmaker):
    for rating in ("1", "5"):
        _fake_verify(monkeypatch, user_id=f"U{rating}".ljust(33, "0"))
        _stub_push_notification(monkeypatch)
        await _seed_course(test_sessionmaker, name=f"科目{rating}", instructor=f"講師{rating}")
        client = http_client_factory(review_submit_api, monkeypatch)

        form = dict(VALID_FORM, course_name=f"科目{rating}", rating=rating, student_id=f"234567{rating}S")
        resp = await client.post("/submit", data=form)
        assert resp.status_code == 200, f"rating={rating} should be accepted"


@pytest.mark.asyncio
async def test_submit_rating_out_of_range_returns_400(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    form = dict(VALID_FORM, rating="6")
    resp = await client.post("/submit", data=form)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_submit_academic_year_out_of_range_returns_400(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    form = dict(VALID_FORM, academic_year="1999")
    resp = await client.post("/submit", data=form)
    assert resp.status_code == 400
