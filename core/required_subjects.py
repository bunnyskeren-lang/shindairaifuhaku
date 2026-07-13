import re

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import DEFAULT_ACADEMIC_YEAR
from models import CourseSection, RequiredSubject, Schedule, Syllabus, UserProfile, UserSyllabus

_STUDENT_ID_DIGITS_RE = re.compile(r'^(\d{7})')

# academic_termの生値をクォーター集合へマップし、時限重複時に学期が実際に重なるかどうかの
# 判定に使う（例: 第3クォーターと第4クォーターは同じ曜日・時限でも共存できる）。
# 未知の表記（想定外の値）は安全側に倒して全クォーターと重なるとみなす。
_TERM_TO_QUARTERS: dict[str, set[int]] = {
    "第1クォーター": {1},
    "第2クォーター": {2},
    "第3クォーター": {3},
    "第4クォーター": {4},
    "前期": {1, 2},
    "後期": {3, 4},
    "通年": {1, 2, 3, 4},
}


def _term_to_quarters(term: str | None) -> set[int]:
    return _TERM_TO_QUARTERS.get(term or "", {1, 2, 3, 4})


def _terms_overlap(term_a: str | None, term_b: str | None) -> bool:
    return bool(_term_to_quarters(term_a) & _term_to_quarters(term_b))


def _student_id_parity(student_id: str | None) -> str | None:
    if not student_id:
        return None
    m = _STUDENT_ID_DIGITS_RE.match(student_id)
    if not m:
        return None
    return "even" if int(m.group(1)[-1]) % 2 == 0 else "odd"


async def register_syllabus_for_user(session, user_id: str, syllabus_id: int) -> None:
    """指定syllabusをuser_syllabiへ登録する。同一曜日・時限かつ学期が重なる既存登録があれば
    差し替える（例: 第3クォーターと第4クォーターの科目は同じ曜日・時限でも共存させる）。
    呼び出し元でcommitすること。"""
    new_syl = await session.get(Syllabus, syllabus_id)
    if new_syl is None:
        return

    # 集中講義（day_of_week="集"）は固定コマを持たないため、曜日・時限の重複判定から除外する
    new_slots = (await session.execute(
        select(Schedule.day_of_week, Schedule.period).where(
            Schedule.syllabus_id == syllabus_id,
            Schedule.day_of_week != "集",
        )
    )).all()

    if new_slots:
        conflict_conditions = [
            (Schedule.day_of_week == d) & (Schedule.period == p) for d, p in new_slots
        ]
        candidates = (await session.execute(
            select(UserSyllabus.syllabus_id, Syllabus.academic_term)
            .join(Schedule, Schedule.syllabus_id == UserSyllabus.syllabus_id)
            .join(Syllabus, Syllabus.id == UserSyllabus.syllabus_id)
            .where(
                UserSyllabus.line_user_id == user_id,
                UserSyllabus.syllabus_id != syllabus_id,
                or_(*conflict_conditions),
            )
        )).all()
        conflicting_ids = {
            sid for sid, term in candidates
            if _terms_overlap(new_syl.academic_term, term)
        }
        if conflicting_ids:
            await session.execute(
                UserSyllabus.__table__.delete().where(
                    UserSyllabus.line_user_id == user_id,
                    UserSyllabus.syllabus_id.in_(conflicting_ids),
                )
            )

    await session.execute(
        pg_insert(UserSyllabus)
        .values(line_user_id=user_id, syllabus_id=syllabus_id)
        .on_conflict_do_nothing(index_elements=["line_user_id", "syllabus_id"])
    )


async def auto_register_required_subjects(session, user_id: str, profile: UserProfile) -> None:
    """プロフィール（学部・学科・学年）に応じた必修科目を自動でuser_syllabiへ登録する。
    学籍番号の偶奇でクラスが分かれる科目（required_subjects.student_id_parity）は
    profile.student_id から判定して対象を絞り込む。呼び出し元でcommitすること。"""
    if not (profile.faculty and profile.department and profile.grade):
        return
    parity = _student_id_parity(profile.student_id)
    reqs = (await session.execute(
        select(RequiredSubject).where(
            RequiredSubject.faculty == profile.faculty,
            RequiredSubject.department == profile.department,
            RequiredSubject.grade == profile.grade,
        )
    )).scalars().all()
    for req in reqs:
        if req.student_id_parity and req.student_id_parity != parity:
            continue
        syllabus_id = (await session.execute(
            select(Syllabus.id)
            .join(CourseSection, CourseSection.id == Syllabus.course_section_id)
            .where(
                CourseSection.subject_id == req.subject_id,
                Syllabus.year == DEFAULT_ACADEMIC_YEAR,
            )
            .order_by(Syllabus.id)
        )).scalars().first()
        if syllabus_id is None:
            continue
        await register_syllabus_for_user(session, user_id, syllabus_id)
