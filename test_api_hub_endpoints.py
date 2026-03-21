from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.api_hub as api_hub_module


class _FakeCompletions:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.completions = _FakeCompletions(response)
        self.chat = SimpleNamespace(completions=self.completions)


class _FakeRegistry:
    def __init__(self):
        self._providers = {
            "deepseek": SimpleNamespace(
                name="deepseek",
                model="deepseek-chat",
                base_url="https://deepseek.test/v1",
                enabled=True,
                client=_FakeClient(
                    SimpleNamespace(id="resp_ds", choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
                ),
            ),
            "openrouter": SimpleNamespace(
                name="openrouter",
                model="",
                base_url="https://openrouter.test/v1",
                enabled=True,
                client=_FakeClient(
                    SimpleNamespace(id="resp_or", choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
                ),
            ),
        }

    def list_all(self):
        return list(self._providers.values())

    def get(self, name: str):
        return self._providers.get(name)

    def enable(self, name: str) -> None:
        self._providers[name].enabled = True

    def disable(self, name: str) -> None:
        self._providers[name].enabled = False

    def update_model(self, name: str, model: str) -> None:
        self._providers[name].model = model


class _FakePools:
    def __init__(self):
        self._timeline_models = {
            "openrouter": ["google/gemini-2.5-pro"],
            "deepseek": ["deepseek-chat"],
        }
        self._pool_entries = {
            "Light": [
                (object(), "deepseek-chat", "deepseek/deepseek-chat"),
            ],
            "Fast": [
                (object(), "google/gemini-2.5-pro", "openrouter/google/gemini-2.5-pro"),
            ],
        }

    def list_models_for_provider(self, provider: str):
        return list(self._timeline_models.get(provider, []))

    def get_all_pools(self):
        return dict(self._pool_entries)

    def reconfigure_pool(self, name: str, entries):
        self._pool_entries[name] = list(entries)


class _FakeHealth:
    def get_status(self, provider: str):
        return SimpleNamespace(
            healthy=(provider != "openrouter"),
            success_rate=1.0 if provider != "openrouter" else 0.5,
            avg_latency_ms=230 if provider == "deepseek" else 780,
            sample_count=3 if provider == "deepseek" else 2,
        )

    def get_all_status(self):
        return {
            "deepseek": self.get_status("deepseek"),
            "openrouter": self.get_status("openrouter"),
        }


class _FakeCostTracker:
    def __init__(self):
        self._prices = {"deepseek/deepseek-chat": {"input": 0.0014, "output": 0.0028}}
        self.updated_prices = []

    def get_timeline(self, period: str = "24h"):
        return {
            "2026-03-21 10:00": {
                "calls": 2,
                "total_tokens": 420,
                "total_cost": 0.0123,
            }
        }

    def get_summary(self, period: str = "24h", group_by: str = "provider"):
        if period == "7d":
            return {
                "deepseek": {"total_cost": 0.4},
                "openrouter": {"total_cost": 0.9},
            }
        if period == "30d":
            return {
                "deepseek": {"total_cost": 1.2},
                "openrouter": {"total_cost": 1.8},
            }
        return {
            "deepseek": {"total_cost": 0.1},
            "openrouter": {"total_cost": 0.2},
        }

    def get_daily_cost(self):
        return 0.34567

    def get_prices(self):
        return dict(self._prices)

    def update_price(self, provider: str, model: str, input_per_1k: float, output_per_1k: float):
        self.updated_prices.append((provider, model, input_per_1k, output_per_1k))
        self._prices[f"{provider}/{model}"] = {
            "input": input_per_1k,
            "output": output_per_1k,
        }
        return None


class _FakeHub:
    def __init__(self):
        self.registry = _FakeRegistry()
        self.pools = _FakePools()
        self.health = _FakeHealth()
        self.cost_tracker = _FakeCostTracker()


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(api_hub_module.router)
    fake_hub = _FakeHub()
    monkeypatch.setattr(api_hub_module, "_get_hub", lambda: fake_hub)
    with TestClient(app) as test_client:
        yield test_client, fake_hub


def test_list_providers_returns_health_snapshot(client):
    test_client, _ = client

    response = test_client.get("/api/hub/providers")

    assert response.status_code == 200
    data = response.json()
    assert data[0]["name"] == "deepseek"
    assert data[0]["healthy"] is True
    assert data[0]["avg_latency_ms"] == 230
    assert data[1]["name"] == "openrouter"
    assert data[1]["healthy"] is False
    assert data[1]["success_rate"] == 0.5


def test_update_provider_mutates_registry_state(client):
    test_client, fake_hub = client

    response = test_client.put(
        "/api/hub/providers/deepseek",
        json={"enabled": False, "model": "deepseek-reasoner"},
    )

    assert response.status_code == 200
    provider = fake_hub.registry.get("deepseek")
    assert provider.enabled is False
    assert provider.model == "deepseek-reasoner"


def test_provider_test_uses_pool_configured_model_when_default_model_is_empty(client):
    test_client, fake_hub = client

    response = test_client.post("/api/hub/providers/openrouter/test")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    calls = fake_hub.registry.get("openrouter").client.completions.calls
    assert len(calls) == 1
    assert calls[0]["model"] == "google/gemini-2.5-pro"


def test_usage_timeline_returns_time_bucket_series(client):
    test_client, _ = client

    response = test_client.get("/api/hub/usage/timeline?period=24h")

    assert response.status_code == 200
    data = response.json()
    assert data["period"] == "24h"
    assert data["series"]["2026-03-21 10:00"]["total_tokens"] == 420
    assert data["series"]["2026-03-21 10:00"]["total_cost"] == 0.0123


def test_list_pools_returns_all_configured_pool_entries(client):
    test_client, _ = client

    response = test_client.get("/api/hub/pools")

    assert response.status_code == 200
    data = response.json()
    assert data == [
        {
            "name": "Light",
            "entries": [
                {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "display": "deepseek/deepseek-chat",
                }
            ],
        },
        {
            "name": "Fast",
            "entries": [
                {
                    "provider": "openrouter",
                    "model": "google/gemini-2.5-pro",
                    "display": "openrouter/google/gemini-2.5-pro",
                }
            ],
        },
    ]


def test_update_pool_reconfigures_entries(client):
    test_client, fake_hub = client

    response = test_client.put(
        "/api/hub/pools/Light",
        json={
            "entries": [
                {"provider": "openrouter", "model": "google/gemini-2.5-pro"},
                {"provider": "deepseek", "model": "deepseek-reasoner"},
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "pool": "Light", "size": 2}
    assert fake_hub.pools.get_all_pools()["Light"] == [
        (fake_hub.registry.get("openrouter").client, "google/gemini-2.5-pro", "openrouter/google/gemini-2.5-pro"),
        (fake_hub.registry.get("deepseek").client, "deepseek-reasoner", "deepseek/deepseek-reasoner"),
    ]


def test_update_pool_rejects_unknown_provider(client):
    test_client, _ = client

    response = test_client.put(
        "/api/hub/pools/Light",
        json={"entries": [{"provider": "missing", "model": "ghost-model"}]},
    )

    assert response.status_code == 400
    assert "not registered" in response.json()["detail"]


def test_usage_summary_and_costs_return_aggregated_budget_views(client):
    test_client, _ = client

    summary_response = test_client.get("/api/hub/usage/summary?period=7d&group_by=model")
    costs_response = test_client.get("/api/hub/usage/costs")

    assert summary_response.status_code == 200
    assert summary_response.json() == {
        "period": "7d",
        "group_by": "model",
        "data": {
            "deepseek": {"total_cost": 0.4},
            "openrouter": {"total_cost": 0.9},
        },
    }
    assert costs_response.status_code == 200
    assert costs_response.json() == {
        "daily_usd": 0.3457,
        "weekly_usd": 1.3,
        "monthly_usd": 3.0,
    }


def test_health_endpoint_returns_registry_wide_snapshot(client):
    test_client, _ = client

    response = test_client.get("/api/hub/health")

    assert response.status_code == 200
    assert response.json() == {
        "deepseek": {
            "healthy": True,
            "success_rate": 1.0,
            "avg_latency_ms": 230,
            "sample_count": 3,
        },
        "openrouter": {
            "healthy": False,
            "success_rate": 0.5,
            "avg_latency_ms": 780,
            "sample_count": 2,
        },
    }


def test_price_endpoints_list_and_update_prices(client):
    test_client, fake_hub = client

    list_response = test_client.get("/api/hub/prices")
    update_response = test_client.put(
        "/api/hub/prices/openrouter/google%2Fgemini-2.5-pro",
        json={"input_per_1k": 0.0042, "output_per_1k": 0.0084},
    )

    assert list_response.status_code == 200
    assert list_response.json() == [
        {
            "key": "deepseek/deepseek-chat",
            "input_per_1k": 0.0014,
            "output_per_1k": 0.0028,
        }
    ]
    assert update_response.status_code == 200
    assert update_response.json() == {
        "status": "ok",
        "provider": "openrouter",
        "model": "google/gemini-2.5-pro",
    }
    assert fake_hub.cost_tracker.updated_prices == [
        ("openrouter", "google/gemini-2.5-pro", 0.0042, 0.0084)
    ]
