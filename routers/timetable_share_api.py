from fastapi import APIRouter, Header, HTTPException, Query, Request

from core.config import DEFAULT_ACADEMIC_YEAR
from core.liff_auth import verify_liff_id_token
from core.security import make_share_token, verify_share_token
from database import AsyncSessionLocal
from models import UserProfile
from routers.timetable_api import _load_timetable_courses, _require_liff_user

router = APIRouter()


@router.get("/api/timetable/share_token")
async def api_timetable_share_token(
    request: Request, x_liff_id_token: str = Header("", alias="X-Liff-Id-Token"),
):
    user_id = await _require_liff_user(x_liff_id_token, request)
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, user_id)
        version = profile.share_token_version if profile else 0
    return {"token": make_share_token(user_id, version)}


@router.post("/api/timetable/share_revoke")
async def api_timetable_share_revoke(request: Request):
    """発行済みの共有リンクをすべて無効化する（share_token_versionをインクリメント）。"""
    body = await request.json()
    user_id = await _require_liff_user(body.get("id_token", ""), request)
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, user_id)
        if not profile:
            raise HTTPException(status_code=404, detail="user profile not found")
        profile.share_token_version = (profile.share_token_version or 0) + 1
        await session.commit()
    return {"ok": True}


@router.get("/api/timetable/shared")
async def api_timetable_shared(
    request: Request, token: str = Query(...), year: int = DEFAULT_ACADEMIC_YEAR,
    x_liff_id_token: str = Header("", alias="X-Liff-Id-Token"),
):
    viewer_id = await verify_liff_id_token(x_liff_id_token, request)
    if not viewer_id:
        raise HTTPException(status_code=401, detail="LINEログインの確認に失敗しました")

    decoded = verify_share_token(token)
    if not decoded:
        raise HTTPException(status_code=404, detail="共有リンクが無効です")
    owner_id, token_version = decoded

    async with AsyncSessionLocal() as session:
        viewer_profile = await session.get(UserProfile, viewer_id)
        if not viewer_profile:
            raise HTTPException(status_code=403, detail="登録済みユーザーのみ閲覧できます")

        owner_profile = await session.get(UserProfile, owner_id)
        if not owner_profile or (owner_profile.share_token_version or 0) != token_version:
            raise HTTPException(status_code=404, detail="共有リンクが無効です（停止された可能性があります）")

        courses = await _load_timetable_courses(session, owner_id, year)
        return {"owner_name": owner_profile.name, "courses": courses}
