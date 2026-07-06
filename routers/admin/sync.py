import ssl

import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core import cache
from core.config import DEV_DATABASE_URL
from core.security import check_admin
from database import AsyncSessionLocal
from models import CourseSection, DisplayOrder, Instructor, Subject, SubjectCreditCategory

router = APIRouter()


def _dev_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@router.post("/admin/sync/master_data")
async def sync_master_data_from_dev(_: str = Depends(check_admin)):
    """dev DBのマスタデータ（display_orders/subjects/instructors/course_sections/
    subject_credit_categories）を読み取り、本番(=このサービス)側にUPSERTする。

    reviews・user_profiles・message_logs等のユーザーデータ/ログ/レビューは対象外。
    dev DBはNetwork Restrictionsが無く読み取れるが、本番DBは接続元IPが制限されており
    開発者の手元からは直接反映できないため、本番アプリ自身から取りに行く。
    """
    if not DEV_DATABASE_URL:
        return JSONResponse({"ok": False, "error": "DEV_DATABASE_URL が未設定です"}, status_code=500)

    dev_conn = await asyncpg.connect(DEV_DATABASE_URL, ssl=_dev_ssl_context())
    try:
        order_rows = await dev_conn.fetch(
            "SELECT id, kind, name, sort_order, parent_group, faculty FROM display_orders ORDER BY id"
        )
        subj_rows = await dev_conn.fetch(
            "SELECT id, name, reading, faculty, classification, "
            "category, senmon_group, sort_order, term, term_type, credits, hide_from_timetable "
            "FROM subjects ORDER BY id"
        )
        instr_rows = await dev_conn.fetch("SELECT id, name, sort_order FROM instructors ORDER BY id")
        section_rows = await dev_conn.fetch(
            "SELECT id, subject_id, instructor_id, course_type, syllabus_url "
            "FROM course_sections ORDER BY id"
        )
        credit_cat_rows = await dev_conn.fetch(
            "SELECT id, subject_id, category_id, credits FROM subject_credit_categories ORDER BY id"
        )
    finally:
        await dev_conn.close()

    async with AsyncSessionLocal() as session:
        for r in order_rows:
            values = {
                "kind": r["kind"], "name": r["name"], "sort_order": r["sort_order"],
                "parent_group": r["parent_group"], "faculty": r["faculty"],
            }
            stmt = pg_insert(DisplayOrder).values(id=r["id"], **values).on_conflict_do_update(
                index_elements=["id"], set_=values,
            )
            await session.execute(stmt)

        for r in subj_rows:
            values = {
                "name": r["name"], "reading": r["reading"], "faculty": r["faculty"],
                "classification": r["classification"], "category": r["category"],
                "senmon_group": r["senmon_group"], "sort_order": r["sort_order"],
                "term": r["term"], "term_type": r["term_type"], "credits": r["credits"],
                "hide_from_timetable": r["hide_from_timetable"],
            }
            stmt = pg_insert(Subject).values(id=r["id"], **values).on_conflict_do_update(
                index_elements=["id"], set_=values,
            )
            await session.execute(stmt)

        for r in instr_rows:
            stmt = pg_insert(Instructor).values(
                name=r["name"], sort_order=r["sort_order"],
            ).on_conflict_do_update(
                index_elements=["name"], set_={"sort_order": r["sort_order"]},
            )
            await session.execute(stmt)

        for r in section_rows:
            values = {
                "subject_id": r["subject_id"], "instructor_id": r["instructor_id"],
                "course_type": r["course_type"], "syllabus_url": r["syllabus_url"],
            }
            stmt = pg_insert(CourseSection).values(id=r["id"], **values).on_conflict_do_update(
                index_elements=["id"], set_=values,
            )
            await session.execute(stmt)

        for r in credit_cat_rows:
            values = {
                "subject_id": r["subject_id"], "category_id": r["category_id"], "credits": r["credits"],
            }
            stmt = pg_insert(SubjectCreditCategory).values(id=r["id"], **values).on_conflict_do_update(
                index_elements=["id"], set_=values,
            )
            await session.execute(stmt)

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
        "course_sections": len(section_rows),
        "subject_credit_categories": len(credit_cat_rows),
    })
