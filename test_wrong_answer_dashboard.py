import sys
import uuid
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, ".")

import routers.wrong_answers_v2 as wrong_answers_v2
from learning_tracking_models import WrongAnswerRetry, WrongAnswerV2
from main import app
from models import Base, Chapter, get_db


class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 3, 13)


_test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Base.metadata.create_all(bind=_test_engine)
_TestSession = sessionmaker(bind=_test_engine)


@pytest.fixture
def db_session():
    connection = _test_engine.connect()
    transaction = connection.begin()
    session = _TestSession(bind=connection)
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, trans):
        nonlocal nested
        if trans.nested and not trans._parent.nested:
            nested = connection.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session):
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _dt(day: date, hour: int = 9) -> datetime:
    return datetime.combine(day, time(hour=hour, minute=0))


def _create_wrong_answer(db_session, **overrides):
    created_at = overrides.get("created_at", _dt(FrozenDate.today() - timedelta(days=1)))
    defaults = {
        "question_fingerprint": uuid.uuid4().hex,
        "question_text": "Which option is correct?",
        "options": {"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D"},
        "correct_answer": "B",
        "explanation": "Option B is correct.",
        "key_point": "dashboard-metric",
        "question_type": "A1",
        "difficulty": "basic",
        "chapter_id": None,
        "severity_tag": "normal",
        "mastery_status": "active",
        "error_count": 1,
        "encounter_count": 1,
        "retry_count": 0,
        "sm2_ef": 2.5,
        "sm2_interval": 0,
        "sm2_repetitions": 0,
        "first_wrong_at": created_at,
        "last_wrong_at": created_at,
        "created_at": created_at,
        "updated_at": created_at,
        "archived_at": None,
        "next_review_date": None,
    }
    defaults.update(overrides)
    item = WrongAnswerV2(**defaults)
    db_session.add(item)
    db_session.flush()
    return item


def test_wrong_answer_dashboard_api_returns_expected_metrics(client, db_session):
    frozen_today = FrozenDate.today()
    week_start = frozen_today - timedelta(days=frozen_today.weekday())
    week_end = week_start + timedelta(days=6)

    db_session.add_all(
        [
            Chapter(id="chap_a", book="Book A", chapter_number="01", chapter_title="Cardio", concepts=[]),
            Chapter(id="chap_b", book="Book A", chapter_number="02", chapter_title="Resp", concepts=[]),
        ]
    )
    db_session.flush()

    active_a1 = _create_wrong_answer(
        db_session,
        chapter_id="chap_a",
        severity_tag="critical",
        mastery_status="active",
        created_at=_dt(frozen_today - timedelta(days=3), 9),
        next_review_date=frozen_today - timedelta(days=1),
    )
    active_a2 = _create_wrong_answer(
        db_session,
        chapter_id="chap_a",
        severity_tag="stubborn",
        mastery_status="active",
        created_at=_dt(frozen_today - timedelta(days=1), 10),
        next_review_date=frozen_today + timedelta(days=1),
    )
    active_b1 = _create_wrong_answer(
        db_session,
        chapter_id="chap_b",
        severity_tag="normal",
        mastery_status="active",
        created_at=_dt(frozen_today, 11),
        next_review_date=week_end,
    )

    _create_wrong_answer(
        db_session,
        chapter_id="chap_a",
        severity_tag="landmine",
        mastery_status="archived",
        created_at=_dt(frozen_today - timedelta(days=12), 8),
        archived_at=_dt(week_start, 18),
        next_review_date=week_start,
    )
    _create_wrong_answer(
        db_session,
        chapter_id="chap_b",
        severity_tag="normal",
        mastery_status="archived",
        created_at=_dt(frozen_today - timedelta(days=11), 8),
        archived_at=_dt(week_start + timedelta(days=1), 18),
        next_review_date=week_start + timedelta(days=1),
    )
    _create_wrong_answer(
        db_session,
        chapter_id="chap_b",
        severity_tag="normal",
        mastery_status="archived",
        created_at=_dt(frozen_today - timedelta(days=10), 8),
        archived_at=_dt(frozen_today - timedelta(days=1), 18),
        next_review_date=frozen_today - timedelta(days=1),
    )
    _create_wrong_answer(
        db_session,
        chapter_id="chap_b",
        severity_tag="normal",
        mastery_status="archived",
        created_at=_dt(frozen_today - timedelta(days=9), 8),
        archived_at=_dt(frozen_today, 18),
        next_review_date=frozen_today,
    )

    db_session.add_all(
        [
            WrongAnswerRetry(
                wrong_answer_id=active_a1.id,
                user_answer="B",
                is_correct=True,
                confidence="sure",
                retried_at=_dt(frozen_today, 12),
            ),
            WrongAnswerRetry(
                wrong_answer_id=active_a1.id,
                user_answer="A",
                is_correct=False,
                confidence="unsure",
                retried_at=_dt(frozen_today - timedelta(days=1), 12),
            ),
            WrongAnswerRetry(
                wrong_answer_id=active_a2.id,
                user_answer="B",
                is_correct=True,
                confidence="sure",
                retried_at=_dt(frozen_today - timedelta(days=2), 12),
            ),
            WrongAnswerRetry(
                wrong_answer_id=active_b1.id,
                user_answer="A",
                is_correct=False,
                confidence="no",
                retried_at=_dt(frozen_today - timedelta(days=8), 12),
            ),
        ]
    )
    db_session.commit()

    with patch.object(wrong_answers_v2, "date", FrozenDate):
        response = client.get("/api/wrong-answers/dashboard")

    assert response.status_code == 200
    data = response.json()

    assert data["overview"] == {
        "active_count": 3,
        "archived_count": 4,
        "total_count": 7,
        "mastery_percent": 57.1,
        "retry_correct_rate": 50.0,
        "retry_rate_delta_vs_last_week": 66.7,
        "streak_days": 3,
        "max_streak_days": 3,
        "active_delta_vs_yesterday": 0,
    }
    assert data["today"] == {
        "new_count": 1,
        "archived_count": 1,
        "retried_count": 1,
        "net_change": 0,
        "trend": "stable",
    }
    assert data["this_week"] == {
        "new_count": 3,
        "archived_count": 4,
        "net_change": 1,
    }
    assert data["severity_distribution"] == {
        "critical": {"count": 1, "percent": 33.3},
        "stubborn": {"count": 1, "percent": 33.3},
        "landmine": {"count": 0, "percent": 0.0},
        "normal": {"count": 1, "percent": 33.3},
    }
    assert data["review_pressure"] == {
        "today_due": 1,
        "tomorrow_due": 2,
        "week_due": 3,
    }
    assert data["projection"]["avg_daily_archived"] == 0.6
    assert data["projection"]["avg_daily_new"] == 0.4
    assert data["projection"]["net_daily_rate"] == 0.1
    assert data["projection"]["estimated_days_to_clear"] == 21
    assert data["projection"]["estimated_clear_date"] == "2026-04-03"
    assert data["projection"]["trend_direction"] == "stable"
    assert data["projection"]["trend_description"] == wrong_answers_v2._trend_description("stable")
    assert "21" in data["projection"]["projection_message"]

    assert len(data["daily_trend"]) == 7
    trend_map = {item["date"]: item for item in data["daily_trend"]}
    assert trend_map["2026-03-10"] == {"date": "2026-03-10", "new": 1, "archived": 1, "net": 0}
    assert trend_map["2026-03-11"] == {"date": "2026-03-11", "new": 0, "archived": 0, "net": 0}
    assert trend_map["2026-03-12"] == {"date": "2026-03-12", "new": 1, "archived": 1, "net": 0}
    assert trend_map["2026-03-13"] == {"date": "2026-03-13", "new": 1, "archived": 1, "net": 0}

    assert data["weak_chapters"] == [
        {
            "chapter_id": "chap_a",
            "chapter_name": "Cardio",
            "active_count": 2,
            "critical_count": 1,
            "stubborn_count": 1,
            "mastery_percent": 33.3,
        },
        {
            "chapter_id": "chap_b",
            "chapter_name": "Resp",
            "active_count": 1,
            "critical_count": 0,
            "stubborn_count": 0,
            "mastery_percent": 75.0,
        },
    ]


def test_wrong_answers_page_renders_embedded_dashboard(client):
    response = client.get("/wrong-answers")

    assert response.status_code == 200
    assert "dashboardActiveCount" in response.text
    assert "dashboardTrendSection" in response.text
    assert "dashboardWeakChaptersBody" in response.text
    assert "/api/wrong-answers/dashboard" in response.text
    assert "/dashboard/stats" not in response.text


def test_legacy_dashboard_route_redirects_to_wrong_answers(client):
    response = client.get("/dashboard/stats", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/wrong-answers"
