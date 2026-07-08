import asyncio
import datetime as _dt
import gzip
import json
from decimal import Decimal

import httpx

from core.config import (
    BACKUP_BUCKET,
    BACKUP_ENABLED,
    BACKUP_INTERVAL_HOURS,
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


async def _request_with_retry(client: httpx.AsyncClient, method: str, url: str, *, retries: int = 2, **kwargs):
    """Supabase Storage APIへの呼び出しを一時的な障害に限り指数バックオフで再試行する。

    全操作（アップロード/一覧/削除）は同一パスへの再実行が安全な設計
    （アップロードはx-upsert、一覧・削除は冪等）なので単純リトライで問題ない。
    """
    for attempt in range(retries + 1):
        try:
            resp = await client.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            is_5xx = isinstance(e, httpx.HTTPStatusError) and e.response.status_code >= 500
            is_transport = isinstance(e, httpx.TransportError)
            if attempt == retries or not (is_5xx or is_transport):
                raise
            await asyncio.sleep(1.0 * (2 ** attempt))


async def upload_backup_to_storage(data: bytes) -> None:
    filename = f"backup_{_dt.datetime.now(JST).strftime('%Y%m%d_%H%M')}.sql.gz"
    async with httpx.AsyncClient(timeout=60) as client:
        await _request_with_retry(
            client, "POST",
            f"{SUPABASE_URL}/storage/v1/object/{BACKUP_BUCKET}/{filename}",
            headers={**_storage_headers(), "Content-Type": "application/gzip", "x-upsert": "true"},
            content=data,
        )

        list_resp = await _request_with_retry(
            client, "POST",
            f"{SUPABASE_URL}/storage/v1/object/list/{BACKUP_BUCKET}",
            headers=_storage_headers(),
            json={"prefix": "", "limit": 1000, "sortBy": {"column": "name", "order": "asc"}},
        )

        cutoff = _dt.datetime.now(JST) - _dt.timedelta(days=BACKUP_RETENTION_DAYS)
        stale = []
        for item in list_resp.json():
            name = item.get("name", "")
            if not (name.startswith("backup_") and name.endswith(".sql.gz")):
                continue
            date_part = name[len("backup_"):-len(".sql.gz")]
            try:
                file_date = _dt.datetime.strptime(date_part, "%Y%m%d_%H%M").replace(tzinfo=JST)
            except ValueError:
                continue
            if file_date < cutoff:
                stale.append(name)

        if stale:
            await _request_with_retry(
                client, "DELETE",
                f"{SUPABASE_URL}/storage/v1/object/{BACKUP_BUCKET}",
                headers=_storage_headers(),
                json={"prefixes": stale},
            )


async def backup_loop() -> None:
    """BACKUP_INTERVAL_HOURS間隔で全テーブルをバックアップし、Supabase Storageへアップロードする。"""
    if not BACKUP_ENABLED:
        return
    await asyncio.sleep(30)
    while True:
        try:
            data = await dump_all_tables_to_sql()
            await upload_backup_to_storage(data)
            print(f"Backup uploaded: {len(data)} bytes", flush=True)
        except Exception as e:
            print(f"Backup failed: {e}", flush=True)
        await asyncio.sleep(BACKUP_INTERVAL_HOURS * 3600)
