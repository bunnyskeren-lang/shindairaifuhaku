# -*- coding: utf-8 -*-
"""
旧テーブル → 新テーブル データ移行スクリプト

実行前に supabase/migrations/20260627150000_fix_subjects_schema.sql を適用済みであること。
冪等設計：途中で失敗しても再実行できる。

移行順序:
  1. courses              → subjects
  2. syllabus_courses(独自) → subjects
  3. course_instructors / courses.instructor / syllabus_courses.instructor → instructors
  4. courses × instructors  → course_sections
  5. syllabus_courses       → syllabi（+ course_sections 追加作成）
  6. course_slots           → schedules
  7. user_courses           → user_syllabi
  8. pending_reviews        → reviews
  9. course_views           → course_section_views
"""
import asyncio
import ssl
import sys
import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

DEV_DB_URL = (
    "postgresql://postgres.ofsvkcptzngbsxtdbqzj:"
    "Developerr6363st@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"
)


def derive_term_type(term: str | None) -> str | None:
    """courses.term の自由テキストから term_type 4値に変換する。"""
    if not term:
        return None
    t = term.strip()
    if "クォーター" in t or "クオーター" in t:
        return "クオーター"
    if "前期" in t or "後期" in t or "セメスター" in t:
        return "セメスター"
    if "集中" in t:
        return "集中"
    if "通年" in t:
        return "通年"
    return None


async def run() -> None:
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    conn = await asyncpg.connect(dsn=DEV_DB_URL, ssl=ssl_ctx)

    try:
        # ── 0. 既存データのプリロード（冪等化のため） ─────────────────────────
        print("[Pre-load] 既存データを読み込み中...")

        # subjects: name → id
        name_to_subject: dict[str, int] = {
            r["name"]: r["id"]
            for r in await conn.fetch("SELECT id, name FROM subjects")
        }
        # instructors: name → id
        instr_to_id: dict[str, int] = {
            r["name"]: r["id"]
            for r in await conn.fetch("SELECT id, name FROM instructors")
        }
        # course_sections: (subject_id, instructor_id) → id
        cs_cache: dict[tuple[int, int], int] = {
            (r["subject_id"], r["instructor_id"]): r["id"]
            for r in await conn.fetch("SELECT id, subject_id, instructor_id FROM course_sections")
        }
        # syllabi: (course_section_id, year, quarter) → id
        syllabi_cache: dict[tuple[int, int, str], int] = {
            (r["course_section_id"], r["year"], r["quarter"]): r["id"]
            for r in await conn.fetch("SELECT id, course_section_id, year, quarter FROM syllabi")
        }

        print(
            f"  subjects={len(name_to_subject)}, instructors={len(instr_to_id)}, "
            f"course_sections={len(cs_cache)}, syllabi={len(syllabi_cache)}"
        )

        # ── 共通ヘルパー ──────────────────────────────────────────────────────

        async def get_or_create_instructor(name: str) -> int | None:
            if not name or not name.strip():
                return None
            n = name.strip()
            if n in instr_to_id:
                return instr_to_id[n]
            new_id = await conn.fetchval(
                "INSERT INTO instructors (name) VALUES ($1)"
                " ON CONFLICT ON CONSTRAINT uq_instructors_name DO UPDATE SET name=EXCLUDED.name"
                " RETURNING id",
                n,
            )
            instr_to_id[n] = new_id
            return new_id

        async def get_or_create_cs(
            subject_id: int, instructor_id: int, syllabus_url: str | None = None
        ) -> int:
            key = (subject_id, instructor_id)
            if key in cs_cache:
                return cs_cache[key]
            new_id = await conn.fetchval(
                "INSERT INTO course_sections (subject_id, instructor_id, syllabus_url)"
                " VALUES ($1,$2,$3)"
                " ON CONFLICT ON CONSTRAINT uq_course_sections_subject_instructor"
                "   DO UPDATE SET syllabus_url = COALESCE(EXCLUDED.syllabus_url, course_sections.syllabus_url)"
                " RETURNING id",
                subject_id, instructor_id, syllabus_url,
            )
            cs_cache[key] = new_id
            return new_id

        # ── 1. courses → subjects ─────────────────────────────────────────────
        print("\n[Step 1] courses → subjects")

        cls_lookup: dict[str, int] = {
            r["name"]: r["id"]
            for r in await conn.fetch(
                "SELECT id, name FROM classification_orders WHERE faculty='経営学部'"
            )
        }

        courses = await conn.fetch("""
            SELECT id, name, reading, faculty, classification, category,
                   senmon_group, sort_order, term, credits, instructor, syllabus_url
            FROM courses ORDER BY id
        """)

        course_to_subject: dict[int, int] = {}
        step1_new = step1_skip = 0

        for c in courses:
            if c["name"] in name_to_subject:
                course_to_subject[c["id"]] = name_to_subject[c["name"]]
                step1_skip += 1
                continue

            sid = await conn.fetchval("""
                INSERT INTO subjects
                    (name, reading, faculty, classification_id, category,
                     senmon_group, sort_order, term, term_type, credits)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                RETURNING id
            """,
                c["name"], c["reading"], c["faculty"],
                cls_lookup.get(c["classification"]),
                c["category"], c["senmon_group"], c["sort_order"],
                c["term"], derive_term_type(c["term"]), c["credits"],
            )
            course_to_subject[c["id"]] = sid
            name_to_subject[c["name"]] = sid
            step1_new += 1

        print(f"  新規={step1_new} / スキップ(既存)={step1_skip}")

        # ── 2. syllabus_courses 独自の科目 → subjects ─────────────────────────
        print("\n[Step 2] syllabus_courses-only → subjects")

        sc_only = await conn.fetch("""
            SELECT DISTINCT ON (name)
                   name, department, term, timetable_code
            FROM syllabus_courses
            WHERE name NOT IN (SELECT name FROM courses)
            ORDER BY name, id
        """)

        step2_new = step2_skip = 0

        for sc in sc_only:
            if sc["name"] in name_to_subject:
                step2_skip += 1
                continue

            code = sc["timetable_code"] or ""
            if len(code) >= 2 and code[1] == "B":
                category, faculty = "専門", "経営学部"
            else:
                category, faculty = "教養", sc["department"] or "教養教育院"

            sid = await conn.fetchval("""
                INSERT INTO subjects (name, faculty, category, term, term_type)
                VALUES ($1,$2,$3,$4,$5) RETURNING id
            """,
                sc["name"], faculty, category,
                sc["term"], derive_term_type(sc["term"]),
            )
            name_to_subject[sc["name"]] = sid
            step2_new += 1

        print(f"  新規={step2_new} / スキップ(既存)={step2_skip}")

        # ── 3. instructors ────────────────────────────────────────────────────
        print("\n[Step 3] instructors")

        courses_with_ci: set[int] = {
            r["course_id"]
            for r in await conn.fetch("SELECT DISTINCT course_id FROM course_instructors")
        }

        for r in await conn.fetch("SELECT DISTINCT name FROM course_instructors"):
            await get_or_create_instructor(r["name"])

        for c in courses:
            if c["id"] not in courses_with_ci and c["instructor"]:
                await get_or_create_instructor(c["instructor"])

        for r in await conn.fetch(
            "SELECT DISTINCT instructor FROM syllabus_courses"
            " WHERE instructor IS NOT NULL AND instructor != ''"
        ):
            await get_or_create_instructor(r["instructor"])

        print(f"  instructors 合計={len(instr_to_id)}")

        # ── 4. course_sections（courses × instructors） ───────────────────────
        print("\n[Step 4] course_sections from courses")

        ci_rows = await conn.fetch("""
            SELECT ci.course_id, ci.name AS iname, ci.url, c.syllabus_url AS csu
            FROM course_instructors ci JOIN courses c ON c.id = ci.course_id
        """)
        for ci in ci_rows:
            sid = course_to_subject.get(ci["course_id"])
            if not sid:
                continue
            iid = await get_or_create_instructor(ci["iname"])
            if iid:
                await get_or_create_cs(sid, iid, ci["url"] or ci["csu"])

        for c in courses:
            if c["id"] not in courses_with_ci and c["instructor"]:
                sid = course_to_subject.get(c["id"])
                if not sid:
                    continue
                iid = await get_or_create_instructor(c["instructor"])
                if iid:
                    await get_or_create_cs(sid, iid, c["syllabus_url"])

        print(f"  course_sections 合計={len(cs_cache)}")

        # ── 5. syllabi（syllabus_courses から） ───────────────────────────────
        print("\n[Step 5] syllabi from syllabus_courses")

        sc_all = await conn.fetch("""
            SELECT id, name, instructor, department, term, year,
                   timetable_code, target_grades, subject_category, numbering_code
            FROM syllabus_courses
        """)

        sc_to_syllabus: dict[int, int] = {}
        syl_ok = syl_warn = 0

        for sc in sc_all:
            sid = name_to_subject.get(sc["name"])
            if not sid:
                print(f"  WARN: subject not found '{sc['name']}'")
                syl_warn += 1
                continue

            iid = (
                await get_or_create_instructor(sc["instructor"])
                if sc["instructor"]
                else None
            )

            if iid:
                csid = await get_or_create_cs(sid, iid)
            else:
                csid = await conn.fetchval(
                    "SELECT id FROM course_sections WHERE subject_id=$1 LIMIT 1", sid
                )
                if not csid:
                    syl_warn += 1
                    continue

            year = sc["year"]
            quarter = sc["term"] or ""
            cache_key = (csid, year, quarter)

            if cache_key in syllabi_cache:
                sc_to_syllabus[sc["id"]] = syllabi_cache[cache_key]
                continue

            try:
                syllabus_id = await conn.fetchval("""
                    INSERT INTO syllabi
                        (course_section_id, year, quarter, timetable_code,
                         target_grades, subject_category, numbering_code, department)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                    ON CONFLICT ON CONSTRAINT uq_syllabi_section_year_quarter
                      DO UPDATE SET timetable_code = EXCLUDED.timetable_code
                    RETURNING id
                """,
                    csid, year, quarter, sc["timetable_code"],
                    sc["target_grades"], sc["subject_category"],
                    sc["numbering_code"], sc["department"],
                )
                sc_to_syllabus[sc["id"]] = syllabus_id
                syllabi_cache[cache_key] = syllabus_id
                syl_ok += 1
            except Exception as e:
                print(f"  ERROR syllabi '{sc['name']}': {e}")
                syl_warn += 1

        print(f"  syllabi: OK={syl_ok} / WARN={syl_warn}")

        # ── 6. schedules（course_slots から） ─────────────────────────────────
        print("\n[Step 6] schedules from course_slots")

        slots = await conn.fetch(
            "SELECT syllabus_course_id, day_of_week, period FROM course_slots"
        )
        sch_ok = sch_warn = 0
        for slot in slots:
            syllabus_id = sc_to_syllabus.get(slot["syllabus_course_id"])
            if not syllabus_id:
                sch_warn += 1
                continue
            try:
                await conn.execute("""
                    INSERT INTO schedules (syllabus_id, day_of_week, period)
                    VALUES ($1,$2,$3)
                    ON CONFLICT ON CONSTRAINT uq_schedules_syllabus_day_period DO NOTHING
                """, syllabus_id, slot["day_of_week"], slot["period"])
                sch_ok += 1
            except Exception as e:
                print(f"  ERROR schedule: {e}")
                sch_warn += 1

        print(f"  schedules: OK={sch_ok} / WARN={sch_warn}")

        # ── 7. user_syllabi（user_courses から） ──────────────────────────────
        print("\n[Step 7] user_syllabi from user_courses")

        uc_rows = await conn.fetch(
            "SELECT line_user_id, syllabus_course_id, created_at FROM user_courses"
        )
        us_ok = us_warn = 0
        for uc in uc_rows:
            syllabus_id = sc_to_syllabus.get(uc["syllabus_course_id"])
            if not syllabus_id:
                print(f"  WARN: syllabus not found for syllabus_course_id={uc['syllabus_course_id']}")
                us_warn += 1
                continue
            try:
                await conn.execute("""
                    INSERT INTO user_syllabi (line_user_id, syllabus_id, created_at)
                    VALUES ($1,$2,$3)
                    ON CONFLICT ON CONSTRAINT uq_user_syllabi DO NOTHING
                """, uc["line_user_id"], syllabus_id, uc["created_at"])
                us_ok += 1
            except Exception as e:
                print(f"  ERROR user_syllabi: {e}")
                us_warn += 1

        print(f"  user_syllabi: OK={us_ok} / WARN={us_warn}")

        # ── 8. reviews（pending_reviews から） ───────────────────────────────
        print("\n[Step 8] reviews from pending_reviews")

        # 既存 reviews（冪等化）: (course_section_id, created_at) でチェック
        existing_reviews: set[tuple[int, object]] = {
            (r["course_section_id"], r["created_at"])
            for r in await conn.fetch("SELECT course_section_id, created_at FROM reviews")
        }

        pr_rows = await conn.fetch("""
            SELECT course_name, comment AS content, rating, ease_rating, grading_method,
                   submitter_name, nickname, student_id, academic_year,
                   is_approved, created_at, selected_instructor
            FROM pending_reviews
        """)
        rv_ok = rv_warn = 0
        for pr in pr_rows:
            sid = name_to_subject.get(pr["course_name"])
            if not sid:
                print(f"  WARN: subject not found for review '{pr['course_name']}'")
                rv_warn += 1
                continue

            # selected_instructor で course_section を絞り込む
            csid = None
            if pr["selected_instructor"]:
                csid = await conn.fetchval("""
                    SELECT cs.id FROM course_sections cs
                    JOIN instructors i ON i.id = cs.instructor_id
                    WHERE cs.subject_id=$1 AND i.name=$2 LIMIT 1
                """, sid, pr["selected_instructor"])
            if not csid:
                csid = await conn.fetchval(
                    "SELECT id FROM course_sections WHERE subject_id=$1 LIMIT 1", sid
                )
            if not csid:
                print(f"  WARN: no course_section for review '{pr['course_name']}'")
                rv_warn += 1
                continue

            if (csid, pr["created_at"]) in existing_reviews:
                continue  # 既に移行済み

            try:
                await conn.execute("""
                    INSERT INTO reviews
                        (course_section_id, content, rating, ease_rating, grading_method,
                         submitter_name, nickname, student_id, academic_year,
                         is_approved, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                """,
                    csid, pr["content"], pr["rating"], pr["ease_rating"],
                    pr["grading_method"], pr["submitter_name"], pr["nickname"],
                    pr["student_id"], pr["academic_year"],
                    pr["is_approved"], pr["created_at"],
                )
                rv_ok += 1
            except Exception as e:
                print(f"  ERROR review '{pr['course_name']}': {e}")
                rv_warn += 1

        print(f"  reviews: OK={rv_ok} / WARN={rv_warn}")

        # ── 9. course_section_views（course_views から） ──────────────────────
        print("\n[Step 9] course_section_views from course_views")

        cv_rows = await conn.fetch(
            "SELECT course_id, view_count, last_viewed_at FROM course_views"
        )
        cv_ok = cv_warn = 0
        for v in cv_rows:
            sid = course_to_subject.get(v["course_id"])
            if not sid:
                cv_warn += 1
                continue
            csid = await conn.fetchval(
                "SELECT id FROM course_sections WHERE subject_id=$1 LIMIT 1", sid
            )
            if not csid:
                cv_warn += 1
                continue
            try:
                await conn.execute("""
                    INSERT INTO course_section_views (course_section_id, view_count, last_viewed_at)
                    VALUES ($1,$2,$3)
                    ON CONFLICT (course_section_id) DO UPDATE
                      SET view_count     = course_section_views.view_count + EXCLUDED.view_count,
                          last_viewed_at = GREATEST(course_section_views.last_viewed_at, EXCLUDED.last_viewed_at)
                """, csid, v["view_count"], v["last_viewed_at"])
                cv_ok += 1
            except Exception as e:
                print(f"  ERROR course_section_views: {e}")
                cv_warn += 1

        print(f"  course_section_views: OK={cv_ok} / WARN={cv_warn}")

        # ── サマリー ──────────────────────────────────────────────────────────
        print("\n" + "=" * 50)
        print("移行完了 - 件数サマリー")
        print("=" * 50)
        for tbl in [
            "subjects", "instructors", "course_sections", "syllabi",
            "schedules", "user_syllabi", "reviews", "course_section_views",
        ]:
            n = await conn.fetchval(f"SELECT COUNT(*) FROM {tbl}")
            print(f"  {tbl:<30}: {n}")

    finally:
        await conn.close()


asyncio.run(run())
