import asyncio
from datetime import date, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from learning_tracking_models import LearningSession
from main import app, _format_dashboard_accuracy
from models import Chapter, DailyUpload, engine
from routers import history as history_router
from routers.history import get_learning_stats, get_upload_history


client = TestClient(app)


def test_dashboard_accuracy_formatter_handles_ratio_and_percent_values():
    assert _format_dashboard_accuracy(0.64) == 64.0
    assert _format_dashboard_accuracy(64.0) == 64.0
    assert _format_dashboard_accuracy(None) is None


def test_history_stats_exposes_streak_days():
    response = client.get("/api/history/stats")

    assert response.status_code == 200
    payload = response.json()
    assert "streak_days" in payload
    assert isinstance(payload["streak_days"], int)


def test_history_endpoints_expose_enriched_fields(monkeypatch):
    from services.data_identity import clear_identity_caches_for_tests

    monkeypatch.setenv("SINGLE_USER_MODE", "false")
    clear_identity_caches_for_tests()

    device_id = "history-test-device"
    chapter_id = f"history-chapter-{uuid4().hex}"
    actor_scope = {
        "request_user_id": None,
        "request_device_id": device_id,
        "candidate_user_id": None,
        "candidate_device_id": device_id,
        "scope_user_id": None,
        "scope_device_id": device_id,
        "scope_device_ids": [device_id],
        "paper_user_id": None,
        "paper_device_id": device_id,
        "actor_key": f"device:{device_id}",
        "actor_keys": [f"device:{device_id}"],
    }

    monkeypatch.setattr(history_router, "resolve_request_actor_scope", lambda: actor_scope)

    with Session(engine) as db:
        db.add(
            Chapter(
                id=chapter_id,
                book="外科学",
                edition="1",
                chapter_number="12",
                chapter_title="术后管理",
                concepts=[{"id": "c1", "name": "感染控制"}],
            )
        )
        db.add(
            DailyUpload(
                device_id=device_id,
                date=date(2026, 3, 18),
                raw_content="upload raw content",
                ai_extracted={
                    "book": "外科学",
                    "chapter_title": "术后管理",
                    "chapter_id": chapter_id,
                    "concepts": [{"id": "c1", "name": "感染控制"}],
                    "summary": "术后管理上传摘要",
                },
                created_at=datetime(2026, 3, 18, 8, 30, 0),
            )
        )
        db.commit()
        uploads_payload = asyncio.run(get_upload_history(days=30, db=db))
        stats_payload = asyncio.run(get_learning_stats(db=db))

    raw_upload = next(item for item in uploads_payload["uploads"] if item["source_type"] == "upload")

    assert raw_upload["chapter_id"] == chapter_id
    assert raw_upload["recorded_at"] == "2026-03-18T08:30:00"
    assert raw_upload["source_label"] == "内容上传"
    assert "active_days" in uploads_payload
    assert "peak_count" in uploads_payload

    assert "active_days" in stats_payload
    assert "busiest_day" in stats_payload
    assert "busiest_day_count" in stats_payload
    assert "source_distribution" in stats_payload


def test_history_merge_records_marks_session_fallbacks_with_source_and_timestamp():
    chapter = Chapter(
        id=f"history-chapter-{uuid4().hex}",
        book="外科学",
        edition="1",
        chapter_number="12",
        chapter_title="术后管理",
        concepts=[{"id": "c1", "name": "感染控制"}],
    )
    session = LearningSession(
        id=f"history-session-{uuid4().hex}",
        session_type="detail_practice",
        chapter_id=chapter.id,
        title="夜间复盘",
        uploaded_content="夜间复盘会话内容",
        knowledge_point="感染控制",
        started_at=datetime(2026, 3, 19, 21, 15, 0),
        created_at=datetime(2026, 3, 19, 21, 15, 0),
    )

    records = history_router._merge_history_records([], [session], {chapter.id: chapter})

    assert len(records) == 1
    assert records[0]["source_type"] == "session"
    assert records[0]["recorded_at"] == datetime(2026, 3, 19, 21, 15, 0)
    assert records[0]["chapter_id"] == chapter.id
    assert records[0]["book"] == "外科学"


def test_history_page_renders_history_board_shell():
    response = client.get("/history")

    assert response.status_code == 200
    html = response.text
    assert "History Board" in html
    assert "来源与科目" in html
    assert "记录明细" in html
    assert "timelineBars" in html
    assert "heroWindowValue" in html


def test_dashboard_page_renders_live_launchpad_metrics():
    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert "累计整卷" in html
    assert "今日到期" in html
    assert "本周新增" in html
    assert "连续学习" in html
    assert "dashboard-bento-card__note" in html
    assert "dashboard-bento-card__metric-value" in html
