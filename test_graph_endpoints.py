from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.domains import ContentBase, CoreBase, ReviewBase, RuntimeBase
from models import Chapter, ConceptLink, ConceptMastery, get_db
import routers.graph as graph_module


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for metadata in (CoreBase.metadata, ContentBase.metadata, RuntimeBase.metadata, ReviewBase.metadata):
        metadata.create_all(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        yield Session
    finally:
        for metadata in (ReviewBase.metadata, RuntimeBase.metadata, ContentBase.metadata, CoreBase.metadata):
            metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(session_factory):
    app = FastAPI()
    app.include_router(graph_module.router)

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


def test_graph_data_returns_chapter_nodes_concept_nodes_and_filtered_custom_links(client, session_factory):
    with session_factory() as db:
        db.add(
            Chapter(
                id="med_ch1",
                book="Medicine",
                edition="1",
                chapter_number="1",
                chapter_title="Cardiology",
            )
        )
        db.add_all(
            [
                ConceptMastery(
                    concept_id="hf_def",
                    chapter_id="med_ch1",
                    name="Heart Failure Definition",
                    retention=0.6,
                    understanding=0.4,
                    application=0.0,
                ),
                ConceptMastery(
                    concept_id="hf_tx",
                    chapter_id="med_ch1",
                    name="Heart Failure Treatment",
                    retention=0.8,
                    understanding=0.9,
                    application=0.0,
                ),
            ]
        )
        db.add_all(
            [
                ConceptLink(
                    from_concept="hf_def",
                    to_concept="hf_tx",
                    link_type="leads_to",
                    strength=0.75,
                    user_created=True,
                ),
                ConceptLink(
                    from_concept="missing",
                    to_concept="hf_tx",
                    link_type="analogy",
                    strength=0.5,
                    user_created=True,
                ),
            ]
        )
        db.commit()

    response = client.get("/api/graph/Medicine")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["nodes"]) == 3
    assert len(payload["links"]) == 3

    chapter_node = next(node for node in payload["nodes"] if node["id"] == "med_ch1")
    concept_node = next(node for node in payload["nodes"] if node["id"] == "hf_tx")
    custom_link = next(link for link in payload["links"] if link["type"] == "leads_to")

    assert chapter_node == {
        "id": "med_ch1",
        "name": "Cardiology",
        "chapter": "Cardiology",
        "mastery": 0.7,
        "radius": 17.0,
    }
    assert concept_node == {
        "id": "hf_tx",
        "name": "Heart Failure Treatment",
        "chapter": "Cardiology",
        "mastery": 0.8,
        "radius": 14.0,
    }
    assert custom_link == {
        "source": "hf_def",
        "target": "hf_tx",
        "type": "leads_to",
        "strength": 0.75,
    }


def test_create_graph_link_validates_concepts_and_persists_link(client, session_factory):
    with session_factory() as db:
        db.add(
            Chapter(
                id="med_ch1",
                book="Medicine",
                edition="1",
                chapter_number="1",
                chapter_title="Cardiology",
            )
        )
        db.add_all(
            [
                ConceptMastery(
                    concept_id="hf_def",
                    chapter_id="med_ch1",
                    name="Heart Failure Definition",
                    retention=0.5,
                    understanding=0.5,
                    application=0.0,
                ),
                ConceptMastery(
                    concept_id="hf_tx",
                    chapter_id="med_ch1",
                    name="Heart Failure Treatment",
                    retention=0.5,
                    understanding=0.5,
                    application=0.0,
                ),
            ]
        )
        db.commit()

    create_response = client.post(
        "/api/graph/link",
        json={
            "from_concept": "hf_def",
            "to_concept": "hf_tx",
            "link_type": "prerequisite",
        },
    )
    missing_response = client.post(
        "/api/graph/link",
        json={
            "from_concept": "hf_def",
            "to_concept": "missing",
            "link_type": "contrast",
        },
    )

    assert create_response.status_code == 200
    assert create_response.json() == {
        "status": "created",
        "message": "连接已创建",
    }
    assert missing_response.status_code == 404
    assert missing_response.json()["detail"] == "知识点不存在"

    with session_factory() as db:
        links = db.query(ConceptLink).all()

        assert len(links) == 1
        assert links[0].from_concept == "hf_def"
        assert links[0].to_concept == "hf_tx"
        assert links[0].link_type == "prerequisite"
