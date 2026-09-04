import asyncio
import ipaddress
import time
from collections import defaultdict

from fastapi import HTTPException, Request

# Cloudflareが公表している送信元IP範囲（https://www.cloudflare.com/ips-v4 / ips-v6）。
# 本番ドメイン(shindairaifuhaku.com)はCloudflareを経由するため、Renderが直接受け取る
# 接続元IPはCloudflareのエッジサーバーのIPになり、末尾XFFだけを見ると実際の利用者IPが
# 分からない（error_logs調査目的でIPを見ても常にCloudflareのエッジIPしか分からなかった、
# 2026-09-04）。CF-Connecting-IPヘッダーに実クライアントIPが入るが、これは*.onrender.com
# 直叩き等Cloudflareを経由しないリクエストでは任意の値を偽装できてしまうため、
# 直接接続元(XFF末尾)が実際にCloudflareのIP範囲内にある場合のみ信用する。
_CLOUDFLARE_NETWORKS = [
    ipaddress.ip_network(cidr) for cidr in (
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
        "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
        "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
        "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
        "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
        "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
    )
]


def _is_cloudflare_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _CLOUDFLARE_NETWORKS)

# 修正理由: /admin/login のパスワード総当たり、/submit の連投、
# /api/parse_seiseki（PDF解析でCPU負荷が高い）の連打が無制限に可能だった。
# Renderは単一dyno構成のため、インメモリのスライディングウィンドウで
# IPアドレス単位に制限する（再起動でカウンタはリセットされる）。
_buckets: dict[str, list[float]] = defaultdict(list)

# 修正理由: 一度でもアクセスされたpath:ipの組み合わせのキー自体はbucketが空になっても
# 辞書から消えず、長期稼働で単調増加し続けていた。既存rate_limiterのwindow_secondsは
# 全て60秒以下のため、5分アクセスが無いキーは安全に破棄できる。
_CLEANUP_INTERVAL_SECONDS = 300


def client_ip(request: Request) -> str:
    # RenderはエッジプロキシからX-Forwarded-Forでオリジナルのクライアントアドレスを渡すため、
    # request.client.host（Renderの内部プロキシIPになり全リクエストで同一値になってしまう）
    # より優先する。ただし先頭の値はクライアント自身が偽装ヘッダーで自由に注入できる
    # （毎回ランダムな値を送るだけでIP単位の制限が無効化される）ため、
    # Renderのプロキシが実接続元IPとして末尾に追記する値＝右端のみを信用する
    xff = request.headers.get("x-forwarded-for")
    trusted_peer = xff.split(",")[-1].strip() if xff else (
        request.client.host if request.client else "unknown"
    )
    # trusted_peerがCloudflareの場合のみ、CF-Connecting-IPヘッダーの実クライアントIPを
    # 信用する（*.onrender.com直叩き等Cloudflareを経由しないリクエストではこのヘッダーは
    # 任意に偽装できるため無条件には信用しない）
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip and _is_cloudflare_ip(trusted_peer):
        return cf_ip.strip()
    return trusted_peer


def rate_limiter(max_requests: int, window_seconds: float):
    async def _dep(request: Request):
        key = f"{request.url.path}:{client_ip(request)}"
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


def _sweep_stale_buckets() -> int:
    """アクセスの絶えたpath:ipキーを_bucketsから間引く。戻り値は削除件数(テスト用)。"""
    cutoff = time.monotonic() - _CLEANUP_INTERVAL_SECONDS
    stale_keys = [k for k, v in _buckets.items() if not v or v[-1] < cutoff]
    for k in stale_keys:
        _buckets.pop(k, None)
    return len(stale_keys)


async def rate_limit_cleanup_loop() -> None:
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
        _sweep_stale_buckets()
