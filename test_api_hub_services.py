from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.domains import RuntimeBase
from services.api_hub.cost_tracker import CostTracker
from services.api_hub.facade import AIClient
from services.api_hub.models import ApiHubPrice, ApiHubUsage
import services.api_hub.facade as facade_module
import services.api_hub.stream_handler as stream_handler_module


@pytest.fixture
def runtime_session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    RuntimeBase.metadata.create_all(
        bind=engine,
        tables=[ApiHubUsage.__table__, ApiHubPrice.__table__],
    )
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        yield Session
    finally:
        RuntimeBase.metadata.drop_all(
            bind=engine,
            tables=[ApiHubUsage.__table__, ApiHubPrice.__table__],
        )
        engine.dispose()


def test_cost_tracker_records_usage_and_reports_summary(runtime_session_factory):
    tracker = CostTracker(db_session_factory=runtime_session_factory)

    tracker.record_usage(
        provider="deepseek",
        model="deepseek-chat",
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        elapsed_ms=320,
        status="success",
        pool_name="Light",
        caller="quiz.generate",
        request_path="/api/quiz",
        logical_call_id="call-1",
    )
    tracker.record_usage(
        provider="deepseek",
        model="deepseek-chat",
        prompt_tokens=500,
        completion_tokens=250,
        total_tokens=750,
        elapsed_ms=210,
        status="success",
        pool_name="Light",
        caller="quiz.generate",
        request_path="/api/quiz",
        logical_call_id="call-2",
    )

    summary = tracker.get_summary(period="24h", group_by="provider")
    timeline = tracker.get_timeline(period="24h")

    assert tracker.calculate_cost("deepseek", "deepseek-chat", 1000, 500) == 0.0028
    assert summary == {
        "deepseek": {
            "calls": 2,
            "prompt_tokens": 1500,
            "completion_tokens": 750,
            "total_tokens": 2250,
            "total_cost": 0.0042,
            "avg_latency_ms": 265,
        }
    }
    assert tracker.get_daily_cost() == 0.0042
    assert len(timeline) == 1
    bucket_payload = next(iter(timeline.values()))
    assert bucket_payload == {
        "calls": 2,
        "total_tokens": 2250,
        "total_cost": 0.0042,
    }


def test_cost_tracker_persists_price_updates_and_can_reload(runtime_session_factory):
    tracker = CostTracker(db_session_factory=runtime_session_factory)
    tracker.update_price("openrouter", "google/gemini-2.5-pro", 0.0042, 0.0084)

    reloaded = CostTracker(db_session_factory=runtime_session_factory)
    reloaded.load_prices_from_db()

    assert reloaded.get_prices()["openrouter/google/gemini-2.5-pro"] == {
        "input": 0.0042,
        "output": 0.0084,
    }


def test_facade_usage_callback_derives_total_tokens_and_request_metadata(monkeypatch):
    captured = []
    client = AIClient.__new__(AIClient)
    client.cost_tracker = SimpleNamespace(record_usage=lambda **kwargs: captured.append(kwargs))

    monkeypatch.setattr(
        facade_module,
        "get_llm_audit_request_context",
        lambda: {"http_path": "/api/challenge/variant"},
    )

    callback = AIClient._make_usage_callback(client)
    callback(
        provider="deepseek",
        model="deepseek-chat",
        usage={"prompt_tokens": 12, "completion_tokens": 8},
        elapsed_ms=180,
        status="success",
        pool_name="Light",
        audit_context={"operation": "challenge.variant", "logical_call_id": "logical-1"},
    )

    assert captured == [
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "total_tokens": 20,
            "elapsed_ms": 180,
            "status": "success",
            "pool_name": "Light",
            "caller": "challenge.variant",
            "request_path": "/api/challenge/variant",
            "logical_call_id": "logical-1",
        }
    ]


@pytest.mark.asyncio
async def test_facade_stream_falls_back_to_non_streaming_when_pool_emits_nothing(monkeypatch):
    client = AIClient.__new__(AIClient)
    client.pools = SimpleNamespace(
        compose_pool=lambda **kwargs: ([("fake-client", "fake-model", "deepseek/deepseek-chat")], "Light")
    )
    client._health_callback = object()
    client._usage_callback = object()
    fallback_calls = []

    async def fake_stream_handler(**kwargs):
        if False:
            yield ""
        return

    async def fake_generate_content(
        prompt,
        max_tokens=4000,
        temperature=0.3,
        timeout=120,
        use_heavy=False,
        preferred_provider=None,
        preferred_model=None,
        audit_context=None,
    ):
        fallback_calls.append(
            {
                "prompt": prompt,
                "timeout": timeout,
                "audit_context": audit_context,
                "use_heavy": use_heavy,
            }
        )
        return "fallback text"

    monkeypatch.setattr(stream_handler_module, "generate_content_stream", fake_stream_handler)
    monkeypatch.setattr(facade_module, "create_llm_call_context", lambda **kwargs: {"call_kind": kwargs["call_kind"]})
    monkeypatch.setattr(
        facade_module,
        "derive_llm_call_context",
        lambda base, **kwargs: dict(base, **kwargs),
    )
    client.generate_content = fake_generate_content

    chunks = [
        chunk
        async for chunk in AIClient.generate_content_stream(
            client,
            prompt="stream prompt",
            timeout=30,
            use_heavy=False,
        )
    ]

    assert chunks == ["fallback text"]
    assert fallback_calls[0]["prompt"] == "stream prompt"
    assert fallback_calls[0]["timeout"] >= 15
    assert fallback_calls[0]["audit_context"]["phase"] == "stream_fallback_text"
