import asyncio
import json
import _env
_env.load_env("dev")

from sqlalchemy import select
from database import AsyncSessionLocal
from models import Subject, CourseSection, Instructor, Syllabus, Review

import pykakasi as _pykakasi
_kks = _pykakasi.kakasi()


def make_reading(text: str) -> str:
    result = _kks.convert(text)
    hira = ''.join(item.get('hira', '') for item in result)
    roma = ''.join(item.get('hepburn', '') for item in result)
    return f"{hira} {roma}".lower().strip()


async def dump_state(s, subject_ids):
    out = {}
    for sid in subject_ids:
        subj = await s.get(Subject, sid)
        cs_res = await s.execute(select(CourseSection).where(CourseSection.subject_id == sid))
        sections = cs_res.scalars().all()
        cs_list = []
        for cs in sections:
            syl_res = await s.execute(select(Syllabus).where(Syllabus.course_section_id == cs.id))
            syls = syl_res.scalars().all()
            rev_res = await s.execute(select(Review).where(Review.course_section_id == cs.id))
            revs = rev_res.scalars().all()
            cs_list.append({
                "id": cs.id, "instructor_id": cs.instructor_id,
                "syllabi": [{"id": sy.id, "year": sy.year, "academic_term": sy.academic_term, "timetable_code": sy.timetable_code} for sy in syls],
                "reviews": [{"id": r.id, "status": r.status, "content": r.content, "rating": r.rating, "ease_rating": r.ease_rating} for r in revs],
            })
        out[sid] = {
            "id": subj.id, "name": subj.name, "faculty": subj.faculty, "department": subj.department,
            "classification": subj.classification, "term_type": subj.term_type,
            "credits": str(subj.credits) if subj.credits is not None else None,
            "reading": subj.reading, "sort_order": subj.sort_order,
            "course_sections": cs_list,
        }
    return out


async def merge_group(s, keep_id, drop_ids, new_name):
    keep = await s.get(Subject, keep_id)
    assert keep is not None

    keep.name = new_name
    keep.reading = make_reading(new_name)

    for drop_id in drop_ids:
        drop = await s.get(Subject, drop_id)
        assert drop is not None

        cs_res = await s.execute(select(CourseSection).where(CourseSection.subject_id == drop_id))
        for cs in cs_res.scalars().all():
            # 同一教員のセクションがkeep側に既にある場合、subject_idの付け替えは
            # UniqueConstraint(subject_id, instructor_id)に抵触する。その場合は
            # drop側に紐づくsyllabi/reviewsをexisting側へ付け替えてからdrop側のセクションを削除する
            existing_res = await s.execute(
                select(CourseSection).where(
                    CourseSection.subject_id == keep_id,
                    CourseSection.instructor_id == cs.instructor_id,
                )
            )
            existing = existing_res.scalars().first()
            if existing is not None:
                syl_res = await s.execute(select(Syllabus).where(Syllabus.course_section_id == cs.id))
                for syl in syl_res.scalars().all():
                    dup_res = await s.execute(
                        select(Syllabus).where(
                            Syllabus.course_section_id == existing.id,
                            Syllabus.year == syl.year,
                            Syllabus.academic_term == syl.academic_term,
                        )
                    )
                    if dup_res.scalars().first() is not None:
                        # existing側に同一年度・学期のシラバスが既にあるので重複を残さず削除する
                        await s.delete(syl)
                    else:
                        syl.course_section_id = existing.id

                rev_res = await s.execute(select(Review).where(Review.course_section_id == cs.id))
                for rev in rev_res.scalars().all():
                    rev.course_section_id = existing.id

                await s.flush()
                await s.delete(cs)
            else:
                cs.subject_id = keep_id

        await s.flush()

        remaining = await s.execute(select(CourseSection).where(CourseSection.subject_id == drop_id))
        assert remaining.scalars().first() is None, f"subject {drop_id} still has course_sections"

        await s.delete(drop)


async def main():
    async with AsyncSessionLocal() as s:
        backup = await dump_state(s, [4691, 4703, 4680, 4690, 4681, 4684, 4705])
        with open("_backup_merge_jobibun_fukuso_fourier_20260905.json", "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2)
        print("backup written")

        await merge_group(s, keep_id=4691, drop_ids=[4703], new_name="常微分方程式論")
        await merge_group(s, keep_id=4680, drop_ids=[4690], new_name="複素関数論")
        await merge_group(s, keep_id=4681, drop_ids=[4684, 4705], new_name="フーリエ解析")

        await s.commit()
        print("merge committed")


if __name__ == "__main__":
    asyncio.run(main())
