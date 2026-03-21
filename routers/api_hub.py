"""API Hub management endpoints — provider control, pool config, usage analytics."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/hub", tags=["api-hub"])


# ── Pydantic schemas ──


class ProviderOut(BaseModel):
    name: str
    model: str
    base_url: str
    enabled: bool
    healthy: bool = True
    success_rate: float = 1.0
    avg_latency_ms: int = 0
    sample_count: int = 0


class ProviderUpdate(BaseModel):
    enabled: Optional[bool] = None
    model: Optional[str] = None


class PoolEntryOut(BaseModel):
    provider: str
    model: str
    display: str


class PoolOut(BaseModel):
    name: str
    entries: List[PoolEntryOut]


class PoolUpdate(BaseModel):
    entries: List[Dict[str, str]]  # [{provider, model}, ...]


class UsageSummaryOut(BaseModel):
    period: str
    group_by: str
    data: Dict[str, Any]


class PriceOut(BaseModel):
    key: str
    input_per_1k: float
    output_per_1k: float


class PriceUpdate(BaseModel):
    input_per_1k: float
    output_per_1k: float


class TestResult(BaseModel):
    provider: str
    success: bool
    latency_ms: int = 0
    error: Optional[str] = None


# ── Helper to get hub components ──


def _get_hub():
    from services.api_hub.facade import get_ai_client
    client = get_ai_client()
    return client


def _resolve_test_model(hub, name: str, info) -> str:
    configured_models = hub.pools.list_models_for_provider(name)
    for model in configured_models:
        if model:
            return model
    if info.model:
        return info.model
    raise HTTPException(400, f"Provider '{name}' has no configured model")


# ── Provider endpoints ──


@router.get("/providers", response_model=List[ProviderOut])
async def list_providers():
    hub = _get_hub()
    providers = hub.registry.list_all()
    result = []
    for p in providers:
        health = hub.health.get_status(p.name)
        result.append(ProviderOut(
            name=p.name,
            model=p.model,
            base_url=p.base_url,
            enabled=p.enabled,
            healthy=health.healthy,
            success_rate=health.success_rate,
            avg_latency_ms=health.avg_latency_ms,
            sample_count=health.sample_count,
        ))
    return result


@router.put("/providers/{name}")
async def update_provider(name: str, body: ProviderUpdate):
    hub = _get_hub()
    info = hub.registry.get(name)
    if info is None:
        raise HTTPException(404, f"Provider '{name}' not found")
    if body.enabled is not None:
        if body.enabled:
            hub.registry.enable(name)
        else:
            hub.registry.disable(name)
    if body.model is not None:
        hub.registry.update_model(name, body.model)
    return {"status": "ok", "provider": name}


@router.post("/providers/{name}/test", response_model=TestResult)
async def test_provider(name: str):
    hub = _get_hub()
    info = hub.registry.get(name)
    if info is None:
        raise HTTPException(404, f"Provider '{name}' not found")
    import time as _time
    started = _time.time()
    try:
        test_model = _resolve_test_model(hub, name, info)
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: info.client.chat.completions.create(
                model=test_model,
                messages=[{"role": "user", "content": "Say 'ok'"}],
                max_tokens=5,
                temperature=0,
            )),
            timeout=15,
        )
        latency = int((_time.time() - started) * 1000)
        return TestResult(provider=name, success=True, latency_ms=latency)
    except Exception as e:
        latency = int((_time.time() - started) * 1000)
        return TestResult(provider=name, success=False, latency_ms=latency, error=str(e)[:200])


# ── Pool endpoints ──


@router.get("/pools", response_model=List[PoolOut])
async def list_pools():
    hub = _get_hub()
    all_pools = hub.pools.get_all_pools()
    result = []
    for name, entries in all_pools.items():
        result.append(PoolOut(
            name=name,
            entries=[
                PoolEntryOut(
                    provider=display.split("/")[0],
                    model=model,
                    display=display,
                )
                for _, model, display in entries
            ],
        ))
    return result


@router.put("/pools/{name}")
async def update_pool(name: str, body: PoolUpdate):
    hub = _get_hub()
    new_entries = []
    for entry in body.entries:
        provider = entry.get("provider", "")
        model = entry.get("model", "")
        info = hub.registry.get(provider)
        if info is None:
            raise HTTPException(400, f"Provider '{provider}' not registered")
        new_entries.append((info.client, model, f"{provider}/{model}"))
    hub.pools.reconfigure_pool(name, new_entries)
    return {"status": "ok", "pool": name, "size": len(new_entries)}


# ── Usage & cost endpoints ──


@router.get("/usage/summary")
async def usage_summary(period: str = "24h", group_by: str = "provider"):
    hub = _get_hub()
    data = hub.cost_tracker.get_summary(period=period, group_by=group_by)
    return UsageSummaryOut(period=period, group_by=group_by, data=data)


@router.get("/usage/costs")
async def usage_costs():
    hub = _get_hub()
    daily = hub.cost_tracker.get_daily_cost()
    weekly = sum(
        v.get("total_cost", 0)
        for v in hub.cost_tracker.get_summary(period="7d").values()
    )
    monthly = sum(
        v.get("total_cost", 0)
        for v in hub.cost_tracker.get_summary(period="30d").values()
    )
    return {
        "daily_usd": round(daily, 4),
        "weekly_usd": round(weekly, 4),
        "monthly_usd": round(monthly, 4),
    }


@router.get("/usage/timeline")
async def usage_timeline(period: str = "24h"):
    """Time-series data for charts (grouped by hour)."""
    hub = _get_hub()
    data = hub.cost_tracker.get_timeline(period=period)
    return {"period": period, "series": data}


# ── Health endpoint ──


@router.get("/health")
async def health_status():
    hub = _get_hub()
    all_status = hub.health.get_all_status()
    providers = hub.registry.list_all()
    result = {}
    for p in providers:
        status = all_status.get(p.name)
        if status:
            result[p.name] = {
                "healthy": status.healthy,
                "success_rate": status.success_rate,
                "avg_latency_ms": status.avg_latency_ms,
                "sample_count": status.sample_count,
            }
        else:
            result[p.name] = {"healthy": True, "success_rate": 1.0, "avg_latency_ms": 0, "sample_count": 0}
    return result


# ── Price endpoints ──


@router.get("/prices", response_model=List[PriceOut])
async def list_prices():
    hub = _get_hub()
    prices = hub.cost_tracker.get_prices()
    return [
        PriceOut(key=k, input_per_1k=v["input"], output_per_1k=v["output"])
        for k, v in prices.items()
    ]


@router.put("/prices/{provider}/{model:path}")
async def update_price(provider: str, model: str, body: PriceUpdate):
    hub = _get_hub()
    hub.cost_tracker.update_price(provider, model, body.input_per_1k, body.output_per_1k)
    return {"status": "ok", "provider": provider, "model": model}
