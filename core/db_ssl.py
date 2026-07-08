import os
import ssl


def make_ssl_context() -> ssl.SSLContext:
    """Supabase(Supavisor pooler)向けのSSLコンテキストを生成する。

    Supabaseのpoolerはチェーン内にself-signed証明書を返すため、標準の
    CA検証は `CERTIFICATE_VERIFY_FAILED: self-signed certificate in
    certificate chain` で失敗することを実機確認済み。そのため通信は暗号化
    されるが証明書の身元検証は行わないのが既定動作。
    ENABLE_SSL_VERIFY=1 かつ Supabaseダッシュボードで発行されるプロジェクト
    固有のCA証明書を別途配置・信頼できる場合のみ検証を有効化できる。
    """
    ctx = ssl.create_default_context()
    if os.environ.get("ENABLE_SSL_VERIFY", "").lower() not in ("1", "true", "yes"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx
