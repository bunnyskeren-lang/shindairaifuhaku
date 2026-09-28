"""/admin/groups: 団体（サークル等）の管理と、団体への支払い額の集計（2026-09-26、core/groups.py）。

団体は物理削除しない（紐づくレビュー・精算履歴を消さない）。無効化は is_active=False。
団体ごとの集計に加え、団体の「管理者」を所属会員の中から指定できる（氏名のみ。学籍番号は表示しない）。
管理者はLINE botの「団体」で、投稿した会員の氏名と件数の一覧を見られる。
"""
import re
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from core import cache
from core.config import (
    GROUP_BANK_FEE,
    GROUP_CONTRIBUTOR_BONUS_AMOUNT,
    GROUP_CONTRIBUTOR_BONUS_UNIT,
    GROUP_REVIEW_PAYOUT_KYOYO,
    GROUP_REVIEW_PAYOUT_SENMON,
)
from core.groups import CODE_LENGTH, empty_group_stats, generate_group_code, group_stats, normalize_group_code
from core.security import check_admin
from core.templates import templates
from database import AsyncSessionLocal
from models import Group, GroupPayout, UserProfile

router = APIRouter()

_MAX_PAYOUT_AMOUNT = 10_000_000  # 1回の精算記録の上限（入力ミスとInteger桁あふれの防止）


@router.get("/admin/groups", response_class=HTMLResponse)
async def admin_groups(request: Request, error: str = "", _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        groups = (await session.execute(select(Group).order_by(Group.created_at.desc(), Group.id.desc()))).scalars().all()
        stats = await group_stats(session)
        payouts = (await session.execute(
            select(GroupPayout).order_by(GroupPayout.paid_at.desc(), GroupPayout.id.desc())
        )).scalars().all()
        member_rows = (await session.execute(
            select(UserProfile.group_id, UserProfile.line_user_id, UserProfile.name)
            .where(UserProfile.group_id.is_not(None)).order_by(UserProfile.name)
        )).all()
    members_by_group: dict[int, list[tuple[str, str]]] = {}
    for gid, uid, name in member_rows:
        members_by_group.setdefault(gid, []).append((uid, name))
    payouts_by_group: dict[int, list[GroupPayout]] = {}
    for p in payouts:
        payouts_by_group.setdefault(p.group_id, []).append(p)
    return templates.TemplateResponse("admin/groups.html", {
        "request": request,
        "nav_counts": await cache.get_admin_nav_counts_cached(),
        "error": error[:100],
        "groups": groups,
        "stats": {g.id: stats.get(g.id) or empty_group_stats() for g in groups},
        "payouts_by_group": payouts_by_group,
        "members_by_group": members_by_group,
        "rules": {
            "kyoyo": GROUP_REVIEW_PAYOUT_KYOYO,
            "senmon": GROUP_REVIEW_PAYOUT_SENMON,
            "bonus_unit": GROUP_CONTRIBUTOR_BONUS_UNIT,
            "bonus_amount": GROUP_CONTRIBUTOR_BONUS_AMOUNT,
            "bank_fee": GROUP_BANK_FEE,
        },
    })


@router.post("/admin/groups/create")
async def admin_group_create(
    name: str = Form(""), code: str = Form(""), note: str = Form(""), _: str = Depends(check_admin),
):
    name = name.strip()[:100]
    if not name:
        return RedirectResponse("/admin/groups", status_code=303)
    # 団体コードを指定した場合はそのまま登録（照合と同じ正規化：全角→半角・大文字化）。空欄なら自動発行
    manual_code = normalize_group_code(code)
    if manual_code and not re.fullmatch(rf"[A-Z0-9]{{{CODE_LENGTH}}}", manual_code):
        return RedirectResponse(
            "/admin/groups?error=" + quote(f"団体コードは英数字{CODE_LENGTH}文字で入力してください"), status_code=303,
        )
    async with AsyncSessionLocal() as session:
        if manual_code:
            session.add(Group(name=name, code=manual_code, note=note.strip()[:500] or None))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return RedirectResponse(
                    "/admin/groups?error=" + quote(f"団体コード「{manual_code}」は既に使われています"), status_code=303,
                )
            return RedirectResponse("/admin/groups", status_code=303)
        # 団体コードは自動発行。衝突（31^8通りなのでほぼ無い）は発行し直す
        for _attempt in range(5):
            session.add(Group(name=name, code=generate_group_code(), note=note.strip()[:500] or None))
            try:
                await session.commit()
                return RedirectResponse("/admin/groups", status_code=303)
            except IntegrityError:
                await session.rollback()
    return RedirectResponse(
        "/admin/groups?error=" + quote("団体コードを自動発行できませんでした。もう一度お試しください"), status_code=303,
    )


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


@router.post("/admin/groups/{group_id}/manager")
async def admin_group_manager(group_id: int, line_user_id: str = Form(""), _: str = Depends(check_admin)):
    """団体の管理者を、その団体の所属会員の中から指定する（空欄で解除）。所属外のユーザーは指定できない。"""
    async with AsyncSessionLocal() as session:
        group = await session.get(Group, group_id)
        if group:
            if not line_user_id:
                group.manager_line_user_id = None
            else:
                member = (await session.execute(
                    select(UserProfile.line_user_id)
                    .where(UserProfile.line_user_id == line_user_id, UserProfile.group_id == group_id)
                )).scalar_one_or_none()
                if member is None:
                    return RedirectResponse(
                        "/admin/groups?error=" + quote("管理者に指定できるのは、その団体の所属会員だけです"), status_code=303,
                    )
                group.manager_line_user_id = member
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


@router.post("/admin/groups/{group_id}/regenerate-code")
async def admin_group_regenerate_code(group_id: int, _: str = Depends(check_admin)):
    """団体コードを新しく発行し直す（漏洩時の無効化用）。

    旧コードを既に入力して所属登録済みだった会員（user_profiles.group_id）は、そのままだと
    投稿フォームが所属先団体の現在のコードを自動で埋めてしまい、本人が新しいコードを知らなくても
    団体経由の投稿が続いてしまう。それでは再発行の意味がないため、所属は一律で解除し、
    次回投稿時は新しいコードの再入力を必須にする。管理者指定も、所属会員であることが前提の
    ため合わせて外す（別の団体へ移った際に元の管理者指定を外すのと同じ扱い、
    routers/review_submit_api.py参照）。
    reviews.group_id（投稿時点の所属・過去の集計）は変えない。衝突（ほぼ無い）は発行し直す。
    """
    async with AsyncSessionLocal() as session:
        group = await session.get(Group, group_id)
        if not group:
            return RedirectResponse("/admin/groups", status_code=303)
        for _attempt in range(5):
            group.code = generate_group_code()
            try:
                await session.execute(
                    update(UserProfile).where(UserProfile.group_id == group_id).values(group_id=None)
                )
                group.manager_line_user_id = None
                await session.commit()
                return RedirectResponse("/admin/groups", status_code=303)
            except IntegrityError:
                await session.rollback()
                group = await session.get(Group, group_id)
    return RedirectResponse(
        "/admin/groups?error=" + quote("団体コードを再発行できませんでした。もう一度お試しください"), status_code=303,
    )


@router.post("/admin/groups/{group_id}/payout")
async def admin_group_payout(
    group_id: int, amount: int = Form(...), note: str = Form(""), _: str = Depends(check_admin),
):
    if not 0 < amount <= _MAX_PAYOUT_AMOUNT:
        return RedirectResponse(
            "/admin/groups?error=" + quote(f"精算額は1〜{_MAX_PAYOUT_AMOUNT:,}円で入力してください"), status_code=303,
        )
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
