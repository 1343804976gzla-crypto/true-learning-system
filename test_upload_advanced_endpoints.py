from __future__ import annotations

from datetime import date
from types import MethodType, SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.upload as upload_module
import services.knowledge_upload_service as knowledge_upload_service_module
from database.audit import ensure_audit_tables
from knowledge_upload_models import KnowledgePendingClassification, KnowledgePointNote
from models import Chapter, SessionLocal


def _make_service():
    service = knowledge_upload_service_module.KnowledgeUploadService.__new__(
        knowledge_upload_service_module.KnowledgeUploadService
    )

    async def fake_parse_content_with_knowledge(content, db):
        text = str(content)
        if "Heart Failure" in text:
            return {
                "book": "Medicine",
                "chapter_number": "2",
                "chapter_title": "Heart Failure",
                "chapter_id": "med_ch2_hf",
                "summary": "Covers diagnosis and treatment of heart failure.",
                "concepts": [
                    {"id": "hf_def", "name": "Definition"},
                    {"id": "hf_tx", "name": "Treatment"},
                ],
            }
        return {
            "book": "Medicine",
            "chapter_number": "99",
            "chapter_title": "Mystery Topic",
            "chapter_id": "unknown_ch99",
            "summary": "Needs manual chapter resolution.",
            "concepts": [
                {"id": "mystery_flag", "name": "Escalation"},
            ],
        }

    async def fake_generate_quiz(*, concept_name, concept_description):
        return {
            "question": f"What best matches {concept_name}?",
            "options": {"A": "Correct", "B": "Distractor"},
            "correct_answer": "A",
            "explanation": f"Review {concept_name}: {concept_description[:40]}",
        }

    async def fake_extract_structured_knowledge(self, raw_text, db):
        return [
            {
                "book_hint": "Medicine",
                "chapter_number_hint": "2",
                "chapter_title_hint": "Heart Failure",
                "chapter_summary": "Heart failure core facts.",
                "source_excerpt": "Heart Failure notes excerpt.",
                "knowledge_points": [
                    {
                        "name": "Definition",
                        "summary": "What heart failure means.",
                        "note_body": "Heart failure is a syndrome caused by impaired pumping.",
                    },
                    {
                        "name": "Treatment",
                        "summary": "How to treat heart failure.",
                        "note_body": "Treatment includes oxygen, diuretics, and afterload reduction.",
                    },
                ],
            },
            {
                "book_hint": "Medicine",
                "chapter_number_hint": "99",
                "chapter_title_hint": "Mystery Topic",
                "chapter_summary": "This item should remain pending first.",
                "source_excerpt": "Pending classification excerpt.",
                "knowledge_points": [
                    {
                        "name": "Escalation",
                        "summary": "When to escalate care.",
                        "note_body": "Escalate care when shock signs persist after fluid support.",
                    }
                ],
            },
        ]

    service.ai = SimpleNamespace(_providers={})
    service.parser = SimpleNamespace(parse_content_with_knowledge=fake_parse_content_with_knowledge)
    service.quiz_service = SimpleNamespace(generate_quiz=fake_generate_quiz)
    service.preview_cache = {}
    service.practice_cache = {}
    service._extract_structured_knowledge = MethodType(fake_extract_structured_knowledge, service)
    return service


@pytest.fixture
def client(monkeypatch):
    ensure_audit_tables()
    app = FastAPI()
    app.include_router(upload_module.router)
    service = _make_service()
    monkeypatch.setattr(upload_module, "get_knowledge_upload_service", lambda: service)
    with SessionLocal() as db:
        db.add_all(
            [
                Chapter(
                    id="med_ch2_hf",
                    book="Medicine",
                    edition="1",
                    chapter_number="2",
                    chapter_title="Heart Failure",
                    content_summary="Heart failure overview.",
                    concepts=[
                        {"id": "hf_def", "name": "Definition"},
                        {"id": "hf_tx", "name": "Treatment"},
                    ],
                    first_uploaded=date(2026, 3, 1),
                ),
                Chapter(
                    id="med_ch3_shock",
                    book="Medicine",
                    edition="1",
                    chapter_number="3",
                    chapter_title="Shock",
                    content_summary="Shock overview.",
                    concepts=[
                        {"id": "shock_escalation", "name": "Escalation"},
                    ],
                    first_uploaded=date(2026, 3, 1),
                ),
            ]
        )
        db.commit()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_upload_advanced_flow_covers_preview_save_pending_resolve_and_practice(client):
    preview_response = client.post(
        "/api/upload/knowledge-preview",
        data={
            "source_mode": "text_paste",
            "source_name": "hf-notes.txt",
            "content_text": "Heart Failure notes plus one unresolved topic.",
        },
    )

    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["item_count"] == 2
    assert preview_payload["pending_count"] == 1
    assert preview_payload["items"][0]["status"] == "resolved"
    assert preview_payload["items"][0]["chapter_id"] == "med_ch2_hf"
    assert preview_payload["items"][1]["status"] == "pending"

    save_response = client.post(
        "/api/upload/knowledge-save",
        json={
            "preview_id": preview_payload["preview_id"],
            "items": preview_payload["items"],
        },
    )

    assert save_response.status_code == 200
    assert save_response.json()["created_notes"] == 2
    assert save_response.json()["updated_notes"] == 0
    assert save_response.json()["pending_items"] == 1

    workspace_response = client.get("/api/upload/workspace")
    assert workspace_response.status_code == 200
    workspace_payload = workspace_response.json()
    assert workspace_payload["stats"]["total_uploads"] == 1
    assert workspace_payload["stats"]["total_knowledge_points"] == 2
    assert workspace_payload["stats"]["pending_count"] == 1
    assert len(workspace_payload["chapters"]) == 1
    assert workspace_payload["recent_uploads"][0]["saved_note_count"] == 2

    with SessionLocal() as db:
        pending = db.query(KnowledgePendingClassification).one()

    resolve_response = client.post(
        f"/api/upload/pending/{pending.id}/resolve",
        json={"chapter_id": "med_ch3_shock"},
    )

    assert resolve_response.status_code == 200
    assert resolve_response.json()["created_notes"] == 1
    assert resolve_response.json()["updated_notes"] == 0

    refreshed_workspace = client.get("/api/upload/workspace")
    assert refreshed_workspace.status_code == 200
    refreshed_payload = refreshed_workspace.json()
    assert refreshed_payload["stats"]["total_knowledge_points"] == 3
    assert refreshed_payload["stats"]["pending_count"] == 0
    assert len(refreshed_payload["chapters"]) == 2

    with SessionLocal() as db:
        definition_note = (
            db.query(KnowledgePointNote)
            .filter(KnowledgePointNote.concept_name == "Definition")
            .one()
        )

    practice_response = client.post(f"/api/upload/knowledge-points/{definition_note.id}/practice")
    assert practice_response.status_code == 200
    practice_payload = practice_response.json()
    assert practice_payload["note_id"] == definition_note.id
    assert practice_payload["concept_name"] == "Definition"
    assert practice_payload["options"] == {"A": "Correct", "B": "Distractor"}

    grade_response = client.post(
        "/api/upload/practice/grade",
        json={
            "practice_id": practice_payload["practice_id"],
            "user_answer": "A",
        },
    )

    assert grade_response.status_code == 200
    assert grade_response.json() == {
        "practice_id": practice_payload["practice_id"],
        "is_correct": True,
        "correct_answer": "A",
        "explanation": "Review Definition: Heart failure is a syndrome caused by im",
    }


def test_daily_report_uses_saved_notes_for_report_snapshot(client):
    preview_response = client.post(
        "/api/upload/knowledge-preview",
        data={
            "source_mode": "text_paste",
            "content_text": "Heart Failure notes plus one unresolved topic.",
        },
    )
    preview_payload = preview_response.json()
    client.post(
        "/api/upload/knowledge-save",
        json={
            "preview_id": preview_payload["preview_id"],
            "items": preview_payload["items"],
        },
    )

    report_response = client.get(f"/api/upload/daily-report?target_date={date.today().isoformat()}")

    assert report_response.status_code == 200
    report_payload = report_response.json()
    assert report_payload["date"] == date.today().isoformat()
    assert report_payload["totals"] == {
        "total_uploads": 1,
        "total_knowledge_points": 2,
        "total_chapters": 1,
        "pending_count": 1,
    }
    assert len(report_payload["created_today"]) == 2
    assert len(report_payload["practice_questions"]) == 2
    assert report_payload["practice_questions"][0]["knowledge_point_id"] >= 1
