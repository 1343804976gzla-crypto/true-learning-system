from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient

from main import app


class _FakeAIClient:
    async def generate_content(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        timeout: int,
        use_heavy: bool,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
    ) -> str:
        assert "search_openviking_context" in prompt
        assert "viking://resources/docs/heart-failure.md" in prompt
        return "I used OpenViking context to answer this request."

    async def generate_content_stream(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        timeout: int,
        use_heavy: bool,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
    ):
        yield "I used "
        yield "OpenViking "
        yield "context."


def test_resolve_requested_tools_only_enables_openviking_when_configured(monkeypatch):
    from services import agent_tools

    message = "Please search the OpenViking docs for this topic."

    monkeypatch.setattr(agent_tools, "is_openviking_enabled", lambda: False)
    disabled_tools = agent_tools.resolve_requested_tools(message, None)
    assert "search_openviking_context" not in disabled_tools

    monkeypatch.setattr(agent_tools, "is_openviking_enabled", lambda: True)
    enabled_tools = agent_tools.resolve_requested_tools(message, None)
    assert "search_openviking_context" in enabled_tools


def test_execute_agent_tool_returns_openviking_payload(monkeypatch):
    from models import SessionLocal, init_db
    from services import agent_tools

    init_db()

    async def _fake_search_openviking_context(*, query: str, target_uri: str = "", limit: int | None = None):
        assert query == "heart failure notes"
        assert target_uri == "viking://resources/notes"
        assert limit == 3
        return {
            "status": "ok",
            "enabled": True,
            "query": query,
            "target_uri": target_uri,
            "count": 1,
            "total": 1,
            "items": [
                {
                    "context_type": "resource",
                    "uri": "viking://resources/notes/heart-failure.md",
                    "level": 2,
                    "score": 0.93,
                    "category": "docs",
                    "match_reason": "heart failure notes",
                    "abstract": "key points summary",
                    "overview": "",
                    "relations": [],
                }
            ],
            "memories": [],
            "resources": [],
            "skills": [],
            "query_plan": {},
            "error": None,
        }

    monkeypatch.setattr(agent_tools, "search_openviking_context", _fake_search_openviking_context)

    with SessionLocal() as db:
        tool_args, payload, duration_ms = asyncio.run(
            agent_tools.execute_agent_tool(
                "search_openviking_context",
                db,
                {
                    "query": "heart failure notes",
                    "target_uri": "viking://resources/notes",
                    "limit": 3,
                },
                device_id=f"openviking-tool-{uuid4().hex}",
            )
        )

    assert tool_args["query"] == "heart failure notes"
    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert payload["items"][0]["uri"].endswith("heart-failure.md")
    assert duration_ms >= 0


def test_agent_chat_uses_openviking_tool_and_source_card(monkeypatch):
    from services import agent_runtime, agent_tools

    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: _FakeAIClient())

    async def _fake_search_openviking_context(*, query: str, target_uri: str = "", limit: int | None = None):
        return {
            "status": "ok",
            "enabled": True,
            "query": query,
            "target_uri": target_uri,
            "count": 2,
            "total": 2,
            "items": [
                {
                    "context_type": "resource",
                    "uri": "viking://resources/docs/heart-failure.md",
                    "level": 2,
                    "score": 0.97,
                    "category": "docs",
                    "match_reason": "heart failure guideline",
                    "abstract": "acute heart failure treatment and review points",
                    "overview": "",
                    "relations": [],
                },
                {
                    "context_type": "memory",
                    "uri": "viking://memories/review/heart-failure",
                    "level": 1,
                    "score": 0.89,
                    "category": "memory",
                    "match_reason": "past study summary",
                    "abstract": "last review missed diuretics and hemodynamics",
                    "overview": "",
                    "relations": [],
                },
            ],
            "memories": [
                {
                    "context_type": "memory",
                    "uri": "viking://memories/review/heart-failure",
                    "level": 1,
                    "score": 0.89,
                    "category": "memory",
                    "match_reason": "past study summary",
                    "abstract": "last review missed diuretics and hemodynamics",
                    "overview": "",
                    "relations": [],
                }
            ],
            "resources": [
                {
                    "context_type": "resource",
                    "uri": "viking://resources/docs/heart-failure.md",
                    "level": 2,
                    "score": 0.97,
                    "category": "docs",
                    "match_reason": "heart failure guideline",
                    "abstract": "acute heart failure treatment and review points",
                    "overview": "",
                    "relations": [],
                }
            ],
            "skills": [],
            "query_plan": {},
            "error": None,
        }

    monkeypatch.setattr(agent_tools, "search_openviking_context", _fake_search_openviking_context)

    client = TestClient(app)
    message = "Search OpenViking docs for the heart failure key points."
    response = client.post(
        "/api/agent/chat",
        json={
            "device_id": f"agent-openviking-{uuid4().hex}",
            "message": message,
            "agent_type": "tutor",
            "requested_tools": ["search_openviking_context"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_calls"][0]["tool_name"] == "search_openviking_context"
    assert payload["tool_calls"][0]["tool_args"]["query"] == message

    structured = payload["assistant_message"]["content_structured"]
    assert "search_openviking_context" in structured["selected_tools"]
    assert any(focus["id"] == "external_context_search" for focus in structured["request_analysis"]["focuses"])
    assert structured["sources"][0]["tool_name"] == "search_openviking_context"
    assert structured["sources"][0]["count"] == 2
    assert "OpenViking" in structured["sources"][0]["title"]
