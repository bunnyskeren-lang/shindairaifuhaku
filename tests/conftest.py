import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
os.environ.setdefault("LINE_CHANNEL_SECRET", "test_channel_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_channel_access_token")
os.environ.setdefault("ADMIN_PASSWORD", "test_admin_password")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import BigInteger  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402

# 修正理由: 結合テストではdev DB(Supabase/PostgreSQL)に接続できない実行環境が
# あるため、SQLiteインメモリDBで代替する。models.pyのCreditRequirement.combined_of/
# UserSeisekiRaw.raw_jsonはPostgreSQL固有のJSONB型を使っており、SQLiteのDDL
# コンパイラは素通しできず`UnsupportedCompilationError`になる。models.py本体は
# 本番のPostgreSQL用定義のまま変更せず、テスト時のみSQLite方言でJSONB→JSON
# として振る舞うようcompilerルールを追加する。


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):
    return "JSON"


# 修正理由: 全テーブルのid主キーはBigInteger(autoincrement=True)だが、SQLiteは
# 「型名が厳密にINTEGERの単一列主キー」だけをrowidエイリアスとして自動採番する。
# BIGINTのままだと自動採番されずid=NULLでINSERTしようとしてNOT NULL制約違反になる
# ため、SQLite方言のときだけINTEGERとしてコンパイルする。
@compiles(BigInteger, "sqlite")
def _compile_biginteger_as_integer_for_sqlite(element, compiler, **kw):
    return "INTEGER"


def _register_postgres_stub_functions(dbapi_connection, connection_record):
    """core/required_subjects.pyのregister_syllabus_for_user()は同時押し対策として
    PostgreSQL固有のpg_advisory_xact_lock(hashtext(...))でユーザー単位のトランザクション
    ロックを取る。SQLiteにこの関数は存在しないため、SQL文はそのまま実行しつつ
    ロック自体は何もしないスタブ関数をDBAPI接続に登録する（単一プロセス内のテストでは
    実際のロック取得は不要なため副作用として問題ない）。"""
    dbapi_connection.create_function("hashtext", 1, lambda s: hash(s) % (2**31))
    dbapi_connection.create_function("pg_advisory_xact_lock", 1, lambda _lock_id: None)


@pytest_asyncio.fixture
async def async_engine():
    """テストごとに独立したSQLiteインメモリDBを作成し、全テーブルを作成する。"""
    from sqlalchemy import event

    import database
    import models  # noqa: F401  (Base.metadataへのテーブル登録に必要)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listens_for(engine.sync_engine, "connect")(_register_postgres_stub_functions)
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
        app = FastAPI()
        app.include_router(router_module.router)
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        created_clients.append(client)
        return client

    yield _factory
    for c in created_clients:
        await c.aclose()
