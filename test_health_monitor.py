from __future__ import annotations

from collections import deque

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.domains import RuntimeBase
from services.api_hub.health_monitor import HealthMonitor
from services.api_hub.models import ApiHubHealthLog


def test_health_monitor_prunes_window_and_tracks_last_success_failure(monkeypatch):
    current_time = {"value": 1000.0}
    monkeypatch.setattr("services.api_hub.health_monitor._time.time", lambda: current_time["value"])

    monitor = HealthMonitor(window_seconds=120, failure_threshold=2)
    monitor.record("deepseek", True, 120)
    current_time["value"] = 1050.0
    monitor.record("deepseek", False, 0)
    current_time["value"] = 1121.0
    monitor.record("deepseek", True, 180)

    status = monitor.get_status("deepseek")

    assert status.provider == "deepseek"
    assert status.healthy is True
    assert status.sample_count == 2
    assert status.success_rate == 0.5
    assert status.avg_latency_ms == 180
    assert status.last_success_at == 1121.0
    assert status.last_failure_at == 1050.0


def test_health_monitor_marks_provider_unhealthy_after_threshold(monkeypatch):
    monkeypatch.setattr("services.api_hub.health_monitor._time.time", lambda: 100.0)
    monitor = HealthMonitor(window_seconds=120, failure_threshold=2)
    monitor._records["openrouter"] = deque(
        [
            (10.0, False, 0),
            (20.0, False, 0),
            (30.0, True, 300),
        ]
    )

    status = monitor.get_status("openrouter")

    assert status.healthy is False
    assert status.success_rate == 0.333
    assert status.avg_latency_ms == 300
    assert status.sample_count == 3


def test_health_monitor_persists_status_snapshots():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    RuntimeBase.metadata.create_all(bind=engine, tables=[ApiHubHealthLog.__table__])
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        monitor = HealthMonitor(window_seconds=120, failure_threshold=2, db_session_factory=Session)
        monitor.record("deepseek", True, 200)
        monitor.record("deepseek", False, 0)
        monitor.record("deepseek", False, 0)

        with Session() as db:
            rows = db.query(ApiHubHealthLog).order_by(ApiHubHealthLog.id.asc()).all()

        assert len(rows) == 3
        assert rows[0].status == "healthy"
        assert rows[0].sample_count == 1
        assert rows[1].status == "healthy"
        assert rows[1].sample_count == 2
        assert rows[2].status == "degraded"
        assert rows[2].sample_count == 3
        assert rows[2].success_rate == 0.333
    finally:
        RuntimeBase.metadata.drop_all(bind=engine, tables=[ApiHubHealthLog.__table__])
        engine.dispose()
