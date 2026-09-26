"""/admin/groups: 団体（サークル等）の管理と、団体への支払い額の集計（2026-09-26、core/groups.py）。

団体は物理削除しない（紐づくレビュー・精算履歴を消さない）。無効化は is_active=False。
画面に出すのは団体ごとの集計だけで、個人名・学籍番号は表示しない。
"""
import re
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core import cache
from core.config import (
    GROUP_CONTRIBUTOR_BONUS_AMOUNT,
    GROUP_CONTRIBUTOR_BONUS_UNIT,
    GROUP_REVIEW_PAYOUT_KYOYO,
    GROUP_REVIEW_PAYOUT_SENMON,
)
from core.groups import CODE_LENGTH, generate_group_code, group_payout_breakdown, group_stats, normalize_group_code
from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import Group, GroupPayout

router = APIRouter()

_EMPTY_STATS = group_payout_breakdown(0, 0, 0) | {"paid": 0, "balance": 0, "member_count": 0}


@router.get("/admin/groups", response_class=HTMLResponse)
async def admin_groups(request: Request, error: str = "", _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        groups = (await session.execute(select(Group).order_by(Group.created_at.desc(), Group.id.desc()))).scalars().all()
        stats = await group_stats(session)
        payouts = (await session.execute(
            select(GroupPayout).order_by(GroupPayout.paid_at.desc(), GroupPayout.id.desc())
        )).scalars().all()
    payouts_by_group: dict[int, list[GroupPayout]] = {}
    for p in payouts:
        payouts_by_group.setdefault(p.group_id, []).append(p)
    return templates.TemplateResponse("admin/groups.html", {
        "request": request,
        "nav_counts": await cache.get_admin_nav_counts_cached(),
        "error": error[:100],
        "groups": groups,
        "stats": {g.id: stats.get(g.id, _EMPTY_STATS) for g in groups},
        "payouts_by_group": payouts_by_group,
        "rules": {
            "kyoyo": GROUP_REVIEW_PAYOUT_KYOYO,
            "senmon": GROUP_REVIEW_PAYOUT_SENMON,
            "bonus_unit": GROUP_CONTRIBUTOR_BONUS_UNIT,
            "bonus_amount": GROUP_CONTRIBUTOR_BONUS_AMOUNT,
        },
    })


@router.post("/admin/groups/create")
async def admin_group_create(
    name: str = Form(""), code: str = Form(""), note: str = Form(""), _: str = Depends(check_admin),
):
    name = name.strip()[:100]
    if not name:
        return RedirectResponse("/admin/groups", status_code=303)
    # 団体番号を指定した場合はそのまま登録（照合と同じ正規化：全角→半角・大文字化）。空欄なら自動発行
    manual_code = normalize_group_code(code)
    if manual_code and not re.fullmatch(rf"[A-Z0-9]{{{CODE_LENGTH}}}", manual_code):
        return RedirectResponse(
            "/admin/groups?error=" + quote(f"団体番号は英数字{CODE_LENGTH}文字で入力してください"), status_code=303,
        )
    async with AsyncSessionLocal() as session:
        if manual_code:
            session.add(Group(name=name, code=manual_code, note=note.strip()[:500] or None))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return RedirectResponse(
                    "/admin/groups?error=" + quote(f"団体番号「{manual_code}」は既に使われています"), status_code=303,
                )
            return RedirectResponse("/admin/groups", status_code=303)
        # 団体番号は自動発行。衝突（31^8通りなのでほぼ無い）は発行し直す
        for _attempt in range(5):
            session.add(Group(name=name, code=generate_group_code(), note=note.strip()[:500] or None))
            try:
                await session.commit()
                break
            except IntegrityError:
                await session.rollback()
    return RedirectResponse("/admin/groups", status_code=303)


@router.post("/admin/groups/{group_id}/update")
async def admin_group_update(
    group_id: int, name: str = Form(""), note: str = Form(""), _: str = Depends(check_admin),
):
    async with AsyncSessionLocal() as session:
        group = await session.get(Group, group_id)
        if group and name.strip():
            group.name = name.strip()[:100]
            group.note = note.strip()[:500] or None
            await session.commit()
    return RedirectResponse("/admin/groups", status_code=303)


@router.post("/admin/groups/{group_id}/toggle")
async def admin_group_toggle(group_id: int, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        group = await session.get(Group, group_id)
        if group:
            group.is_active = not group.is_active
            await session.commit()
    return RedirectResponse("/admin/groups", status_code=303)


@router.post("/admin/groups/{group_id}/payout")
async def admin_group_payout(
    group_id: int, amount: int = Form(...), note: str = Form(""), _: str = Depends(check_admin),
):
    if amount > 0:
        async with AsyncSessionLocal() as session:
            if await session.get(Group, group_id):
                session.add(GroupPayout(
                    group_id=group_id, amount=amount, paid_at=datetime.now(UTC),
                    note=note.strip()[:500] or None,
                ))
                await session.commit()
    return RedirectResponse("/admin/groups", status_code=303)


@router.post("/admin/groups/payout/{payout_id}/delete")
async def admin_group_payout_delete(payout_id: int, _: str = Depends(check_admin)):
    # 入力ミスの取り消し用。精算記録だけを消す（レビューや団体には影響しない）
    async with AsyncSessionLocal() as session:
        payout = await session.get(GroupPayout, payout_id)
        if payout:
            await session.delete(payout)
            await session.commit()
    return RedirectResponse("/admin/groups", status_code=303)
