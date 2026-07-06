import ssl

import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core import cache
from core.config import DEV_DATABASE_URL
from core.security import check_admin
from database import AsyncSessionLocal
from models import Instructor, Subject

router = APIRouter()


def _dev_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@router.post("/admin/sync/subjects_instructors")
async def sync_subjects_instructors_from_dev(_: str = Depends(check_admin)):
    """dev DBのsubjects/instructorsのみを読み取り、本番(=このサービス)側にUPSERTする。

    dev DBはNetwork Restrictionsが無く読み取れるが、本番DBは接続元IPが制限されており
    開発者の手元からは直接反映できないため、本番アプリ自身から取りに行く。
    """
    if not DEV_DATABASE_URL:
        return JSONResponse({"ok": False, "error": "DEV_DATABASE_URL が未設定です"}, status_code=500)

    dev_conn = await asyncpg.connect(DEV_DATABASE_URL, ssl=_dev_ssl_context())
    try:
        subj_rows = await dev_conn.fetch(
            "SELECT id, name, reading, faculty, classification, "
            "category, senmon_group, sort_order, term, term_type, credits "
            "FROM subjects ORDER BY id"
        )
        instr_rows = await dev_conn.fetch("SELECT id, name, sort_order FROM instructors ORDER BY id")
    finally:
        await dev_conn.close()

    async with AsyncSessionLocal() as session:
        for r in subj_rows:
            values = {
                "name": r["name"], "reading": r["reading"], "faculty": r["faculty"],
                "classification": r["classification"], "category": r["category"],
                "senmon_group": r["senmon_group"], "sort_order": r["sort_order"],
                "term": r["term"], "term_type": r["term_type"], "credits": r["credits"],
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

        await session.commit()

    cache.invalidate_courses_cache()
    cache.invalidate_cls_caches()

    return JSONResponse({"ok": True, "subjects": len(subj_rows), "instructors": len(instr_rows)})
