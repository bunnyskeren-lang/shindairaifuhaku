from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from core import cache
from core.config import make_cls_sort
from core.security import check_admin
from database import AsyncSessionLocal
from models import DisplayOrder, Subject
from routers.admin._common import swap_by_index, upsert_display_order_sequence

router = APIRouter()


@router.post("/admin/courses/classification/rename")
async def rename_classification(
    _: str = Depends(check_admin),
    old_name: str = Form(...),
    new_name: str = Form(...),
):
    new_name = new_name.strip()
    if not new_name or new_name == old_name:
        return RedirectResponse(url="/admin/courses", status_code=303)
    async with AsyncSessionLocal() as session:
        courses = (await session.execute(
            select(Subject).where(Subject.classification == old_name)
        )).scalars().all()
        for course in courses:
            course.classification = new_name
        cls_row = (await session.execute(
            select(DisplayOrder).where(DisplayOrder.kind == "classification", DisplayOrder.name == old_name)
        )).scalar_one_or_none()
        if cls_row:
            # 修正理由: new_nameが既存の分類名（2つの分類を1つに統合するケース）だと
            # (kind, name, faculty)のUNIQUE制約に違反しIntegrityErrorで科目側の
            # classification更新まで全てロールバックしていた。既存分類への統合の場合は
            # 旧DisplayOrder行を削除する（新分類側の並び順をそのまま使う）。
            new_row_exists = (await session.execute(
                select(DisplayOrder).where(DisplayOrder.kind == "classification", DisplayOrder.name == new_name)
            )).scalar_one_or_none()
            if new_row_exists:
                await session.delete(cls_row)
            else:
                cls_row.name = new_name
        await session.commit()
    cache.invalidate_cls_caches()
    cache.invalidate_courses_cache()
    return RedirectResponse(url="/admin/courses", status_code=303)


@router.post("/admin/courses/classification/delete")
async def delete_classification(
    _: str = Depends(check_admin),
    classification: str = Form(...),
):
    async with AsyncSessionLocal() as session:
        courses_in_class = (await session.execute(
            select(Subject).where(Subject.classification == classification)
        )).scalars().all()
        for course in courses_in_class:
            course.classification = None
        cls_row = (await session.execute(
            select(DisplayOrder).where(DisplayOrder.kind == "classification", DisplayOrder.name == classification)
        )).scalar_one_or_none()
        if cls_row:
            await session.delete(cls_row)
        await session.commit()
    cache.invalidate_cls_caches()
    cache.invalidate_courses_cache()
    return RedirectResponse(url="/admin/courses", status_code=303)


@router.post("/admin/courses/classification/move")
async def admin_cls_move(request: Request, _=Depends(check_admin)):
    data = await request.json()
    name = data.get("name", "")
    direction = data.get("direction", "")
    if not name or direction not in ("up", "down"):
        return JSONResponse({"ok": False})

    async with AsyncSessionLocal() as session:
        all_cls = sorted(
            [c for c in (await session.execute(
                select(Subject.classification).distinct()
            )).scalars().all() if c],
        )
        cls_map = await cache.get_cls_order_map()
        _cls_sort = make_cls_sort(cls_map)
        sorted_cls = sorted(all_cls, key=_cls_sort)

        moved = swap_by_index(sorted_cls, name, direction)
        if moved is None:
            return JSONResponse({"ok": False})
        if moved:
            await upsert_display_order_sequence(session, "classification", sorted_cls)
            await session.commit()
    cache.invalidate_cls_caches()
    cache.invalidate_courses_cache()
    return JSONResponse({"ok": True})


@router.post("/admin/courses/classification/set_parent")
async def admin_cls_set_parent(
    _: str = Depends(check_admin),
    classification: str = Form(...),
    parent_group: str = Form(default=""),
):
    parent_group = parent_group.strip()
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            select(DisplayOrder).where(DisplayOrder.kind == "classification", DisplayOrder.name == classification)
        )).scalar_one_or_none()
        if row:
            row.parent_group = parent_group or None
        else:
            session.add(DisplayOrder(kind="classification", name=classification, sort_order=0, parent_group=parent_group or None))
        await session.commit()
    cache.invalidate_cls_caches()
    cache.invalidate_courses_cache()
    return RedirectResponse(url="/admin/courses", status_code=303)
