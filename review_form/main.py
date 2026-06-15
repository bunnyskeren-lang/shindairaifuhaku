import html as _html
import os
import re
import secrets
from datetime import timezone, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from database import init_db, AsyncSessionLocal
from models import Course, PendingReview, UserProfile, PushSubscription

from dotenv import load_dotenv
load_dotenv()

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
JST = timezone(timedelta(hours=9))


def _fmt_jst(dt, fmt="%m/%d %H:%M") -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST).strftime(fmt)
STUDENT_ID_RE = re.compile(r'^\d{7}(MM|ME|MH|[LHJEBSTAZ])$', re.IGNORECASE)
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_EMAIL = os.environ.get("VAPID_EMAIL", "admin@example.com")


async def send_push_notification(course_name: str, rating: int, ease_rating: str, comment: str):
    if not VAPID_PRIVATE_KEY:
        return
    import asyncio, json as _json
    from pywebpush import webpush, WebPushException
    from sqlalchemy import delete as sa_delete

    async with AsyncSessionLocal() as session:
        subs = (await session.execute(select(PushSubscription))).scalars().all()
    if not subs:
        return

    stars = "★" * rating + "☆" * (5 - rating)
    payload = _json.dumps({
        "title": f"📝 新着レビュー: {course_name}",
        "body": f"{stars}  楽単: {ease_rating}\n{comment[:80]}",
    })

    expired_ids = []
    for sub in subs:
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{VAPID_EMAIL}"},
            )
        except WebPushException as e:
            if e.response is not None and e.response.status_code == 410:
                expired_ids.append(sub.id)
        except Exception:
            pass

    if expired_ids:
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_delete(PushSubscription).where(PushSubscription.id.in_(expired_ids))
            )
            await session.commit()

security = HTTPBasic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ok = secrets.compare_digest(
        credentials.password.encode(), ADMIN_PASSWORD.encode()
    )
    if not ok:
        raise HTTPException(
            status_code=401,
            headers={"WWW-Authenticate": "Basic"},
            detail="Unauthorized",
        )
    return credentials.username


# ── Public routes ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, uid: str = Query(default="")):
    is_new_user = False
    if uid:
        async with AsyncSessionLocal() as session:
            profile = (await session.execute(
                select(UserProfile).where(UserProfile.line_user_id == uid)
            )).scalar_one_or_none()
            is_new_user = profile is None
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "uid": uid, "is_new_user": is_new_user},
    )


@app.get("/api/courses")
async def search_courses(q: str = ""):
    async with AsyncSessionLocal() as session:
        if q.strip():
            tokens = [tok for tok in re.split(r'[\s　]+', q.strip()) if tok]
            def _escape(tok: str) -> str:
                return tok.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            from sqlalchemy import or_
            stmt = select(Course.name, Course.instructor)
            for tok in tokens:
                t = _escape(tok)
                stmt = stmt.where(or_(
                    Course.name.ilike(f"%{t}%", escape="\\"),
                    Course.reading.ilike(f"%{t}%", escape="\\"),
                ))
            stmt = (
                stmt
                .order_by(Course.name)
                .limit(10)
            )
        else:
            stmt = select(Course.name, Course.instructor).order_by(Course.name).limit(30)
        result = await session.execute(stmt)
        courses = result.all()
    return {"courses": [{"name": c[0], "instructor": c[1] or ""} for c in courses]}


@app.post("/submit")
async def submit(
    request: Request,
    submitter_name: str = Form(...),
    course_name: str = Form(...),
    rating: int = Form(...),
    ease_rating: str = Form(...),
    grading_method: str = Form(default=""),
    comment: str = Form(...),
    line_user_id: str = Form(default=""),
    reg_name: str = Form(default=""),
    student_id: str = Form(default=""),
):
    if not (1 <= rating <= 5):
        raise HTTPException(status_code=400, detail="Invalid rating")
    if ease_rating not in ("SS", "S", "A", "B", "C"):
        raise HTTPException(status_code=400, detail="Invalid ease_rating")

    async with AsyncSessionLocal() as session:
        uid = line_user_id.strip()
        if uid:
            existing = (await session.execute(
                select(UserProfile).where(UserProfile.line_user_id == uid)
            )).scalar_one_or_none()
            if existing is None:
                if not reg_name.strip():
                    raise HTTPException(status_code=400, detail="お名前を入力してください")
                if not STUDENT_ID_RE.match(student_id.strip()):
                    raise HTTPException(
                        status_code=400,
                        detail="学籍番号の形式が正しくありません（例：2345678S、医学部は2345678MM）",
                    )
                session.add(UserProfile(
                    line_user_id=uid,
                    name=reg_name.strip()[:100],
                    student_id=student_id.strip(),
                ))

        review = PendingReview(
            submitter_name=submitter_name.strip()[:20],
            course_name=course_name.strip()[:200],
            rating=rating,
            ease_rating=ease_rating,
            grading_method=grading_method.strip()[:500] or None,
            comment=comment.strip()[:500],
            is_approved=False,
        )
        session.add(review)
        await session.commit()

    await send_push_notification(
        course_name=course_name.strip(),
        rating=rating,
        ease_rating=ease_rating,
        comment=comment.strip(),
    )

    return templates.TemplateResponse(
        "success.html", {"request": request, "course_name": course_name}
    )


# ── Admin routes ───────────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(username: str = Depends(verify_admin)):
    async with AsyncSessionLocal() as session:
        pending_result = await session.execute(
            select(PendingReview)
            .where(PendingReview.is_approved == False)
            .order_by(PendingReview.created_at.desc())
        )
        pending = pending_result.scalars().all()

        approved_result = await session.execute(
            select(PendingReview)
            .where(PendingReview.is_approved == True)
            .order_by(PendingReview.created_at.desc())
            .limit(50)
        )
        approved = approved_result.scalars().all()

    def row(r, is_pending: bool):
        badge = (
            '<span style="background:#f59e0b;color:#fff;padding:2px 8px;border-radius:999px;font-size:11px">未承認</span>'
            if is_pending else
            '<span style="background:#10b981;color:#fff;padding:2px 8px;border-radius:999px;font-size:11px">承認済</span>'
        )
        actions = ""
        if is_pending:
            actions = (
                f"<form method='post' action='/admin/approve/{r.id}' style='display:inline'>"
                "<button style='background:#10b981;color:#fff;border:none;padding:4px 12px;border-radius:6px;cursor:pointer;margin-right:4px'>✅ 承認</button></form>"
                f"<form method='post' action='/admin/reject/{r.id}' style='display:inline'"
                " onsubmit='return confirm(\"削除しますか？\")'>"
                "<button style='background:#ef4444;color:#fff;border:none;padding:4px 12px;border-radius:6px;cursor:pointer'>🗑 却下</button></form>"
            )
        ts = _fmt_jst(r.created_at)
        grading = _html.escape(r.grading_method or "―")
        return (
            f"<tr>"
            f"<td style='padding:10px 8px'>{badge}</td>"
            f"<td style='padding:10px 8px'>{ts}</td>"
            f"<td style='padding:10px 8px;font-weight:bold'>{_html.escape(r.course_name)}</td>"
            f"<td style='padding:10px 8px'>{_html.escape(r.submitter_name)}</td>"
            f"<td style='padding:10px 8px'>{'★'*r.rating}{'☆'*(5-r.rating)}</td>"
            f"<td style='padding:10px 8px'>{_html.escape(r.ease_rating)}</td>"
            f"<td style='padding:10px 8px;font-size:12px;color:#555;max-width:160px;word-break:break-word'>{grading}</td>"
            f"<td style='padding:10px 8px;max-width:200px;word-break:break-word'>{_html.escape(r.comment)}</td>"
            f"<td style='padding:10px 8px'>{actions}</td>"
            f"</tr>"
        )

    pending_rows = "".join(row(r, True) for r in pending) or (
        "<tr><td colspan='9' style='text-align:center;color:#9ca3af;padding:20px'>未承認のレビューはありません</td></tr>"
    )
    approved_rows = "".join(row(r, False) for r in approved) or (
        "<tr><td colspan='9' style='text-align:center;color:#9ca3af;padding:20px'>承認済みレビューはありません</td></tr>"
    )

    table_style = (
        "border-collapse:collapse;width:100%;background:#fff;"
        "border-radius:8px;overflow:hidden;font-size:13px"
    )
    th_style = "background:#4f46e5;color:#fff;padding:10px 8px;text-align:left"

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>レビュー管理</title>
<style>
  body{{font-family:sans-serif;margin:0;padding:20px;background:#f8fafc}}
  h2{{color:#1e293b;margin-top:32px}}
  table{{margin-top:12px}}
  tr:hover{{background:#f1f5f9}}
  nav{{margin-bottom:16px;font-size:13px}}
  nav a{{color:#6366f1;text-decoration:none;margin-right:12px}}
</style>
</head>
<body>
<h1 style="color:#1e293b">📋 レビュー管理</h1>
<nav><a href="/admin">📋 レビュー</a><a href="/admin/users">👥 ユーザー登録</a></nav>
<p style="color:#64748b;font-size:13px">未承認レビューを確認して承認・却下してください。</p>

<h2>⏳ 未承認 ({len(pending)}件)</h2>
<div style="overflow-x:auto">
<table style="{table_style}">
<tr>
  <th style="{th_style}">状態</th>
  <th style="{th_style}">日時</th>
  <th style="{th_style}">科目名</th>
  <th style="{th_style}">投稿者</th>
  <th style="{th_style}">評価</th>
  <th style="{th_style}">楽単</th>
  <th style="{th_style}">評価方法</th>
  <th style="{th_style}">コメント</th>
  <th style="{th_style}">操作</th>
</tr>
{pending_rows}
</table>
</div>

<h2>✅ 承認済み（直近50件）</h2>
<div style="overflow-x:auto">
<table style="{table_style}">
<tr>
  <th style="{th_style}">状態</th>
  <th style="{th_style}">日時</th>
  <th style="{th_style}">科目名</th>
  <th style="{th_style}">投稿者</th>
  <th style="{th_style}">評価</th>
  <th style="{th_style}">楽単</th>
  <th style="{th_style}">評価方法</th>
  <th style="{th_style}">コメント</th>
  <th style="{th_style}"></th>
</tr>
{approved_rows}
</table>
</div>
</body>
</html>"""
    return HTMLResponse(html)


@app.post("/admin/approve/{review_id}")
async def approve(review_id: int, username: str = Depends(verify_admin)):
    async with AsyncSessionLocal() as session:
        review = await session.get(PendingReview, review_id)
        if review:
            review.is_approved = True
            await session.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/reject/{review_id}")
async def reject(review_id: int, username: str = Depends(verify_admin)):
    async with AsyncSessionLocal() as session:
        review = await session.get(PendingReview, review_id)
        if review:
            await session.delete(review)
            await session.commit()
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(username: str = Depends(verify_admin)):
    async with AsyncSessionLocal() as session:
        profiles = (await session.execute(
            select(UserProfile).order_by(UserProfile.created_at.desc())
        )).scalars().all()

    table_style = (
        "border-collapse:collapse;width:100%;background:#fff;"
        "border-radius:8px;overflow:hidden;font-size:13px"
    )
    th_style = "background:#4f46e5;color:#fff;padding:10px 8px;text-align:left"

    rows_html = "".join(
        f"<tr>"
        f"<td style='padding:10px 8px;font-size:11px;word-break:break-all'>{_html.escape(p.line_user_id)}</td>"
        f"<td style='padding:10px 8px'>{_html.escape(p.name)}</td>"
        f"<td style='padding:10px 8px'>{_html.escape(p.student_id)}</td>"
        f"<td style='padding:10px 8px'>{_fmt_jst(p.created_at, '%Y/%m/%d %H:%M')}</td>"
        f"</tr>"
        for p in profiles
    ) or (
        "<tr><td colspan='4' style='text-align:center;color:#9ca3af;padding:20px'>登録ユーザーはありません</td></tr>"
    )

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ユーザー登録一覧</title>
<style>
  body{{font-family:sans-serif;margin:0;padding:20px;background:#f8fafc}}
  tr:hover{{background:#f1f5f9}}
  nav{{margin-bottom:16px;font-size:13px}}
  nav a{{color:#6366f1;text-decoration:none;margin-right:12px}}
</style>
</head>
<body>
<h1 style="color:#1e293b">👥 ユーザー登録一覧</h1>
<nav><a href="/admin">📋 レビュー</a><a href="/admin/users">👥 ユーザー登録</a></nav>
<p style="color:#64748b;font-size:13px">登録ユーザー数：{len(profiles)}件</p>
<div style="overflow-x:auto">
<table style="{table_style}">
<tr>
  <th style="{th_style}">LINE ユーザーID</th>
  <th style="{th_style}">氏名（漢字）</th>
  <th style="{th_style}">学籍番号</th>
  <th style="{th_style}">登録日時</th>
</tr>
{rows_html}
</table>
</div>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/health")
@app.head("/health")
async def health():
    return {"status": "healthy"}
