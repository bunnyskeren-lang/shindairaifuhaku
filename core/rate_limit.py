import time
from collections import defaultdict

from fastapi import HTTPException, Request

# 修正理由: /admin/login のパスワード総当たり、/submit の連投、
# /api/parse_seiseki（PDF解析でCPU負荷が高い）の連打が無制限に可能だった。
# Renderは単一dyno構成のため、インメモリのスライディングウィンドウで
# IPアドレス単位に制限する（再起動でカウンタはリセットされる）。
_buckets: dict[str, list[float]] = defaultdict(list)


def _client_ip(request: Request) -> str:
    # RenderはエッジプロキシからX-Forwarded-Forでオリジナルのクライアントアドレスを渡すため、
    # request.client.host（Renderの内部プロキシIPになり全リクエストで同一値になってしまう）
    # より優先する。値はプロキシ経路順にカンマ区切りで並ぶため先頭が実クライアントIP
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limiter(max_requests: int, window_seconds: float):
    async def _dep(request: Request):
        key = f"{request.url.path}:{_client_ip(request)}"
        now = time.monotonic()
        cutoff = now - window_seconds
        bucket = _buckets[key]
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= max_requests:
            raise HTTPException(
                status_code=429,
                detail="リクエストが多すぎます。しばらく待ってから再度お試しください",
            )
        bucket.append(now)
    return _dep
