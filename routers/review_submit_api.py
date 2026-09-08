import asyncio
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from core import cache, moderation
from core.activity_log import save_error_log
from core.config import (
    BAN_MESSAGE_TEXT,
    OMNIBUS_INSTRUCTOR_LABEL,
    REVIEW_SUBMISSION_FACULTY_MISMATCH_MESSAGE,
    REVIEW_SUBMISSION_SENMON_CATEGORY, REVIEW_SUBMISSION_RESTRICTED_MESSAGE,
    STUDENT_ID_RE, LINE_USER_ID_RE, is_profile_complete, normalize_student_id,
    subject_submittable_for_profile,
)
from core.liff_auth import verify_liff_id_token
from core.push import send_push_notification
from core.rate_limit import rate_limiter
from core.subject_variants import is_hoken_gakka_senko
from core.templates import templates
from database import AsyncSessionLocal
from models import CourseSection, Instructor, Review, ReviewStatus, Subject, UserProfile

router = APIRouter()

# 修正理由: レビュー連投によるスパム・審査キュー圧迫を防ぐため、IPアドレス単位で1分あたり3回までに制限する
_submit_rate_limit = rate_limiter(max_requests=3, window_seconds=60)


def _success_redirect(course_name: str, review_count: int):
    # PRG（Post/Redirect/Get）: 送信成功時はテンプレートを直接返さず 303 で GET ページへ。
    # ブラウザ履歴・タブ復元・bfcache 復元で再実行されるのが無害な GET になり、
    # フォーム POST 自体の再送（二重送信の主因）が減る。
    # 累計投稿数はURLクエリに載せるとユーザーが書き換えて「初投稿おめでとう」等を任意に
    # 出せてしまう（実害はないが表示の信頼性の問題）。短命Cookieで渡し、/submit/done は
    # クエリを一切見ない。Cookieが無い/壊れている場合は祝いメッセージ自体を出さない。
    qs = urlencode({"course_name": course_name.strip()})
    resp = RedirectResponse(url=f"/submit/done?{qs}", status_code=303)
    resp.set_cookie(
        "kobe_review_n", str(review_count),
        max_age=120, httponly=True, samesite="lax", path="/submit/done",
    )
    return resp


async def _review_count(session, sid: str) -> int:
    """成功画面に出す「累計投稿数」。管理画面 /admin/reviews に並ぶのと同じ、
    その学籍番号のレビュー総数（待機中＋承認済み＋却下済み、ステータス不問）。"""
    return (await session.execute(
        select(func.count(Review.id)).where(Review.student_id == sid)
    )).scalar_one()


async def _prior_review_by_nonce(session, nonce: str):
    """同じ submit_nonce のレビューが既にあれば (student_id, subject_id) を返す。
    二重送信（送信直後のアプリbg化でOS/webviewが保留POSTを再送する事象）の検知に使う。"""
    return (await session.execute(
        select(Review.student_id, CourseSection.subject_id)
        .join(CourseSection, CourseSection.id == Review.course_section_id)
        .where(Review.submit_nonce == nonce)
    )).first()


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
    submit_nonce: str = Form(default=""),
    course_display_name: str = Form(default=""),
    _rl: None = Depends(_submit_rate_limit),
):
    uid: str | None = None

    def _form_error(msg: str, *, telemetry: bool = False):
        # 修正理由: バリデーション拒否は例外を投げずTemplateResponseを直接返すため、
        # main.pyのHTTPException/Exceptionハンドラを一切通らずerror_logsに何も残らなかった。
        # 400を返す理由を追跡できるよう明示的に記録する（レスポンスは待たせずfire-and-forget）。
        # telemetry=True の拒否（二重送信による「既に投稿済み」など、ユーザーの操作ミスでも
        # サーバー不具合でもない想定内の事象。送信直後にLINEアプリがバックグラウンドへ回り
        # モバイルOS/webviewが保留中の送信POSTを後から再送するのが主因）は:
        #  - action接頭辞を submit_duplicate: に分け、/admin/errors の既定一覧（＝本物のエラー）
        #    には出さず /admin/errors?view=submit_duplicate だけに表示する
        #  - Push通知は従来どおり行う（発生状況を取りこぼさないためユーザー指示）が、
        #    クールダウン枠を本物のエラーと別キーにして、二重送信のバーストが障害Pushを
        #    マスクしないようにする
        prefix = "submit_duplicate" if telemetry else "submit_rejected"
        asyncio.create_task(save_error_log(
            RuntimeError(msg),
            user_id=uid,
            action=f"{prefix}:{course_name.strip()[:150]}",
            push_cooldown_key="submit_duplicate" if telemetry else "error",
        ))
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
    if len(comment.strip()) < 30:
        return _form_error(f"コメントは30文字以上で入力してください（現在 {len(comment.strip())} 文字）")

    sid = normalize_student_id(student_id)
    if not STUDENT_ID_RE.match(sid):
        return _form_error("学籍番号の形式が正しくありません（例：2345678S、医学部は2345678MM）")

    # 成功画面に出す科目名。バリアント統合科目はフォーム上「英米法(A/B)」のようなグループ表記で
    # 見えているが、course_name には実際に紐づく1変種名（英米法A）が入るため、ユーザーが見ていた
    # 表記があればそちらを優先して表示する（DB検索・重複判定・Push通知には使わない表示専用）。
    display_name = course_display_name.strip()[:200] or course_name

    # 冪等キー先行チェック: 送信直後のアプリbg化でOS/webviewが保留POSTを再送する事象に備え、
    # 同じ submit_nonce のレビューが既にあれば、LINEログイン再検証（再送POSTは期限切れトークンを
    # 抱えていることが多い）や重複エラー画面を経由せず、1回目のレビューの成功ページへ直行する。
    nonce = submit_nonce.strip()[:64] or None
    if nonce:
        async with AsyncSessionLocal() as session:
            prior = await _prior_review_by_nonce(session, nonce)
            if prior is not None and prior.student_id == sid:
                rc = await _review_count(session, sid)
                return _success_redirect(display_name, rc)

    uid = await verify_liff_id_token(id_token, request)
    if not uid or not LINE_USER_ID_RE.match(uid):
        return _form_error("LINEログインの確認に失敗しました。LINEアプリの「レビュー投稿」から開き直してください")
    if await moderation.is_banned(uid):
        return _form_error(BAN_MESSAGE_TEXT)

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
        is_omnibus = instr_name == OMNIBUS_INSTRUCTOR_LABEL
        cs_obj = None
        if is_omnibus:
            # オムニバス（チーム開講）は特定の担当教員に紐づけず、科目の代表course_section
            # （id昇順の先頭）へ束ねる。残り枠・1件上限の管理はしない擬似候補。
            cs_obj = (await session.execute(
                select(CourseSection)
                .where(CourseSection.subject_id == subject.id)
                .order_by(CourseSection.id)
            )).scalars().first()
            if cs_obj is None:
                return _form_error("この科目はレビューを受け付けていません")
        elif instr_name:
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

        # 学部をまたぐ同名科目は担当教員で subject を確定させたあとに判定する。
        # 教養科目は全員、専門科目は投稿者本人の学部（会員登録情報）のぶんのみ受け付ける。
        # 2026-09-08、ユーザー指示でオンデマンド配信科目のレビュー募集締切ロジックを撤廃。
        # 「教員によらず内容は同一」は科目閲覧LIFFの注記（ON_DEMAND_SAME_CONTENT_NOTE）で
        # 案内するのみとし、投稿自体は担当教員を問わず受け付ける。
        if not subject_submittable_for_profile(
            subject.category, subject.faculty, subject.department,
            existing.faculty, existing.department,
        ):
            if subject.category == REVIEW_SUBMISSION_SENMON_CATEGORY:
                return _form_error(REVIEW_SUBMISSION_FACULTY_MISMATCH_MESSAGE)
            return _form_error(REVIEW_SUBMISSION_RESTRICTED_MESSAGE)

        # 末尾バリアントグループ（例: 線形代数1/2/3/4）に属する科目は、同じ教員が複数メンバーを
        # 担当している場合、レビュー閲覧側では既に1つの科目としてまとめて表示している
        # （routers/liff_api.py _group_subject_ids）。投稿側の重複防止・上限判定もグループ全体で
        # 見ないと、同じ教員のバリアント違い科目それぞれに1件ずつ投稿でき「1科目1件まで」の
        # 上限をすり抜けられてしまう（2026-09-01発覚）。
        group_subject_ids = await cache.get_variant_group_subject_ids(subject)

        if is_omnibus:
            # オムニバスは残り枠・1件上限の管理対象外。同一学籍番号での
            # オムニバス重複投稿だけを科目（バリアントグループ）単位で防ぐ。
            dup_omnibus = (await session.execute(
                select(Review.id)
                .join(CourseSection, CourseSection.id == Review.course_section_id)
                .where(
                    CourseSection.subject_id.in_(group_subject_ids),
                    Review.selected_instructor == OMNIBUS_INSTRUCTOR_LABEL,
                    Review.student_id == sid,
                    Review.status.in_((ReviewStatus.PENDING, ReviewStatus.APPROVED)),
                )
            )).scalars().first()
            if dup_omnibus is not None:
                return _form_error("この科目のオムニバスには、既にレビューを投稿済みです", telemetry=True)
            group_cs_ids = []  # 下の上限チェックはスキップ（is_omnibus分岐で通らない）
        else:
            group_cs_ids = [cs_obj.id]
        if not is_omnibus and len(group_subject_ids) > 1:
            if is_hoken_gakka_senko(subject.faculty or "", subject.department or ""):
                # 保健学科4専攻をまたいだ完全同名科目は、担当教員（専攻）が異なっていても
                # レビュー1件で全専攻分の募集を締め切る共有プールとして扱う（2026-09-06、ユーザー指示）
                group_cs_ids = (await session.execute(
                    select(CourseSection.id).where(CourseSection.subject_id.in_(group_subject_ids))
                )).scalars().all()
            else:
                group_cs_ids = (await session.execute(
                    select(CourseSection.id).where(
                        CourseSection.subject_id.in_(group_subject_ids),
                        CourseSection.instructor_id == cs_obj.instructor_id,
                    )
                )).scalars().all()

        # 修正理由: 同じ学籍番号の人が同じ科目×担当教員の組み合わせへ複数回レビュー投稿できてしまっていたため、
        # 既に投稿済み（待機中+承認済み）があればサーバー側で拒否する（フォーム側のグレーアウトは補助的なもの）
        # （オムニバスは上の is_omnibus 分岐で専用の重複チェック済み・上限管理対象外）
        if not is_omnibus:
            dup_review = (await session.execute(
                select(Review.id).where(
                    Review.course_section_id.in_(group_cs_ids),
                    Review.student_id == sid,
                    Review.status.in_((ReviewStatus.PENDING, ReviewStatus.APPROVED)),
                )
            )).scalars().first()
            if dup_review is not None:
                return _form_error("この科目・担当教員の組み合わせには、既にレビューを投稿済みです", telemetry=True)

            # 2026-09-08、ユーザー指示で「科目×教員あたりの投稿受付上限」を撤廃。
            # 同一学生の重複投稿禁止（上の dup_review）だけを残し、件数上限チェックは廃止した。

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
            submit_nonce=nonce,
        )
        session.add(review)
        try:
            await session.commit()
        except IntegrityError:
            # 同じ submit_nonce の並行INSERT（ほぼ同時に届いた再送POST）。1回目が勝っているので、
            # こちらは重複として扱い既存レビューの成功ページへ流す（エラー画面は出さない）。
            await session.rollback()
            if nonce:
                prior = await _prior_review_by_nonce(session, nonce)
                if prior is not None:
                    rc = await _review_count(session, prior.student_id)
                    return _success_redirect(display_name, rc)
            raise
        cache.invalidate_full_pairs_cache()

        review_count = await _review_count(session, sid)

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

    return _success_redirect(display_name, review_count)


@router.get("/submit/done")
async def submit_done(request: Request, course_name: str = ""):
    # PRG のリダイレクト先。POST /submit が 303 で飛ばしてくる（直接の再送POSTが
    # 無害なGETに置き換わる）。累計投稿数は _success_redirect が張った短命Cookieから
    # 読む（URLクエリには載せない）。Cookieが無い/数値でないときは review_count=0 とし、
    # テンプレート側は 0 のとき祝いメッセージを出さない。
    try:
        review_count = int(request.cookies.get("kobe_review_n", "0"))
    except (TypeError, ValueError):
        review_count = 0
    resp = templates.TemplateResponse(
        "form_success.html", {
            "request": request,
            "course_name": course_name,
            "review_count": max(0, review_count),
        }
    )
    resp.delete_cookie("kobe_review_n", path="/submit/done")
    return resp
