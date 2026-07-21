"""timetable_api.pyの主要エンドポイントに対するAPI経由のE2Eテスト。

httpx.AsyncClient(ASGITransport)で実際のHTTPリクエスト→ルーティング→
バリデーション→DB書き込み→レスポンスまでの一連のフローを検証する。
LINEプラットフォームへの実通信を伴うverify_liff_id_tokenはネットワーク不要な
フェイク関数に差し替える。
"""
import pytest

import routers.timetable_api as timetable_api
from models import UserProfile


def _fake_verify(monkeypatch, user_id: str = "Ue2euser1"):
    async def _verify(id_token, request=None):
        return user_id if id_token == "valid-token" else None
    monkeypatch.setattr(timetable_api, "verify_liff_id_token", _verify)


@pytest.mark.asyncio
async def test_timetable_years_returns_200(http_client_factory, monkeypatch):
    client = http_client_factory(timetable_api, monkeypatch)
    resp = await client.get("/api/timetable/years")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_profile_set_and_get_round_trip(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    client = http_client_factory(timetable_api, monkeypatch)

    async with test_sessionmaker() as session:
        session.add(UserProfile(line_user_id="Ue2euser1", name="神戸太郎", student_id="2345678S"))
        await session.commit()

    resp = await client.post("/api/timetable/profile", json={
        "id_token": "valid-token", "faculty": "経営学部", "grade": 2, "department": "",
    })
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    async with test_sessionmaker() as session:
        p = await session.get(UserProfile, "Ue2euser1")
        assert p.faculty == "経営学部"
        assert p.grade == 2


@pytest.mark.asyncio
async def test_profile_set_unauthenticated_returns_401(http_client_factory, monkeypatch):
    _fake_verify(monkeypatch)
    client = http_client_factory(timetable_api, monkeypatch)

    resp = await client.post("/api/timetable/profile", json={
        "id_token": "invalid-token", "faculty": "経営学部", "grade": 2,
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_profile_set_missing_user_profile_returns_404(http_client_factory, monkeypatch):
    # verify_liff_id_tokenは成功するが、UserProfile行が事前に作られていないケース
    # (会員登録前にmakeへアクセスする異常系)
    _fake_verify(monkeypatch)
    client = http_client_factory(timetable_api, monkeypatch)

    resp = await client.post("/api/timetable/profile", json={
        "id_token": "valid-token", "faculty": "経営学部", "grade": 2,
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_profile_set_grade_out_of_range_returns_422(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    client = http_client_factory(timetable_api, monkeypatch)
    async with test_sessionmaker() as session:
        session.add(UserProfile(line_user_id="Ue2euser1", name="神戸太郎", student_id="2345678S"))
        await session.commit()

    resp = await client.post("/api/timetable/profile", json={
        "id_token": "valid-token", "faculty": "経営学部", "grade": 7,
    })
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body


@pytest.mark.asyncio
async def test_profile_set_grade_non_integer_returns_422(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    client = http_client_factory(timetable_api, monkeypatch)
    async with test_sessionmaker() as session:
        session.add(UserProfile(line_user_id="Ue2euser1", name="神戸太郎", student_id="2345678S"))
        await session.commit()

    resp = await client.post("/api/timetable/profile", json={
        "id_token": "valid-token", "faculty": "経営学部", "grade": "abc",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_profile_set_grade_boundary_values_accepted(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    client = http_client_factory(timetable_api, monkeypatch)
    async with test_sessionmaker() as session:
        session.add(UserProfile(line_user_id="Ue2euser1", name="神戸太郎", student_id="2345678S"))
        await session.commit()

    for grade in (1, 6):
        resp = await client.post("/api/timetable/profile", json={
            "id_token": "valid-token", "faculty": "経営学部", "grade": grade,
        })
        assert resp.status_code == 200, f"grade={grade} should be accepted"


@pytest.mark.asyncio
async def test_profile_set_department_mismatch_returns_400(http_client_factory, monkeypatch, test_sessionmaker):
    _fake_verify(monkeypatch)
    client = http_client_factory(timetable_api, monkeypatch)
    async with test_sessionmaker() as session:
        session.add(UserProfile(line_user_id="Ue2euser1", name="神戸太郎", student_id="2345678S"))
        await session.commit()

    resp = await client.post("/api/timetable/profile", json={
        "id_token": "valid-token", "faculty": "経営学部", "department": "存在しない学科",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_timetable_slots_invalid_day_returns_400(http_client_factory, monkeypatch):
    client = http_client_factory(timetable_api, monkeypatch)
    resp = await client.get("/api/timetable/slots/不正曜日/1")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_register_unauthenticated_returns_401(http_client_factory, monkeypatch):
    _fake_verify(monkeypatch)
    client = http_client_factory(timetable_api, monkeypatch)
    resp = await client.post("/api/timetable/register/1", json={"id_token": ""})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_register_nonexistent_course_returns_404(http_client_factory, monkeypatch):
    _fake_verify(monkeypatch)
    client = http_client_factory(timetable_api, monkeypatch)
    resp = await client.post("/api/timetable/register/999999", json={"id_token": "valid-token"})
    assert resp.status_code == 404
