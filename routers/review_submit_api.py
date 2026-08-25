import asyncio

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import func, select

from core import cache
from core.activity_log import save_error_log
from core.config import (
    MAX_REVIEWS_PER_COURSE_SECTION,
    STUDENT_ID_RE, LINE_USER_ID_RE, is_profile_complete, make_course_liff_url,
)
from core.liff_auth import verify_liff_id_token
from core.push import send_push_notification
from core.rate_limit import rate_limiter
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, Instructor, Review, ReviewStatus, Subject, UserProfile

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
        # 修正理由: 以前はここで未登録ユーザーのプロフィールをreg_name入力だけで
        # その場作成できたが、会員登録(/register)を必ず経由させる方針に変更したため、
        # 会員登録済み（faculty/departmentまで入力済み）でなければ投稿を拒否する
        # (投稿フォーム側もオーバーレイで未登録者をブロックするが、直接APIを叩く迂回策への防御)
        if not is_profile_complete(existing):
            return _form_error("会員登録がまだのようです。先に会員登録を済ませてください")
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

        # 修正理由: 同じ学籍番号の人が同じ科目×担当教員の組み合わせへ複数回レビュー投稿できてしまっていたため、
        # 既に投稿済み（待機中+承認済み）があればサーバー側で拒否する（フォーム側のグレーアウトは補助的なもの）
        dup_review = (await session.execute(
            select(Review.id).where(
                Review.course_section_id == cs_obj.id,
                Review.student_id == sid,
                Review.status.in_((ReviewStatus.PENDING, ReviewStatus.APPROVED)),
            )
        )).scalars().first()
        if dup_review is not None:
            return _form_error("この科目・担当教員の組み合わせには、既にレビューを投稿済みです")

        existing_review_count = (await session.execute(
            select(func.count(Review.id)).where(
                Review.course_section_id == cs_obj.id,
                Review.status.in_((ReviewStatus.PENDING, ReviewStatus.APPROVED)),
            )
        )).scalar_one()
        if existing_review_count >= MAX_REVIEWS_PER_COURSE_SECTION:
            return _form_error("この科目・担当教員へのレビュー投稿数が上限に達したため、募集は締め切りました")

        review = Review(
            course_section_id=cs_obj.id,
            submitter_name=submitter_name,
            content=comment.strip()[:500],
            rating=rating,
            ease_rating=ease_rating,
            # 修正理由: JSON配列形式（core/grading_method.py）に変わり構造上のオーバーヘッドが
            # 増えたため、旧形式時代の上限(500)のままだとJSON途中で切り詰められ壊れる恐れがあった
            grading_method=grading_method.strip()[:2000] or None,
            selected_instructor=instr_name,
            nickname=nickname.strip()[:30] or None,
            academic_year=academic_year,
            student_id=sid or None,
            status=ReviewStatus.PENDING,
        )
        session.add(review)
        await session.commit()
        cache.invalidate_full_pairs_cache()

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
            "course_liff_url": make_course_liff_url(course_id),
        }
    )
