from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from core.config import REVIEW_FORM_URL
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
    "kyoyoin":   "https://www.iphe.kobe-u.ac.jp/general-education-courses/",
}


@router.get("/r/{name}")
async def richmenu_redirect(name: str):
    url = RICHMENU_URLS.get(name)
    if not url:
        raise HTTPException(status_code=404)
    async with AsyncSessionLocal() as session:
        session.add(RichMenuTap(button=name))
        await session.commit()
    return RedirectResponse(url=url, status_code=302)
