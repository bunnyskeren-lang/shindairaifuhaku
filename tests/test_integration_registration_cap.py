"""_get_registration_cap()の結合テスト(SQLiteインメモリDB使用)。

学部・学科・年度のCAP判定は「学科一致行を優先し、無ければ学部共通(department NULL)行に
フォールバックする」というORDER BYに依存したロジックで、モックでは検証できない
実際のSQLクエリの正しさを確認する。
"""
import pytest

from models import RegistrationCap
from routers.timetable_api import _get_registration_cap
from tests.conftest import patch_async_session_local


@pytest.mark.asyncio
async def test_department_specific_cap_takes_priority_over_faculty_wide(test_sessionmaker, monkeypatch):
    import routers.timetable_api as mod
    patch_async_session_local(monkeypatch, mod, test_sessionmaker)

    async with test_sessionmaker() as session:
        session.add_all([
            RegistrationCap(faculty="工学部", department=None, year=2026, max_credits=54),
            RegistrationCap(faculty="工学部", department="機械工学科", year=2026, max_credits=60),
        ])
        await session.commit()

        cap = await _get_registration_cap(session, "工学部", "機械工学科", 2026)
        assert cap == 60


@pytest.mark.asyncio
async def test_falls_back_to_faculty_wide_cap_when_department_specific_missing(test_sessionmaker, monkeypatch):
    import routers.timetable_api as mod
    patch_async_session_local(monkeypatch, mod, test_sessionmaker)

    async with test_sessionmaker() as session:
        session.add(RegistrationCap(faculty="経営学部", department=None, year=2026, max_credits=49))
        await session.commit()

        cap = await _get_registration_cap(session, "経営学部", None, 2026)
        assert cap == 49

        # 学科名を指定しても、その学科専用の行が無ければ学部共通行にフォールバックする
        cap2 = await _get_registration_cap(session, "経営学部", "存在しない学科", 2026)
        assert cap2 == 49


@pytest.mark.asyncio
async def test_returns_none_when_no_matching_row(test_sessionmaker, monkeypatch):
    import routers.timetable_api as mod
    patch_async_session_local(monkeypatch, mod, test_sessionmaker)

    async with test_sessionmaker() as session:
        session.add(RegistrationCap(faculty="経営学部", department=None, year=2026, max_credits=49))
        await session.commit()

        # 学部が違う
        assert await _get_registration_cap(session, "農学部", None, 2026) is None
        # 年度が違う
        assert await _get_registration_cap(session, "経営学部", None, 2027) is None


@pytest.mark.asyncio
async def test_returns_none_immediately_when_faculty_is_falsy(test_sessionmaker, monkeypatch):
    import routers.timetable_api as mod
    patch_async_session_local(monkeypatch, mod, test_sessionmaker)

    async with test_sessionmaker() as session:
        # facultyが空/Noneなら、DBに行があっても即Noneを返す(DBアクセス自体が行われない設計)
        session.add(RegistrationCap(faculty="経営学部", department=None, year=2026, max_credits=49))
        await session.commit()

        assert await _get_registration_cap(session, None, None, 2026) is None
        assert await _get_registration_cap(session, "", None, 2026) is None
