"""団体（サークル等）経由のレビュー収集（core/groups.py・団体コード欄・/admin/groups）のテスト。"""
import json

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
    assert "団体コード" in resp.text
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
async def test_submit_other_code_switches_group(http_client_factory, monkeypatch, test_sessionmaker):
    client, gid = await _setup_submit(http_client_factory, monkeypatch, test_sessionmaker)
    other_gid = await _seed_group(test_sessionmaker, code="OTHR2345", name="別の団体")
    async with test_sessionmaker() as s:
        (await s.get(UserProfile, UID)).group_id = gid
        await s.commit()
    # 別の有効な番号を入力すると所属団体が書き換わり、そのレビューも新しい団体に計上される
    resp = await client.post("/submit", data={**VALID_FORM, "group_code": "OTHR2345"})
    assert resp.status_code == 303
    reviews, profile = await _only_review_and_profile(test_sessionmaker)
    assert reviews[0].group_id == other_gid != gid and profile.group_id == other_gid


@pytest.mark.asyncio
async def test_submit_member_without_code_is_not_counted(http_client_factory, monkeypatch, test_sessionmaker):
    """所属済みでも、団体コードを消した（空の）状態で投稿したレビューは団体に数えない。"""
    client, gid = await _setup_submit(http_client_factory, monkeypatch, test_sessionmaker)
    async with test_sessionmaker() as s:
        (await s.get(UserProfile, UID)).group_id = gid
        await s.commit()
    assert (await client.post("/submit", data=VALID_FORM)).status_code == 303
    reviews, profile = await _only_review_and_profile(test_sessionmaker)
    assert reviews[0].group_id is None
    assert profile.group_id == gid  # 所属自体は保持


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
async def test_submit_other_code_switches_group_for_all_accounts_of_same_student_id(
    http_client_factory, monkeypatch, test_sessionmaker
):
    """同じ学籍番号の別LINEアカウントのプロフィールも、新しい団体へ揃って書き換わる。"""
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
    assert reviews[0].group_id == other_gid != gid
    assert profile.group_id == other_gid
    async with test_sessionmaker() as s:
        other = (await s.execute(select(UserProfile).where(UserProfile.line_user_id == "U99999999999999999999999999999999"))).scalar_one()
        assert other.group_id == other_gid


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
    assert d["group"] == {"name": "起業部", "active": True, "code": GROUP_CODE}  # 番号を入力済みにするため返す


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

    # 団体コードを手入力（全角・小文字は正規化）。重複はエラー表示で追加されない
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
    assert "文字数制限をなくしました" not in resp.text  # 2026-09-26 お知らせ枠は削除済み


@pytest.mark.asyncio
async def test_admin_reviews_page_shows_group_code(http_client_factory, monkeypatch, test_sessionmaker):
    """レビュー承認画面に、団体コード経由で投稿されたレビューの団体コードが表示される。"""
    import routers.admin.reviews as admin_reviews
    async with test_sessionmaker() as s:
        g = Group(name="起業部", code=GROUP_CODE)
        s.add(g)
        await s.flush()
        kyoyo_cs, _ = await _seed_two_courses(s)
        await _add_review(s, kyoyo_cs, "1000001A", g.id, status=ReviewStatus.PENDING)
        await _add_review(s, kyoyo_cs, "1000002B", None, status=ReviewStatus.PENDING)
        await s.commit()
    client = http_client_factory(admin_reviews, monkeypatch)
    client.cookies.set(ADMIN_COOKIE, make_admin_token())
    resp = await client.get("/admin/reviews")
    assert resp.status_code == 200
    assert resp.text.count(f"団体コード {GROUP_CODE}") >= 1


# ── 団体の管理者とLINE botの「団体」（団体の成果の確認）──────────────────────────

async def _seed_report(test_sessionmaker):
    """管理者(M1)と会員(M2)と未投稿の会員(M3)がいる団体。M1が2件・M2が1件の承認済みレビューを投稿済み。"""
    async with test_sessionmaker() as s:
        g = Group(name="起業部", code=GROUP_CODE, manager_line_user_id="U_M1")
        s.add(g)
        await s.flush()
        for uid, name, sid in (("U_M1", "管理者 太郎", "2412345A"), ("U_M2", "会員 花子", "2612345B"),
                               ("U_M3", "未投稿 次郎", "1000003C")):
            s.add(UserProfile(line_user_id=uid, name=name, student_id=sid, faculty="経営学部", group_id=g.id))
        kyoyo_cs, _ = await _seed_two_courses(s)
        await _add_review(s, kyoyo_cs, "2412345A", g.id)
        await _add_review(s, kyoyo_cs, "2412345A", g.id)
        await _add_review(s, kyoyo_cs, "2612345B", g.id)
        await _add_review(s, kyoyo_cs, "1000003C", g.id, status=ReviewStatus.PENDING)
        await s.commit()
        return g.id


@pytest.mark.asyncio
async def test_group_report_lists_members_only_to_manager(test_sessionmaker):
    from core.groups import grade_label, group_of_user, group_report
    await _seed_report(test_sessionmaker)
    async with test_sessionmaker() as s:
        g = await group_of_user(s, "U_M1")
        mgr = await group_report(s, g, "U_M1")
        mem = await group_report(s, g, "U_M2")
    assert mgr["is_manager"] and mgr["members"] == [("管理者 太郎", grade_label("2412345A"), 2), ("会員 花子", grade_label("2612345B"), 1)]  # 承認済みのみ・多い順
    assert (mgr["my_count"], mem["my_count"]) == (2, 1)
    assert mem["is_manager"] is False and mem["members"] == []
    assert mem["kyoyo_count"] == 3 and mem["contributor_count"] == 2


@pytest.mark.asyncio
async def test_group_report_flex_shows_member_names_only_to_manager(test_sessionmaker, monkeypatch):
    from core.groups import group_report_for_user
    from line_bot.flex_builders import make_group_report_flex
    monkeypatch.setattr("database.AsyncSessionLocal", test_sessionmaker)
    await _seed_report(test_sessionmaker)

    def render(uid_report):
        name, r = uid_report
        return json.dumps(json.loads(make_group_report_flex(name, r).to_json()), ensure_ascii=False)

    manager_json = render(await group_report_for_user("U_M1"))
    member_json = render(await group_report_for_user("U_M2"))
    assert "管理者のみ表示" in manager_json and "会員 花子" in manager_json and "1件" in manager_json
    assert "会員 花子" not in member_json and "管理者のみ" not in member_json
    assert "起業部" in member_json and "あなた" in member_json
    assert all(x in member_json for x in ("教養", "3件 × 50円", "150円", "専門", "0件 × 30円"))
    assert "2412345A" not in manager_json  # 学籍番号は出さない
    assert await group_report_for_user("U_NOBODY") is None


def test_group_members_flex_lists_everyone_and_report_links_to_it():
    from line_bot.flex_builders import GROUP_MEMBERS_TEXT, make_group_members_flex, make_group_report_flex
    members = [(f"会員{i}", "2回生", 10 - i) for i in range(8)]
    r = dict(kyoyo_count=1, senmon_count=0, kyoyo_amount=50, senmon_amount=0, contributor_count=8, my_count=0,
             accrued=50, bonus_remaining=2, is_manager=True, members=members)
    dump = lambda m: json.dumps(json.loads(m.to_json()), ensure_ascii=False)  # noqa: E731
    report = dump(make_group_report_flex("起業部", r))
    assert "会員4" in report and "会員5" not in report  # 上位5人だけ
    assert GROUP_MEMBERS_TEXT in report and "全員を見る（8人）" in report
    assert GROUP_MEMBERS_TEXT not in dump(make_group_report_flex("起業部", {**r, "members": members[:5]}))
    full = dump(make_group_members_flex("起業部", members))
    assert all(f"会員{i}" in full for i in range(8))


@pytest.mark.asyncio
async def test_admin_can_set_manager_only_from_group_members(http_client_factory, monkeypatch, test_sessionmaker):
    client = _admin_client(http_client_factory, monkeypatch)
    gid = await _seed_report(test_sessionmaker)
    await client.post(f"/admin/groups/{gid}/manager", data={"line_user_id": "U_M2"})
    async with test_sessionmaker() as s:
        assert (await s.get(Group, gid)).manager_line_user_id == "U_M2"
    await client.post(f"/admin/groups/{gid}/manager", data={"line_user_id": "U_OUTSIDER"})  # 所属外は変わらない
    async with test_sessionmaker() as s:
        assert (await s.get(Group, gid)).manager_line_user_id == "U_M2"
    await client.post(f"/admin/groups/{gid}/manager", data={"line_user_id": ""})  # 空欄で解除
    async with test_sessionmaker() as s:
        assert (await s.get(Group, gid)).manager_line_user_id is None


def test_grade_label_from_student_id_and_academic_year():
    from datetime import UTC, datetime

    from core.groups import grade_label
    sept_2026 = datetime(2026, 9, 27, tzinfo=UTC)
    assert grade_label("2612345A", sept_2026) == "1回生"
    assert grade_label("2412345A", sept_2026) == "3回生"
    # 年度は4月始まり: 2027年3月はまだ2026年度なので26入学は1回生、4月から2回生
    assert grade_label("2612345A", datetime(2027, 3, 31, tzinfo=UTC)) == "1回生"
    assert grade_label("2612345A", datetime(2027, 4, 1, tzinfo=UTC)) == "2回生"
    # 読み取れない・入学前・年数が不自然なものは空文字
    for bad in ("", None, "X612345A", "2", "2712345A", "1012345A"):
        assert grade_label(bad, sept_2026) == ""


# ── 2026-09-27 追加: 振込額・区分の検証・管理者の付け替え・入力エラー表示 ──────────────

def test_transfer_amount_is_full_balance_bank_fee_not_deducted():
    from core.config import GROUP_BANK_FEE
    from core.groups import _with_settlement, empty_group_stats
    # 振込手数料は運営負担なので、振込額は残高そのまま（差し引かない）
    info = _with_settlement(group_payout_breakdown(100, 0, 0), paid=0, member_count=0)  # 5,000円
    assert info["transfer_amount"] == 5000
    assert info["bank_fee"] == GROUP_BANK_FEE  # 運営側のコスト把握用に残る
    small = _with_settlement(group_payout_breakdown(2, 0, 0), paid=0, member_count=0)  # 100円
    assert small["transfer_amount"] == 100
    assert empty_group_stats()["transfer_amount"] == 0
    assert set(empty_group_stats()) >= {"accrued", "paid", "balance", "member_count", "bank_fee", "transfer_amount"}


def test_subject_category_only_accepts_kyoyo_or_senmon():
    Subject(name="A", faculty="経営学部", category="教養")
    Subject(name="B", faculty="経営学部", category="専門")
    Subject(name="C", faculty="経営学部")  # 未設定（NULL）は既存行との互換で許容
    with pytest.raises(ValueError):
        Subject(name="D", faculty="経営学部", category="その他")


@pytest.mark.asyncio
async def test_admin_course_create_rejects_invalid_category(http_client_factory, monkeypatch, test_sessionmaker):
    import routers.admin.courses as admin_courses
    monkeypatch.setattr(admin_courses.cache, "invalidate_courses_cache", lambda: None, raising=False)
    client = http_client_factory(admin_courses, monkeypatch)
    client.cookies.set(ADMIN_COOKIE, make_admin_token())
    resp = await client.post(
        "/admin/courses/create", data={"name": "謎科目", "category": "その他"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.json()["error"] == "invalid_category"
    async with test_sessionmaker() as s:
        assert (await s.execute(select(Subject).where(Subject.name == "謎科目"))).first() is None


@pytest.mark.asyncio
async def test_switching_group_clears_old_group_manager(http_client_factory, monkeypatch, test_sessionmaker):
    client, gid = await _setup_submit(http_client_factory, monkeypatch, test_sessionmaker)
    other_gid = await _seed_group(test_sessionmaker, code="OTHR2345", name="別の団体")
    async with test_sessionmaker() as s:
        (await s.get(UserProfile, UID)).group_id = gid
        (await s.get(Group, gid)).manager_line_user_id = UID
        await s.commit()
    assert (await client.post("/submit", data={**VALID_FORM, "group_code": "OTHR2345"})).status_code == 303
    async with test_sessionmaker() as s:
        assert (await s.get(Group, gid)).manager_line_user_id is None  # 元団体の管理者指定は外れる
        assert (await s.get(UserProfile, UID)).group_id == other_gid


@pytest.mark.asyncio
async def test_admin_manager_outsider_and_payout_range_show_error(http_client_factory, monkeypatch, test_sessionmaker):
    client = _admin_client(http_client_factory, monkeypatch)
    gid = await _seed_report(test_sessionmaker)
    r = await client.post(f"/admin/groups/{gid}/manager", data={"line_user_id": "U_OUTSIDER"})
    assert "error=" in r.headers["location"]
    for bad in ("-5", "0", "10000001"):
        r = await client.post(f"/admin/groups/{gid}/payout", data={"amount": bad})
        assert "error=" in r.headers["location"]
    async with test_sessionmaker() as s:
        assert (await s.execute(select(GroupPayout))).scalars().all() == []


@pytest.mark.asyncio
async def test_admin_users_page_flags_reviews_posted_with_group_code(
    http_client_factory, monkeypatch, test_sessionmaker
):
    """ユーザー設定画面のレビュー内訳に、団体コードを入力して投稿されたレビューだけ団体名バッジが付く。"""
    import routers.admin.users_errors as users_errors

    gid = await _seed_group(test_sessionmaker, name="起業部")
    async with test_sessionmaker() as s:
        s.add(UserProfile(
            line_user_id=UID, name="団体太郎", student_id="2211111A",
            faculty="経営学部", department="", coop_jobsite_known="はい",
        ))
        subj = Subject(name="経営管理", faculty="経営学部", category="専門")
        s.add(subj)
        await s.flush()
        instr = Instructor(name="山田太郎")
        s.add(instr)
        await s.flush()
        cs = CourseSection(subject_id=subj.id, instructor_id=instr.id)
        s.add(cs)
        await s.flush()
        s.add_all([
            Review(
                course_section_id=cs.id, content="団体経由の投稿", rating=5, ease_rating="A",
                student_id="2211111A", status=ReviewStatus.APPROVED, group_id=gid,
            ),
            Review(
                course_section_id=cs.id, content="団体コードなしの投稿", rating=4, ease_rating="B",
                student_id="2211111A", status=ReviewStatus.APPROVED, group_id=None,
            ),
        ])
        await s.commit()

    client = http_client_factory(users_errors, monkeypatch)
    client.cookies.set(ADMIN_COOKIE, make_admin_token())
    resp = await client.get("/admin/users")
    text = resp.text
    assert "団体太郎" in text
    assert "🏫団体コード1件" in text  # summaryに付く合計バッジ
    assert "🏫 起業部" in text  # breakdownの行に付くバッジ（団体経由の1件のみ）
    # 全体の合計は2件のまま（団体経由・非経由を合わせた件数）
    assert "2件<span" in text


@pytest.mark.asyncio
async def test_regenerate_code_clears_membership_but_keeps_past_review_links(
    http_client_factory, monkeypatch, test_sessionmaker
):
    """コード再発行後は、旧コードで所属登録済みだった会員の所属（user_profiles.group_id）・管理者指定は
    解除され、次回投稿には新しいコードの再入力が必要になる。ただし過去のreviews.group_idは変わらない
    （集計に影響しない）。旧コードはもう照合に使えない。"""
    client = _admin_client(http_client_factory, monkeypatch)
    gid = await _seed_group(test_sessionmaker, code="OLDCODE1", name="コード再発行テスト")
    async with test_sessionmaker() as s:
        s.add(UserProfile(
            line_user_id=UID, name="太郎", student_id="2211111A",
            faculty="経営学部", department="", coop_jobsite_known="はい", group_id=gid,
        ))
        (await s.get(Group, gid)).manager_line_user_id = UID
        kyoyo_cs, _ = await _seed_two_courses(s)
        await _add_review(s, kyoyo_cs, "2211111A", gid)
        await s.commit()

    resp = await client.post(f"/admin/groups/{gid}/regenerate-code")
    assert resp.status_code == 303

    async with test_sessionmaker() as s:
        g = await s.get(Group, gid)
        assert g.code != "OLDCODE1"
        assert len(g.code) == 8
        assert g.manager_line_user_id is None  # 管理者指定も外れる
        # 所属（user_profiles.group_id）は解除される。次回投稿はコードの再入力が要る
        profile = await s.get(UserProfile, UID)
        assert profile.group_id is None
        # 過去の投稿(reviews.group_id)は動かない＝既発生額は消えない
        review = (await s.execute(select(Review).where(Review.student_id == "2211111A"))).scalar_one()
        assert review.group_id == gid

    lookup_client = http_client_factory(group_api, monkeypatch)
    old = await lookup_client.post("/api/group/lookup", json={"code": "OLDCODE1"})
    assert old.json()["ok"] is False  # 旧コードはもう照合に使えない
