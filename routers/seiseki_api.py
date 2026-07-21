import asyncio

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from sqlalchemy import or_, select

from core import cache
from core.liff_auth import verify_liff_id_token
from core.rate_limit import rate_limiter
from core.seiseki import PDFPLUMBER_OK, classify_seiseki_raw, parse_seiseki_pdf
from database import AsyncSessionLocal
from models import CreditRequirement, Subject, SubjectCreditCategory, UserCustomCourse, UserSeisekiRaw

router = APIRouter()

# 修正理由: PDF解析(pdfplumber)はCPU負荷が高く、認証不要で誰でも叩けるため
# IPアドレス単位で1分あたり5回までに制限する
_parse_seiseki_rate_limit = rate_limiter(max_requests=5, window_seconds=60)

# 修正理由: /api/reclassify_seiseki は認証不要で誰でも叩ける上、raw の中身に
# サイズ上限が無いため、巨大なJSONを連投されるとCPU/メモリを消費するDoSが可能だった。
# /api/seiseki/save_raw は認証必須だが同様にrawのサイズ上限が無く、
# 正規ユーザー1人でもuser_seiseki_rawへの書き込みを無制限に連打できたため、
# 他の書き込み系エンドポイントと同水準のレート制限を揃える。
_reclassify_seiseki_rate_limit = rate_limiter(max_requests=20, window_seconds=60)
_save_raw_rate_limit = rate_limiter(max_requests=10, window_seconds=60)


@router.post("/api/parse_seiseki")
async def api_parse_seiseki(
    request: Request,
    file: UploadFile = File(...),
    x_liff_id_token: str = Header("", alias="X-Liff-Id-Token"),
    _rl: None = Depends(_parse_seiseki_rate_limit),
):
    if not PDFPLUMBER_OK:
        raise HTTPException(status_code=503, detail="PDF parsing not available")
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF ファイルを送ってください")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="ファイルサイズが大きすぎます（10MB 以下）")
    try:
        # pdfplumberのCPU拘束処理はイベントループをブロックしないよう別スレッドで実行する
        result = await asyncio.to_thread(parse_seiseki_pdf, data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"PDF の解析に失敗しました: {e}")
    raw_data = result["raw"]
    if not raw_data.get("gaigo_courses") and not raw_data.get("senmon_courses") and not any(raw_data.get("summaries", {}).values()):
        raise HTTPException(status_code=422, detail="成績表の内容を読み取れませんでした。神戸大学の成績表PDFかご確認ください。")
    uid = await verify_liff_id_token(x_liff_id_token, request)
    if uid:
        async with AsyncSessionLocal() as session:
            existing = await session.get(UserSeisekiRaw, uid)
            if existing:
                existing.raw_json = raw_data
                if result["gpa"] is not None:
                    existing.gpa = result["gpa"]
                row = existing
            else:
                row = UserSeisekiRaw(line_user_id=uid, raw_json=raw_data, gpa=result["gpa"])
                session.add(row)
            await session.commit()
            await session.refresh(row)
            result["updated_at"] = row.updated_at.isoformat() if row.updated_at else None
    return result


@router.post("/api/reclassify_seiseki")
async def api_reclassify_seiseki(request: Request, _rl: None = Depends(_reclassify_seiseki_rate_limit)):
    body = await request.json()
    raw = body.get("raw")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="raw data required")
    return {"credits": classify_seiseki_raw(raw)}


@router.get("/api/seiseki/credits")
async def api_seiseki_credits(
    request: Request, x_liff_id_token: str = Header("", alias="X-Liff-Id-Token"),
):
    uid = await verify_liff_id_token(x_liff_id_token, request)
    if not uid:
        return {}
    async with AsyncSessionLocal() as session:
        row = await session.get(UserSeisekiRaw, uid)
        # マイ時間割で手動追加した科目のうち、分類（credit_requirements.category_id）を
        # 指定したものは取得単位数に加算する
        custom_rows = (await session.execute(
            select(UserCustomCourse.classification, UserCustomCourse.credits).where(
                UserCustomCourse.line_user_id == uid,
                UserCustomCourse.classification.is_not(None),
            )
        )).all()
    if not row and not custom_rows:
        return {}
    credits = classify_seiseki_raw(row.raw_json) if row else {}
    for category_id, c in custom_rows:
        credits[category_id] = credits.get(category_id, 0) + c
    return {
        "credits": credits,
        "gpa": row.gpa if row else None,
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
    }


@router.post("/api/seiseki/save_raw")
async def api_seiseki_save_raw(request: Request, _rl: None = Depends(_save_raw_rate_limit)):
    body = await request.json()
    uid = await verify_liff_id_token((body.get("id_token") or "").strip(), request)
    raw = body.get("raw")
    gpa = body.get("gpa")
    if not uid or not raw:
        raise HTTPException(status_code=400, detail="id_token and raw required")
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
        # 修正理由: 以前はfaculty絞り込み無しでsubject_credit_categories全件（他学部分も含む）を
        # 毎回JOIN取得しており、農学部だけで500件超あるテーブルを無駄に全走査していた。
        # このリクエストで使うのはrows（=対象faculty/departmentのcategory_id）分だけのため絞り込む。
        category_ids = [r.category_id for r in rows]
        cc_rows = []
        if category_ids:
            cc_rows = (await session.execute(
                select(SubjectCreditCategory.category_id, Subject.name)
                .join(Subject, Subject.id == SubjectCreditCategory.subject_id)
                .where(SubjectCreditCategory.category_id.in_(category_ids))
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
            "max_credits":      r.max_credits,
            "combined_of":      r.combined_of,
            "note":             r.note or "",
            "approved_courses": courses_by_cat.get(r.category_id, []),
        }
        for r in rows
    ]
