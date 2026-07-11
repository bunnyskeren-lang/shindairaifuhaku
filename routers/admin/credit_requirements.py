import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy import update as sa_update

from core import cache
from core.security import check_admin
from core.seiseki import SENMON_GROUPS, classify_senmon
from core.templates import templates
from database import AsyncSessionLocal
from models import CreditRequirement, DisplayOrder, Subject, SubjectCreditCategory

router = APIRouter()

# ── 経営学部 管理ページ ────────────────────────────────────────────────────────


@router.get("/admin/keiei", response_class=HTMLResponse)
async def admin_keiei(request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        courses = (await session.execute(
            select(Subject)
            .where(Subject.faculty.like("%経営学部%"))
            .order_by(Subject.classification, Subject.sort_order, Subject.name)
        )).scalars().all()
        reqs = (await session.execute(
            select(CreditRequirement).order_by(CreditRequirement.sort_order)
        )).scalars().all()
        group_order = await cache.get_credit_group_order()
        reqs = sorted(
            reqs,
            key=lambda r: (group_order.get((r.faculty, r.group_name), cache.CREDIT_GROUP_ORDER_FALLBACK), r.sort_order),
        )
        kyotsu_candidates = (await session.execute(
            select(Subject.name, Subject.credits)
            .where(Subject.classification.like("%共通専門基礎%"))
            .order_by(Subject.sort_order, Subject.name)
        )).all()
        approved_rows = (await session.execute(
            select(SubjectCreditCategory.category_id, Subject.name)
            .join(Subject, Subject.id == SubjectCreditCategory.subject_id)
        )).all()
    approved_by_cat: dict[str, set[str]] = {}
    for cat_id, course_name in approved_rows:
        approved_by_cat.setdefault(cat_id, set()).add(course_name)
    auto_groups = {c.id: classify_senmon(c.name) for c in courses}
    group_names = list(dict.fromkeys(r.group_name for r in reqs if r.group_name))
    return templates.TemplateResponse("admin/keiei.html", {
        "request": request,
        "courses": courses,
        "senmon_groups": SENMON_GROUPS,
        "reqs": reqs,
        "auto_groups": auto_groups,
        "kyotsu_candidates": kyotsu_candidates,
        "approved_by_cat": approved_by_cat,
        "group_names": group_names,
        "group_faculty": "経営学部",
    })


@router.post("/admin/keiei/category_courses/{cat_id}")
async def admin_save_category_courses(cat_id: str, request: Request, _: str = Depends(check_admin)):
    form = await request.form()
    checked = form.getlist("courses")
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(SubjectCreditCategory).where(SubjectCreditCategory.category_id == cat_id)
        )
        for name in checked:
            # 経営学部専用ページのため、経済学部等の同名科目に誤って紐づかないよう学部で絞り込む
            subj = (await session.execute(
                select(Subject.id, Subject.credits)
                .where(Subject.name == name, Subject.faculty.like("%経営学部%"))
            )).first()
            if subj:
                session.add(SubjectCreditCategory(
                    subject_id=subj.id,
                    category_id=cat_id,
                    credits=float(subj.credits) if subj.credits else 2.0,
                ))
        await session.commit()
    return JSONResponse({"ok": True})


@router.post("/admin/keiei/courses/{course_id}/senmon_group")
async def admin_keiei_set_group(course_id: int, request: Request, _: str = Depends(check_admin)):
    body = await request.json()
    group = body.get("group") or None
    if group and group not in SENMON_GROUPS:
        raise HTTPException(status_code=400, detail="invalid group")
    async with AsyncSessionLocal() as session:
        course = await session.get(Subject, course_id)
        if not course:
            raise HTTPException(status_code=404)
        course.senmon_group = group
        await session.commit()
    cache.invalidate_senmon_cache()
    asyncio.create_task(cache.reload_senmon_cache())
    cache.invalidate_courses_cache()
    return JSONResponse({"ok": True})


@router.post("/admin/keiei/credit_requirements/update")
async def admin_keiei_update_requirements(request: Request, _: str = Depends(check_admin)):
    form = await request.form()
    async with AsyncSessionLocal() as session:
        existing_ids = {
            r for (r,) in (await session.execute(select(CreditRequirement.category_id))).all()
        }
        for cat_id in existing_ids:
            values: dict = {}
            if f"req_{cat_id}" in form:
                try:
                    values["required_credits"] = max(0, int(form[f"req_{cat_id}"]))
                except ValueError:
                    pass
            if f"max_{cat_id}" in form:
                max_val = form[f"max_{cat_id}"].strip()
                try:
                    values["max_credits"] = max(0, int(max_val)) if max_val else None
                except ValueError:
                    pass
            if f"note_{cat_id}" in form:
                note_val = form[f"note_{cat_id}"].strip()
                values["note"] = note_val or None
            if f"label_{cat_id}" in form:
                values["label"] = form[f"label_{cat_id}"].strip()
            if f"group_{cat_id}" in form:
                values["group_name"] = form[f"group_{cat_id}"].strip()
            if f"sort_{cat_id}" in form:
                try:
                    values["sort_order"] = int(form[f"sort_{cat_id}"])
                except ValueError:
                    pass
            if values:
                await session.execute(
                    sa_update(CreditRequirement)
                    .where(CreditRequirement.category_id == cat_id)
                    .values(**values)
                )
        await session.commit()
    return RedirectResponse("/admin/keiei", status_code=303)


@router.post("/admin/keiei/credit_requirements/add")
async def admin_keiei_add_requirement(request: Request, _: str = Depends(check_admin)):
    form = await request.form()
    label  = form.get("new_label", "").strip()
    group  = form.get("new_group", "").strip()
    note   = form.get("new_note", "").strip() or None
    if not label:
        return RedirectResponse("/admin/keiei?error=invalid", status_code=303)
    try:
        req    = max(0, int(form.get("new_req", "0")))
        sort_v = int(form.get("new_sort", "999"))
    except ValueError:
        req, sort_v = 0, 999
    cat_id = f"cat_{int(time.time() * 1000)}"
    async with AsyncSessionLocal() as session:
        session.add(CreditRequirement(
            category_id=cat_id,
            label=label,
            group_name=group,
            sort_order=sort_v,
            required_credits=req,
            note=note,
        ))
        await session.commit()
    return RedirectResponse("/admin/keiei", status_code=303)


@router.post("/admin/keiei/credit_requirements/{cat_id}/delete")
async def admin_keiei_delete_requirement(cat_id: str, request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        row = await session.get(CreditRequirement, cat_id)
        if row:
            # 修正理由: SubjectCreditCategory.category_idはondelete指定なし(RESTRICT相当)のため、
            # 科目が紐づけ済みのカテゴリを削除しようとすると未処理のIntegrityErrorで500になっていた。
            in_use = (await session.execute(
                select(SubjectCreditCategory).where(SubjectCreditCategory.category_id == cat_id).limit(1)
            )).scalar_one_or_none()
            if in_use:
                return RedirectResponse("/admin/keiei?error=in_use", status_code=303)
            await session.delete(row)
            await session.commit()
    return RedirectResponse("/admin/keiei", status_code=303)


@router.post("/admin/credit_requirements/group/move")
async def admin_credit_group_move(request: Request, _: str = Depends(check_admin)):
    data = await request.json()
    faculty = data.get("faculty", "")
    group_name = data.get("group_name", "")
    direction = data.get("direction", "")
    if not faculty or not group_name or direction not in ("up", "down"):
        return JSONResponse({"ok": False})

    async with AsyncSessionLocal() as session:
        distinct_groups = (await session.execute(
            select(CreditRequirement.group_name)
            .where(CreditRequirement.faculty == faculty, CreditRequirement.group_name != "")
            .distinct()
        )).scalars().all()
        group_order = await cache.get_credit_group_order()
        sorted_groups = sorted(
            distinct_groups,
            key=lambda g: group_order.get((faculty, g), cache.CREDIT_GROUP_ORDER_FALLBACK),
        )

        try:
            idx = sorted_groups.index(group_name)
        except ValueError:
            return JSONResponse({"ok": False})

        delta = -1 if direction == "up" else 1
        swap_idx = idx + delta
        if swap_idx < 0 or swap_idx >= len(sorted_groups):
            return JSONResponse({"ok": True})

        sorted_groups[idx], sorted_groups[swap_idx] = sorted_groups[swap_idx], sorted_groups[idx]

        for i, g in enumerate(sorted_groups):
            existing = (await session.execute(
                select(DisplayOrder).where(
                    DisplayOrder.kind == "credit_requirement_group",
                    DisplayOrder.name == g,
                    DisplayOrder.faculty == faculty,
                )
            )).scalar_one_or_none()
            if existing:
                existing.sort_order = i
            else:
                session.add(DisplayOrder(kind="credit_requirement_group", name=g, faculty=faculty, sort_order=i))
        await session.commit()
    cache.invalidate_credit_group_order_cache()
    return JSONResponse({"ok": True})


# ── システム情報学部 管理ページ ────────────────────────────────────────────────

@router.get("/admin/sysinfo", response_class=HTMLResponse)
async def admin_sysinfo(request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        reqs = (await session.execute(
            select(CreditRequirement)
            .where(CreditRequirement.faculty == "システム情報学部")
            .order_by(CreditRequirement.sort_order)
        )).scalars().all()
        group_order = await cache.get_credit_group_order()
        reqs = sorted(
            reqs,
            key=lambda r: (group_order.get((r.faculty, r.group_name), cache.CREDIT_GROUP_ORDER_FALLBACK), r.sort_order),
        )
        courses = (await session.execute(
            select(Subject)
            .where(Subject.faculty.like("%システム情報学部%"))
            .order_by(Subject.name)
        )).scalars().all()
    group_names = list(dict.fromkeys(r.group_name for r in reqs if r.group_name))
    return templates.TemplateResponse("admin/sysinfo.html", {
        "request": request,
        "reqs": reqs,
        "courses": courses,
        "group_names": group_names,
        "group_faculty": "システム情報学部",
    })


@router.post("/admin/sysinfo/credit_requirements/update")
async def admin_sysinfo_update_requirements(request: Request, _: str = Depends(check_admin)):
    form = await request.form()
    async with AsyncSessionLocal() as session:
        existing_ids = {
            r for (r,) in (await session.execute(
                select(CreditRequirement.category_id)
                .where(CreditRequirement.faculty == "システム情報学部")
            )).all()
        }
        for cat_id in existing_ids:
            values: dict = {}
            if f"req_{cat_id}" in form:
                try:
                    values["required_credits"] = max(0, int(form[f"req_{cat_id}"]))
                except ValueError:
                    pass
            if f"max_{cat_id}" in form:
                max_val = form[f"max_{cat_id}"].strip()
                try:
                    values["max_credits"] = max(0, int(max_val)) if max_val else None
                except ValueError:
                    pass
            if f"note_{cat_id}" in form:
                note_val = form[f"note_{cat_id}"].strip()
                values["note"] = note_val or None
            if f"label_{cat_id}" in form:
                values["label"] = form[f"label_{cat_id}"].strip()
            if f"group_{cat_id}" in form:
                values["group_name"] = form[f"group_{cat_id}"].strip()
            if f"sort_{cat_id}" in form:
                try:
                    values["sort_order"] = int(form[f"sort_{cat_id}"])
                except ValueError:
                    pass
            if values:
                await session.execute(
                    sa_update(CreditRequirement)
                    .where(CreditRequirement.category_id == cat_id)
                    .values(**values)
                )
        await session.commit()
    return RedirectResponse("/admin/sysinfo", status_code=303)


@router.post("/admin/sysinfo/credit_requirements/add")
async def admin_sysinfo_add_requirement(request: Request, _: str = Depends(check_admin)):
    form = await request.form()
    label  = form.get("new_label", "").strip()
    group  = form.get("new_group", "").strip()
    note   = form.get("new_note", "").strip() or None
    if not label:
        return RedirectResponse("/admin/sysinfo?error=invalid", status_code=303)
    try:
        req    = max(0, int(form.get("new_req", "0")))
        sort_v = int(form.get("new_sort", "999"))
    except ValueError:
        req, sort_v = 0, 999
    cat_id = f"si_{int(time.time() * 1000)}"
    async with AsyncSessionLocal() as session:
        session.add(CreditRequirement(
            category_id=cat_id,
            label=label,
            group_name=group,
            sort_order=sort_v,
            required_credits=req,
            note=note,
            faculty="システム情報学部",
        ))
        await session.commit()
    return RedirectResponse("/admin/sysinfo", status_code=303)


@router.post("/admin/sysinfo/credit_requirements/{cat_id}/delete")
async def admin_sysinfo_delete_requirement(cat_id: str, request: Request, _: str = Depends(check_admin)):
    async with AsyncSessionLocal() as session:
        row = await session.get(CreditRequirement, cat_id)
        if row:
            # 修正理由: SubjectCreditCategory.category_idはondelete指定なし(RESTRICT相当)のため、
            # 科目が紐づけ済みのカテゴリを削除しようとすると未処理のIntegrityErrorで500になっていた。
            in_use = (await session.execute(
                select(SubjectCreditCategory).where(SubjectCreditCategory.category_id == cat_id).limit(1)
            )).scalar_one_or_none()
            if in_use:
                return RedirectResponse("/admin/sysinfo?error=in_use", status_code=303)
            await session.delete(row)
            await session.commit()
    return RedirectResponse("/admin/sysinfo", status_code=303)
