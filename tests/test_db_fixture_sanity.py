import pytest
from sqlalchemy import select

from models import Instructor


@pytest.mark.asyncio
async def test_sqlite_fixture_can_insert_and_query(test_sessionmaker):
    async with test_sessionmaker() as session:
        session.add(Instructor(name="テスト太郎"))
        await session.commit()

    async with test_sessionmaker() as session:
        rows = (await session.execute(select(Instructor))).scalars().all()
        assert len(rows) == 1
        assert rows[0].name == "テスト太郎"


@pytest.mark.asyncio
async def test_sqlite_fixture_is_isolated_between_tests(test_sessionmaker):
    # 前のテストの副作用が残っていないことを確認(テストごとに新しいengineが使われる)
    async with test_sessionmaker() as session:
        rows = (await session.execute(select(Instructor))).scalars().all()
        assert len(rows) == 0
