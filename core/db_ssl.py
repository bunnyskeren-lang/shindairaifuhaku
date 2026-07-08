import os
import ssl

_CA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "certs", "supabase-ca.crt")


def make_ssl_context() -> ssl.SSLContext:
    """Supabase(Supavisor pooler)向けのSSLコンテキストを生成する。

    Supabaseは全プロジェクト共通の自己署名Root CA（Supabase Root 2021 CA）を
    使っており、標準のシステムCAストアには含まれないため、リポジトリに同梱した
    certs/supabase-ca.crt を明示的に信頼して検証する（dev/prod両方の接続先で
    同一CAであることを実機確認済み）。
    DISABLE_SSL_VERIFY=1 で（緊急時の切り戻し用に）検証を無効化できる。
    """
    if os.environ.get("DISABLE_SSL_VERIFY", "").lower() in ("1", "true", "yes"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=_CA_PATH)
