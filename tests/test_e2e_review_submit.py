"""review_submit_api.py /submit (レビュー投稿)のAPI経由E2Eテスト。

会員登録済み（/api/register経由でfaculty/departmentまで入力済み）のユーザーによる
フォーム投稿→バリデーション→レビュー保存という一連のフローと、主要な異常系
(未登録・不正評価値・学籍番号形式・認証失敗・登録情報との不一致)を実HTTPリクエスト経由で検証する。
"""
import pytest
from sqlalchemy import select

import routers.review_submit_api as review_submit_api
from models import CourseSection, Instructor, Review, Subject, UserProfile

UID = "U65326572657669657765723100000000"


def _fake_verify(monkeypatch, user_id: str = UID):
    async def _verify(id_token, request=None):
        return user_id if id_token == "valid-token" else None
    monkeypatch.setattr(review_submit_api, "verify_liff_id_token", _verify)


def _stub_push_notification(monkeypatch):
    async def _noop(**kwargs):
        return None
    monkeypatch.setattr(review_submit_api, "send_push_notification", _noop)


async def _seed_course(test_sessionmaker, name="経営管理", instructor="山田太郎", category="教養"):
    async with test_sessionmaker() as session:
        subj = Subject(name=name, faculty="経営学部", category=category)
        session.add(subj)
        await session.flush()
        instr = Instructor(name=instructor)
        session.add(instr)
        await session.flush()
        session.add(CourseSection(subject_id=subj.id, instructor_id=instr.id))
        await session.commit()


async def _seed_variant_courses(test_sessionmaker, names, instructor="山田太郎", faculty="経営学部", category="教養"):
    """同一教員が担当する末尾バリアント違いの科目群（例: 線形代数1/線形代数2）をシードする。"""
    async with test_sessionmaker() as session:
        instr = Instructor(name=instructor)
        session.add(instr)
        await session.flush()
        for name in names:
            subj = Subject(name=name, faculty=faculty, category=category)
            session.add(subj)
            await session.flush()
            session.add(CourseSection(subject_id=subj.id, instructor_id=instr.id))
        await session.commit()


async def _seed_variant_courses_multi_instructor(
    test_sessionmaker, name_instructor_pairs, faculty="教養教育院", category="教養",
):
    """末尾バリアント違いの科目群を、科目ごとに別々の担当教員でシードする
    （語学初級科目のようにA面/B面で教員が変わるケースの再現用）。"""
    async with test_sessionmaker() as session:
        instructors_by_name: dict[str, Instructor] = {}
        for name, instructor in name_instructor_pairs:
            subj = Subject(name=name, faculty=faculty, category=category)
            session.add(subj)
            instr = instructors_by_name.get(instructor)
            if instr is None:
                instr = Instructor(name=instructor)
                session.add(instr)
                instructors_by_name[instructor] = instr
            await session.flush()
            session.add(CourseSection(subject_id=subj.id, instructor_id=instr.id))
        await session.commit()


async def _seed_profile(test_sessionmaker, user_id: str = UID, student_id: str = "2345678S", name: str = "神戸太郎"):
    async with test_sessionmaker() as session:
        session.add(UserProfile(
            line_user_id=user_id, name=name, student_id=student_id,
            faculty="経営学部", department="経営学科",
        ))
        await session.commit()


VALID_FORM = {
    "course_name": "経営管理",
    "rating": "4",
    "ease_rating": "A",
    "comment": "とても勉強になりました",
    "id_token": "valid-token",
    "student_id": "2345678S",
    "academic_year": "2026",
}


@pytest.mark.asyncio
async def test_submit_creates_review_for_registered_user(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    await _seed_profile(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    resp = await client.post("/submit", data=VALID_FORM)
    assert resp.status_code == 200

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 1
        assert reviews[0].content == "とても勉強になりました"
        assert reviews[0].status == "pending"
        assert reviews[0].submitter_name == "神戸太郎"


@pytest.mark.asyncio
async def test_submit_senmon_course_returns_400(http_client_factory, monkeypatch, test_sessionmaker):
    """レビュー投稿は教養科目(category=='教養')のみ受け付け、専門科目は拒否する。"""
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker, category="専門")
    await _seed_profile(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    resp = await client.post("/submit", data=VALID_FORM)
    assert resp.status_code == 400
    assert "教養科目のみ" in resp.text

    async with test_sessionmaker() as session:
        assert (await session.execute(select(Review))).scalars().first() is None


@pytest.mark.asyncio
async def test_submit_without_registered_profile_returns_400(http_client_factory, monkeypatch, test_sessionmaker):
    """会員登録(/register)を経由せず直接/submitを叩く迂回策への防御を確認する。"""
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    resp = await client.post("/submit", data=VALID_FORM)
    assert resp.status_code == 400

    async with test_sessionmaker() as session:
        assert (await session.execute(select(Review))).scalars().first() is None


@pytest.mark.asyncio
async def test_submit_with_incomplete_profile_returns_400(http_client_factory, monkeypatch, test_sessionmaker):
    """faculty/department未入力（学部・学科未選択のまま）の不完全なプロフィールでは拒否される。"""
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    async with test_sessionmaker() as session:
        session.add(UserProfile(line_user_id=UID, name="神戸太郎", student_id="2345678S"))
        await session.commit()
    client = http_client_factory(review_submit_api, monkeypatch)

    resp = await client.post("/submit", data=VALID_FORM)
    assert resp.status_code == 400


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
async def test_submit_student_id_mismatch_with_own_profile_returns_400(http_client_factory, monkeypatch, test_sessionmaker):
    """フォームに入力した学籍番号が、自分の会員登録情報の学籍番号と食い違うケース。"""
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    await _seed_profile(test_sessionmaker, student_id="9999999S")
    client = http_client_factory(review_submit_api, monkeypatch)

    resp = await client.post("/submit", data=VALID_FORM)  # VALID_FORMのstudent_idは2345678S
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
        user_id = f"U{rating}".ljust(33, "0")
        sid = f"234567{rating}S"
        _fake_verify(monkeypatch, user_id=user_id)
        _stub_push_notification(monkeypatch)
        await _seed_course(test_sessionmaker, name=f"科目{rating}", instructor=f"講師{rating}")
        await _seed_profile(test_sessionmaker, user_id=user_id, student_id=sid)
        client = http_client_factory(review_submit_api, monkeypatch)

        form = dict(VALID_FORM, course_name=f"科目{rating}", rating=rating, student_id=sid)
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


# ── 末尾バリアントグループ（例: 線形代数1/2）の募集枠共有 ──────────────────────────

@pytest.mark.asyncio
async def test_submit_variant_group_shares_recruitment_slot(http_client_factory, monkeypatch, test_sessionmaker):
    """同一教員が担当する線形代数1/線形代数2は表示上1科目に統合されるため、
    先に線形代数1で枠が埋まったら線形代数2への投稿も上限扱いで拒否されるべき。"""
    _fake_verify(monkeypatch, user_id="U1".ljust(33, "0"))
    _stub_push_notification(monkeypatch)
    await _seed_variant_courses(test_sessionmaker, ["線形代数1", "線形代数2"])
    await _seed_profile(test_sessionmaker, user_id="U1".ljust(33, "0"), student_id="1111111S")
    client = http_client_factory(review_submit_api, monkeypatch)

    resp1 = await client.post("/submit", data=dict(VALID_FORM, course_name="線形代数1", student_id="1111111S"))
    assert resp1.status_code == 200

    _fake_verify(monkeypatch, user_id="U2".ljust(33, "0"))
    await _seed_profile(test_sessionmaker, user_id="U2".ljust(33, "0"), student_id="2222222S", name="別学生")
    resp2 = await client.post("/submit", data=dict(VALID_FORM, course_name="線形代数2", student_id="2222222S"))
    assert resp2.status_code == 400
    assert "上限に達した" in resp2.text

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 1


# ── 語学初級科目（独語/仏語/露語/中国語）の1ユーザー2件までの特例 ──────────────────

@pytest.mark.asyncio
async def test_submit_language_subject_allows_two_reviews_different_instructors(
    http_client_factory, monkeypatch, test_sessionmaker,
):
    """ドイツ語初級はA面/B面で教員が変わるため、同じ学生が異なる担当教員へ2件までは投稿できる。"""
    uid = "U1".ljust(33, "0")
    _fake_verify(monkeypatch, user_id=uid)
    _stub_push_notification(monkeypatch)
    await _seed_variant_courses_multi_instructor(test_sessionmaker, [
        ("ドイツ語初級A1", "田中"), ("ドイツ語初級B1", "佐藤"),
    ])
    await _seed_profile(test_sessionmaker, user_id=uid, student_id="1111111S")
    client = http_client_factory(review_submit_api, monkeypatch)

    resp1 = await client.post("/submit", data=dict(
        VALID_FORM, course_name="ドイツ語初級A1", selected_instructor="田中", student_id="1111111S",
    ))
    assert resp1.status_code == 200

    resp2 = await client.post("/submit", data=dict(
        VALID_FORM, course_name="ドイツ語初級B1", selected_instructor="佐藤", student_id="1111111S",
    ))
    assert resp2.status_code == 200

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 2


@pytest.mark.asyncio
async def test_submit_language_subject_rejects_third_review(http_client_factory, monkeypatch, test_sessionmaker):
    """同じ学生が3人目の教員へ3件目を投稿しようとすると上限エラーになる。"""
    uid = "U1".ljust(33, "0")
    _fake_verify(monkeypatch, user_id=uid)
    _stub_push_notification(monkeypatch)
    await _seed_variant_courses_multi_instructor(test_sessionmaker, [
        ("ドイツ語初級A1", "田中"), ("ドイツ語初級B1", "佐藤"), ("ドイツ語初級A2", "鈴木"),
    ])
    await _seed_profile(test_sessionmaker, user_id=uid, student_id="1111111S")
    client = http_client_factory(review_submit_api, monkeypatch)

    resp1 = await client.post("/submit", data=dict(
        VALID_FORM, course_name="ドイツ語初級A1", selected_instructor="田中", student_id="1111111S",
    ))
    assert resp1.status_code == 200
    resp2 = await client.post("/submit", data=dict(
        VALID_FORM, course_name="ドイツ語初級B1", selected_instructor="佐藤", student_id="1111111S",
    ))
    assert resp2.status_code == 200

    resp3 = await client.post("/submit", data=dict(
        VALID_FORM, course_name="ドイツ語初級A2", selected_instructor="鈴木", student_id="1111111S",
    ))
    assert resp3.status_code == 400
    assert "2件までです" in resp3.text

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 2


@pytest.mark.asyncio
async def test_submit_language_subject_still_blocks_same_instructor_reuse(
    http_client_factory, monkeypatch, test_sessionmaker,
):
    """2件目の投稿でも同じ教員は選べない（既存のdup_reviewチェックがグループ内でも効く）。"""
    uid = "U1".ljust(33, "0")
    _fake_verify(monkeypatch, user_id=uid)
    _stub_push_notification(monkeypatch)
    await _seed_variant_courses_multi_instructor(test_sessionmaker, [
        ("ドイツ語初級A1", "田中"), ("ドイツ語初級A2", "田中"),
    ])
    await _seed_profile(test_sessionmaker, user_id=uid, student_id="1111111S")
    client = http_client_factory(review_submit_api, monkeypatch)

    resp1 = await client.post("/submit", data=dict(
        VALID_FORM, course_name="ドイツ語初級A1", selected_instructor="田中", student_id="1111111S",
    ))
    assert resp1.status_code == 200

    resp2 = await client.post("/submit", data=dict(
        VALID_FORM, course_name="ドイツ語初級A2", selected_instructor="田中", student_id="1111111S",
    ))
    assert resp2.status_code == 400
    assert "既にレビューを投稿済み" in resp2.text

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 1


@pytest.mark.asyncio
async def test_submit_non_language_variant_group_still_unaffected(http_client_factory, monkeypatch, test_sessionmaker):
    """語学初級以外のバリアントグループ（例: 線形代数）は今回の特例対象外で、教員を変えれば
    従来通り3件目以降も投稿できる（この特例が対象科目以外へ波及していないことの確認）。"""
    uid = "U1".ljust(33, "0")
    _fake_verify(monkeypatch, user_id=uid)
    _stub_push_notification(monkeypatch)
    await _seed_variant_courses_multi_instructor(test_sessionmaker, [
        ("線形代数1", "田中"), ("線形代数2", "佐藤"), ("線形代数3", "鈴木"),
    ], faculty="経営学部")
    await _seed_profile(test_sessionmaker, user_id=uid, student_id="1111111S")
    client = http_client_factory(review_submit_api, monkeypatch)

    for name, instructor in [("線形代数1", "田中"), ("線形代数2", "佐藤"), ("線形代数3", "鈴木")]:
        resp = await client.post("/submit", data=dict(
            VALID_FORM, course_name=name, selected_instructor=instructor, student_id="1111111S",
        ))
        assert resp.status_code == 200, f"{name}/{instructor} should be accepted"

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 3


@pytest.mark.asyncio
async def test_submit_variant_group_blocks_same_student_dup(http_client_factory, monkeypatch, test_sessionmaker):
    """同じ学生が線形代数1に投稿済みなら、実質同じ授業である線形代数2への投稿も
    「既に投稿済み」として拒否されるべき（別のsubject_idへの迂回で上限をすり抜けられない）。"""
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_variant_courses(test_sessionmaker, ["線形代数1", "線形代数2"])
    await _seed_profile(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    resp1 = await client.post("/submit", data=dict(VALID_FORM, course_name="線形代数1"))
    assert resp1.status_code == 200

    resp2 = await client.post("/submit", data=dict(VALID_FORM, course_name="線形代数2"))
    assert resp2.status_code == 400
    assert "投稿済み" in resp2.text

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 1


@pytest.mark.asyncio
async def test_submit_academic_year_out_of_range_returns_400(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    form = dict(VALID_FORM, academic_year="1999")
    resp = await client.post("/submit", data=form)
    assert resp.status_code == 400
