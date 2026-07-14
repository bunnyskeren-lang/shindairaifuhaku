import base64
import hashlib
import hmac
import secrets as py_secrets
from datetime import datetime, timezone

from fastapi import HTTPException, Request

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


def check_admin(request: Request):
    if not verify_admin_token(request.cookies.get(ADMIN_COOKIE, "")):
        raise HTTPException(status_code=302, headers={"Location": f"/admin/login?next={request.url.path}"})


def verify_line_signature(body: bytes, signature: str) -> bool:
    digest = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


_SHARE_HMAC_KEY = hashlib.sha256((CHANNEL_SECRET + "timetable_share").encode()).digest()


def make_share_token(line_user_id: str, version: int) -> str:
    """マイ時間割の友達共有リンク用トークンを発行する。有効期限は無いが、
    UserProfile.share_token_versionと照合することで「共有を停止する」による失効に対応する。"""
    payload = f"{line_user_id}:{version}"
    sig = hmac.new(_SHARE_HMAC_KEY, payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{sig}"


def verify_share_token(token: str) -> tuple[str, int] | None:
    try:
        payload, sig = token.rsplit(".", 1)
        line_user_id, version_str = payload.rsplit(":", 1)
        expected = hmac.new(_SHARE_HMAC_KEY, payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        return line_user_id, int(version_str)
    except Exception:
        return None
