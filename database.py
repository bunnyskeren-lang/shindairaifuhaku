import os
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from models import Course
    from courses import COURSES

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Course).limit(1))
        if result.scalar_one_or_none() is None:
            for name, data in COURSES.items():
                course = Course(
                    name=name,
                    instructor=data["instructor"],
                    format=data["format"],
                    classification=data["classification"],
                    content=data["content"],
                    evaluation=data["evaluation"],
                    rating=data["rating"],
                    ease_rating=data["ease_rating"],
                    comment=data["comment"],
                    syllabus_url=data.get("syllabus_url", ""),
                )
                session.add(course)
            await session.commit()
