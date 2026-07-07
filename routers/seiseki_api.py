from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import or_, select

from core import cache
from core.config import LINE_USER_ID_RE
from core.seiseki import PDFPLUMBER_OK, classify_seiseki_raw, parse_seiseki_pdf
from database import AsyncSessionLocal
from models import CreditRequirement, Subject, SubjectCreditCategory, UserSeisekiRaw

router = APIRouter()


@router.post("/api/parse_seiseki")
async def api_parse_seiseki(request: Request, file: UploadFile = File(...)):
    if not PDFPLUMBER_OK:
        raise HTTPException(status_code=503, detail="PDF parsing not available")
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF ファイルを送ってください")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="ファイルサイズが大きすぎます（10MB 以下）")
    try:
        result = parse_seiseki_pdf(data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"PDF の解析に失敗しました: {e}")
    uid = request.headers.get("X-Line-User-Id", "").strip()
    if uid and LINE_USER_ID_RE.match(uid):
        async with AsyncSessionLocal() as session:
            existing = await session.get(UserSeisekiRaw, uid)
            raw_data = result["raw"]
            if existing:
                existing.raw_json = raw_data
                if result["gpa"] is not None:
                    existing.gpa = result["gpa"]
            else:
                session.add(UserSeisekiRaw(line_user_id=uid, raw_json=raw_data, gpa=result["gpa"]))
            await session.commit()
    return result


@router.post("/api/reclassify_seiseki")
async def api_reclassify_seiseki(request: Request):
    body = await request.json()
    raw = body.get("raw")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="raw data required")
    return {"credits": classify_seiseki_raw(raw)}


@router.get("/api/seiseki/credits")
async def api_seiseki_credits(uid: str):
    if not uid or not LINE_USER_ID_RE.match(uid):
        return {}
    async with AsyncSessionLocal() as session:
        row = await session.get(UserSeisekiRaw, uid)
    if not row:
        return {}
    raw = row.raw_json
    return {"credits": classify_seiseki_raw(raw), "gpa": row.gpa}


@router.post("/api/seiseki/save_raw")
async def api_seiseki_save_raw(request: Request):
    body = await request.json()
    uid = body.get("uid", "").strip()
    raw = body.get("raw")
    gpa = body.get("gpa")
    if not uid or not LINE_USER_ID_RE.match(uid) or not raw:
        raise HTTPException(status_code=400, detail="uid and raw required")
    async with AsyncSessionLocal() as session:
        existing = await session.get(UserSeisekiRaw, uid)
        if existing:
            existing.raw_json = raw
            if gpa is not None:
                existing.gpa = gpa
        else:
            session.add(UserSeisekiRaw(line_user_id=uid, raw_json=raw, gpa=gpa))
        await session.commit()
    return {"ok": True}


@router.get("/api/credit_requirements")
async def api_credit_requirements(faculty: str = Query("経営学部"), department: str | None = Query(None)):
    async with AsyncSessionLocal() as session:
        stmt = select(CreditRequirement).where(CreditRequirement.faculty == faculty)
        if department:
            # department未設定の行（学部内全学科共通の要件）も含めて返す
            stmt = stmt.where(or_(CreditRequirement.department == department, CreditRequirement.department.is_(None)))
        rows = (await session.execute(
            stmt.order_by(CreditRequirement.sort_order)
        )).scalars().all()
        group_order = await cache.get_credit_group_order()
        rows = sorted(
            rows,
            key=lambda r: (group_order.get((r.faculty, r.group_name), cache.CREDIT_GROUP_ORDER_FALLBACK), r.sort_order),
        )
        cc_rows = (await session.execute(
            select(SubjectCreditCategory.category_id, Subject.name)
            .join(Subject, Subject.id == SubjectCreditCategory.subject_id)
        )).all()
    courses_by_cat: dict[str, list[str]] = {}
    for cat_id, course_name in cc_rows:
        courses_by_cat.setdefault(cat_id, []).append(course_name)
    return [
        {
            "category_id":      r.category_id,
            "label":            r.label,
            "group_name":       r.group_name,
            "sort_order":       r.sort_order,
            "required_credits": r.required_credits,
            "note":             r.note or "",
            "approved_courses": courses_by_cat.get(r.category_id, []),
        }
        for r in rows
    ]
