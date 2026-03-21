"""Targeted API Hub fallback and pool-management tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from routers.api_hub import _resolve_test_model
from services.api_hub import retry_engine
from services.api_hub._types import PoolEntry, ProviderInfo
from services.api_hub.facade import AIClient
from services.api_hub.pool_manager import PoolManager
from services.api_hub.provider_registry import ProviderRegistry


class _FakeClient:
    """Minimal OpenAI-compatible client stub."""

    def __init__(
        self,
        name: str,
        response_text: str = "ok",
        responses: list[object] | None = None,
    ):
        self.name = name
        self.base_url = f"https://{name}.test/v1"
        self._responses = list(responses) if responses is not None else [response_text]
        self.calls = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        current = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if current == "__FAIL__":
            raise RuntimeError("Error code: 503 - No available channels")
        if isinstance(current, Exception):
            raise current
        return SimpleNamespace(
            id=f"resp_{self.name}",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=str(current)),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8),
        )


def _build_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["deepseek"] = ProviderInfo(
        name="deepseek",
        client=_FakeClient("deepseek"),
        model="deepseek-chat",
        base_url="https://deepseek.test/v1",
        enabled=True,
    )
    registry._providers["qingyun"] = ProviderInfo(
        name="qingyun",
        client=_FakeClient("qingyun"),
        model="claude-sonnet-4-6",
        base_url="https://qingyun.test/v1",
        enabled=True,
    )
    registry._providers["gemini"] = ProviderInfo(
        name="gemini",
        client=_FakeClient("gemini"),
        model="gemini-3.1-flash-lite-preview",
        base_url="https://gemini.test/v1",
        enabled=True,
    )
    return registry


def test_generate_content_falls_back_after_preferred_model_failure():
    preferred_client = _FakeClient("qingyun", "__FAIL__")
    fallback_client = _FakeClient("gemini", "fallback-ok")

    pool: list[PoolEntry] = [
        (preferred_client, "claude-sonnet-4-6", "qingyun/claude-sonnet-4-6"),
        (fallback_client, "gemini-3.1-flash-lite-preview", "gemini/gemini-3.1-flash-lite-preview"),
    ]

    messages = [{"role": "user", "content": "fallback test"}]

    result = asyncio.run(
        retry_engine.call_pool(
            pool=pool,
            pool_name="Preferred(qingyun/claude-sonnet-4-6) -> Light",
            messages=messages,
            max_tokens=128,
            temperature=0.3,
            timeout=30,
        )
    )

    assert result == "fallback-ok"


def test_compose_pool_puts_preferred_first():
    registry = _build_registry()
    pm = PoolManager(registry)
    pm.reconfigure_pool("Light", [
        (
            registry.get("deepseek").client,
            "deepseek-chat",
            "deepseek/deepseek-chat",
        ),
    ])

    pool, pool_name = pm.compose_pool(
        use_heavy=False,
        preferred_provider="qingyun",
        preferred_model="claude-sonnet-4-6",
    )

    assert pool[0][2] == "qingyun/claude-sonnet-4-6"
    assert len(pool) == 2
    assert pool[1][2] == "deepseek/deepseek-chat"
    assert "Preferred" in pool_name


def test_disabled_provider_is_filtered_from_pool_and_rejected_as_preferred():
    registry = _build_registry()
    pm = PoolManager(registry)
    pm.reconfigure_pool("Light", [
        (
            registry.get("deepseek").client,
            "deepseek-chat",
            "deepseek/deepseek-chat",
        ),
        (
            registry.get("gemini").client,
            "gemini-3.1-flash-lite-preview",
            "gemini/gemini-3.1-flash-lite-preview",
        ),
    ])

    registry.disable("deepseek")
    pool = pm.get_pool("Light")

    assert [entry[2] for entry in pool] == ["gemini/gemini-3.1-flash-lite-preview"]
    with pytest.raises(RuntimeError, match="disabled"):
        pm.resolve_preferred("deepseek", None)


def test_registry_model_updates_propagate_into_configured_pools():
    registry = _build_registry()
    pm = PoolManager(registry)
    pm.reconfigure_pool("Light", [
        (
            registry.get("deepseek").client,
            "deepseek-chat",
            "deepseek/deepseek-chat",
        ),
    ])

    registry.update_model("deepseek", "deepseek-reasoner")

    assert [entry[2] for entry in pm.get_pool("Light")] == ["deepseek/deepseek-reasoner"]


def test_provider_test_endpoint_uses_pool_configured_model_when_default_is_empty():
    hub = SimpleNamespace(
        pools=SimpleNamespace(
            list_models_for_provider=lambda provider: ["google/gemini-2.5-pro"]
        )
    )
    info = SimpleNamespace(model="")

    assert _resolve_test_model(hub, "openrouter", info) == "google/gemini-2.5-pro"


@pytest.mark.asyncio
async def test_call_model_with_audit_retries_transient_error_before_success(monkeypatch):
    async def _no_sleep(_seconds: int):
        return None

    monkeypatch.setattr(retry_engine.asyncio, "sleep", _no_sleep)
    client = _FakeClient(
        "deepseek",
        responses=[RuntimeError("429 rate limit exceeded"), "recovered-ok"],
    )
    health_events = []
    usage_events = []

    result = await retry_engine.call_model_with_audit(
        client=client,
        model="deepseek-chat",
        provider_name="deepseek/deepseek-chat",
        messages=[{"role": "user", "content": "retry"}],
        max_tokens=64,
        temperature=0.2,
        timeout=20,
        pool_name="Light",
        pool_index=1,
        pool_size=1,
        health_callback=lambda provider, success, latency_ms: health_events.append((provider, success, latency_ms)),
        usage_callback=lambda **kwargs: usage_events.append(kwargs),
    )

    assert result == "recovered-ok"
    assert len(client.calls) == 2
    assert health_events and health_events[-1][0] == "deepseek"
    assert health_events[-1][1] is True
    assert usage_events[-1]["status"] == "success"
    assert usage_events[-1]["usage"]["total_tokens"] == 8


@pytest.mark.asyncio
async def test_call_model_with_audit_reports_terminal_error_callbacks():
    client = _FakeClient("deepseek", responses=[RuntimeError("validation failed")])
    health_events = []
    usage_events = []

    with pytest.raises(RuntimeError, match="validation failed"):
        await retry_engine.call_model_with_audit(
            client=client,
            model="deepseek-chat",
            provider_name="deepseek/deepseek-chat",
            messages=[{"role": "user", "content": "boom"}],
            max_tokens=64,
            temperature=0.2,
            timeout=20,
            pool_name="Light",
            pool_index=1,
            pool_size=1,
            health_callback=lambda provider, success, latency_ms: health_events.append((provider, success, latency_ms)),
            usage_callback=lambda **kwargs: usage_events.append(kwargs),
        )

    assert len(client.calls) == 1
    assert len(health_events) == 1
    assert health_events[0][0] == "deepseek"
    assert health_events[0][1] is False
    assert len(usage_events) == 1
    assert usage_events[0]["provider"] == "deepseek"
    assert usage_events[0]["model"] == "deepseek-chat"
    assert usage_events[0]["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    assert usage_events[0]["status"] == "error"
    assert usage_events[0]["pool_name"] == "Light"
    assert usage_events[0]["audit_context"] is None


@pytest.mark.asyncio
async def test_call_pool_reallocates_remaining_timeout_after_failure(monkeypatch):
    captured_timeouts = []

    async def fake_call_model_with_audit(**kwargs):
        captured_timeouts.append(kwargs["timeout"])
        if len(captured_timeouts) == 1:
            raise RuntimeError("first failed")
        return "second-ok"

    monkeypatch.setattr(retry_engine, "call_model_with_audit", fake_call_model_with_audit)

    result = await retry_engine.call_pool(
        pool=[
            (_FakeClient("first"), "m1", "first/m1"),
            (_FakeClient("second"), "m2", "second/m2"),
        ],
        pool_name="Light",
        messages=[{"role": "user", "content": "budget"}],
        timeout=40,
    )

    assert result == "second-ok"
    assert 18 <= captured_timeouts[0] <= 20
    assert captured_timeouts[1] >= 39


def test_list_models_for_provider_dedupes_pool_models_and_appends_default_model():
    registry = _build_registry()
    registry.update_model("deepseek", "deepseek-reasoner")
    pm = PoolManager(registry)
    pm.reconfigure_pool("Light", [
        (
            registry.get("deepseek").client,
            "deepseek-chat",
            "deepseek/deepseek-chat",
        ),
        (
            registry.get("deepseek").client,
            "deepseek-chat",
            "deepseek/deepseek-chat",
        ),
    ])
    pm.reconfigure_pool("Fast", [
        (
            registry.get("deepseek").client,
            "deepseek-chat",
            "deepseek/deepseek-chat",
        ),
    ])

    assert pm.list_models_for_provider("deepseek") == [
        "deepseek-chat",
        "deepseek-reasoner",
    ]


def test_compose_pool_keeps_preferred_only_when_no_fallback_pool_exists():
    registry = _build_registry()
    pm = PoolManager(registry)

    pool, pool_name = pm.compose_pool(
        use_heavy=False,
        preferred_provider="qingyun",
        preferred_model="claude-sonnet-4-6",
    )

    assert [entry[2] for entry in pool] == ["qingyun/claude-sonnet-4-6"]
    assert pool_name == "Preferred(qingyun/claude-sonnet-4-6)"


def test_filter_unhealthy_pool_skips_failed_fallback_providers():
    client = AIClient.__new__(AIClient)
    client.health = SimpleNamespace(is_healthy=lambda provider: provider != "gemini")

    pool = [
        (_FakeClient("deepseek"), "deepseek-chat", "deepseek/deepseek-chat"),
        (_FakeClient("gemini"), "gemini-3.1-flash-lite-preview", "gemini/gemini-3.1-flash-lite-preview"),
        (_FakeClient("qingyun"), "claude-sonnet-4-6", "qingyun/claude-sonnet-4-6"),
    ]

    filtered = client._filter_unhealthy_pool(pool, pool_name="Light")

    assert [entry[2] for entry in filtered] == [
        "deepseek/deepseek-chat",
        "qingyun/claude-sonnet-4-6",
    ]


def test_filter_unhealthy_pool_fails_open_when_every_provider_is_unhealthy():
    client = AIClient.__new__(AIClient)
    client.health = SimpleNamespace(is_healthy=lambda _provider: False)

    pool = [
        (_FakeClient("deepseek"), "deepseek-chat", "deepseek/deepseek-chat"),
        (_FakeClient("gemini"), "gemini-3.1-flash-lite-preview", "gemini/gemini-3.1-flash-lite-preview"),
    ]

    filtered = client._filter_unhealthy_pool(pool, pool_name="Light")

    assert filtered == pool
