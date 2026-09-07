"""review_submit_api.py /submit (レビュー投稿)のAPI経由E2Eテスト。

会員登録済み（/api/register経由でfaculty/departmentまで入力済み）のユーザーによる
フォーム投稿→バリデーション→レビュー保存という一連のフローと、主要な異常系
(未登録・不正評価値・学籍番号形式・認証失敗・登録情報との不一致)を実HTTPリクエスト経由で検証する。
"""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import routers.review_submit_api as review_submit_api
from models import CourseSection, Instructor, Review, ReviewStatus, Subject, UserProfile

UID = "U65326572657669657765723100000000"


def _fake_verify(monkeypatch, user_id: str = UID):
    async def _verify(id_token, request=None):
        return user_id if id_token == "valid-token" else None
    monkeypatch.setattr(review_submit_api, "verify_liff_id_token", _verify)


def _stub_push_notification(monkeypatch):
    async def _noop(**kwargs):
        return None
    monkeypatch.setattr(review_submit_api, "send_push_notification", _noop)


async def _seed_course(test_sessionmaker, name="経営管理", instructor="山田太郎", category="教養", faculty="経営学部"):
    async with test_sessionmaker() as session:
        subj = Subject(name=name, faculty=faculty, category=category)
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


async def _seed_hoken_gakka_courses(test_sessionmaker, name="生理学", category="教養"):
    """医学部保健学科4専攻の完全同名科目（専攻ごとに別Subject・別担当教員）をシードする。"""
    departments = [
        ("保健学科看護学専攻", "看護太郎"),
        ("保健学科理学療法学専攻", "理学花子"),
        ("保健学科作業療法学専攻", "作業次郎"),
        ("保健学科検査技術科学専攻", "検査三郎"),
    ]
    async with test_sessionmaker() as session:
        for department, instructor_name in departments:
            subj = Subject(name=name, faculty="医学部", department=department, category=category)
            session.add(subj)
            await session.flush()
            instr = Instructor(name=instructor_name)
            session.add(instr)
            await session.flush()
            session.add(CourseSection(subject_id=subj.id, instructor_id=instr.id))
        await session.commit()


async def _seed_profile(test_sessionmaker, user_id: str = UID, student_id: str = "2345678S", name: str = "神戸太郎"):
    async with test_sessionmaker() as session:
        session.add(UserProfile(
            line_user_id=user_id, name=name, student_id=student_id,
            faculty="経営学部", department="経営学科", coop_jobsite_known="はい",
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

    # 成功時は PRG（Post/Redirect/Get）で 303 → GET /submit/done
    resp = await client.post("/submit", data=VALID_FORM)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/submit/done?")

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 1
        assert reviews[0].content == "とても勉強になりました"
        assert reviews[0].status == "pending"
        assert reviews[0].submitter_name == "神戸太郎"


@pytest.mark.asyncio
async def test_submit_senmon_course_matching_faculty_creates_review(http_client_factory, monkeypatch, test_sessionmaker):
    """2026-09-07以降、専門科目でも投稿者本人の登録学部と一致すればレビュー投稿できる。"""
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    # _seed_profile の faculty は「経営学部」。同じ学部の専門科目。
    await _seed_course(test_sessionmaker, category="専門", faculty="経営学部")
    await _seed_profile(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    resp = await client.post("/submit", data=VALID_FORM)
    assert resp.status_code == 303

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 1
        assert reviews[0].status == "pending"


@pytest.mark.asyncio
async def test_submit_senmon_course_faculty_mismatch_returns_400(http_client_factory, monkeypatch, test_sessionmaker):
    """専門科目は、投稿者本人の登録学部と異なる学部のものは拒否する。"""
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    # _seed_profile の faculty は「経営学部」。別学部（法学部）の専門科目。
    await _seed_course(test_sessionmaker, category="専門", faculty="法学部")
    await _seed_profile(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    resp = await client.post("/submit", data=VALID_FORM)
    assert resp.status_code == 400
    assert "ご登録の学部" in resp.text

    async with test_sessionmaker() as session:
        assert (await session.execute(select(Review))).scalars().first() is None


@pytest.mark.asyncio
async def test_submit_kyotsu_senmon_kiso_course_creates_review(http_client_factory, monkeypatch, test_sessionmaker):
    """共通専門基礎科目（category=='専門' かつ faculty=='教養教育院'）は学部を問わず投稿できる。"""
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    # _seed_profile の faculty は「経営学部」だが、共通専門基礎は学部不問。
    await _seed_course(test_sessionmaker, category="専門", faculty="教養教育院")
    await _seed_profile(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    resp = await client.post("/submit", data=VALID_FORM)
    assert resp.status_code == 303

    async with test_sessionmaker() as session:
        assert len((await session.execute(select(Review))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_submit_non_review_category_returns_400(http_client_factory, monkeypatch, test_sessionmaker):
    """教養・専門のいずれでもないcategoryの科目は投稿を拒否する。"""
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker, category="その他")
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
        assert resp.status_code == 303, f"rating={rating} should be accepted"


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
    assert resp1.status_code == 303

    _fake_verify(monkeypatch, user_id="U2".ljust(33, "0"))
    await _seed_profile(test_sessionmaker, user_id="U2".ljust(33, "0"), student_id="2222222S", name="別学生")
    resp2 = await client.post("/submit", data=dict(VALID_FORM, course_name="線形代数2", student_id="2222222S"))
    assert resp2.status_code == 400
    assert "上限に達した" in resp2.text

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 1


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
    assert resp1.status_code == 303

    resp2 = await client.post("/submit", data=dict(VALID_FORM, course_name="線形代数2"))
    assert resp2.status_code == 400
    assert "投稿済み" in resp2.text

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 1


# ── 医学部保健学科4専攻をまたいだ完全同名科目のレビュー共有 ──────────────────────

@pytest.mark.asyncio
async def test_submit_hoken_gakka_cross_department_shares_recruitment_slot(http_client_factory, monkeypatch, test_sessionmaker):
    """看護学専攻の「生理学」で枠が埋まったら、担当教員（専攻）が別の理学療法学専攻
    「生理学」への投稿も上限扱いで拒否されるべき（専攻をまたいだ完全同名科目はレビューを共有する）。"""
    _fake_verify(monkeypatch, user_id="U1".ljust(33, "0"))
    _stub_push_notification(monkeypatch)
    await _seed_hoken_gakka_courses(test_sessionmaker)
    await _seed_profile(test_sessionmaker, user_id="U1".ljust(33, "0"), student_id="1111111S")
    client = http_client_factory(review_submit_api, monkeypatch)

    resp1 = await client.post("/submit", data=dict(
        VALID_FORM, course_name="生理学", student_id="1111111S", selected_instructor="看護太郎",
    ))
    assert resp1.status_code == 303

    _fake_verify(monkeypatch, user_id="U2".ljust(33, "0"))
    await _seed_profile(test_sessionmaker, user_id="U2".ljust(33, "0"), student_id="2222222S", name="別学生")
    resp2 = await client.post("/submit", data=dict(
        VALID_FORM, course_name="生理学", student_id="2222222S", selected_instructor="理学花子",
    ))
    assert resp2.status_code == 400
    assert "上限に達した" in resp2.text

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 1


@pytest.mark.asyncio
async def test_submit_hoken_gakka_cross_department_blocks_same_student_dup(http_client_factory, monkeypatch, test_sessionmaker):
    """同じ学生が看護学専攻の「生理学」に投稿済みなら、専攻違いの同名科目への投稿も
    「既に投稿済み」として拒否されるべき（別専攻への迂回で上限をすり抜けられない）。"""
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_hoken_gakka_courses(test_sessionmaker)
    await _seed_profile(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    resp1 = await client.post("/submit", data=dict(VALID_FORM, course_name="生理学", selected_instructor="看護太郎"))
    assert resp1.status_code == 303

    resp2 = await client.post("/submit", data=dict(VALID_FORM, course_name="生理学", selected_instructor="理学花子"))
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


@pytest.mark.asyncio
async def test_submit_same_nonce_is_idempotent(http_client_factory, monkeypatch, test_sessionmaker):
    """送信直後のアプリbg化でOS/webviewが同じPOSTを再送する事象を模す。
    同じ submit_nonce の2回目は新規レビューを作らず、1回目の成功ページ(303→/submit/done)へ流す。
    「既に投稿済み」の400エラーをユーザーに見せない。"""
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    await _seed_profile(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    form = dict(VALID_FORM, submit_nonce="fixed-nonce-abc123")
    resp1 = await client.post("/submit", data=form)
    assert resp1.status_code == 303
    resp2 = await client.post("/submit", data=form)
    assert resp2.status_code == 303
    assert resp2.headers["location"].startswith("/submit/done?")

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 1
        assert reviews[0].submit_nonce == "fixed-nonce-abc123"


@pytest.mark.asyncio
async def test_submit_expired_token_on_replay_still_shows_success(http_client_factory, monkeypatch, test_sessionmaker):
    """再送POSTがLINEトークン期限切れ（verify失敗）でも、既に1回目が保存済みなら
    冪等キー先行チェックで成功ページへ流す（ログイン再検証を待たない）。"""
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker)
    await _seed_profile(test_sessionmaker)
    client = http_client_factory(review_submit_api, monkeypatch)

    form = dict(VALID_FORM, submit_nonce="replay-nonce-xyz")
    resp1 = await client.post("/submit", data=form)
    assert resp1.status_code == 303

    # 再送時はトークンが検証できない状態を模す
    _fake_verify(monkeypatch, user_id="")
    resp2 = await client.post("/submit", data=dict(form, id_token="stale-token"))
    assert resp2.status_code == 303

    async with test_sessionmaker() as session:
        reviews = (await session.execute(select(Review))).scalars().all()
        assert len(reviews) == 1


@pytest.mark.asyncio
async def test_submit_done_page_renders(http_client_factory, monkeypatch, test_sessionmaker):
    client = http_client_factory(review_submit_api, monkeypatch)
    resp = await client.get("/submit/done", params={"course_name": "経営管理", "course_id": 1, "n": 1})
    assert resp.status_code == 200
    assert "経営管理" in resp.text
    assert "初レビュー投稿" in resp.text


@pytest.mark.asyncio
async def test_submit_nonce_partial_unique_index_is_enforced_at_db_level(test_sessionmaker):
    """submit_nonce の部分UNIQUEインデックス（models.py Review.__table_args__ で宣言）が
    create_all で作られ、DBレベルで効いていることの確認。NULL は複数可・非NULLは一意。
    これが張られていないと review_submit_api.py の except IntegrityError 経路
    （ほぼ同時に届いた再送POSTの並行INSERT）が本番でしか通らない死角になる。"""
    await _seed_course(test_sessionmaker)

    def _mk(nonce):
        return Review(course_section_id=1, rating=5, ease_rating="A",
                      status=ReviewStatus.PENDING, submit_nonce=nonce)

    # NULL は何件でも入る
    async with test_sessionmaker() as session:
        session.add_all([_mk(None), _mk(None)])
        await session.commit()

    # 非NULLの同一値は2件目でDBが弾く
    async with test_sessionmaker() as session:
        session.add(_mk("dup-nonce"))
        await session.commit()
    with pytest.raises(IntegrityError):
        async with test_sessionmaker() as session:
            session.add(_mk("dup-nonce"))
            await session.commit()
