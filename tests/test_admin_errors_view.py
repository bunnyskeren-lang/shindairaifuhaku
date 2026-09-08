"""routers/admin/users_errors.py の /admin/errors ビュー分割のテスト。

error_logs には本物の障害に加えて「レビュー二重送信（submit_duplicate:）」の
想定内テレメトリが相乗りしている。既定ビューは本物の障害だけを出し、
?view=submit_duplicate でテレメトリだけを出す。action IS NULL の障害行が
NOT LIKE 条件で取りこぼされないことも確認する。
"""
import pytest

import routers.admin.users_errors as users_errors
from core.config import ADMIN_COOKIE
from core.security import make_admin_token
from models import ErrorLog


async def _seed_errors(test_sessionmaker):
    async with test_sessionmaker() as session:
        session.add_all([
            ErrorLog(user_id=None, action=None,
                     error_type="ValueError", error_message="本物の障害A", traceback="tb"),
            ErrorLog(user_id=None, action="submit_push_notification",
                     error_type="RuntimeError", error_message="本物の障害B", traceback="tb"),
            ErrorLog(user_id=None, action="submit_duplicate:経営管理",
                     error_type="RuntimeError", error_message="この科目・担当教員には既に投稿済みです",
                     traceback="tb"),
            ErrorLog(user_id=None, action="submit_duplicate:簿記",
                     error_type="RuntimeError", error_message="この科目のオムニバスには既に投稿済みです",
                     traceback="tb"),
        ])
        await session.commit()


def _admin_client(http_client_factory, monkeypatch):
    client = http_client_factory(users_errors, monkeypatch)
    client.cookies.set(ADMIN_COOKIE, make_admin_token())
    return client


@pytest.mark.asyncio
async def test_default_view_excludes_submit_duplicate_but_keeps_null_action_errors(
    http_client_factory, monkeypatch, test_sessionmaker
):
    await _seed_errors(test_sessionmaker)
    client = _admin_client(http_client_factory, monkeypatch)

    resp = await client.get("/admin/errors")
    assert resp.status_code == 200
    assert "本物の障害A" in resp.text   # action IS NULL でも出る
    assert "本物の障害B" in resp.text
    assert "オムニバスには既に投稿済み" not in resp.text
    assert "担当教員には既に投稿済み" not in resp.text
    # タブのバッジは二重送信の総数（2件）を出す
    assert "レビュー二重送信（2）" in resp.text


@pytest.mark.asyncio
async def test_submit_duplicate_view_shows_only_telemetry(
    http_client_factory, monkeypatch, test_sessionmaker
):
    await _seed_errors(test_sessionmaker)
    client = _admin_client(http_client_factory, monkeypatch)

    resp = await client.get("/admin/errors?view=submit_duplicate")
    assert resp.status_code == 200
    assert "オムニバスには既に投稿済み" in resp.text
    assert "担当教員には既に投稿済み" in resp.text
    assert "本物の障害A" not in resp.text
    assert "本物の障害B" not in resp.text
