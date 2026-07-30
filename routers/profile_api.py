import re as _re

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select

from core import cache, line_client
from core.activity_log import save_error_log
from core.config import (
    DEPARTMENT_UNDECIDED_FACULTIES, DEPARTMENT_UNDECIDED_VALUE, FACULTIES, FACULTY_DEPARTMENTS,
    REGISTER_LIFF_ID, STUDENT_ID_RE, LINE_USER_ID_RE,
    is_profile_complete,
)
from core.liff_auth import verify_liff_id_token
from core.rate_limit import rate_limiter
from core.templates import templates
from database import AsyncSessionLocal
from models import Review, UserProfile

router = APIRouter()

# 修正理由: student_idの総当たりによる他人の氏名取得を防ぐため、IPアドレス単位で1分あたり10回までに制限する
_autofill_rate_limit = rate_limiter(max_requests=10, window_seconds=60)
# 修正理由: /submit等の他の書き込み系エンドポイントにはレート制限があるのに/api/registerだけ
# 無制限だった。id_token検証には120秒のキャッシュ(core/liff_auth.py)があり、有効なトークン1つで
# 検証をバイパスしてDB書き込みを連打できたため、同水準の制限を設ける
_register_rate_limit = rate_limiter(max_requests=5, window_seconds=60)


@router.post("/api/profile/status")
async def profile_status(request: Request):
    body = await request.json()
    uid = await verify_liff_id_token((body.get("id_token") or "").strip(), request)
    if not uid:
        return {"complete": False}
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, uid)
        return {"complete": is_profile_complete(profile)}


@router.post("/api/profile/prefill")
async def profile_prefill(request: Request):
    """LIFF ID token検証済みの本人の既存プロフィールを返す（新規登録フォームのプリフィル用）。

    修正理由: 以前は ?uid= から直接DB照会してテンプレートに埋め込んでいたため、
    任意のuidを指定するだけで他人の氏名・学籍番号等が閲覧できるIDOR/PII漏洩になっていた。
    """
    body = await request.json()
    uid = await verify_liff_id_token((body.get("id_token") or "").strip(), request)
    if not uid:
        return {"found": False}
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, uid)
    if not profile:
        return {"found": False}
    return {
        "found": True,
        "name": profile.name,
        "student_id": profile.student_id,
        "faculty": profile.faculty,
        "grade": profile.grade,
        "department": profile.department,
    }


@router.post("/api/register")
async def register_profile(
    request: Request,
    id_token: str = Form(...),
    name: str = Form(...),
    student_id: str = Form(...),
    faculty: str = Form(...),
    grade: int = Form(...),
    department: str = Form(...),
    _rl=Depends(_register_rate_limit),
):
    def _form_error(msg: str):
        return templates.TemplateResponse(
            "form_error.html", {"request": request, "message": msg}, status_code=400
        )

    uid = await verify_liff_id_token(id_token, request)
    if not uid or not LINE_USER_ID_RE.match(uid):
        return _form_error("LINEログインの確認に失敗しました。LINEアプリから開き直してください")
    name = _re.sub(r'[\s　]+', '', name)
    if not name:
        return _form_error("お名前を入力してください")
    sid = _re.sub(r'[\s　]+', '', student_id).upper()
    if not STUDENT_ID_RE.match(sid):
        return _form_error("学籍番号の形式が正しくありません（例：2345678S、医学部は2345678MM）")
    if faculty not in FACULTIES:
        return _form_error("学部を選択してください")
    if not (1 <= grade <= 6):
        return _form_error("学年を選択してください")
    # 2年次からコース分岐する学部（農学部等）は1年次に所属コースが無いため「コース未定」を許容し、
    # departmentはNULLで保存する
    if faculty in DEPARTMENT_UNDECIDED_FACULTIES and department == DEPARTMENT_UNDECIDED_VALUE:
        department = None
    elif department not in FACULTY_DEPARTMENTS.get(faculty, []):
        return _form_error("学科を選択してください")

    async with AsyncSessionLocal() as session:
        taken = (await session.execute(
            select(UserProfile.line_user_id).where(UserProfile.student_id == sid)
        )).scalars().first()
        if taken is not None and taken != uid:
            return _form_error("この学籍番号はすでに別のアカウントで登録されています")

        profile = await session.get(UserProfile, uid)
        if profile:
            profile.name = name[:100]
            profile.student_id = sid
            profile.faculty = faculty
            profile.grade = grade
            profile.department = department
        else:
            profile = UserProfile(
                line_user_id=uid,
                name=name[:100],
                student_id=sid,
                faculty=faculty,
                grade=grade,
                department=department,
            )
            session.add(profile)
        try:
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await save_error_log(exc, user_id=uid, action="register_profile")
            return _form_error("登録に失敗しました。もう一度お試しください")

    cache.set_registration_complete(uid)

    try:
        await line_client.unlink_rich_menu(uid)
    except Exception as exc:
        await save_error_log(exc, user_id=uid, action="register_richmenu_unlink")

    return templates.TemplateResponse(
        "form_register_success.html", {"request": request, "liff_id": REGISTER_LIFF_ID}
    )


@router.post("/api/autofill")
async def autofill_profile(request: Request, _rl: None = Depends(_autofill_rate_limit)):
    body = await request.json()
    id_token = (body.get("id_token") or "").strip()
    student_id = (body.get("student_id") or "")
    uid = await verify_liff_id_token(id_token, request)
    sid = student_id.strip().upper()
    if not uid or not sid or not STUDENT_ID_RE.match(sid):
        return {"found": False}
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(UserProfile).where(UserProfile.line_user_id == uid)
        )).scalar_one_or_none()
        if existing:
            return {"found": True, "name": existing.name}
        row = (await session.execute(
            select(Review.submitter_name)
            .where(Review.student_id == sid)
            .order_by(Review.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        if not row:
            return {"found": False}
        taken = (await session.execute(
            select(UserProfile.line_user_id).where(UserProfile.student_id == sid)
        )).scalars().first()
        if taken is not None and taken != uid:
            return {"found": False}
        if not taken:
            try:
                session.add(UserProfile(line_user_id=uid, name=row, student_id=sid))
                await session.commit()
            except Exception as exc:
                await session.rollback()
                await save_error_log(exc, user_id=uid, action="autofill_profile_create")
        return {"found": True, "name": row}
