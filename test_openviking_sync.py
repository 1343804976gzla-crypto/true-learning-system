from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from learning_tracking_models import LearningSession, QuestionRecord
from models import Chapter, DailyUpload, SessionLocal
from services import openviking_sync


def test_build_sync_operation_for_question_record_contains_generated_question_snapshot():
    with SessionLocal() as db:
        session = LearningSession(
            id=f"ov-sync-session-{uuid4().hex}",
            session_type="exam",
            title="Cardiology Exam",
            status="completed",
        )
        db.add(session)
        db.flush()

        record = QuestionRecord(
            session_id=session.id,
            question_index=0,
            question_type="A2",
            difficulty="advanced",
            question_text="Heart failure patients with pulmonary edema should receive what first-line therapy?",
            options={"A": "Loop diuretics", "B": "Beta blockers"},
            correct_answer="A",
            explanation="Loop diuretics reduce preload and relieve congestion.",
            key_point="Acute heart failure",
            user_answer="B",
            is_correct=False,
            answered_at=datetime.now(),
        )
        db.add(record)
        db.flush()

        operation = openviking_sync.build_sync_operation(record, action="upsert")

        assert operation is not None
        assert operation.table_name == "question_records"
        assert operation.payload is not None
        assert operation.payload["record"]["question_text"].startswith("Heart failure patients")
        assert operation.payload["record"]["options"]["A"] == "Loop diuretics"
        assert operation.resource_uri.endswith(".md")
        assert "Acute heart failure" in (operation.document_text or "")

        db.rollback()


def test_build_sync_operation_for_chapter_uses_primary_key_in_title():
    chapter = Chapter(
        id="medicine_ch1-1",
        book="Internal Medicine",
        chapter_number="1-1",
        chapter_title="Introduction",
    )

    operation = openviking_sync.build_sync_operation(chapter, action="upsert")

    assert operation is not None
    assert operation.document_title.startswith("Chapter: id=medicine_ch1-1")
    assert "Introduction" in operation.document_title


def test_openviking_sync_hooks_capture_question_generation_commit(monkeypatch):
    monkeypatch.setenv("OPENVIKING_SYNC_ENABLED", "true")
    openviking_sync.install_openviking_sync_hooks()

    submitted: list[list[openviking_sync.SyncOperation]] = []
    monkeypatch.setattr(
        openviking_sync,
        "_submit_sync_operations",
        lambda operations: submitted.append(list(operations)),
    )

    with SessionLocal() as db:
        session = LearningSession(
            id=f"ov-sync-session-{uuid4().hex}",
            session_type="exam",
            title="Respiratory Quiz",
            status="completed",
        )
        db.add(session)

        record = QuestionRecord(
            session=session,
            question_index=0,
            question_type="A1",
            difficulty="basic",
            question_text="Which sign is classic for asthma exacerbation?",
            options={"A": "Wheezing", "B": "Jaundice"},
            correct_answer="A",
            explanation="Wheezing is a common sign of airflow obstruction.",
            key_point="Asthma",
            user_answer="A",
            is_correct=True,
            answered_at=datetime.now(),
        )
        db.add(record)
        db.commit()

    assert submitted
    synced_tables = {operation.table_name for operation in submitted[0]}
    assert "learning_sessions" in synced_tables
    assert "question_records" in synced_tables


def test_process_sync_operations_writes_exports_and_upserts_remote(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENVIKING_SYNC_ENABLED", "true")
    monkeypatch.setenv("OPENVIKING_ENABLED", "true")
    monkeypatch.setenv("OPENVIKING_URL", "http://localhost:1933")
    monkeypatch.setenv("OPENVIKING_SYNC_EXPORT_DIR", str(tmp_path))
    monkeypatch.setenv("OPENVIKING_SYNC_ROOT_URI", "viking://resources/test-sync")

    openviking_sync._ENSURED_REMOTE_DIRS.clear()

    calls: dict[str, list] = {"stat": [], "mkdir": [], "remove": [], "add": []}

    monkeypatch.setattr(
        openviking_sync,
        "openviking_stat",
        lambda uri, missing_ok=False: calls["stat"].append((uri, missing_ok)) or None,
    )
    monkeypatch.setattr(
        openviking_sync,
        "openviking_mkdir",
        lambda uri: calls["mkdir"].append(uri) or {"uri": uri},
    )
    monkeypatch.setattr(
        openviking_sync,
        "openviking_remove_uri",
        lambda uri, missing_ok=True, recursive=False: calls["remove"].append((uri, missing_ok, recursive)) or {"uri": uri},
    )
    monkeypatch.setattr(
        openviking_sync,
        "openviking_add_resource",
        lambda **kwargs: calls["add"].append(kwargs) or {"root_uri": kwargs.get("to")},
    )

    upload = DailyUpload(
        id=42,
        date=date(2026, 3, 16),
        raw_content="Cardiology lecture notes about acute heart failure and pulmonary edema.",
        ai_extracted={"book": "Internal Medicine", "chapter_title": "Heart Failure"},
    )

    upsert_operation = openviking_sync.build_sync_operation(upload, action="upsert")
    assert upsert_operation is not None

    upsert_counts = openviking_sync.process_sync_operations([upsert_operation])
    assert upsert_counts == {"upserted": 1, "deleted": 0, "failed": 0}
    assert upsert_operation.export_path.exists()
    assert "Heart Failure" in upsert_operation.export_path.read_text(encoding="utf-8")
    assert calls["mkdir"] == [
        "viking://resources/test-sync",
        "viking://resources/test-sync/daily_uploads",
    ]
    assert calls["remove"][0] == (upsert_operation.resource_uri, True, True)
    assert calls["add"][0]["to"] == upsert_operation.resource_uri

    delete_operation = openviking_sync.build_sync_operation(upload, action="delete")
    assert delete_operation is not None

    delete_counts = openviking_sync.process_sync_operations([delete_operation])
    assert delete_counts == {"upserted": 0, "deleted": 1, "failed": 0}
    assert not upsert_operation.export_path.exists()
    assert calls["remove"][-1] == (delete_operation.resource_uri, True, True)


def test_bulk_import_openviking_exports_uploads_per_table_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENVIKING_SYNC_ENABLED", "true")
    monkeypatch.setenv("OPENVIKING_ENABLED", "true")
    monkeypatch.setenv("OPENVIKING_URL", "http://localhost:1933")
    monkeypatch.setenv("OPENVIKING_SYNC_EXPORT_DIR", str(tmp_path))
    monkeypatch.setenv("OPENVIKING_SYNC_ROOT_URI", "viking://resources/test-sync")

    daily_dir = tmp_path / "daily_uploads"
    question_dir = tmp_path / "question_records"
    daily_dir.mkdir(parents=True)
    question_dir.mkdir(parents=True)
    (daily_dir / "id=1.md").write_text("# DailyUpload\n\nhello", encoding="utf-8")
    (question_dir / "id=1.md").write_text("# QuestionRecord\n\nworld", encoding="utf-8")
    (question_dir / "id=2.md").write_text("# QuestionRecord\n\nworld2", encoding="utf-8")

    openviking_sync._ENSURED_REMOTE_DIRS.clear()

    mkdir_calls: list[str] = []
    remove_calls: list[tuple[str, bool, bool]] = []
    add_calls: list[dict] = []

    monkeypatch.setattr(
        openviking_sync,
        "openviking_stat",
        lambda uri, missing_ok=False: {"uri": uri} if uri == "viking://resources/test-sync" else None,
    )
    monkeypatch.setattr(
        openviking_sync,
        "openviking_mkdir",
        lambda uri: mkdir_calls.append(uri) or {"uri": uri},
    )
    monkeypatch.setattr(
        openviking_sync,
        "openviking_remove_uri",
        lambda uri, missing_ok=True, recursive=False: remove_calls.append((uri, missing_ok, recursive)) or {"uri": uri},
    )
    monkeypatch.setattr(
        openviking_sync,
        "openviking_add_resource",
        lambda **kwargs: add_calls.append(kwargs) or {"root_uri": kwargs.get("to")},
    )

    counts = openviking_sync.bulk_import_openviking_exports(model_names=["DailyUpload", "QuestionRecord"])

    assert counts == {"DailyUpload": 1, "QuestionRecord": 2}
    assert mkdir_calls == []
    assert remove_calls == [
        ("viking://resources/test-sync/daily_uploads", True, True),
        ("viking://resources/test-sync/question_records", True, True),
    ]
    assert add_calls[0]["path"] == str(daily_dir)
    assert add_calls[0]["to"] == "viking://resources/test-sync/daily_uploads"
    assert add_calls[1]["path"] == str(question_dir)
    assert add_calls[1]["to"] == "viking://resources/test-sync/question_records"
