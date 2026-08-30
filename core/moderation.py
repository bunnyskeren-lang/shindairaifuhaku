"""虚偽投稿等でLINE bot利用を永久停止（BAN）されたユーザーの判定を一箇所に集約する。
BAN操作自体はrouters/admin/users_errors.py、判定結果のキャッシュはcore/cache.pyが持つ。"""
from fastapi import HTTPException

from core import cache
from core.config import BAN_MESSAGE_TEXT


async def is_banned(line_user_id: str) -> bool:
    return await cache.get_ban_status_cached(line_user_id)


async def raise_if_banned(line_user_id: str) -> None:
    """LIFFの書き込み系APIエンドポイントで、id_token検証後に呼ぶ。BAN中なら403を送出する。
    detailはフロントエンドがそのままユーザーに表示するため、BAN_MESSAGE_TEXTと文言を統一する。"""
    if await is_banned(line_user_id):
        raise HTTPException(status_code=403, detail=BAN_MESSAGE_TEXT)
