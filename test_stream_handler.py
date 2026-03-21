from __future__ import annotations

from types import SimpleNamespace

import pytest

import services.api_hub.stream_handler as stream_handler


def test_extract_stream_delta_supports_string_and_list_content():
    string_chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="hello"))]
    )
    list_chunk = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=[
                        {"text": "foo"},
                        SimpleNamespace(text="bar"),
                    ]
                )
            )
        ]
    )

    assert stream_handler._extract_stream_delta(string_chunk) == "hello"
    assert stream_handler._extract_stream_delta(list_chunk) == "foobar"


@pytest.mark.asyncio
async def test_generate_content_stream_yields_chunks_and_reports_success(monkeypatch):
    logged = []
    usage_events = []
    health_events = []

    async def fake_call_model_stream(**kwargs):
        yield "foo"
        yield "bar"

    monkeypatch.setattr(stream_handler, "call_model_stream", fake_call_model_stream)
    monkeypatch.setattr(stream_handler, "log_llm_attempt", lambda **kwargs: logged.append(kwargs))

    chunks = [
        chunk
        async for chunk in stream_handler.generate_content_stream(
            pool=[(SimpleNamespace(base_url="https://deepseek.test"), "deepseek-chat", "deepseek/deepseek-chat")],
            pool_name="Light",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=16,
            temperature=0.2,
            timeout=30,
            audit_context={"operation": "stream"},
            usage_callback=lambda **kwargs: usage_events.append(kwargs),
            health_callback=lambda provider, success, latency_ms: health_events.append((provider, success, latency_ms)),
        )
    ]

    assert chunks == ["foo", "bar"]
    assert logged[-1]["status"] == "success"
    assert logged[-1]["output_chars"] == 6
    assert usage_events == [
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "elapsed_ms": usage_events[0]["elapsed_ms"],
            "status": "success",
            "pool_name": "Light",
            "audit_context": {"operation": "stream"},
        }
    ]
    assert health_events == [("deepseek", True, health_events[0][2])]


@pytest.mark.asyncio
async def test_generate_content_stream_returns_nothing_when_all_models_fail_without_output(monkeypatch):
    logged = []
    usage_events = []
    health_events = []
    calls = []

    async def fake_call_model_stream(**kwargs):
        calls.append(kwargs["provider_name"])
        raise RuntimeError(f"stream failed for {kwargs['provider_name']}")
        yield  # pragma: no cover

    monkeypatch.setattr(stream_handler, "call_model_stream", fake_call_model_stream)
    monkeypatch.setattr(stream_handler, "log_llm_attempt", lambda **kwargs: logged.append(kwargs))

    chunks = [
        chunk
        async for chunk in stream_handler.generate_content_stream(
            pool=[
                (SimpleNamespace(base_url="https://first.test"), "m1", "first/m1"),
                (SimpleNamespace(base_url="https://second.test"), "m2", "second/m2"),
            ],
            pool_name="Fast",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=16,
            temperature=0.2,
            timeout=40,
            usage_callback=lambda **kwargs: usage_events.append(kwargs),
            health_callback=lambda provider, success, latency_ms: health_events.append((provider, success, latency_ms)),
        )
    ]

    assert chunks == []
    assert calls == ["first/m1", "second/m2"]
    assert [entry["status"] for entry in logged] == ["error", "error"]
    assert [event["status"] for event in usage_events] == ["error", "error"]
    assert health_events[0][1] is False and health_events[1][1] is False


@pytest.mark.asyncio
async def test_generate_content_stream_raises_if_model_fails_after_partial_output(monkeypatch):
    async def fake_call_model_stream(**kwargs):
        yield "partial"
        raise RuntimeError("mid-stream failure")

    monkeypatch.setattr(stream_handler, "call_model_stream", fake_call_model_stream)
    monkeypatch.setattr(stream_handler, "log_llm_attempt", lambda **kwargs: None)

    with pytest.raises(RuntimeError, match="mid-stream failure"):
        async for _chunk in stream_handler.generate_content_stream(
            pool=[(SimpleNamespace(base_url="https://deepseek.test"), "deepseek-chat", "deepseek/deepseek-chat")],
            pool_name="Light",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=16,
            temperature=0.2,
            timeout=30,
        ):
            pass
