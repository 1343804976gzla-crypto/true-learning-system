from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from learning_tracking_models import DailyReviewPaper, DailyReviewPaperItem, WrongAnswerV2, make_fingerprint
from main import app
from models import Base, get_db
from routers.wrong_answers_v2 import _build_daily_review_stem_fingerprint
from services.data_identity import build_actor_key, clear_identity_caches_for_tests


@pytest.fixture(autouse=True)
def disable_single_user_mode(monkeypatch):
    monkeypatch.setenv("SINGLE_USER_MODE", "false")
    clear_identity_caches_for_tests()
    try:
        yield
    finally:
        monkeypatch.delenv("SINGLE_USER_MODE", raising=False)
        clear_identity_caches_for_tests()


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        yield Session
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(session_factory):
    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_wrong_answer(
    session,
    wrong_answer_id: int,
    *,
    device_id: str,
    question_text: str,
    key_point: str,
    created_at: datetime,
    next_review_date: date | None = None,
    question_type: str = "A1",
    difficulty: str = "基础",
) -> None:
    session.add(
        WrongAnswerV2(
            id=wrong_answer_id,
            device_id=device_id,
            question_fingerprint=make_fingerprint(f"{device_id}:{question_text}:{wrong_answer_id}"),
            question_text=question_text,
            options={"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D"},
            correct_answer="A",
            explanation=f"Explanation for {question_text}",
            key_point=key_point,
            question_type=question_type,
            difficulty=difficulty,
            chapter_id=f"chapter-{key_point}",
            error_count=1,
            encounter_count=1,
            severity_tag="normal",
            mastery_status="active",
            first_wrong_at=created_at,
            last_wrong_at=created_at,
            next_review_date=next_review_date,
            created_at=created_at,
            updated_at=created_at,
        )
    )


def _seed_candidate_pool(session, *, device_id: str, count: int, base_time: datetime) -> None:
    for item_id in range(1, count + 1):
        _seed_wrong_answer(
            session,
            item_id,
            device_id=device_id,
            question_text=f"PDF date question {item_id}",
            key_point=f"kp-{item_id}",
            created_at=base_time + timedelta(minutes=item_id),
            next_review_date=None,
            question_type="X" if item_id % 3 == 0 else "A1",
            difficulty="难题" if item_id % 4 == 0 else "基础",
        )
    session.commit()


def _paper_item_ids(session, *, paper_date: date, actor_key: str) -> list[int]:
    paper = (
        session.query(DailyReviewPaper)
        .filter(DailyReviewPaper.paper_date == paper_date, DailyReviewPaper.actor_key == actor_key)
        .first()
    )
    assert paper is not None
    return [int(item.wrong_answer_id) for item in sorted(paper.items, key=lambda item: item.position)]


def test_daily_review_pdf_export_uses_requested_date_and_creates_ten_items(client, session_factory):
    device_id = "pdf-date-device"
    paper_date = date(2026, 3, 18)
    base_time = datetime(2026, 3, 1, 9, 0, 0)
    actor_key = build_actor_key(None, device_id)

    with session_factory() as db:
        _seed_candidate_pool(db, device_id=device_id, count=18, base_time=base_time)

    response = client.get(
        "/api/wrong-answers/daily-review-pdf",
        params={"paper_date": paper_date.isoformat()},
        headers={"X-TLS-Device-ID": device_id},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "daily-review-2026-03-18.pdf" in (response.headers.get("content-disposition") or "")

    with session_factory() as db:
        selected_ids = _paper_item_ids(db, paper_date=paper_date, actor_key=actor_key)
        assert len(selected_ids) == 10


def test_daily_review_pdf_export_avoids_recent_items_across_month_boundary(client, session_factory):
    device_id = "pdf-boundary-device"
    previous_date = date(2026, 3, 31)
    next_date = date(2026, 4, 2)
    base_time = datetime(2026, 3, 1, 8, 0, 0)
    actor_key = build_actor_key(None, device_id)

    with session_factory() as db:
        for wrong_answer_id in range(1, 11):
            _seed_wrong_answer(
                db,
                wrong_answer_id,
                device_id=device_id,
                question_text=f"Recent-window question {wrong_answer_id}",
                key_point=f"recent-kp-{wrong_answer_id}",
                created_at=base_time + timedelta(minutes=wrong_answer_id),
                question_type="A1",
                difficulty="基础",
            )
        for wrong_answer_id in range(11, 21):
            _seed_wrong_answer(
                db,
                wrong_answer_id,
                device_id=device_id,
                question_text=f"Fresh-window question {wrong_answer_id}",
                key_point=f"fresh-kp-{wrong_answer_id}",
                created_at=base_time + timedelta(minutes=wrong_answer_id),
                question_type="X" if wrong_answer_id <= 15 else "A1",
                difficulty="难题" if wrong_answer_id in {11, 12, 13, 14, 15} else "基础",
            )
        db.commit()
        previous_paper = DailyReviewPaper(
            device_id=device_id,
            actor_key=actor_key,
            paper_date=previous_date,
            total_questions=10,
            config={"seed": "previous"},
            created_at=base_time + timedelta(days=1),
            updated_at=base_time + timedelta(days=1),
        )
        db.add(previous_paper)
        db.flush()
        for position, wrong_answer_id in enumerate(range(1, 11), start=1):
            previous_paper.items.append(
                DailyReviewPaperItem(
                    wrong_answer_id=wrong_answer_id,
                    position=position,
                    stem_fingerprint=_build_daily_review_stem_fingerprint(f"Recent-window question {wrong_answer_id}"),
                    source_bucket="supplement",
                    snapshot={"question_text": f"Recent-window question {wrong_answer_id}"},
                    created_at=base_time + timedelta(days=1),
                )
            )
        db.commit()

    response = client.get(
        "/api/wrong-answers/daily-review-pdf",
        params={"paper_date": next_date.isoformat()},
        headers={"X-TLS-Device-ID": device_id},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "daily-review-2026-04-02.pdf" in (response.headers.get("content-disposition") or "")

    with session_factory() as db:
        selected_ids = _paper_item_ids(db, paper_date=next_date, actor_key=actor_key)
        assert len(selected_ids) == 10
        assert set(selected_ids).isdisjoint(set(range(1, 11)))
        assert set(selected_ids) == set(range(11, 21))


def test_daily_review_pdf_export_still_works_for_far_future_dates(client, session_factory):
    device_id = "pdf-future-device"
    paper_date = date(2026, 7, 15)
    base_time = datetime(2026, 3, 1, 10, 0, 0)
    actor_key = build_actor_key(None, device_id)

    with session_factory() as db:
        _seed_candidate_pool(db, device_id=device_id, count=16, base_time=base_time)

    response = client.get(
        "/api/wrong-answers/daily-review-pdf",
        params={"paper_date": paper_date.isoformat()},
        headers={"X-TLS-Device-ID": device_id},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "daily-review-2026-07-15.pdf" in (response.headers.get("content-disposition") or "")

    with session_factory() as db:
        selected_ids = _paper_item_ids(db, paper_date=paper_date, actor_key=actor_key)
        assert len(selected_ids) == 10
