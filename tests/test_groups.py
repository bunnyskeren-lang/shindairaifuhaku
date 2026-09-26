"""団体（サークル等）経由のレビュー収集（core/groups.py・団体番号欄・/admin/groups）のテスト。"""
import pytest
from sqlalchemy import select

import routers.admin.groups as admin_groups
import routers.group_api as group_api
import routers.profile_api as profile_api
import routers.review_submit_api as review_submit_api
from core.config import ADMIN_COOKIE
from core.groups import (
    CODE_ALPHABET,
    CODE_LENGTH,
    generate_group_code,
    group_payout_breakdown,
    group_stats,
    normalize_group_code,
)
from core.security import make_admin_token
from models import CourseSection, Group, GroupPayout, Instructor, Review, ReviewStatus, Subject, UserProfile
from tests.test_e2e_review_submit import UID, VALID_FORM, _fake_verify, _seed_course, _stub_push_notification

GROUP_CODE = "ABCD2345"


# ── コードの生成・正規化 ─────────────────────────────────────────────────────

def test_generated_code_has_no_confusable_characters():
    for ch in "0O1IL":
        assert ch not in CODE_ALPHABET
    for _ in range(200):
        code = generate_group_code()
        assert len(code) == CODE_LENGTH >= 8
        assert set(code) <= set(CODE_ALPHABET)


@pytest.mark.parametrize("raw,expected", [
    ("abcd2345", "ABCD2345"),
    ("  abcd2345 \n", "ABCD2345"),
    ("ＡＢＣＤ２３４５", "ABCD2345"),   # 全角
    ("", ""),
    (None, ""),
])
def test_normalize_group_code(raw, expected):
    assert normalize_group_code(raw) == expected


# ── 支払額の計算（境界: 9/10/19/20人）────────────────────────────────────────

@pytest.mark.parametrize("people,bonus", [(0, 0), (9, 0), (10, 500), (19, 500), (20, 1000), (30, 1500)])
def test_contributor_bonus_boundaries(people, bonus):
    b = group_payout_breakdown(0, 0, people)
    assert b["bonus_amount"] == bonus
    assert b["accrued"] == bonus


def test_payout_unit_prices_and_total():
    b = group_payout_breakdown(kyoyo_count=3, senmon_count=2, contributor_count=10)
    assert b["kyoyo_amount"] == 150
    assert b["senmon_amount"] == 60
    assert b["bonus_amount"] == 500
    assert b["accrued"] == 710


# ── 集計（group_stats）───────────────────────────────────────────────────────

async def _add_review(session, cs_id, sid, group_id, status=ReviewStatus.APPROVED, copied_from=None):
    r = Review(
        course_section_id=cs_id, content="x" * 40, rating=3, ease_rating="A", student_id=sid,
        status=status, group_id=group_id, copied_from_review_id=copied_from,
    )
    session.add(r)
    await session.flush()
    return r


async def _seed_two_courses(session):
    """教養1科目・専門1科目のcourse_section idを返す。"""
    ids = []
    for name, category in (("教養科目", "教養"), ("専門科目", "専門")):
        subj = Subject(name=name, faculty="経営学部", category=category)
        session.add(subj)
        await session.flush()
        instr = Instructor(name=f"教員{name}")
        session.add(instr)
        await session.flush()
        cs = CourseSection(subject_id=subj.id, instructor_id=instr.id)
        session.add(cs)
        await session.flush()
        ids.append(cs.id)
    return ids


@pytest.mark.asyncio
async def test_group_stats_counts_only_approved_and_distinct_students(test_sessionmaker):
    async with test_sessionmaker() as s:
        g = Group(name="起業部", code=GROUP_CODE)
        other = Group(name="他団体", code="ZZZZ2345")
        s.add_all([g, other])
        await s.flush()
        kyoyo_cs, senmon_cs = await _seed_two_courses(s)
        # 同じ学生が教養2件（別科目扱いにするため別レビュー）＋専門1件 → 人数は1
        await _add_review(s, kyoyo_cs, "1000001A", g.id)
        await _add_review(s, kyoyo_cs, "1000001A", g.id)
        await _add_review(s, senmon_cs, "1000001A", g.id)
        await _add_review(s, kyoyo_cs, "1000002B", g.id)
        # 集計外: 待機中・却下・団体なし・他団体・複製レビュー
        await _add_review(s, kyoyo_cs, "1000003C", g.id, status=ReviewStatus.PENDING)
        await _add_review(s, kyoyo_cs, "1000004D", g.id, status=ReviewStatus.REJECTED)
        await _add_review(s, kyoyo_cs, "1000005E", None)
        await _add_review(s, kyoyo_cs, "1000006F", other.id)
        orig = await _add_review(s, kyoyo_cs, "1000007G", None)
        await _add_review(s, kyoyo_cs, "1000007G", g.id, copied_from=orig.id)
        s.add(GroupPayout(group_id=g.id, amount=100))
        s.add(GroupPayout(group_id=g.id, amount=30))
        await s.commit()
        gid, oid = g.id, other.id

    async with test_sessionmaker() as s:
        stats = await group_stats(s)
    st = stats[gid]
    assert (st["kyoyo_count"], st["senmon_count"], st["contributor_count"]) == (3, 1, 2)
    assert st["accrued"] == 3 * 50 + 1 * 30
    assert st["paid"] == 130
    assert st["balance"] == 180 - 130
    assert stats[oid]["kyoyo_count"] == 1


@pytest.mark.asyncio
async def test_group_stats_rejected_after_approval_drops_out(test_sessionmaker):
    async with test_sessionmaker() as s:
        g = Group(name="起業部", code=GROUP_CODE)
        s.add(g)
        await s.flush()
        kyoyo_cs, _ = await _seed_two_courses(s)
        r = await _add_review(s, kyoyo_cs, "1000001A", g.id)
        await s.commit()
        gid, rid = g.id, r.id
    async with test_sessionmaker() as s:
        assert (await group_stats(s))[gid]["accrued"] == 50
    async with test_sessionmaker() as s:
        (await s.get(Review, rid)).status = ReviewStatus.REJECTED
        await s.commit()
    async with test_sessionmaker() as s:
        assert gid not in await group_stats(s)


# ── /api/group/lookup ───────────────────────────────────────────────────────

async def _seed_group(test_sessionmaker, code=GROUP_CODE, name="起業部", active=True):
    async with test_sessionmaker() as s:
        g = Group(name=name, code=code, is_active=active)
        s.add(g)
        await s.commit()
        return g.id


@pytest.mark.asyncio
async def test_lookup_returns_name_case_insensitively(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_group(test_sessionmaker)
    client = http_client_factory(group_api, monkeypatch)
    resp = await client.post("/api/group/lookup", json={"code": " abcd2345 "})
    assert resp.json() == {"ok": True, "name": "起業部"}


@pytest.mark.asyncio
async def test_lookup_unknown_and_inactive_codes_report_reason(http_client_factory, monkeypatch, test_sessionmaker):
    await _seed_group(test_sessionmaker, code="STOP2345", active=False)
    client = http_client_factory(group_api, monkeypatch)
    unknown = (await client.post("/api/group/lookup", json={"code": "NOPE2345"})).json()
    assert unknown["ok"] is False and unknown["reason"] == "not_found"
    inactive = (await client.post("/api/group/lookup", json={"code": "STOP2345"})).json()
    assert inactive["ok"] is False and inactive["reason"] == "inactive"
    empty = (await client.post("/api/group/lookup", json={"code": ""})).json()
    assert empty["ok"] is False and empty["reason"] == "empty"


@pytest.mark.asyncio
async def test_lookup_is_rate_limited(http_client_factory, monkeypatch, test_sessionmaker):
    client = http_client_factory(group_api, monkeypatch)
    codes = [(await client.post("/api/group/lookup", json={"code": "NOPE2345"})).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10
    assert codes[10:] == [429, 429]


# ── /submit ─────────────────────────────────────────────────────────────────

async def _setup_submit(http_client_factory, monkeypatch, test_sessionmaker, *, group_active=True, category="教養"):
    _fake_verify(monkeypatch)
    _stub_push_notification(monkeypatch)
    await _seed_course(test_sessionmaker, category=category)
    async with test_sessionmaker() as s:
        s.add(UserProfile(
            line_user_id=UID, name="神戸太郎", student_id="2345678S",
            faculty="経営学部", department="経営学科", coop_jobsite_known="はい",
        ))
        await s.commit()
    gid = await _seed_group(test_sessionmaker, active=group_active)
    return http_client_factory(review_submit_api, monkeypatch), gid


async def _only_review_and_profile(test_sessionmaker):
    async with test_sessionmaker() as s:
        reviews = (await s.execute(select(Review))).scalars().all()
        profile = await s.get(UserProfile, UID)
        return reviews, profile


@pytest.mark.asyncio
async def test_submit_with_valid_code_sets_group_on_review_and_profile(http_client_factory, monkeypatch, test_sessionmaker):
    client, gid = await _setup_submit(http_client_factory, monkeypatch, test_sessionmaker)
    resp = await client.post("/submit", data={**VALID_FORM, "group_code": "abcd2345"})
    assert resp.status_code == 303
    reviews, profile = await _only_review_and_profile(test_sessionmaker)
    assert reviews[0].group_id == gid
    assert profile.group_id == gid


@pytest.mark.asyncio
async def test_submit_without_code_has_no_group(http_client_factory, monkeypatch, test_sessionmaker):
    client, _ = await _setup_submit(http_client_factory, monkeypatch, test_sessionmaker)
    assert (await client.post("/submit", data=VALID_FORM)).status_code == 303
    reviews, profile = await _only_review_and_profile(test_sessionmaker)
    assert reviews[0].group_id is None and profile.group_id is None


@pytest.mark.asyncio
async def test_submit_with_unknown_code_is_rejected_not_ignored(http_client_factory, monkeypatch, test_sessionmaker):
    client, _ = await _setup_submit(http_client_factory, monkeypatch, test_sessionmaker)
    resp = await client.post("/submit", data={**VALID_FORM, "group_code": "NOPE2345"})
    assert resp.status_code == 400
    assert "団体番号" in resp.text
    reviews, profile = await _only_review_and_profile(test_sessionmaker)
    assert reviews == [] and profile.group_id is None


@pytest.mark.asyncio
async def test_submit_with_inactive_code_is_rejected(http_client_factory, monkeypatch, test_sessionmaker):
    client, _ = await _setup_submit(http_client_factory, monkeypatch, test_sessionmaker, group_active=False)
    resp = await client.post("/submit", data={**VALID_FORM, "group_code": GROUP_CODE})
    assert resp.status_code == 400
    assert "停止中" in resp.text
    reviews, _ = await _only_review_and_profile(test_sessionmaker)
    assert reviews == []


@pytest.mark.asyncio
async def test_submit_fixed_group_ignores_other_code(http_client_factory, monkeypatch, test_sessionmaker):
    client, gid = await _setup_submit(http_client_factory, monkeypatch, test_sessionmaker)
    await _seed_group(test_sessionmaker, code="OTHR2345", name="別の団体")
    async with test_sessionmaker() as s:
        (await s.get(UserProfile, UID)).group_id = gid
        await s.commit()
    # 別の有効な番号を入力しても、最初の団体に固定されたまま（エラーにもならない）
    resp = await client.post("/submit", data={**VALID_FORM, "group_code": "OTHR2345"})
    assert resp.status_code == 303
    reviews, profile = await _only_review_and_profile(test_sessionmaker)
    assert reviews[0].group_id == gid and profile.group_id == gid


@pytest.mark.asyncio
async def test_submit_fixed_to_inactive_group_is_not_counted(http_client_factory, monkeypatch, test_sessionmaker):
    client, gid = await _setup_submit(http_client_factory, monkeypatch, test_sessionmaker, group_active=False)
    async with test_sessionmaker() as s:
        (await s.get(UserProfile, UID)).group_id = gid
        await s.commit()
    resp = await client.post("/submit", data=VALID_FORM)
    assert resp.status_code == 303
    reviews, profile = await _only_review_and_profile(test_sessionmaker)
    assert reviews[0].group_id is None       # 停止中の団体には計上しない
    assert profile.group_id == gid           # 所属自体は保持


@pytest.mark.asyncio
async def test_submit_same_student_id_on_other_account_is_locked_to_first_group(
    http_client_factory, monkeypatch, test_sessionmaker
):
    """同じ学籍番号で別のLINEアカウントに登録済みの所属団体があれば、そちらに固定される。"""
    client, gid = await _setup_submit(http_client_factory, monkeypatch, test_sessionmaker)
    other_gid = await _seed_group(test_sessionmaker, code="OTHR2345", name="別の団体")
    async with test_sessionmaker() as s:
        s.add(UserProfile(
            line_user_id="U99999999999999999999999999999999", name="神戸太郎", student_id="2345678S",
            faculty="経営学部", department="経営学科", coop_jobsite_known="はい", group_id=gid,
        ))
        await s.commit()
    resp = await client.post("/submit", data={**VALID_FORM, "group_code": "OTHR2345"})
    assert resp.status_code == 303
    reviews, profile = await _only_review_and_profile(test_sessionmaker)
    assert reviews[0].group_id == gid != other_gid
    assert profile.group_id == gid


# ── prefill ─────────────────────────────────────────────────────────────────

def _fake_profile_verify(monkeypatch):
    async def _verify(id_token, request=None):
        return UID if id_token == "valid-token" else None
    monkeypatch.setattr(profile_api, "verify_liff_id_token", _verify)


@pytest.mark.asyncio
async def test_prefill_returns_group_when_member_belongs_to_one(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_profile_verify(monkeypatch)
    gid = await _seed_group(test_sessionmaker)
    async with test_sessionmaker() as s:
        s.add(UserProfile(
            line_user_id=UID, name="神戸太郎", student_id="2345678S",
            faculty="経営学部", department="経営学科", coop_jobsite_known="はい", group_id=gid,
        ))
        await s.commit()
    client = http_client_factory(profile_api, monkeypatch)
    d = (await client.post("/api/profile/prefill", json={"id_token": "valid-token"})).json()
    assert d["group"] == {"name": "起業部", "active": True}


@pytest.mark.asyncio
async def test_prefill_group_is_null_without_membership(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_profile_verify(monkeypatch)
    async with test_sessionmaker() as s:
        s.add(UserProfile(
            line_user_id=UID, name="神戸太郎", student_id="2345678S",
            faculty="経営学部", department="経営学科", coop_jobsite_known="はい",
        ))
        await s.commit()
    client = http_client_factory(profile_api, monkeypatch)
    d = (await client.post("/api/profile/prefill", json={"id_token": "valid-token"})).json()
    assert d["found"] is True and d["group"] is None


# ── 管理画面 ────────────────────────────────────────────────────────────────

def _admin_client(http_client_factory, monkeypatch):
    client = http_client_factory(admin_groups, monkeypatch)
    client.cookies.set(ADMIN_COOKIE, make_admin_token())
    return client


@pytest.mark.asyncio
async def test_admin_groups_page_renders_empty_and_with_stats(http_client_factory, monkeypatch, test_sessionmaker):
    client = _admin_client(http_client_factory, monkeypatch)
    resp = await client.get("/admin/groups")
    assert resp.status_code == 200
    assert "団体はまだありません" in resp.text

    async with test_sessionmaker() as s:
        g = Group(name="起業部", code=GROUP_CODE)
        s.add(g)
        await s.flush()
        kyoyo_cs, _ = await _seed_two_courses(s)
        await _add_review(s, kyoyo_cs, "1000001A", g.id)
        s.add(GroupPayout(group_id=g.id, amount=20, note="9月分"))
        await s.commit()
    resp = await client.get("/admin/groups")
    assert resp.status_code == 200
    assert "起業部" in resp.text and GROUP_CODE in resp.text
    assert "1000001A" not in resp.text  # 学籍番号は表示しない
    assert "50円" in resp.text          # 発生額
    assert "30円" in resp.text          # 未精算残高 50-20


@pytest.mark.asyncio
async def test_admin_create_update_toggle_and_payout(http_client_factory, monkeypatch, test_sessionmaker):
    client = _admin_client(http_client_factory, monkeypatch)
    assert (await client.post("/admin/groups/create", data={"name": "テニス部", "note": "窓口A"})).status_code == 303
    async with test_sessionmaker() as s:
        g = (await s.execute(select(Group))).scalar_one()
        assert g.name == "テニス部" and g.is_active and len(g.code) == CODE_LENGTH
        gid = g.id

    # 団体番号を手入力（全角・小文字は正規化）。重複はエラー表示で追加されない
    r = await client.post("/admin/groups/create", data={"name": "書道部", "code": "ｓｈ２０２６ab"})
    assert r.status_code == 303
    r = await client.post("/admin/groups/create", data={"name": "別団体", "code": "SH2026AB"})
    assert "error=" in r.headers["location"]
    for bad in ("SHO2026", "SH2026ABC", "SHO-2026"):  # 8文字未満・超過・記号は追加されない
        r = await client.post("/admin/groups/create", data={"name": "不正", "code": bad})
        assert "error=" in r.headers["location"]
    async with test_sessionmaker() as s:
        assert (await s.execute(select(Group).where(Group.name == "不正"))).first() is None
        assert [g.name for g in (await s.execute(select(Group).where(Group.code == "SH2026AB"))).scalars()] == ["書道部"]
        assert (await s.execute(select(Group).where(Group.name == "別団体"))).first() is None

    await client.post(f"/admin/groups/{gid}/update", data={"name": "硬式テニス部", "note": ""})
    await client.post(f"/admin/groups/{gid}/toggle")
    await client.post(f"/admin/groups/{gid}/payout", data={"amount": "500", "note": "9/30振込"})
    await client.post(f"/admin/groups/{gid}/payout", data={"amount": "0"})  # 0円以下は記録しない
    async with test_sessionmaker() as s:
        g = await s.get(Group, gid)
        assert g.name == "硬式テニス部" and g.is_active is False
        payouts = (await s.execute(select(GroupPayout))).scalars().all()
        assert [(p.amount, p.note) for p in payouts] == [(500, "9/30振込")]

    await client.post(f"/admin/groups/payout/{payouts[0].id}/delete")
    async with test_sessionmaker() as s:
        assert (await s.execute(select(GroupPayout))).scalars().all() == []
        assert await s.get(Group, gid) is not None  # 団体は残る


@pytest.mark.asyncio
async def test_admin_groups_requires_login(http_client_factory, monkeypatch):
    client = http_client_factory(admin_groups, monkeypatch)
    resp = await client.get("/admin/groups", follow_redirects=False)
    assert resp.status_code in (302, 303, 401, 403)



@pytest.mark.asyncio
async def test_review_form_has_optional_group_code_field():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    import routers.pages as pages
    app = FastAPI()
    app.include_router(pages.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers={"user-agent": "curl/8"}) as c:
        resp = await c.get("/")
    assert resp.status_code == 200
    assert 'name="group_code"' in resp.text
    assert "文字数制限をなくしました" in resp.text  # 青いお知らせ枠には触れていない
