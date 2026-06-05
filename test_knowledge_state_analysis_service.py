from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta

from sqlalchemy import UniqueConstraint, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_contracts import KnowledgeStateAnalyzeResponse
from database.domains import ContentBase, CoreBase, ReviewBase, RuntimeBase
from learning_tracking_models import (
    KnowledgeStateEvent,
    KnowledgeStateProfile,
    LearningSession,
    QuestionRecord,
    SessionStatus,
    WrongAnswerV2,
)
from services.knowledge_state_analysis import ApiHubKnowledgeStateLlm, analyze_completed_session


class FakeKnowledgeStateLLM:
    def __init__(self, payload):
        self.payload = payload

    def analyze_knowledge_state(self, evidence_packet):
        return self.payload


def _make_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for metadata in (CoreBase.metadata, ContentBase.metadata, RuntimeBase.metadata, ReviewBase.metadata):
        metadata.create_all(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return Session(), engine


def _close_db(db, engine) -> None:
    db.close()
    for metadata in (ReviewBase.metadata, RuntimeBase.metadata, ContentBase.metadata, CoreBase.metadata):
        metadata.drop_all(engine)
    engine.dispose()


def test_knowledge_state_models_create_runtime_tables():
    db, engine = _make_db()
    try:
        profile = KnowledgeStateProfile(
            scope_key="scope-1",
            user_id="user-1",
            device_id="device-1",
            knowledge_point="Cardiac output regulation",
            current_state="Conscious Weakness",
            state_confidence="medium",
            stability_score=0.42,
            calibration_score=0.35,
            last_transition="first_observed",
            last_session_id="session-1",
            evidence_snapshot={"attempt_count": 3},
        )
        db.add(profile)
        db.flush()
        event = KnowledgeStateEvent(
            profile_id=profile.id,
            scope_key="scope-1",
            session_id="session-1",
            knowledge_point="Cardiac output regulation",
            previous_state=None,
            current_state="Conscious Weakness",
            transition="first_observed",
            state_confidence="medium",
            evidence_packet={"knowledge_points": []},
            llm_analysis={"fallback_used": True},
            guardrail_flags=["confidence_missing"],
        )
        db.add(event)
        db.commit()

        assert db.query(KnowledgeStateProfile).count() == 1
        assert db.query(KnowledgeStateEvent).count() == 1
        assert profile.events[0].transition == "first_observed"
    finally:
        _close_db(db, engine)


def test_knowledge_state_event_model_has_session_point_unique_constraint():
    constraint = next(
        (
            item
            for item in KnowledgeStateEvent.__table__.constraints
            if isinstance(item, UniqueConstraint)
            and item.name == "uq_knowledge_state_events_scope_session_point"
        ),
        None,
    )

    assert constraint is not None
    assert [column.name for column in constraint.columns] == [
        "scope_key",
        "session_id",
        "knowledge_point",
    ]


def test_analyze_completed_session_detects_illusion_of_competence_and_persists_event():
    db, engine = _make_db()
    try:
        session = LearningSession(
            id="session-illusion",
            user_id="u1",
            device_id="d1",
            session_type="detail_practice",
            status=SessionStatus.COMPLETED,
            started_at=datetime.now() - timedelta(minutes=10),
            completed_at=datetime.now(),
            total_questions=1,
            answered_questions=1,
            correct_count=0,
            wrong_count=1,
            accuracy=0.0,
        )
        db.add(session)
        db.add(
            QuestionRecord(
                user_id="u1",
                device_id="d1",
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="basic",
                question_text="Which ion drives resting membrane potential?",
                options={"A": "K+", "B": "Na+"},
                correct_answer="A",
                user_answer="B",
                is_correct=False,
                confidence="sure",
                key_point="Resting potential mechanism",
                answered_at=datetime.now(),
                time_spent_seconds=12,
            )
        )
        db.add(
            WrongAnswerV2(
                user_id="u1",
                device_id="d1",
                scope_key="u:u1",
                question_fingerprint="fp-1",
                question_text="Which ion drives resting membrane potential?",
                options={"A": "K+", "B": "Na+"},
                correct_answer="A",
                key_point="Resting potential mechanism",
                error_count=2,
                encounter_count=2,
                retry_count=0,
                severity_tag="critical",
                mastery_status="active",
            )
        )
        db.commit()

        result = analyze_completed_session(db, "session-illusion", scope_key="u:u1", llm_client=None)

        assert result["fallback_used"] is True
        assert result["cards"][0]["knowledge_point"] == "Resting potential mechanism"
        assert result["cards"][0]["current_state"] == "Illusion of Competence"
        assert result["cards"][0]["transition"] == "first_observed"
        assert result["cards"][0]["state_confidence"] == "medium"
        assert db.query(KnowledgeStateProfile).one().current_state == "Illusion of Competence"
        assert db.query(KnowledgeStateEvent).one().guardrail_flags == ["sure_wrong"]
    finally:
        _close_db(db, engine)


def test_analyze_completed_session_detects_calibration_improved_transition():
    db, engine = _make_db()
    try:
        db.add(
            KnowledgeStateProfile(
                scope_key="u:u1",
                user_id="u1",
                device_id="d1",
                knowledge_point="Afterload compensation",
                current_state="Illusion of Competence",
                state_confidence="medium",
                last_transition="first_observed",
                last_session_id="old-session",
            )
        )
        session = LearningSession(
            id="session-calibrated",
            user_id="u1",
            device_id="d1",
            session_type="detail_practice",
            status=SessionStatus.COMPLETED,
            started_at=datetime.now() - timedelta(minutes=10),
            completed_at=datetime.now(),
            total_questions=1,
            answered_questions=1,
            correct_count=0,
            wrong_count=1,
            accuracy=0.0,
        )
        db.add(session)
        db.add(
            QuestionRecord(
                user_id="u1",
                device_id="d1",
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="basic",
                question_text="Afterload rises. What happens first?",
                options={"A": "Shortening velocity falls", "B": "Velocity rises"},
                correct_answer="A",
                user_answer="B",
                is_correct=False,
                confidence="unsure",
                key_point="Afterload compensation",
                answered_at=datetime.now(),
            )
        )
        db.commit()

        result = analyze_completed_session(db, "session-calibrated", scope_key="u:u1", llm_client=None)

        assert result["cards"][0]["previous_state"] == "Illusion of Competence"
        assert result["cards"][0]["current_state"] == "Conscious Weakness"
        assert result["cards"][0]["transition"] == "calibration_improved"
        assert "自信判断" in result["cards"][0]["interesting_insight"]
    finally:
        _close_db(db, engine)


def test_analyze_completed_session_keeps_missing_confidence_state_when_llm_claims_mastery():
    db, engine = _make_db()
    try:
        session = LearningSession(
            id="session-missing-confidence",
            user_id="u1",
            device_id="d1",
            session_type="detail_practice",
            status=SessionStatus.COMPLETED,
            started_at=datetime.now() - timedelta(minutes=10),
            completed_at=datetime.now(),
            total_questions=1,
            answered_questions=1,
            correct_count=1,
            wrong_count=0,
            accuracy=1.0,
        )
        db.add(session)
        db.add(
            QuestionRecord(
                user_id="u1",
                device_id="d1",
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="basic",
                question_text="What is preload?",
                options={"A": "End diastolic stretch", "B": "Pressure after ejection"},
                correct_answer="A",
                user_answer="A",
                is_correct=True,
                confidence=None,
                key_point="Preload definition",
                answered_at=datetime.now(),
            )
        )
        db.commit()

        llm_client = FakeKnowledgeStateLLM(
            {
                "popup_title": "Model title",
                "overall_summary": "Model summary",
                "overall_trend": "observed",
                "cards": [
                    {
                        "knowledge_point": "Preload definition",
                        "current_state": "True Mastery",
                    }
                ],
            }
        )

        result = analyze_completed_session(
            db,
            "session-missing-confidence",
            scope_key="u:u1",
            llm_client=llm_client,
        )

        assert result["cards"][0]["current_state"] == "Lucky / Underconfident Correct"
        assert result["cards"][0]["state_confidence"] == "low"
        assert "confidence_missing" in result["cards"][0]["guardrail_flags"]
        assert result["low_reliability_notes"]
        assert db.query(KnowledgeStateProfile).one().current_state == "Lucky / Underconfident Correct"
    finally:
        _close_db(db, engine)


def test_analyze_completed_session_uses_storage_scope_key_for_implicit_wrong_memory(monkeypatch):
    monkeypatch.setenv("SINGLE_USER_MODE", "false")
    db, engine = _make_db()
    try:
        session = LearningSession(
            id="session-implicit-scope",
            user_id="u1",
            device_id="d1",
            session_type="detail_practice",
            status=SessionStatus.COMPLETED,
            started_at=datetime.now() - timedelta(minutes=10),
            completed_at=datetime.now(),
            total_questions=1,
            answered_questions=1,
            correct_count=0,
            wrong_count=1,
            accuracy=0.0,
        )
        db.add(session)
        db.add(
            QuestionRecord(
                user_id="u1",
                device_id="d1",
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="basic",
                question_text="Which receptor mediates the reflex?",
                options={"A": "Baroreceptor", "B": "Chemoreceptor"},
                correct_answer="A",
                user_answer="B",
                is_correct=False,
                confidence="unsure",
                key_point="Baroreflex receptor",
                answered_at=datetime.now(),
            )
        )
        db.add(
            WrongAnswerV2(
                user_id="u1",
                device_id="d1",
                scope_key="user:u1",
                question_fingerprint="fp-implicit-scope",
                question_text="Which receptor mediates the reflex?",
                options={"A": "Baroreceptor", "B": "Chemoreceptor"},
                correct_answer="A",
                key_point="Baroreflex receptor",
                error_count=3,
                encounter_count=3,
                retry_count=0,
                severity_tag="stubborn",
                mastery_status="active",
            )
        )
        db.commit()

        llm_client = FakeKnowledgeStateLLM(
            {
                "cards": [
                    {
                        "knowledge_point": "Baroreflex receptor",
                        "current_state": "True Mastery",
                    }
                ]
            }
        )

        result = analyze_completed_session(db, "session-implicit-scope", llm_client=llm_client)

        assert result["cards"][0]["current_state"] == "Stubborn Error"
        assert "stubborn_memory" in result["cards"][0]["guardrail_flags"]
        assert db.query(KnowledgeStateProfile).one().scope_key == "user:u1"
    finally:
        _close_db(db, engine)


def test_analyze_completed_session_keeps_deterministic_state_when_llm_claims_weaker_state():
    db, engine = _make_db()
    try:
        db.add(
            KnowledgeStateProfile(
                scope_key="u:u1",
                user_id="u1",
                device_id="d1",
                knowledge_point="Cardiac output regulation",
                current_state="True Mastery",
                state_confidence="medium",
                last_transition="mastery_stabilized",
                last_session_id="old-session",
            )
        )
        session = LearningSession(
            id="session-llm-override",
            user_id="u1",
            device_id="d1",
            session_type="detail_practice",
            status=SessionStatus.COMPLETED,
            started_at=datetime.now() - timedelta(minutes=10),
            completed_at=datetime.now(),
            total_questions=2,
            answered_questions=2,
            correct_count=2,
            wrong_count=0,
            accuracy=1.0,
        )
        db.add(session)
        for index in range(2):
            db.add(
                QuestionRecord(
                    user_id="u1",
                    device_id="d1",
                    session_id=session.id,
                    question_index=index,
                    question_type="A1",
                    difficulty="basic",
                    question_text=f"Cardiac output regulation question {index}",
                    options={"A": "Correct", "B": "Wrong"},
                    correct_answer="A",
                    user_answer="A",
                    is_correct=True,
                    confidence="sure",
                    key_point="Cardiac output regulation",
                    answered_at=datetime.now() + timedelta(seconds=index),
                )
            )
        db.commit()
        llm_client = FakeKnowledgeStateLLM(
            {
                "cards": [
                    {
                        "knowledge_point": "Cardiac output regulation",
                        "current_state": "Conscious Weakness",
                        "interesting_insight": "Model narrative insight",
                        "evidence_summary": [{"bad": "dict"}, "valid"],
                    }
                ]
            }
        )

        result = analyze_completed_session(db, "session-llm-override", scope_key="u:u1", llm_client=llm_client)

        card = result["cards"][0]
        event = db.query(KnowledgeStateEvent).one()
        assert card["current_state"] == "True Mastery"
        assert card["transition"] == "state_stable"
        assert card["state_confidence"] == "low"
        assert card["next_action"]["type"] == "advance"
        assert card["interesting_insight"] == "Model narrative insight"
        assert card["evidence_summary"] == ["valid"]
        assert all(isinstance(item, str) for item in card["evidence_summary"])
        assert not any(isinstance(item, dict) for item in card["evidence_summary"])
        assert event.current_state == "True Mastery"
        assert event.transition == "state_stable"
    finally:
        _close_db(db, engine)


def test_analyze_completed_session_preserves_sure_wrong_guardrail_for_llm_mastery_override():
    db, engine = _make_db()
    try:
        session = LearningSession(
            id="session-llm-sure-wrong",
            user_id="u1",
            device_id="d1",
            session_type="detail_practice",
            status=SessionStatus.COMPLETED,
            started_at=datetime.now() - timedelta(minutes=10),
            completed_at=datetime.now(),
            total_questions=1,
            answered_questions=1,
            correct_count=0,
            wrong_count=1,
            accuracy=0.0,
        )
        db.add(session)
        db.add(
            QuestionRecord(
                user_id="u1",
                device_id="d1",
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="basic",
                question_text="Which ion drives resting membrane potential?",
                options={"A": "K+", "B": "Na+"},
                correct_answer="A",
                user_answer="B",
                is_correct=False,
                confidence="sure",
                key_point="Resting potential mechanism",
                answered_at=datetime.now(),
            )
        )
        db.commit()
        llm_client = FakeKnowledgeStateLLM(
            {
                "popup_title": "Model narrative title",
                "overall_summary": "Model summary",
                "overall_trend": "model_trend",
                "cards": [
                    {
                        "knowledge_point": "Resting potential mechanism",
                        "current_state": "True Mastery",
                    }
                ],
            }
        )

        result = analyze_completed_session(db, "session-llm-sure-wrong", scope_key="u:u1", llm_client=llm_client)

        assert result["fallback_used"] is False
        assert result["popup_title"] == "Model narrative title"
        assert result["cards"][0]["current_state"] == "Illusion of Competence"
        assert db.query(KnowledgeStateProfile).one().current_state == "Illusion of Competence"
    finally:
        _close_db(db, engine)


def test_analyze_completed_session_rejects_malformed_llm_next_action_for_unchanged_state():
    db, engine = _make_db()
    try:
        session = LearningSession(
            id="session-llm-malformed-action",
            user_id="u1",
            device_id="d1",
            session_type="detail_practice",
            status=SessionStatus.COMPLETED,
            started_at=datetime.now() - timedelta(minutes=10),
            completed_at=datetime.now(),
            total_questions=2,
            answered_questions=2,
            correct_count=2,
            wrong_count=0,
            accuracy=1.0,
        )
        db.add(session)
        for index in range(2):
            db.add(
                QuestionRecord(
                    user_id="u1",
                    device_id="d1",
                    session_id=session.id,
                    question_index=index,
                    question_type="A1",
                    difficulty="basic",
                    question_text=f"Preload definition question {index}",
                    options={"A": "Correct", "B": "Wrong"},
                    correct_answer="A",
                    user_answer="A",
                    is_correct=True,
                    confidence="sure",
                    key_point="Preload definition",
                    answered_at=datetime.now() + timedelta(seconds=index),
                )
            )
        db.commit()
        llm_client = FakeKnowledgeStateLLM(
            {
                "popup_title": "Model title",
                "overall_summary": "Model summary",
                "overall_trend": "observed",
                "cards": [
                    {
                        "knowledge_point": "Preload definition",
                        "current_state": "True Mastery",
                        "next_action": {"type": "custom"},
                    }
                ],
            }
        )

        result = analyze_completed_session(db, "session-llm-malformed-action", scope_key="u:u1", llm_client=llm_client)

        action = result["cards"][0]["next_action"]
        assert action["type"] == "advance"
        assert action["label"]
        KnowledgeStateAnalyzeResponse(**result)
    finally:
        _close_db(db, engine)


def test_api_hub_knowledge_state_llm_calls_generate_json_with_production_options(monkeypatch):
    calls = []

    class FakeAiClient:
        def generate_json(self, prompt, schema, **kwargs):
            calls.append({"prompt": prompt, "schema": schema, "kwargs": kwargs})
            return {"popup_title": "模型标题", "overall_summary": "摘要", "overall_trend": "observed", "cards": []}

    facade_module = types.ModuleType("services.api_hub.facade")
    facade_module.get_ai_client = lambda: FakeAiClient()
    api_hub_module = types.ModuleType("services.api_hub")
    monkeypatch.setitem(sys.modules, "services.api_hub", api_hub_module)
    monkeypatch.setitem(sys.modules, "services.api_hub.facade", facade_module)

    result = ApiHubKnowledgeStateLlm().analyze_knowledge_state({"knowledge_points": []})

    assert result == {"popup_title": "模型标题", "overall_summary": "摘要", "overall_trend": "observed", "cards": []}
    assert calls[0]["kwargs"]["timeout"] == 45
    assert calls[0]["kwargs"]["use_heavy"] is False
    assert calls[0]["kwargs"]["max_tokens"] == 2200
    assert calls[0]["kwargs"]["temperature"] == 0.35
    assert "只输出 JSON" in calls[0]["prompt"]
    assert set(calls[0]["schema"]["required"]) >= {"popup_title", "overall_summary", "overall_trend", "cards"}


def test_api_hub_knowledge_state_llm_resolves_async_generate_json(monkeypatch):
    class FakeAiClient:
        async def generate_json(self, prompt, schema, **kwargs):
            return {"popup_title": "异步标题", "overall_summary": "摘要", "overall_trend": "observed", "cards": []}

    facade_module = types.ModuleType("services.api_hub.facade")
    facade_module.get_ai_client = lambda: FakeAiClient()
    api_hub_module = types.ModuleType("services.api_hub")
    monkeypatch.setitem(sys.modules, "services.api_hub", api_hub_module)
    monkeypatch.setitem(sys.modules, "services.api_hub.facade", facade_module)

    result = ApiHubKnowledgeStateLlm().analyze_knowledge_state({"knowledge_points": []})

    assert result == {"popup_title": "异步标题", "overall_summary": "摘要", "overall_trend": "observed", "cards": []}


def test_analyze_completed_session_is_idempotent_for_same_scope_session_and_point():
    db, engine = _make_db()
    try:
        session = LearningSession(
            id="session-idempotent",
            user_id="u1",
            device_id="d1",
            session_type="detail_practice",
            status=SessionStatus.COMPLETED,
            started_at=datetime.now() - timedelta(minutes=10),
            completed_at=datetime.now(),
            total_questions=1,
            answered_questions=1,
            correct_count=0,
            wrong_count=1,
            accuracy=0.0,
        )
        db.add(session)
        db.add(
            QuestionRecord(
                user_id="u1",
                device_id="d1",
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="basic",
                question_text="Which ion drives resting membrane potential?",
                options={"A": "K+", "B": "Na+"},
                correct_answer="A",
                user_answer="B",
                is_correct=False,
                confidence="sure",
                key_point="Resting potential mechanism",
                answered_at=datetime.now(),
            )
        )
        db.commit()

        first = analyze_completed_session(db, "session-idempotent", scope_key="u:u1", llm_client=None)
        second = analyze_completed_session(db, "session-idempotent", scope_key="u:u1", llm_client=None)

        assert db.query(KnowledgeStateEvent).count() == 1
        assert first["cards"][0]["transition"] == "first_observed"
        assert second["cards"][0]["transition"] == "first_observed"
        assert db.query(KnowledgeStateProfile).one().last_transition == "first_observed"
    finally:
        _close_db(db, engine)


def test_analyze_completed_session_uses_answered_record_over_null_answered_placeholder():
    db, engine = _make_db()
    try:
        session = LearningSession(
            id="session-null-ordering",
            user_id="u1",
            device_id="d1",
            session_type="detail_practice",
            status=SessionStatus.COMPLETED,
            started_at=datetime.now() - timedelta(minutes=10),
            completed_at=datetime.now(),
            total_questions=1,
            answered_questions=1,
            correct_count=1,
            wrong_count=0,
            accuracy=1.0,
        )
        db.add(session)
        db.add(
            QuestionRecord(
                user_id="u1",
                device_id="d1",
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="basic",
                question_text="What is preload?",
                options={"A": "End diastolic stretch", "B": "Pressure after ejection"},
                correct_answer="A",
                user_answer="A",
                is_correct=True,
                confidence="sure",
                key_point="Preload definition",
                answered_at=datetime.now() - timedelta(minutes=1),
            )
        )
        db.flush()
        db.add(
            QuestionRecord(
                user_id="u1",
                device_id="d1",
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="basic",
                question_text="Placeholder draft",
                options={"A": "End diastolic stretch", "B": "Pressure after ejection"},
                correct_answer="A",
                user_answer=None,
                is_correct=False,
                confidence="sure",
                key_point="Preload definition",
                answered_at=None,
            )
        )
        db.commit()

        result = analyze_completed_session(db, "session-null-ordering", scope_key="u:u1", llm_client=None)

        evidence = db.query(KnowledgeStateProfile).one().evidence_snapshot
        assert result["cards"][0]["current_state"] == "True Mastery"
        assert evidence["correct_count"] == 1
        assert evidence["wrong_count"] == 0
    finally:
        _close_db(db, engine)
