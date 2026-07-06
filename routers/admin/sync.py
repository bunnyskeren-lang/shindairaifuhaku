import ssl

import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core import cache
from core.config import DEV_DATABASE_URL
from core.security import check_admin
from database import AsyncSessionLocal, engine
from models import (
    CourseSection, CourseSectionView, DisplayOrder, Instructor, Review,
    Subject, SubjectCreditCategory,
)

router = APIRouter()

_LEGACY_TABLES = [
    "courses", "course_instructors", "syllabus_courses", "course_slots",
    "user_courses", "pending_reviews", "course_views",
]


@router.get("/admin/sync/inspect_legacy")
async def inspect_legacy_tables(_: str = Depends(check_admin)):
    """本番に残っている旧テーブルの存在有無・件数・カラム構成を確認する（読み取り専用）。

    reviews/syllabi/schedules等の本移行スクリプトを書く前提の事前調査用。
    """
    result = {}
    async with engine.begin() as conn:
        for tbl in _LEGACY_TABLES:
            exists = (await conn.execute(text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = :t)"
            ), {"t": tbl})).scalar()
            if not exists:
                result[tbl] = {"exists": False}
                continue
            count = (await conn.execute(text(f"SELECT COUNT(*) FROM {tbl}"))).scalar()
            cols = (await conn.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = :t ORDER BY ordinal_position"
            ), {"t": tbl})).all()
            result[tbl] = {
                "exists": True,
                "count": count,
                "columns": [{"name": c[0], "type": c[1]} for c in cols],
            }
    return JSONResponse(result)


def _dev_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@router.post("/admin/sync/master_data")
async def sync_master_data_from_dev(_: str = Depends(check_admin)):
    """dev DBのマスタデータ（display_orders/subjects/instructors/course_sections/
    subject_credit_categories）を読み取り、本番(=このサービス)側にUPSERTする。

    dev/本番でid列は独立したシーケンスのため、idを直接コピーせず、
    名前などの自然キーで既存行を突き合わせてUPSERTする（idの衝突・identity列の制約を回避）。
    reviews・user_profiles・message_logs等のユーザーデータ/ログ/レビューは対象外。
    dev DBはNetwork Restrictionsが無く読み取れるが、本番DBは接続元IPが制限されており
    開発者の手元からは直接反映できないため、本番アプリ自身から取りに行く。
    """
    if not DEV_DATABASE_URL:
        return JSONResponse({"ok": False, "error": "DEV_DATABASE_URL が未設定です"}, status_code=500)

    dev_conn = await asyncpg.connect(DEV_DATABASE_URL, ssl=_dev_ssl_context())
    try:
        order_rows = await dev_conn.fetch(
            "SELECT kind, name, sort_order, parent_group, faculty FROM display_orders ORDER BY id"
        )
        subj_rows = await dev_conn.fetch(
            "SELECT name, reading, faculty, classification, category, senmon_group, "
            "sort_order, term, term_type, credits, hide_from_timetable "
            "FROM subjects ORDER BY id"
        )
        instr_rows = await dev_conn.fetch("SELECT name, sort_order FROM instructors ORDER BY id")
        section_rows = await dev_conn.fetch(
            "SELECT s.name AS subject_name, i.name AS instructor_name, "
            "cs.course_type, cs.syllabus_url "
            "FROM course_sections cs "
            "JOIN subjects s ON s.id = cs.subject_id "
            "JOIN instructors i ON i.id = cs.instructor_id "
            "ORDER BY cs.id"
        )
        credit_cat_rows = await dev_conn.fetch(
            "SELECT s.name AS subject_name, scc.category_id, scc.credits "
            "FROM subject_credit_categories scc "
            "JOIN subjects s ON s.id = scc.subject_id "
            "ORDER BY scc.id"
        )
    finally:
        await dev_conn.close()

    async with AsyncSessionLocal() as session:
        # ── display_orders: 自然キー (kind, name, faculty) ──────────────────
        for r in order_rows:
            values = {"sort_order": r["sort_order"], "parent_group": r["parent_group"]}
            stmt = pg_insert(DisplayOrder).values(
                kind=r["kind"], name=r["name"], faculty=r["faculty"], **values,
            ).on_conflict_do_update(
                index_elements=["kind", "name", "faculty"], set_=values,
            )
            await session.execute(stmt)

        # ── subjects: 自然キー = name（unique制約が無いため手動UPSERT） ──────
        for r in subj_rows:
            values = {
                "reading": r["reading"], "faculty": r["faculty"], "classification": r["classification"],
                "category": r["category"], "senmon_group": r["senmon_group"], "sort_order": r["sort_order"],
                "term": r["term"], "term_type": r["term_type"], "credits": r["credits"],
                "hide_from_timetable": r["hide_from_timetable"],
            }
            existing_id = (await session.execute(
                select(Subject.id).where(Subject.name == r["name"])
            )).scalar_one_or_none()
            if existing_id is not None:
                await session.execute(
                    Subject.__table__.update().where(Subject.id == existing_id).values(**values)
                )
            else:
                await session.execute(
                    Subject.__table__.insert().values(name=r["name"], **values)
                )

        # ── instructors: 自然キー = name（unique制約あり） ───────────────────
        for r in instr_rows:
            stmt = pg_insert(Instructor).values(
                name=r["name"], sort_order=r["sort_order"],
            ).on_conflict_do_update(
                index_elements=["name"], set_={"sort_order": r["sort_order"]},
            )
            await session.execute(stmt)

        await session.flush()

        subject_id_by_name = dict((await session.execute(select(Subject.name, Subject.id))).all())
        instructor_id_by_name = dict((await session.execute(select(Instructor.name, Instructor.id))).all())

        # ── course_sections: 自然キー = (subject_id, instructor_id) ─────────
        cs_count = 0
        for r in section_rows:
            sid = subject_id_by_name.get(r["subject_name"])
            iid = instructor_id_by_name.get(r["instructor_name"])
            if not sid or not iid:
                continue
            values = {"course_type": r["course_type"], "syllabus_url": r["syllabus_url"]}
            stmt = pg_insert(CourseSection).values(
                subject_id=sid, instructor_id=iid, **values,
            ).on_conflict_do_update(
                index_elements=["subject_id", "instructor_id"], set_=values,
            )
            await session.execute(stmt)
            cs_count += 1

        # ── subject_credit_categories: 自然キー = (subject_id, category_id) ─
        scc_count = 0
        for r in credit_cat_rows:
            sid = subject_id_by_name.get(r["subject_name"])
            if not sid:
                continue
            stmt = pg_insert(SubjectCreditCategory).values(
                subject_id=sid, category_id=r["category_id"], credits=r["credits"],
            ).on_conflict_do_update(
                index_elements=["subject_id", "category_id"], set_={"credits": r["credits"]},
            )
            await session.execute(stmt)
            scc_count += 1

        await session.commit()

    cache.invalidate_courses_cache()
    cache.invalidate_cls_caches()
    cache.invalidate_faculty_order_cache()
    cache.invalidate_credit_group_order_cache()
    cache.invalidate_senmon_cache()

    return JSONResponse({
        "ok": True,
        "display_orders": len(order_rows),
        "subjects": len(subj_rows),
        "instructors": len(instr_rows),
        "course_sections": cs_count,
        "subject_credit_categories": scc_count,
    })


@router.post("/admin/sync/migrate_legacy_reviews")
async def migrate_legacy_reviews(_: str = Depends(check_admin)):
    """本番に残る旧テーブル pending_reviews / course_views を、新スキーマの
    reviews / course_section_views へ移行する（本番DB内で完結、冪等）。

    course_name は新schemaのsubjectsをname一致で探し、見つからなければ
    courses（旧テーブル）の情報で補完しつつ最小限のSubjectを新規作成する。
    course_section は selected_instructor 一致を優先し、無ければ該当科目の
    どれか1件にフォールバックする（元のUI上もinstructor別に厳密区別していないため）。
    """
    async with AsyncSessionLocal() as session:
        legacy_courses = {
            row.name: row for row in (await session.execute(text(
                "SELECT name, classification, category, reading, sort_order, term, credits "
                "FROM courses"
            ))).mappings()
        }

        subject_id_by_name = dict((await session.execute(select(Subject.name, Subject.id))).all())

        async def get_or_create_subject(name: str) -> int:
            sid = subject_id_by_name.get(name)
            if sid is not None:
                return sid
            lc = legacy_courses.get(name)
            values = {
                "classification": lc["classification"] if lc else None,
                "category": lc["category"] if lc else None,
                "reading": lc["reading"] if lc else None,
                "sort_order": lc["sort_order"] if lc else 0,
                "term": lc["term"] if lc else None,
                "credits": lc["credits"] if lc else None,
            }
            new_id = (await session.execute(
                Subject.__table__.insert().values(name=name, **values).returning(Subject.id)
            )).scalar_one()
            subject_id_by_name[name] = new_id
            return new_id

        async def get_course_section(subject_id: int, instructor_name):
            if instructor_name:
                csid = (await session.execute(
                    select(CourseSection.id)
                    .join(Instructor, Instructor.id == CourseSection.instructor_id)
                    .where(CourseSection.subject_id == subject_id, Instructor.name == instructor_name)
                )).scalar_one_or_none()
                if csid is not None:
                    return csid
            return (await session.execute(
                select(CourseSection.id).where(CourseSection.subject_id == subject_id).limit(1)
            )).scalar_one_or_none()

        existing_reviews = set((await session.execute(
            select(Review.course_section_id, Review.created_at)
        )).all())

        pr_rows = (await session.execute(text(
            "SELECT course_name, comment, rating, ease_rating, grading_method, "
            "submitter_name, nickname, student_id, academic_year, is_approved, "
            "created_at, selected_instructor FROM pending_reviews"
        ))).mappings().all()

        migrated = 0
        skipped = []
        for pr in pr_rows:
            sid = await get_or_create_subject(pr["course_name"])
            csid = await get_course_section(sid, pr["selected_instructor"])
            if csid is None:
                skipped.append(pr["course_name"])
                continue
            if (csid, pr["created_at"]) in existing_reviews:
                continue
            await session.execute(Review.__table__.insert().values(
                course_section_id=csid,
                content=pr["comment"],
                rating=pr["rating"],
                ease_rating=pr["ease_rating"],
                grading_method=pr["grading_method"],
                submitter_name=pr["submitter_name"],
                nickname=pr["nickname"],
                student_id=pr["student_id"],
                academic_year=pr["academic_year"],
                selected_instructor=pr["selected_instructor"],
                is_approved=pr["is_approved"],
                created_at=pr["created_at"],
            ))
            existing_reviews.add((csid, pr["created_at"]))
            migrated += 1

        cv_rows = (await session.execute(text(
            "SELECT course_name, view_count, last_viewed_at FROM course_views"
        ))).mappings().all()
        cv_migrated = 0
        for cv in cv_rows:
            sid = subject_id_by_name.get(cv["course_name"])
            if sid is None:
                continue
            csid = await get_course_section(sid, None)
            if csid is None:
                continue
            existing_view = (await session.execute(
                select(CourseSectionView.course_section_id).where(CourseSectionView.course_section_id == csid)
            )).scalar_one_or_none()
            if existing_view is not None:
                await session.execute(
                    CourseSectionView.__table__.update()
                    .where(CourseSectionView.course_section_id == csid)
                    .values(view_count=cv["view_count"], last_viewed_at=cv["last_viewed_at"])
                )
            else:
                await session.execute(CourseSectionView.__table__.insert().values(
                    course_section_id=csid, view_count=cv["view_count"], last_viewed_at=cv["last_viewed_at"],
                ))
            cv_migrated += 1

        await session.commit()

    cache.invalidate_review_cache()

    return JSONResponse({
        "ok": True,
        "reviews_migrated": migrated,
        "reviews_skipped": skipped,
        "course_views_migrated": cv_migrated,
    })


_DROP_ORDER = [
    "course_views", "category_courses", "user_courses", "course_slots",
    "syllabus_courses", "course_instructors", "pending_reviews", "courses",
]


@router.post("/admin/sync/drop_legacy_tables")
async def drop_legacy_tables(_: str = Depends(check_admin)):
    """移行済みの旧テーブルを削除する。安全のため、pending_reviewsの件数が
    新reviewsテーブルの件数以下であることを確認してからでないと実行しない。
    """
    async with engine.begin() as conn:
        pending_exists = (await conn.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pending_reviews')"
        ))).scalar()
        if pending_exists:
            pending_count = (await conn.execute(text("SELECT COUNT(*) FROM pending_reviews"))).scalar()
            reviews_count = (await conn.execute(text("SELECT COUNT(*) FROM reviews"))).scalar()
            if pending_count > reviews_count:
                return JSONResponse({
                    "ok": False,
                    "error": (
                        f"pending_reviews({pending_count}件)がreviews({reviews_count}件)より多いため中止しました。"
                        "先に /admin/sync/migrate_legacy_reviews を実行してください。"
                    ),
                }, status_code=409)

        dropped = []
        for tbl in _DROP_ORDER:
            exists = (await conn.execute(text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = :t)"
            ), {"t": tbl})).scalar()
            if not exists:
                continue
            await conn.execute(text(f"DROP TABLE IF EXISTS {tbl} CASCADE"))
            dropped.append(tbl)

    return JSONResponse({"ok": True, "dropped": dropped})
