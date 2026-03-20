import asyncio
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from models import Base, Chapter
from utils.chapter_catalog import extract_book_name_from_text


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_list_chapters_prefers_canonical_catalog_rows(db_session):
    db_session.add_all(
        [
            Chapter(
                id="physio_ch16",
                book="生理学",
                edition="test",
                chapter_number="16",
                chapter_title="口腔食管和胃内消化",
                concepts=[],
                first_uploaded=date.today(),
            ),
            Chapter(
                id="surgery_ch01",
                book="外科学",
                edition="test",
                chapter_number="01",
                chapter_title="颈部疾病与食管疾病",
                concepts=[],
                first_uploaded=date.today(),
            ),
            Chapter(
                id="internal_medicine_ch11",
                book="内科学",
                edition="test",
                chapter_number="11",
                chapter_title="心力衰竭",
                concepts=[],
                first_uploaded=date.today(),
            ),
            Chapter(
                id="chapter-1234567890abcdef",
                book="生理学",
                edition="test",
                chapter_number="16",
                chapter_title="口腔食管和胃内消化",
                concepts=[],
                first_uploaded=date.today(),
            ),
        ]
    )
    db_session.commit()

    rows = asyncio.run(main.list_chapters(include_empty=True, db=db_session))
    returned_ids = [row["id"] for row in rows]

    assert returned_ids == ["surgery_ch01", "physio_ch16"]


def test_extract_book_name_from_text_supports_course_aliases():
    hint = extract_book_name_from_text(
        "01.内科含诊断 COPD1 天天师兄26考研.mp4",
        allowed_books=["内科学", "外科学", "生理学"],
    )

    assert hint == "内科学"
