import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select

from core.activity_log import save_error_log
from core.config import (
    APP_URL, FACULTIES, FACULTY_DEPARTMENTS, IS_DEV, KYOYO_REQUIRED_CREDITS,
    LIFF_ID, REVIEW_FORM_URL, TIMETABLE_LIFF_ID,
)
from core.templates import templates
from database import AsyncSessionLocal
from models import UserProfile

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, uid: str = Query(default="")):
    is_new_user = False
    stored_name = ""
    stored_student_id = ""
    if uid:
        async with AsyncSessionLocal() as session:
            profile = (await session.execute(
                select(UserProfile).where(UserProfile.line_user_id == uid)
            )).scalar_one_or_none()
            is_new_user = profile is None
            if profile:
                stored_name = profile.name
                stored_student_id = profile.student_id
    response = templates.TemplateResponse(
        "form_index.html",
        {
            "request": request,
            "uid": uid,
            "is_new_user": is_new_user,
            "stored_name": stored_name,
            "stored_student_id": stored_student_id,
            "IS_DEV": IS_DEV,
        },
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, uid: str = Query(default="")):
    profile = None
    if uid:
        async with AsyncSessionLocal() as session:
            profile = await session.get(UserProfile, uid)
    response = templates.TemplateResponse(
        "form_register.html",
        {
            "request": request,
            "uid": uid,
            "stored_name": profile.name if profile else "",
            "stored_student_id": profile.student_id if profile else "",
            "stored_faculty": profile.faculty if profile else "",
            "stored_grade": profile.grade if profile else "",
            "stored_department": profile.department if profile else "",
            "faculties": FACULTIES,
            "faculty_departments_json": json.dumps(FACULTY_DEPARTMENTS, ensure_ascii=False),
            "IS_DEV": IS_DEV,
        },
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})


@router.get("/sw.js")
async def service_worker():
    js = """
self.addEventListener('push', function(e) {
  const d = e.data ? e.data.json() : {};
  e.waitUntil(self.registration.showNotification(d.title || '新着レビュー', {
    body: d.body || '',
    icon: 'https://cdn-icons-png.flaticon.com/512/1041/1041916.png',
    badge: 'https://cdn-icons-png.flaticon.com/512/1041/1041916.png',
  }));
});
self.addEventListener('notificationclick', function(e) {
  e.notification.close();
  e.waitUntil(clients.openWindow('/admin/courses'));
});
""".strip()
    return Response(content=js, media_type="application/javascript")


@router.get("/liff/course", response_class=HTMLResponse)
async def liff_course(request: Request):
    try:
        return templates.TemplateResponse("liff/course.html", {
            "request": request,
            "liff_id": LIFF_ID,
            "review_form_url": REVIEW_FORM_URL,
            "base_url": APP_URL,
            "IS_DEV": IS_DEV,
        })
    except Exception as exc:
        await save_error_log(exc, action="liff_course")
        raise


@router.get("/liff/timetable", response_class=HTMLResponse)
async def liff_timetable(request: Request):
    return templates.TemplateResponse("liff/timetable.html", {
        "request": request,
        "liff_id": TIMETABLE_LIFF_ID,
        "base_url": APP_URL,
        "IS_DEV": IS_DEV,
        "kyoyo_required_credits": KYOYO_REQUIRED_CREDITS,
    })
