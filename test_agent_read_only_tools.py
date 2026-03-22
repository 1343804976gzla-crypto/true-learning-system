from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.agent_actions import (
    AgentWriteActionsDisabledError,
    are_agent_write_actions_enabled,
    execute_agent_action,
    list_action_tool_definitions,
)
from services.agent_runtime import _build_action_suggestions
from services.agent_tools import list_available_agent_tools
from utils.agent_contracts import AgentActionExecuteRequest


def test_agent_write_actions_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_WRITE_ACTIONS_ENABLED", raising=False)

    assert are_agent_write_actions_enabled() is False
    assert list_action_tool_definitions() == []

    tools = list_available_agent_tools()
    assert tools
    assert all(tool.tool_type == "read" for tool in tools)


def test_agent_write_actions_can_be_reenabled(monkeypatch):
    monkeypatch.setenv("AGENT_WRITE_ACTIONS_ENABLED", "true")

    tools = list_action_tool_definitions()

    assert tools
    assert all(tool.tool_type == "write" for tool in tools)


def test_agent_runtime_skips_action_suggestions_when_write_actions_disabled(monkeypatch):
    monkeypatch.delenv("AGENT_WRITE_ACTIONS_ENABLED", raising=False)

    suggestions = _build_action_suggestions(
        request_analysis={"output_mode": "plan"},
        tool_results={
            "get_wrong_answers": {
                "items": [
                    {"id": 1, "mastery_status": "active", "last_retry_correct": True},
                ]
            },
            "get_knowledge_mastery": {
                "weak_concepts": [
                    {"concept_id": "concept-1"},
                ]
            },
        },
    )

    assert suggestions == []


def test_execute_agent_action_rejected_when_write_actions_disabled(monkeypatch):
    monkeypatch.delenv("AGENT_WRITE_ACTIONS_ENABLED", raising=False)

    payload = AgentActionExecuteRequest(
        session_id="session-1",
        tool_name="log_agent_decision",
        tool_args={
            "decision_type": "plan",
            "summary": "record the planning decision",
        },
    )

    with pytest.raises(AgentWriteActionsDisabledError, match="只允许只读工具"):
        execute_agent_action(
            db=None,  # type: ignore[arg-type]
            session=SimpleNamespace(id="session-1"),
            payload=payload,
        )
