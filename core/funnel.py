"""友だち追加〜会員登録〜レビュー投稿の入口の計測（2026-09-25）。

背景: レビュー収集第1弾で、Discordの呼びかけ→登録画面→登録完了のどこで人が離れたかが
サーバーに一切残っておらず、「反響が悪かった」原因を切り分けられなかった。各ページの表示と
登録完了を`funnel_events`に1行ずつ記録し、`/admin/usage-stats`で段階ごとの到達数を見られるようにする。

記録するもの:
- event: 下記 EVENT_* のいずれか
- source: リンクに付けた `?src=`（例: `/join?src=discord_0925`）。付いていなければ空
- visitor_id: ブラウザごとにサーバーが発行するランダムなCookie（`lh_vid`）。LINEユーザーIDや
  学籍番号とは紐付けない。同じ人の再訪を数えるための目安（LINEアプリ内ブラウザと外部ブラウザを
  行き来すると別人として数えられるので、厳密な人数ではない）

ボット（Discord/LINE/Twitter等のリンクプレビュー取得や各種クローラー）のアクセスは記録しない。
ページ表示に付随する書き込みなので、連打時は計測だけ黙って捨てる（ページ自体は通常どおり返す）。
"""
import logging
import re
import secrets

from fastapi import Request, Response

from core.background_tasks import fire_and_forget
from core.rate_limit import rate_limit_allows
from database import AsyncSessionLocal
from models import FunnelEvent

logger = logging.getLogger(__name__)

EVENT_JOIN_VIEW = "join_view"                    # /join（友だち追加のOGPランディング）
EVENT_LIFF_REVIEW_VIEW = "liff_review_view"      # /liff/review（レビュー投稿フォームのLIFF中継）
EVENT_REVIEW_FORM_VIEW = "review_form_view"      # /（レビュー投稿フォーム。リッチメニュー経由の既存ユーザーも含む）
EVENT_REGISTER_VIEW = "register_view"            # /register（会員登録画面。未登録者にだけ出る）
EVENT_REGISTER_DONE = "register_done"            # 会員登録の新規完了（POST /api/register 成功）

# 漏斗として画面に出す順序（管理画面もこの順で並べる）
FUNNEL_EVENTS_IN_ORDER = (
    EVENT_JOIN_VIEW,
    EVENT_LIFF_REVIEW_VIEW,
    EVENT_REVIEW_FORM_VIEW,
    EVENT_REGISTER_VIEW,
    EVENT_REGISTER_DONE,
)

VISITOR_COOKIE = "lh_vid"
_VISITOR_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
_VISITOR_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SOURCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

# リンクプレビュー・クローラー・監視系のUser-Agent。LINEアプリ内ブラウザのUAは "Line/x.y.z" を
# 含むが "bot" 等は含まないので、実ユーザーは除外されない。
_BOT_UA_RE = re.compile(
    r"bot|crawl|spider|slurp|facebookexternalhit|line-poker|preview|fetch|monitor|"
    r"headless|python-|curl/|wget|go-http-client|uptime|render",
    re.IGNORECASE,
)


def is_bot_user_agent(user_agent: str | None) -> bool:
    # UAが無いリクエストは実ブラウザではまず有り得ないので、ボット扱いにする
    return not user_agent or bool(_BOT_UA_RE.search(user_agent))


def sanitize_source(raw: str | None) -> str:
    """`?src=`の値。英数字・アンダースコア・ハイフンのみ40字まで。それ以外は空にする。"""
    raw = (raw or "").strip()
    return raw if _SOURCE_RE.match(raw) else ""


def _visitor_id_from_cookie(request: Request) -> str | None:
    vid = request.cookies.get(VISITOR_COOKIE, "")
    return vid if _VISITOR_ID_RE.match(vid) else None


async def _insert(event: str, source: str, visitor_id: str | None) -> None:
    try:
        async with AsyncSessionLocal() as session:
            session.add(FunnelEvent(event=event, source=source, visitor_id=visitor_id))
            await session.commit()
    except Exception:  # 計測の失敗でページを壊さない
        logger.warning("funnel event insert failed: %s", event, exc_info=True)


def track(
    request: Request,
    response: Response | None,
    event: str,
    *,
    set_cookie: bool = True,
) -> None:
    """ページ表示・登録完了を1件記録する。ページの応答は絶対に妨げない。

    response を渡し、かつ set_cookie=True なら、未発行のブラウザには visitor_id を発行して
    Set-Cookie する。共有キャッシュされうる応答（Cache-Control: public）では set_cookie=False にする。
    """
    if request.method == "HEAD" or is_bot_user_agent(request.headers.get("user-agent")):
        return
    if not rate_limit_allows(request, "funnel", max_requests=30, window_seconds=60):
        return
    visitor_id = _visitor_id_from_cookie(request)
    if visitor_id is None and set_cookie and response is not None:
        visitor_id = secrets.token_hex(16)
        response.set_cookie(
            VISITOR_COOKIE, visitor_id,
            max_age=_VISITOR_COOKIE_MAX_AGE, httponly=True, samesite="lax",
            secure=request.url.scheme == "https",
        )
    source = sanitize_source(request.query_params.get("src"))
    fire_and_forget(_insert(event, source, visitor_id))
