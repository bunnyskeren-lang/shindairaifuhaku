import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
os.environ.setdefault("LINE_CHANNEL_SECRET", "test_channel_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_channel_access_token")
os.environ.setdefault("ADMIN_PASSWORD", "test_admin_password")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import BigInteger  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402

# 修正理由: 結合テストではdev DB(Supabase/PostgreSQL)に接続できない実行環境が
# あるため、SQLiteインメモリDBで代替する。


# 修正理由: 全テーブルのid主キーはBigInteger(autoincrement=True)だが、SQLiteは
# 「型名が厳密にINTEGERの単一列主キー」だけをrowidエイリアスとして自動採番する。
# BIGINTのままだと自動採番されずid=NULLでINSERTしようとしてNOT NULL制約違反になる
# ため、SQLite方言のときだけINTEGERとしてコンパイルする。
@compiles(BigInteger, "sqlite")
def _compile_biginteger_as_integer_for_sqlite(element, compiler, **kw):
    return "INTEGER"


@pytest_asyncio.fixture
async def async_engine():
    """テストごとに独立したSQLiteインメモリDBを作成し、全テーブルを作成する。"""
    import database
    import models  # noqa: F401  (Base.metadataへのテーブル登録に必要)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_sessionmaker(async_engine):
    """SQLiteインメモリDBに紐づくasync_sessionmaker。本番のAsyncSessionLocalと
    同じパラメータ(expire_on_commit=False)で作成する。"""
    return async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


def patch_async_session_local(monkeypatch, module, sessionmaker_):
    """指定モジュールが`from database import AsyncSessionLocal`のように
    トップレベルでimportしている場合、`database.AsyncSessionLocal`を書き換えても
    そのモジュール内の参照は古いエンジンを掴んだままになる。使用側モジュールの
    属性を個別に差し替えることでテスト用DBへ向ける。"""
    monkeypatch.setattr(module, "AsyncSessionLocal", sessionmaker_)


@pytest.fixture(autouse=True)
def _reset_admin_revoke_cache():
    """core.cache.get_admin_revoke_epoch_cached()はTTL(10秒)付きでモジュールグローバルに
    キャッシュされる。テストごとに独立したSQLiteインメモリDBを使うため、前のテストで
    キャッシュされた値が短時間残って別テストのDBに対する判定に混ざらないよう都度リセットする。"""
    from core import cache
    cache.invalidate_admin_revoke_cache()
    yield
    cache.invalidate_admin_revoke_cache()


@pytest.fixture(autouse=True)
def _reset_ban_status_cache():
    """core.cache._ban_status_cacheはline_user_id単位でTTL付きモジュールグローバルに
    キャッシュされる。テストごとに独立したSQLiteインメモリDBを使うため、同じuidを
    複数テストで使い回すとBAN状態のキャッシュが古いDBの結果のまま次のテストへ
    漏れて誤判定を起こす(2026-08-29、tests/test_ban_feature.py追加時に発覚)。"""
    from core import cache
    cache._ban_status_cache.clear()
    yield
    cache._ban_status_cache.clear()


@pytest.fixture(autouse=True)
def _reset_courses_cache():
    """core.cache.get_courses_cached()等(科目一覧・バリアントグループ・募集枠)はTTL付きで
    モジュールグローバルにキャッシュされる。テストごとに独立したSQLiteインメモリDBを使うため、
    前のテストでキャッシュされた科目一覧が残っていると、別テストのDBに存在しない科目名で
    バリアントグループ判定をしてしまい誤判定になる(2026-09-01、バリアントグループの
    レビュー投稿上限テスト追加時に発覚)。"""
    from core import cache
    cache.invalidate_courses_cache()
    cache.invalidate_full_pairs_cache()
    yield
    cache.invalidate_courses_cache()
    cache.invalidate_full_pairs_cache()


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """core.rate_limit._bucketsはIPアドレス単位のグローバル状態で、テストクライアントは
    毎回同一の疑似IPを使うため、レート制限テスト以外のE2Eテストが429で誤って
    落ちるのを防ぐためテストごとにクリアする。"""
    from core.rate_limit import _buckets
    _buckets.clear()
    yield
    _buckets.clear()


@pytest_asyncio.fixture
async def http_client_factory(test_sessionmaker):
    """指定したrouterモジュールだけをマウントした軽量FastAPIアプリに対して
    httpx.AsyncClient(ASGITransport)でリクエストできるファクトリを提供する。
    main.appはlifespanで実DBへのinit_db()・自己ping等を行うため使わず、
    対象routerのみを含む最小アプリを都度構築する。DBはtest_sessionmakerに
    差し替え済みなので、呼び出し側はさらに認証関数等を個別にmonkeypatchすること。"""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    created_clients = []

    def _factory(router_module, monkeypatch):
        patch_async_session_local(monkeypatch, router_module, test_sessionmaker)
        # core.security.check_admin は core.cache.get_admin_revoke_epoch_cached() 経由で
        # DBを参照する(管理者トークンのサーバー側失効機構)。/admin/*系のrouterは全てこの
        # 依存を持つため、router自身だけでなくcore.cacheのAsyncSessionLocalも差し替える
        import core.cache as cache_module
        patch_async_session_local(monkeypatch, cache_module, test_sessionmaker)
        app = FastAPI()
        app.include_router(router_module.router)
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        created_clients.append(client)
        return client

    yield _factory
    for c in created_clients:
        await c.aclose()
