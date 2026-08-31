import re as _re

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select

from core import cache, line_client, moderation
from core.activity_log import save_error_log
from core.config import (
    BAN_MESSAGE_TEXT,
    DEPARTMENT_UNDECIDED_FACULTIES, DEPARTMENT_UNDECIDED_VALUE,
    FACULTIES, FACULTY_DEPARTMENTS,
    REGISTER_LIFF_ID, REGISTRATION_WELCOME_UNLOCK_CREDITS, RICHMENU_ID_MAIN,
    STUDENT_ID_RE, LINE_USER_ID_RE,
    WELCOME_PROMO_SUBJECT_ID,
    is_profile_complete, make_course_liff_url, make_review_liff_url, normalize_student_id,
)
from core.liff_auth import verify_liff_id_token
from core.rate_limit import rate_limiter
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, Instructor, Review, ReviewStatus, Subject, UserProfile

router = APIRouter()

# 修正理由: /submit等の他の書き込み系エンドポイントにはレート制限があるのに/api/registerだけ
# 無制限だった。id_token検証には120秒のキャッシュ(core/liff_auth.py)があり、有効なトークン1つで
# 検証をバイパスしてDB書き込みを連打できたため、同水準の制限を設ける
_register_rate_limit = rate_limiter(max_requests=5, window_seconds=60)


@router.post("/api/profile/status")
async def profile_status(request: Request):
    body = await request.json()
    uid = await verify_liff_id_token((body.get("id_token") or "").strip(), request)
    if not uid:
        # 修正理由: LINE側のID token検証APIが一時的に失敗した場合も found=False に
        # なり、呼び出し側が「未登録」と誤判定していた(2026-08-31、本番で発生・
        # 大西さんの報告で発覚)。検証失敗と本当にプロフィールが無いケースを
        # 呼び出し側で区別できるようフラグを追加する。
        return {"complete": False, "found": False, "auth_failed": True}
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, uid)
        # foundはUserProfile自体の有無を区別するために追加。completeだけだと
        # 「未登録」と「登録済みだが学部学科未入力」を区別できず、前者を会員登録
        # フォームの新規入力へ、後者をプリフィル入りの編集へ、と出し分けられない
        return {"complete": is_profile_complete(profile), "found": profile is not None}


@router.post("/api/profile/prefill")
async def profile_prefill(request: Request):
    """LIFF ID token検証済みの本人の既存プロフィールを返す（新規登録フォームのプリフィル用）。

    修正理由: 以前は ?uid= から直接DB照会してテンプレートに埋め込んでいたため、
    任意のuidを指定するだけで他人の氏名・学籍番号等が閲覧できるIDOR/PII漏洩になっていた。
    """
    body = await request.json()
    uid = await verify_liff_id_token((body.get("id_token") or "").strip(), request)
    if not uid:
        # 修正理由: profile_status()と同様、LINE側のID token検証失敗と本当に
        # プロフィールが無いケースを呼び出し側で区別できるようフラグを追加する。
        return {"found": False, "auth_failed": True}
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
        # レビュー投稿フォーム(form_index.html)がこのフラグでオーバーレイブロックする。
        # お問い合わせフォーム(contact.html)は意図的にこのフラグを見ず、BAN中でも
        # 学籍番号等プリフィルは通常通り行う(BANされたユーザーの異議申立て手段のため)
        "banned": profile.banned_at is not None,
    }


@router.post("/api/register")
async def register_profile(
    request: Request,
    id_token: str = Form(""),
    name: str = Form(""),
    student_id: str = Form(""),
    faculty: str = Form(""),
    department: str = Form(""),
    _rl=Depends(_register_rate_limit),
):
    def _form_error(msg: str):
        return templates.TemplateResponse(
            "form_error.html", {"request": request, "message": msg}, status_code=400
        )

    uid = await verify_liff_id_token(id_token, request)
    if not uid or not LINE_USER_ID_RE.match(uid):
        return _form_error("LINEログインの確認に失敗しました。LINEアプリから開き直してください")
    if await moderation.is_banned(uid):
        return _form_error(BAN_MESSAGE_TEXT)
    name = _re.sub(r'[\s　]+', '', name)
    if not name:
        return _form_error("お名前を入力してください")
    sid = normalize_student_id(student_id)
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
        # LINE側のデフォルトリッチメニューは登録前メニュー(setup_richmenu.py参照)なので、
        # 登録完了後は通常メニューを明示的にlinkする(unlinkだけだとデフォルト=登録前に戻ってしまう)
        await line_client.link_rich_menu(uid, RICHMENU_ID_MAIN)
    except Exception as exc:
        await save_error_log(exc, user_id=uid, action="register_richmenu_link")

    return templates.TemplateResponse(
        "form_register_success.html", {
            "request": request,
            "liff_id": REGISTER_LIFF_ID,
            "welcome_credits": REGISTRATION_WELCOME_UNLOCK_CREDITS if is_new_registration else 0,
            "promo_course_name": promo_subject_name,
            "promo_course_url": make_course_liff_url(WELCOME_PROMO_SUBJECT_ID) if promo_subject_name else "",
            # 会員登録は「レビュー投稿フォームを開こうとして未登録だったので誘導された」流れの
            # 最終ステップとして辿り着くのがほとんどのため、登録完了後はLINEのトーク画面を挟まず
            # 直接レビュー投稿フォームへ戻す
            "review_liff_url": make_review_liff_url(user_id=uid),
        }
    )


