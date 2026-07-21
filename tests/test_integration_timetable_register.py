"""register_syllabus_for_user()の結合テスト(SQLiteインメモリDB使用)。

「1コマ1科目」制約(同一曜日・時限・学期が重なる既存登録の自動差し替え)は
PostgreSQL固有のpg_advisory_xact_lockとON CONFLICT DO NOTHINGに依存しており、
モックでは実際のSQLロジックを検証できないため結合テストで確認する。
"""
import pytest
from sqlalchemy import select

from core.required_subjects import register_syllabus_for_user
from models import CourseSection, Instructor, Schedule, Subject, Syllabus, UserSyllabus

USER = "Utestuser1"


async def _make_syllabus(session, *, name: str, day: str, period: int, year: int = 2026, term: str = "前期") -> int:
    subj = Subject(name=name, faculty="経営学部", category="専門")
    session.add(subj)
    await session.flush()
    instr = Instructor(name=f"{name}担当")
    session.add(instr)
    await session.flush()
    cs = CourseSection(subject_id=subj.id, instructor_id=instr.id)
    session.add(cs)
    await session.flush()
    syl = Syllabus(course_section_id=cs.id, year=year, academic_term=term)
    session.add(syl)
    await session.flush()
    session.add(Schedule(syllabus_id=syl.id, day_of_week=day, period=period))
    await session.flush()
    return syl.id


@pytest.mark.asyncio
async def test_register_new_course_creates_one_row(test_sessionmaker):
    async with test_sessionmaker() as session:
        syl_id = await _make_syllabus(session, name="経営学基礎論", day="月", period=1)
        await register_syllabus_for_user(session, USER, syl_id)
        await session.commit()

        rows = (await session.execute(
            select(UserSyllabus).where(UserSyllabus.line_user_id == USER)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].syllabus_id == syl_id


@pytest.mark.asyncio
async def test_register_same_course_twice_is_idempotent(test_sessionmaker):
    async with test_sessionmaker() as session:
        syl_id = await _make_syllabus(session, name="経営管理", day="月", period=1)
        await register_syllabus_for_user(session, USER, syl_id)
        await register_syllabus_for_user(session, USER, syl_id)
        await session.commit()

        rows = (await session.execute(
            select(UserSyllabus).where(UserSyllabus.line_user_id == USER)
        )).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_registering_conflicting_slot_replaces_existing_course(test_sessionmaker):
    async with test_sessionmaker() as session:
        old_id = await _make_syllabus(session, name="旧科目", day="火", period=2, term="前期")
        new_id = await _make_syllabus(session, name="新科目", day="火", period=2, term="前期")
        await register_syllabus_for_user(session, USER, old_id)
        await session.commit()

        await register_syllabus_for_user(session, USER, new_id)
        await session.commit()

        rows = (await session.execute(
            select(UserSyllabus).where(UserSyllabus.line_user_id == USER)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].syllabus_id == new_id


@pytest.mark.asyncio
async def test_non_overlapping_terms_coexist_in_same_slot(test_sessionmaker):
    # 第3クォーターと第4クォーターは同じ曜日・時限でも共存できる(学期が重ならないため)
    async with test_sessionmaker() as session:
        q3_id = await _make_syllabus(session, name="Q3科目", day="水", period=3, term="第3クォーター")
        q4_id = await _make_syllabus(session, name="Q4科目", day="水", period=3, term="第4クォーター")
        await register_syllabus_for_user(session, USER, q3_id)
        await session.commit()

        await register_syllabus_for_user(session, USER, q4_id)
        await session.commit()

        rows = (await session.execute(
            select(UserSyllabus).where(UserSyllabus.line_user_id == USER)
        )).scalars().all()
        assert {r.syllabus_id for r in rows} == {q3_id, q4_id}


@pytest.mark.asyncio
async def test_intensive_course_slot_marker_is_excluded_from_conflict_check(test_sessionmaker):
    # day_of_week="集"(集中講義)は固定コマを持たないため、他の科目との重複判定対象外
    async with test_sessionmaker() as session:
        normal_id = await _make_syllabus(session, name="通常科目", day="木", period=4, term="前期")
        intensive_id = await _make_syllabus(session, name="集中科目", day="集", period=1, term="前期")
        await register_syllabus_for_user(session, USER, normal_id)
        await session.commit()

        await register_syllabus_for_user(session, USER, intensive_id)
        await session.commit()

        rows = (await session.execute(
            select(UserSyllabus).where(UserSyllabus.line_user_id == USER)
        )).scalars().all()
        assert {r.syllabus_id for r in rows} == {normal_id, intensive_id}


@pytest.mark.asyncio
async def test_other_user_registration_is_unaffected(test_sessionmaker):
    async with test_sessionmaker() as session:
        old_id = await _make_syllabus(session, name="A科目", day="金", period=5, term="前期")
        new_id = await _make_syllabus(session, name="B科目", day="金", period=5, term="前期")
        await register_syllabus_for_user(session, "Uuser_a", old_id)
        await session.commit()

        # 別ユーザーが同じコマに別科目を登録しても、user_aの登録には影響しない
        await register_syllabus_for_user(session, "Uuser_b", new_id)
        await session.commit()

        rows_a = (await session.execute(
            select(UserSyllabus).where(UserSyllabus.line_user_id == "Uuser_a")
        )).scalars().all()
        assert {r.syllabus_id for r in rows_a} == {old_id}


@pytest.mark.asyncio
async def test_register_nonexistent_syllabus_id_is_noop(test_sessionmaker):
    async with test_sessionmaker() as session:
        await register_syllabus_for_user(session, USER, 999999)
        await session.commit()

        rows = (await session.execute(
            select(UserSyllabus).where(UserSyllabus.line_user_id == USER)
        )).scalars().all()
        assert rows == []
