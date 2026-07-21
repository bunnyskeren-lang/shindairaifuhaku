import asyncio

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import func, select

from core.activity_log import save_error_log
from core.config import APP_URL, STUDENT_ID_RE, LINE_USER_ID_RE
from core.liff_auth import verify_liff_id_token
from core.push import send_push_notification
from core.rate_limit import rate_limiter
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, Instructor, Review, Subject, UserProfile

router = APIRouter()

# 修正理由: レビュー連投によるスパム・審査キュー圧迫を防ぐため、IPアドレス単位で1分あたり3回までに制限する
_submit_rate_limit = rate_limiter(max_requests=3, window_seconds=60)


@router.post("/submit")
async def submit(
    request: Request,
    course_name: str = Form(...),
    rating: int = Form(...),
    ease_rating: str = Form(...),
    grading_method: str = Form(default=""),
    comment: str = Form(...),
    id_token: str = Form(default=""),
    reg_name: str = Form(default=""),
    student_id: str = Form(default=""),
    selected_instructor: str = Form(default=""),
    nickname: str = Form(default=""),
    academic_year: int = Form(default=0),
    _rl: None = Depends(_submit_rate_limit),
):
    def _form_error(msg: str):
        return templates.TemplateResponse(
            "form_error.html", {"request": request, "message": msg}, status_code=400
        )

    if not (1 <= rating <= 5):
        return _form_error("評価が不正です")
    if ease_rating not in ("SS", "S", "A", "B", "C"):
        return _form_error("楽単度が不正です")
    if not (2000 <= academic_year <= 2100):
        return _form_error("受講年度を選択してください")
    if not comment.strip():
        return _form_error("コメントを入力してください")

    sid = student_id.strip().upper()
    if not STUDENT_ID_RE.match(sid):
        return _form_error("学籍番号の形式が正しくありません（例：2345678S、医学部は2345678MM）")

    uid = await verify_liff_id_token(id_token, request)
    if not uid or not LINE_USER_ID_RE.match(uid):
        return _form_error("LINEログインの確認に失敗しました。LINEアプリの「レビュー投稿」から開き直してください")

    async with AsyncSessionLocal() as session:
        # 学部をまたいで同名科目が実在しうるため、ここでは存在確認のみ行い
        # .first()で1件だけ取得する（どの学部の科目かは後段の担当教員絞り込みで確定させる）
        subject = (await session.execute(
            select(Subject).where(Subject.name == course_name.strip())
        )).scalars().first()
        if not subject:
            return _form_error("指定された科目が見つかりません")

        existing = (await session.execute(
            select(UserProfile).where(UserProfile.line_user_id == uid)
        )).scalar_one_or_none()
        if existing is None:
            if not reg_name.strip():
                return _form_error("お名前を入力してください")
            taken = (await session.execute(
                select(UserProfile.line_user_id).where(UserProfile.student_id == sid)
            )).scalars().first()
            if taken is not None and taken != uid:
                return _form_error("この学籍番号はすでに別のアカウントで登録されています")
            submitter_name = reg_name.strip()[:100]
            try:
                session.add(UserProfile(
                    line_user_id=uid,
                    name=submitter_name,
                    student_id=sid,
                ))
                await session.flush()
            except Exception as exc:
                await session.rollback()
                await save_error_log(exc, user_id=uid, action="submit_profile_create")
                return _form_error("プロフィールの保存に失敗しました")
        else:
            if existing.student_id != sid:
                return _form_error("学籍番号が登録情報と一致しません")
            submitter_name = existing.name

        # 担当教員に対応する course_section を探す
        instr_name = selected_instructor.strip()[:100] or None
        cs_obj = None
        if instr_name:
            # 科目名＋担当教員名でjoinし直すことで、学部をまたいで同名科目が存在する場合でも
            # 正しいsubject（先頭取得のものとは限らない）とcourse_sectionを一意に特定する
            row = (await session.execute(
                select(Subject, CourseSection)
                .join(CourseSection, CourseSection.subject_id == Subject.id)
                .join(Instructor, Instructor.id == CourseSection.instructor_id)
                .where(Subject.name == course_name.strip(), Instructor.name == instr_name)
            )).first()
            if row is not None:
                subject, cs_obj = row
            # 修正理由: 教員名が指定されたのに一致するcourse_sectionが見つからない場合
            # （教員名変更・統合との競合など）、無条件で「科目の先頭のcourse_section」に
            # フォールバックしていたため、別教員のレビューとして紐づく恐れがあった。
            # 教員未指定（instr_name無し）の場合のみ先頭フォールバックを許可する。
            if cs_obj is None:
                return _form_error("担当教員の情報が更新されています。ページを再読み込みしてもう一度お試しください")
        else:
            cs_obj = (await session.execute(
                select(CourseSection).where(CourseSection.subject_id == subject.id)
            )).scalars().first()
        if cs_obj is None:
            return _form_error("この科目の担当教員情報が見つかりません")

        review = Review(
            course_section_id=cs_obj.id,
            submitter_name=submitter_name,
            content=comment.strip()[:500],
            rating=rating,
            ease_rating=ease_rating,
            grading_method=grading_method.strip()[:500] or None,
            selected_instructor=instr_name,
            nickname=nickname.strip()[:30] or None,
            academic_year=academic_year,
            student_id=sid or None,
            is_approved=False,
        )
        session.add(review)
        await session.commit()

        review_count = (await session.execute(
            select(func.count(Review.id)).where(Review.student_id == sid)
        )).scalar_one()
        course_id = subject.id

    # レビューは既にcommit済みのため、push通知はレスポンスを待たせず
    # バックグラウンドで送る（購読者数が増えても投稿完了レスポンスの速度に影響しないように）。
    async def _notify() -> None:
        try:
            await send_push_notification(
                course_name=course_name.strip(),
                rating=rating,
                ease_rating=ease_rating,
                comment=comment.strip(),
            )
        except Exception as exc:
            await save_error_log(exc, user_id=uid, action="submit_push_notification")

    asyncio.create_task(_notify())

    return templates.TemplateResponse(
        "form_success.html", {
            "request": request,
            "course_name": course_name,
            "course_id": course_id,
            "review_count": review_count,
            "base_url": APP_URL,
        }
    )
