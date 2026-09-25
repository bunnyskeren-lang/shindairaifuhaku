"""団体番号の照合API（レビュー投稿フォームの「団体番号」欄が入力時に呼ぶ。2026-09-26、core/groups.py）。"""
from fastapi import APIRouter, Depends, Request

from core.groups import (
    GROUP_CODE_INACTIVE_MESSAGE, GROUP_CODE_NOT_FOUND_MESSAGE, find_group_by_code, normalize_group_code,
)
from core.rate_limit import rate_limiter
from database import AsyncSessionLocal

router = APIRouter()

# 団体番号の総当たり（存在する団体名の探り出し）を防ぐため、IPあたり1分10回まで
_lookup_rate_limit = rate_limiter(max_requests=10, window_seconds=60)


@router.post("/api/group/lookup")
async def group_lookup(request: Request, _rl: None = Depends(_lookup_rate_limit)):
    """団体番号から団体名を返す。無効・停止中は黙って無視せず、理由つきで知らせる。"""
    try:
        body = await request.json()
    except ValueError:
        body = {}
    code = normalize_group_code(body.get("code") if isinstance(body, dict) else "")
    if not code:
        return {"ok": False, "reason": "empty", "message": "団体番号を入力してください"}
    async with AsyncSessionLocal() as session:
        group = await find_group_by_code(session, code)
    if group is None:
        return {"ok": False, "reason": "not_found", "message": GROUP_CODE_NOT_FOUND_MESSAGE}
    if not group.is_active:
        return {"ok": False, "reason": "inactive", "message": GROUP_CODE_INACTIVE_MESSAGE}
    return {"ok": True, "name": group.name}
