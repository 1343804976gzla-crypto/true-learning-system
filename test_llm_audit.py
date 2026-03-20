from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.ai_client import AIClient
from services.llm_audit import (
    create_llm_call_context,
    iter_llm_audit_events,
    reset_llm_audit_request_context,
    set_llm_audit_request_context,
    summarize_llm_audit_events,
)


class _DummyCompletions:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        return self._response


class _DummyChat:
    def __init__(self, response):
        self.completions = _DummyCompletions(response)


class _DummyClient:
    def __init__(self, response):
        self.chat = _DummyChat(response)
        self.base_url = "https://unit.test/v1"


@pytest.mark.asyncio
async def test_llm_audit_records_successful_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_AUDIT_ENABLED", "true")
    monkeypatch.setenv("LLM_AUDIT_LOG_PATH", str(tmp_path / "llm_audit.jsonl"))

    response = SimpleNamespace(
        id="resp_unit_1",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="audit-ok"),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=12,
            completion_tokens=8,
            total_tokens=20,
        ),
    )
    dummy_client = _DummyClient(response)
    ai = AIClient()
    messages = [{"role": "user", "content": "hello audit"}]
    call_context = create_llm_call_context(
        call_kind="content",
        messages=messages,
        max_tokens=128,
        temperature=0.2,
        timeout=30,
        use_heavy=True,
        preferred_provider=None,
        preferred_model=None,
        operation="services.quiz_service_v2:_generate_single_paper",
        phase="json_prompt_1",
    )
    token = set_llm_audit_request_context(
        request_id="req_test_1",
        method="POST",
        path="/api/quiz/batch/submit/exam-1",
        query="",
        referer="http://localhost:8000/quiz/batch/0",
        user_agent="pytest",
        user_id="user-1",
        device_id="device-1",
    )

    try:
        text = await ai._call_model_with_audit(
            client=dummy_client,
            model="demo-model",
            provider_name="demo/demo-model",
            messages=messages,
            max_tokens=128,
            temperature=0.2,
            timeout=30,
            pool_name="Heavy",
            pool_index=1,
            pool_size=2,
            audit_context=call_context,
        )
    finally:
        reset_llm_audit_request_context(token)

    assert text == "audit-ok"
    events = list(iter_llm_audit_events(path=tmp_path / "llm_audit.jsonl"))
    assert len(events) == 1
    event = events[0]
    assert event["request_id"] == "req_test_1"
    assert event["http_path"] == "/api/quiz/batch/submit/exam-1"
    assert event["referer"] == "http://localhost:8000/quiz/batch/0"
    assert event["provider"] == "demo"
    assert event["provider_display"] == "demo/demo-model"
    assert event["total_tokens"] == 20
    assert event["prompt_tokens"] == 12
    assert event["completion_tokens"] == 8
    assert event["logical_call_id"] == call_context["logical_call_id"]
    assert event["operation"] == "services.quiz_service_v2:_generate_single_paper"


def test_llm_audit_summary_groups_requests():
    events = [
        {
            "request_id": "req-a",
            "http_method": "POST",
            "http_path": "/api/quiz/batch/submit/exam-a",
            "referer": "http://localhost:8000/quiz/batch/0",
            "provider": "openrouter",
            "model": "google/gemini-2.5-pro",
            "provider_display": "openrouter/google/gemini-2.5-pro",
            "operation": "services.quiz_service_v2:_generate_single_paper",
            "status": "success",
            "total_tokens": 300,
            "created_at": "2026-03-19T22:00:00",
        },
        {
            "request_id": "req-a",
            "http_method": "POST",
            "http_path": "/api/quiz/batch/submit/exam-a",
            "referer": "http://localhost:8000/quiz/batch/0",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "provider_display": "deepseek/deepseek-chat",
            "operation": "services.quiz_service_v2:_generate_single_paper",
            "status": "error",
            "total_tokens": 0,
            "created_at": "2026-03-19T22:00:05",
        },
        {
            "request_id": "req-b",
            "http_method": "POST",
            "http_path": "/api/agent/chat",
            "referer": "http://localhost:8000/agent",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "provider_display": "deepseek/deepseek-chat",
            "operation": "services.agent_runtime:chat",
            "status": "success",
            "total_tokens": 120,
            "created_at": "2026-03-19T22:01:00",
        },
    ]

    summary = summarize_llm_audit_events(events)

    assert summary["total_events"] == 3
    assert summary["requests"][0]["request_id"] == "req-b"
    req_a = next(item for item in summary["requests"] if item["request_id"] == "req-a")
    assert req_a["events"] == 2
    assert req_a["errors"] == 1
    assert req_a["total_tokens"] == 300
    provider_top = summary["providers"][0]
    assert provider_top["provider"] == "openrouter"
    assert provider_top["total_tokens"] == 300
