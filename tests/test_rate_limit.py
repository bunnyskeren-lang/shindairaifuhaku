from starlette.requests import Request

from core.rate_limit import client_ip


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
