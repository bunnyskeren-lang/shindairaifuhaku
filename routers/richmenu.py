from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from core.config import REVIEW_FORM_URL
from core.rate_limit import rate_limiter
from database import AsyncSessionLocal
from models import RichMenuTap

router = APIRouter()

RICHMENU_URLS: dict[str, str] = {
    "review":    REVIEW_FORM_URL,
    "beefplus":  "https://beefplus.center.kobe-u.ac.jp/login",
    "uribop":    "https://www.uriboportal.ofc.kobe-u.ac.jp/",
    "shokudo":   "https://west2-univ.jp/sp/kobe-univ.php",
    "toshokan":  "https://lib.kobe-u.ac.jp/services/barcode/",
    "bus":       "https://kotsu.city.kobe.lg.jp/",
}

# 修正理由: ボタン名は公開情報で誰でも予測可能なため、未認証かつ無制限のDB書き込み(INSERT)が
# 連打可能だった。実際のリッチメニュークリック頻度を大きく上回る水準で制限する
_tap_rate_limit = rate_limiter(max_requests=20, window_seconds=60)


@router.get("/r/{name}")
async def richmenu_redirect(name: str, _rl=Depends(_tap_rate_limit)):
    url = RICHMENU_URLS.get(name)
    if not url:
        raise HTTPException(status_code=404)
    async with AsyncSessionLocal() as session:
        session.add(RichMenuTap(button=name))
        await session.commit()
    return RedirectResponse(url=url, status_code=302)
