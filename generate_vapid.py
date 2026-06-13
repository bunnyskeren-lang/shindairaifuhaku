"""
VAPIDキーを生成するスクリプト。初回だけ実行してください。
出力された値を両方のRenderサービスの環境変数に設定してください。

実行: python generate_vapid.py
"""
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import base64

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()

key = ec.generate_private_key(ec.SECP256R1(), default_backend())
pub = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
priv_raw = key.private_numbers().private_value.to_bytes(32, 'big')

print("以下の3つを shindairaifuhaku と shindairaifuhaku-1 の両方の環境変数に設定してください:\n")
print(f"VAPID_PUBLIC_KEY={b64url(pub)}")
print(f"VAPID_PRIVATE_KEY={b64url(priv_raw)}")
print(f"VAPID_EMAIL=your-email@example.com  ← 自分のメアドに変えてください")
