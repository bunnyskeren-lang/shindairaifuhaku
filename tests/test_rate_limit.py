from starlette.requests import Request

from core.rate_limit import _client_ip


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
    # 全リクエストで同一値になってしまうため、X-Forwarded-Forの先頭値を優先する
    req = _make_request(headers={"X-Forwarded-For": "203.0.113.5, 10.0.0.1"}, client_host="10.0.0.1")
    assert _client_ip(req) == "203.0.113.5"


def test_client_ip_falls_back_to_request_client_host():
    req = _make_request(headers={}, client_host="198.51.100.9")
    assert _client_ip(req) == "198.51.100.9"
