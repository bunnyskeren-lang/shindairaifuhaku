import ssl

import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core import cache
from core.config import DEV_DATABASE_URL
from core.security import check_admin
from database import AsyncSessionLocal, engine
from models import CourseSection, DisplayOrder, Instructor, Subject, SubjectCreditCategory

router = APIRouter()

_LEGACY_TABLES = [
    "courses", "course_instructors", "syllabus_courses", "course_slots",
    "user_courses", "pending_reviews", "course_views",
]


@router.get("/admin/sync/inspect_legacy")
async def inspect_legacy_tables(_: str = Depends(check_admin)):
    """本番に残っている旧テーブルの存在有無・件数・カラム構成を確認する（読み取り専用）。

    reviews/syllabi/schedules等の本移行スクリプトを書く前提の事前調査用。
    """
    result = {}
    async with engine.begin() as conn:
        for tbl in _LEGACY_TABLES:
            exists = (await conn.execute(text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = :t)"
            ), {"t": tbl})).scalar()
            if not exists:
                result[tbl] = {"exists": False}
                continue
            count = (await conn.execute(text(f"SELECT COUNT(*) FROM {tbl}"))).scalar()
            cols = (await conn.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = :t ORDER BY ordinal_position"
            ), {"t": tbl})).all()
            result[tbl] = {
                "exists": True,
                "count": count,
                "columns": [{"name": c[0], "type": c[1]} for c in cols],
            }
    return JSONResponse(result)


def _dev_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@router.post("/admin/sync/master_data")
async def sync_master_data_from_dev(_: str = Depends(check_admin)):
    """dev DBのマスタデータ（display_orders/subjects/instructors/course_sections/
    subject_credit_categories）を読み取り、本番(=このサービス)側にUPSERTする。

    dev/本番でid列は独立したシーケンスのため、idを直接コピーせず、
    名前などの自然キーで既存行を突き合わせてUPSERTする（idの衝突・identity列の制約を回避）。
    reviews・user_profiles・message_logs等のユーザーデータ/ログ/レビューは対象外。
    dev DBはNetwork Restrictionsが無く読み取れるが、本番DBは接続元IPが制限されており
    開発者の手元からは直接反映できないため、本番アプリ自身から取りに行く。
    """
    if not DEV_DATABASE_URL:
        return JSONResponse({"ok": False, "error": "DEV_DATABASE_URL が未設定です"}, status_code=500)

    dev_conn = await asyncpg.connect(DEV_DATABASE_URL, ssl=_dev_ssl_context())
    try:
        order_rows = await dev_conn.fetch(
            "SELECT kind, name, sort_order, parent_group, faculty FROM display_orders ORDER BY id"
        )
        subj_rows = await dev_conn.fetch(
            "SELECT name, reading, faculty, classification, category, senmon_group, "
            "sort_order, term, term_type, credits, hide_from_timetable "
            "FROM subjects ORDER BY id"
        )
        instr_rows = await dev_conn.fetch("SELECT name, sort_order FROM instructors ORDER BY id")
        section_rows = await dev_conn.fetch(
            "SELECT s.name AS subject_name, i.name AS instructor_name, "
            "cs.course_type, cs.syllabus_url "
            "FROM course_sections cs "
            "JOIN subjects s ON s.id = cs.subject_id "
            "JOIN instructors i ON i.id = cs.instructor_id "
            "ORDER BY cs.id"
        )
        credit_cat_rows = await dev_conn.fetch(
            "SELECT s.name AS subject_name, scc.category_id, scc.credits "
            "FROM subject_credit_categories scc "
            "JOIN subjects s ON s.id = scc.subject_id "
            "ORDER BY scc.id"
        )
    finally:
        await dev_conn.close()

    async with AsyncSessionLocal() as session:
        # ── display_orders: 自然キー (kind, name, faculty) ──────────────────
        for r in order_rows:
            values = {"sort_order": r["sort_order"], "parent_group": r["parent_group"]}
            stmt = pg_insert(DisplayOrder).values(
                kind=r["kind"], name=r["name"], faculty=r["faculty"], **values,
            ).on_conflict_do_update(
                index_elements=["kind", "name", "faculty"], set_=values,
            )
            await session.execute(stmt)

        # ── subjects: 自然キー = name（unique制約が無いため手動UPSERT） ──────
        for r in subj_rows:
            values = {
                "reading": r["reading"], "faculty": r["faculty"], "classification": r["classification"],
                "category": r["category"], "senmon_group": r["senmon_group"], "sort_order": r["sort_order"],
                "term": r["term"], "term_type": r["term_type"], "credits": r["credits"],
                "hide_from_timetable": r["hide_from_timetable"],
            }
            existing_id = (await session.execute(
                select(Subject.id).where(Subject.name == r["name"])
            )).scalar_one_or_none()
            if existing_id is not None:
                await session.execute(
                    Subject.__table__.update().where(Subject.id == existing_id).values(**values)
                )
            else:
                await session.execute(
                    Subject.__table__.insert().values(name=r["name"], **values)
                )

        # ── instructors: 自然キー = name（unique制約あり） ───────────────────
        for r in instr_rows:
            stmt = pg_insert(Instructor).values(
                name=r["name"], sort_order=r["sort_order"],
            ).on_conflict_do_update(
                index_elements=["name"], set_={"sort_order": r["sort_order"]},
            )
            await session.execute(stmt)

        await session.flush()

        subject_id_by_name = dict((await session.execute(select(Subject.name, Subject.id))).all())
        instructor_id_by_name = dict((await session.execute(select(Instructor.name, Instructor.id))).all())

        # ── course_sections: 自然キー = (subject_id, instructor_id) ─────────
        cs_count = 0
        for r in section_rows:
            sid = subject_id_by_name.get(r["subject_name"])
            iid = instructor_id_by_name.get(r["instructor_name"])
            if not sid or not iid:
                continue
            values = {"course_type": r["course_type"], "syllabus_url": r["syllabus_url"]}
            stmt = pg_insert(CourseSection).values(
                subject_id=sid, instructor_id=iid, **values,
            ).on_conflict_do_update(
                index_elements=["subject_id", "instructor_id"], set_=values,
            )
            await session.execute(stmt)
            cs_count += 1

        # ── subject_credit_categories: 自然キー = (subject_id, category_id) ─
        scc_count = 0
        for r in credit_cat_rows:
            sid = subject_id_by_name.get(r["subject_name"])
            if not sid:
                continue
            stmt = pg_insert(SubjectCreditCategory).values(
                subject_id=sid, category_id=r["category_id"], credits=r["credits"],
            ).on_conflict_do_update(
                index_elements=["subject_id", "category_id"], set_={"credits": r["credits"]},
            )
            await session.execute(stmt)
            scc_count += 1

        await session.commit()

    cache.invalidate_courses_cache()
    cache.invalidate_cls_caches()
    cache.invalidate_faculty_order_cache()
    cache.invalidate_credit_group_order_cache()
    cache.invalidate_senmon_cache()

    return JSONResponse({
        "ok": True,
        "display_orders": len(order_rows),
        "subjects": len(subj_rows),
        "instructors": len(instr_rows),
        "course_sections": cs_count,
        "subject_credit_categories": scc_count,
    })
