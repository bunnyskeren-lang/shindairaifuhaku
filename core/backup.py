import asyncio
import datetime as _dt
import gzip
import json
from decimal import Decimal

import httpx

from core.config import (
    BACKUP_BUCKET,
    BACKUP_ENABLED,
    BACKUP_RETENTION_DAYS,
    JST,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)
from database import Base, engine


def _sql_literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'::jsonb"
    if isinstance(value, (_dt.datetime, _dt.date)):
        return "'" + value.isoformat() + "'"
    return "'" + str(value).replace("'", "''") + "'"


async def dump_all_tables_to_sql() -> bytes:
    """全テーブルをFK依存順にSELECTし、INSERT文形式のSQLをgzip圧縮して返す。"""
    from models import (  # noqa: F401
        MessageLog, UserProfile, UserActivity, ErrorLog,
        PushSubscription, DisplayOrder, RichMenuTap,
        CreditRequirement, UserSeisekiRaw,
        Subject, Instructor, CourseSection, Syllabus, Schedule, Review,
        CourseSectionView, UserSyllabus, SubjectCreditCategory,
    )

    lines = []
    async with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            columns = list(table.columns.keys())
            col_list = ", ".join(f'"{c}"' for c in columns)
            result = await conn.execute(table.select())
            rows = result.fetchall()
            lines.append(f"-- {table.name}: {len(rows)} rows")
            for row in rows:
                values = ", ".join(_sql_literal(v) for v in row)
                lines.append(f'INSERT INTO "{table.name}" ({col_list}) VALUES ({values});')
    sql_text = "\n".join(lines) + "\n"
    return gzip.compress(sql_text.encode("utf-8"))


def _storage_headers() -> dict:
    return {
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
    }


async def upload_backup_to_storage(data: bytes) -> None:
    filename = f"backup_{_dt.datetime.now(JST).strftime('%Y%m%d')}.sql.gz"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{SUPABASE_URL}/storage/v1/object/{BACKUP_BUCKET}/{filename}",
            headers={**_storage_headers(), "Content-Type": "application/gzip", "x-upsert": "true"},
            content=data,
        )
        resp.raise_for_status()

        list_resp = await client.post(
            f"{SUPABASE_URL}/storage/v1/object/list/{BACKUP_BUCKET}",
            headers=_storage_headers(),
            json={"prefix": "", "limit": 1000, "sortBy": {"column": "name", "order": "asc"}},
        )
        list_resp.raise_for_status()

        cutoff = _dt.datetime.now(JST) - _dt.timedelta(days=BACKUP_RETENTION_DAYS)
        stale = []
        for item in list_resp.json():
            name = item.get("name", "")
            if not (name.startswith("backup_") and name.endswith(".sql.gz")):
                continue
            date_part = name[len("backup_"):-len(".sql.gz")]
            try:
                file_date = _dt.datetime.strptime(date_part, "%Y%m%d").replace(tzinfo=JST)
            except ValueError:
                continue
            if file_date < cutoff:
                stale.append(name)

        if stale:
            del_resp = await client.request(
                "DELETE",
                f"{SUPABASE_URL}/storage/v1/object/{BACKUP_BUCKET}",
                headers=_storage_headers(),
                json={"prefixes": stale},
            )
            del_resp.raise_for_status()


async def backup_loop() -> None:
    """毎日JST 3:00に全テーブルをバックアップし、Supabase Storageへアップロードする。"""
    if not BACKUP_ENABLED:
        return
    while True:
        now = _dt.datetime.now(JST)
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += _dt.timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        try:
            data = await dump_all_tables_to_sql()
            await upload_backup_to_storage(data)
            print(f"Backup uploaded: {len(data)} bytes", flush=True)
        except Exception as e:
            print(f"Backup failed: {e}", flush=True)
