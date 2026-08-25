import base64
import hashlib
import hmac
import secrets as py_secrets
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from core import cache
from core.config import ADMIN_COOKIE, ADMIN_PASSWORD, ADMIN_TOKEN_TTL, CHANNEL_SECRET

_HMAC_KEY = hashlib.sha256((CHANNEL_SECRET + ADMIN_PASSWORD).encode()).digest()


def make_admin_token() -> str:
    ts = int(datetime.now(timezone.utc).timestamp())
    nonce = py_secrets.token_hex(8)
    sig = hmac.new(_HMAC_KEY, f"admin:{ts}:{nonce}".encode(), hashlib.sha256).hexdigest()
    return f"{ts}:{nonce}:{sig}"


def verify_admin_token(token: str) -> bool:
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return False
        ts_str, nonce, sig = parts
        ts = int(ts_str)
        if datetime.now(timezone.utc).timestamp() - ts > ADMIN_TOKEN_TTL:
            return False
        expected = hmac.new(_HMAC_KEY, f"admin:{ts_str}:{nonce}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


async def check_admin(request: Request):
    token = request.cookies.get(ADMIN_COOKIE, "")
    if not verify_admin_token(token):
        raise HTTPException(status_code=302, headers={"Location": f"/admin/login?next={request.url.path}"})
    # サーバー側の一括ログアウト対応。トークン自体はTTL(4時間)いっぱい署名的には有効だが、
    # ログアウト以前に発行されたトークンはrevoked_beforeより古いものとして拒否する
    revoke_epoch = await cache.get_admin_revoke_epoch_cached()
    if revoke_epoch and int(token.split(":", 1)[0]) < revoke_epoch:
        raise HTTPException(status_code=302, headers={"Location": f"/admin/login?next={request.url.path}"})


def verify_line_signature(body: bytes, signature: str) -> bool:
    digest = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)
