import pytest
from fastapi import HTTPException
from starlette.requests import Request

from core.rate_limit import client_ip, rate_limiter


def _make_request(headers: dict[str, str] | None = None, client_host: str = "1.2.3.4") -> Request:
    headers = headers or {}
    scope = {
        "type": "http",
        "path": "/test",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_client_ip_prefers_x_forwarded_for():
    # Renderのようなリバースプロキシ環境ではrequest.client.hostがプロキシの内部IPになり
    # 全リクエストで同一値になってしまうため、X-Forwarded-Forを優先する。
    # ただし先頭値はクライアントが自由に注入できるため、Renderのプロキシが
    # 実接続元IPとして末尾に追記する右端の値のみを信用する
    req = _make_request(headers={"X-Forwarded-For": "203.0.113.5"}, client_host="10.0.0.1")
    assert client_ip(req) == "203.0.113.5"


def test_client_ip_ignores_spoofed_x_forwarded_for_prefix():
    # 攻撃者が「X-Forwarded-For: <偽IP>」を付けて送ると、プロキシは実IPを末尾に追記して
    # 「偽IP, 実IP」の形で渡してくる。先頭を信用するとリクエスト毎に別バケットになり
    # レート制限が無効化されるため、右端（プロキシが追記した実IP）を採用すること
    req = _make_request(headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.5"}, client_host="10.0.0.1")
    assert client_ip(req) == "203.0.113.5"


def test_client_ip_falls_back_to_request_client_host():
    req = _make_request(headers={}, client_host="198.51.100.9")
    assert client_ip(req) == "198.51.100.9"


# ── 境界値: rate_limiter() ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limiter_allows_exactly_max_requests():
    dep = rate_limiter(max_requests=3, window_seconds=60)
    req = _make_request(client_host="192.0.2.1")
    for i in range(3):
        await dep(req)  # 1〜3回目は許可される


@pytest.mark.asyncio
async def test_rate_limiter_rejects_request_beyond_max():
    dep = rate_limiter(max_requests=3, window_seconds=60)
    req = _make_request(client_host="192.0.2.2")
    for _ in range(3):
        await dep(req)
    with pytest.raises(HTTPException) as exc_info:
        await dep(req)  # 4回目(max+1)は拒否される
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_rate_limiter_resets_after_window_expires(monkeypatch):
    dep = rate_limiter(max_requests=2, window_seconds=10)
    req = _make_request(client_host="192.0.2.3")
    t = [1000.0]
    monkeypatch.setattr("core.rate_limit.time.monotonic", lambda: t[0])

    await dep(req)
    await dep(req)
    with pytest.raises(HTTPException):
        await dep(req)

    # window_seconds(10秒)経過後は古いアクセス記録が捨てられ、再び許可される
    t[0] += 10.001
    await dep(req)  # 例外が起きなければ成功


@pytest.mark.asyncio
async def test_rate_limiter_buckets_are_independent_per_ip():
    dep = rate_limiter(max_requests=1, window_seconds=60)
    req_a = _make_request(client_host="192.0.2.10")
    req_b = _make_request(client_host="192.0.2.20")

    await dep(req_a)
    with pytest.raises(HTTPException):
        await dep(req_a)
    # 別IPのバケットは独立しているため、req_aが制限に達していてもreq_bは許可される
    await dep(req_b)


@pytest.mark.asyncio
async def test_rate_limiter_buckets_are_independent_per_path():
    dep = rate_limiter(max_requests=1, window_seconds=60)
    req_p1 = _make_request(client_host="192.0.2.30")
    req_p1.scope["path"] = "/api/a"
    req_p2 = _make_request(client_host="192.0.2.30")
    req_p2.scope["path"] = "/api/b"

    await dep(req_p1)
    with pytest.raises(HTTPException):
        await dep(req_p1)
    # 同一IPでもエンドポイント(path)が違えばバケットは独立している
    await dep(req_p2)
