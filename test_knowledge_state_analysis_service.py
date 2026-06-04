from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.domains import ContentBase, CoreBase, ReviewBase, RuntimeBase
from learning_tracking_models import KnowledgeStateEvent, KnowledgeStateProfile


def test_knowledge_state_models_create_runtime_tables():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for metadata in (CoreBase.metadata, ContentBase.metadata, RuntimeBase.metadata, ReviewBase.metadata):
        metadata.create_all(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    try:
        db = Session()
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
        db.close()
        for metadata in (ReviewBase.metadata, RuntimeBase.metadata, ContentBase.metadata, CoreBase.metadata):
            metadata.drop_all(engine)
        engine.dispose()
