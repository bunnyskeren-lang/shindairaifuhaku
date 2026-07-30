import base64
import hashlib
import hmac
import time
from datetime import datetime, timezone

from core.config import ADMIN_TOKEN_TTL, CHANNEL_SECRET
from core.security import (
    _HMAC_KEY,
    make_admin_token,
    verify_admin_token,
    verify_line_signature,
)


def test_admin_token_round_trip():
    token = make_admin_token()
    assert verify_admin_token(token)


def test_admin_token_rejects_tampered_signature():
    token = make_admin_token()
    ts, nonce, sig = token.split(":")
    bad_sig = "0" * len(sig) if sig[0] != "0" else "1" * len(sig)
    assert not verify_admin_token(f"{ts}:{nonce}:{bad_sig}")


def test_admin_token_rejects_expired():
    ts = int(datetime.now(timezone.utc).timestamp()) - ADMIN_TOKEN_TTL - 1
    nonce = "deadbeefdeadbeef"
    sig = hmac.new(_HMAC_KEY, f"admin:{ts}:{nonce}".encode(), hashlib.sha256).hexdigest()
    assert not verify_admin_token(f"{ts}:{nonce}:{sig}")


def test_admin_token_accepts_just_within_ttl():
    ts = int(datetime.now(timezone.utc).timestamp()) - ADMIN_TOKEN_TTL + 5
    nonce = "deadbeefdeadbeef"
    sig = hmac.new(_HMAC_KEY, f"admin:{ts}:{nonce}".encode(), hashlib.sha256).hexdigest()
    assert verify_admin_token(f"{ts}:{nonce}:{sig}")


def test_admin_token_rejects_malformed_strings():
    assert not verify_admin_token("")
    assert not verify_admin_token("not-a-token")
    assert not verify_admin_token("1:2")  # パーツ数が足りない
    assert not verify_admin_token("1:2:3:4")  # パーツ数が多すぎる
    assert not verify_admin_token("abc:nonce:sig")  # tsが数値でない


def test_line_signature_valid():
    body = b'{"events": []}'
    digest = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode()
    assert verify_line_signature(body, signature)


def test_line_signature_rejects_tampered_body():
    body = b'{"events": []}'
    digest = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode()
    assert not verify_line_signature(b'{"events": [1]}', signature)


def test_line_signature_rejects_wrong_signature():
    body = b'{"events": []}'
    assert not verify_line_signature(body, base64.b64encode(b"wrong").decode())


def test_admin_token_timestamp_is_current():
    before = int(time.time())
    token = make_admin_token()
    after = int(time.time())
    ts = int(token.split(":")[0])
    assert before <= ts <= after
