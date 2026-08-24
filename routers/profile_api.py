import re as _re

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select

from core import cache, line_client
from core.activity_log import save_error_log
from core.config import (
    DEPARTMENT_UNDECIDED_FACULTIES, DEPARTMENT_UNDECIDED_VALUE, FACULTIES, FACULTY_DEPARTMENTS,
    REGISTER_LIFF_ID, REGISTRATION_WELCOME_UNLOCK_CREDITS, STUDENT_ID_RE, LINE_USER_ID_RE,
    WELCOME_PROMO_SUBJECT_ID,
    is_profile_complete, make_course_liff_url,
)
from core.liff_auth import verify_liff_id_token
from core.rate_limit import rate_limiter
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, Instructor, Review, ReviewStatus, Subject, UserProfile

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
        # 同一学籍番号での「科目×担当教員」重複投稿をフォーム側でグレーアウト表示するため、
        # 既に投稿済み（待機中+承認済み）の組み合わせを合わせて返す。実際の受付可否は/submit側で再確認する。
        reviewed_rows = (await session.execute(
            select(CourseSection.subject_id, Instructor.name)
            .join(Review, Review.course_section_id == CourseSection.id)
            .join(Instructor, Instructor.id == CourseSection.instructor_id)
            .where(
                Review.student_id == profile.student_id,
                Review.status.in_((ReviewStatus.PENDING, ReviewStatus.APPROVED)),
            )
            .distinct()
        )).all()
    return {
        "found": True,
        "name": profile.name,
        "student_id": profile.student_id,
        "faculty": profile.faculty,
        "department": profile.department,
        "reviewed_pairs": [[sid, name] for sid, name in reviewed_rows],
    }


@router.post("/api/register")
async def register_profile(
    request: Request,
    id_token: str = Form(...),
    name: str = Form(...),
    student_id: str = Form(...),
    faculty: str = Form(...),
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
        is_new_registration = profile is None
        promo_subject_name = None
        if is_new_registration:
            # 会員登録直後、もらったチケットの使い方を体験してもらうための案内科目
            promo_subject_name = (await session.execute(
                select(Subject.name).where(Subject.id == WELCOME_PROMO_SUBJECT_ID)
            )).scalar_one_or_none()
        if profile:
            profile.name = name[:100]
            profile.student_id = sid
            profile.faculty = faculty
            profile.department = department
        else:
            # 会員登録（UserProfile初回作成）した全員へ、レビュー閲覧権チケットをプレゼントする
            profile = UserProfile(
                line_user_id=uid,
                name=name[:100],
                student_id=sid,
                faculty=faculty,
                department=department,
                unlock_credits=REGISTRATION_WELCOME_UNLOCK_CREDITS,
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
        "form_register_success.html", {
            "request": request,
            "liff_id": REGISTER_LIFF_ID,
            "welcome_credits": REGISTRATION_WELCOME_UNLOCK_CREDITS if is_new_registration else 0,
            "promo_course_name": promo_subject_name,
            "promo_course_url": make_course_liff_url(WELCOME_PROMO_SUBJECT_ID) if promo_subject_name else "",
        }
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
