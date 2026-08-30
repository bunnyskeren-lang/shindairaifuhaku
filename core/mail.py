import httpx

from core.activity_log import save_error_log
from core.config import BREVO_API_KEY, MAIL_FROM_ADDRESS, MAIL_FROM_NAME

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


async def send_verification_email(to_email: str, verify_url: str, user_id: str | None = None) -> bool:
    """Brevo Transactional Email APIでメール認証用のマジックリンクを送信する。
    BREVO_API_KEY/MAIL_FROM_ADDRESS未設定時（送信サービス契約前）はエラーログに記録した上で
    送信をスキップする。呼び出し元へのユーザー向け失敗文言は実際のAPI障害と同じだが、
    運営側が/admin/errorsで「設定ミス」と「実際の送信障害」を区別できるようerror_type/messageを
    分けて残す（2026-08-30、両方とも同じFalse・同じprint()止まりで見分けが付かなかったのを修正）。
    """
    if not BREVO_API_KEY or not MAIL_FROM_ADDRESS:
        print(f"[mail] BREVO_API_KEY/MAIL_FROM_ADDRESS未設定のため送信をスキップ: to={to_email} url={verify_url}")
        await save_error_log(
            RuntimeError("BREVO_API_KEY/MAIL_FROM_ADDRESS未設定のため送信をスキップしました"),
            user_id=user_id, action="send_verification_email/not_configured",
        )
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                BREVO_API_URL,
                headers={"api-key": BREVO_API_KEY, "content-type": "application/json"},
                json={
                    "sender": {"name": MAIL_FROM_NAME, "email": MAIL_FROM_ADDRESS},
                    "to": [{"email": to_email}],
                    "subject": "【神大ライフハック】メールアドレス確認のお願い",
                    "htmlContent": (
                        "<p>なりすまし防止のため、以下のリンクをタップして本人確認を完了してください"
                        "（30分以内、他の方には転送しないでください）。</p>"
                        f'<p><a href="{verify_url}">{verify_url}</a></p>'
                    ),
                },
            )
            resp.raise_for_status()
            return True
    except Exception as exc:
        await save_error_log(exc, user_id=user_id, action="send_verification_email")
        return False
