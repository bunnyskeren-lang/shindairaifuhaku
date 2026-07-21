from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select, text

from database import AsyncSessionLocal
from models import Schedule, Syllabus, UserCustomCourse, UserSyllabus
from routers.timetable_api import _VALID_DAYS, _require_liff_user

router = APIRouter()


@router.post("/api/timetable/custom")
async def api_timetable_custom_create(request: Request):
    """マイ時間割にシラバスDBに無い科目を手動追加する。他ユーザーには表示されない個人用の科目。"""
    body = await request.json()
    user_id = await _require_liff_user(body.get("id_token", ""), request)

    name = (body.get("name") or "").strip()[:100]
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    instructor = (body.get("instructor") or "").strip()[:100] or None
    # classificationはcredit_requirements.category_idと一致させ、単位チェッカーの取得単位数に
    # 加算する（core/seiseki.pyのapi_seiseki_credits参照）。フロントエンドは自由入力ではなく
    # ユーザーの学部・学科の区分一覧から選択させるため、ここでは形式チェックのみ行う。
    classification = (body.get("classification") or "").strip()[:100] or None
    try:
        credits = int(body.get("credits"))
    except (TypeError, ValueError):
        credits = 2
    credits = max(1, min(credits, 10))

    day = body.get("day_of_week")
    if day not in _VALID_DAYS:
        raise HTTPException(status_code=400, detail="invalid day")
    try:
        year = int(body.get("year"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="year must be an integer")

    if day == "集":
        period = 0
    else:
        try:
            period = int(body.get("period"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="period must be an integer")
        if not (1 <= period <= 6):
            raise HTTPException(status_code=400, detail="period must be between 1 and 6")

    async with AsyncSessionLocal() as session:
        # register_syllabus_for_userと同様、同時押し等でのすり抜けを防ぐため直列化する
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:uid))"), {"uid": user_id})

        if day != "集":
            # 「1コマ1科目」の原則に合わせ、同一曜日・時限の既存登録（マスタ科目・手動追加科目とも）を
            # 差し替える。手動追加科目はクォーター等の学期情報を持たないため、集中講義以外は
            # 学期を問わず常に差し替える（register_syllabus_for_userのような学期重複判定は行わない）
            await session.execute(
                UserCustomCourse.__table__.delete().where(
                    UserCustomCourse.line_user_id == user_id,
                    UserCustomCourse.year == year,
                    UserCustomCourse.day_of_week == day,
                    UserCustomCourse.period == period,
                )
            )
            conflicting_ids = (await session.execute(
                select(UserSyllabus.syllabus_id)
                .join(Schedule, Schedule.syllabus_id == UserSyllabus.syllabus_id)
                .join(Syllabus, Syllabus.id == UserSyllabus.syllabus_id)
                .where(
                    UserSyllabus.line_user_id == user_id,
                    Syllabus.year == year,
                    Schedule.day_of_week == day,
                    Schedule.period == period,
                )
            )).scalars().all()
            if conflicting_ids:
                await session.execute(
                    UserSyllabus.__table__.delete().where(
                        UserSyllabus.line_user_id == user_id,
                        UserSyllabus.syllabus_id.in_(conflicting_ids),
                    )
                )

        course = UserCustomCourse(
            line_user_id=user_id, name=name, instructor=instructor,
            classification=classification, credits=credits,
            year=year, day_of_week=day, period=period,
        )
        session.add(course)
        await session.commit()
        await session.refresh(course)

    return {
        "ok": True,
        "course": {
            "id": course.id,
            "name": course.name,
            "instructor": course.instructor or "",
            "classification": course.classification or "",
            "credits": course.credits,
            "academic_term": None,
            "timetable_code": "",
            "department": "",
            "target_grades": "",
            "subject_category": "",
            "registered": True,
            "is_custom": True,
        },
    }


@router.delete("/api/timetable/custom/{custom_id}")
async def api_timetable_custom_delete(
    request: Request, custom_id: int, x_liff_id_token: str = Header("", alias="X-Liff-Id-Token"),
):
    user_id = await _require_liff_user(x_liff_id_token, request)
    async with AsyncSessionLocal() as session:
        course = await session.get(UserCustomCourse, custom_id)
        if course and course.line_user_id == user_id:
            await session.delete(course)
            await session.commit()
