import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
os.environ.setdefault("LINE_CHANNEL_SECRET", "test_channel_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_channel_access_token")
os.environ.setdefault("ADMIN_PASSWORD", "test_admin_password")

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
