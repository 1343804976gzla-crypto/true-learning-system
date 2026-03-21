"""Pool routing, default pool construction, and dynamic switching."""

from __future__ import annotations

import logging
import os
import threading
from typing import Dict, List, Optional, Tuple

from services.api_hub._types import PoolEntry
from services.api_hub.provider_registry import ProviderRegistry

logger = logging.getLogger(__name__)
PoolSpec = Tuple[str, str]


class PoolManager:
    """Manages named model pools with runtime reconfiguration."""

    def __init__(self, registry: ProviderRegistry, strict_heavy: bool = False):
        self._registry = registry
        self._strict_heavy = strict_heavy
        self._pool_specs: Dict[str, List[PoolSpec]] = {}
        self._lock = threading.Lock()
        self._registry.add_model_update_listener(self.replace_provider_model)

    # ── Load from environment ──

    def load_from_env(self) -> None:
        """Parse POOL_HEAVY / POOL_LIGHT / POOL_FAST / POOL_VISION from environment."""
        heavy = self._parse_pool_env("POOL_HEAVY", self._default_heavy_pool())
        light = self._parse_pool_env("POOL_LIGHT", self._default_light_pool())
        fast = self._parse_pool_env("POOL_FAST", self._default_fast_pool())
        vision = self._parse_pool_env("POOL_VISION", heavy)
        with self._lock:
            self._pool_specs["Heavy"] = heavy
            self._pool_specs["Light"] = light
            self._pool_specs["Fast"] = fast
            self._pool_specs["Vision"] = vision

    def _parse_pool_env(
        self, env_key: str, default: List[PoolSpec]
    ) -> List[PoolSpec]:
        raw = (os.getenv(env_key) or "").strip()
        if not raw:
            return list(default)

        pool: List[PoolSpec] = []
        for entry in raw.split(","):
            entry = entry.strip()
            if ":" not in entry:
                logger.warning(
                    "Pool %s: '%s' has invalid format (need provider:model), skipped",
                    env_key, entry,
                )
                continue
            provider_name, model = entry.split(":", 1)
            provider_name = provider_name.strip()
            model = model.strip()
            info = self._registry.get(provider_name)
            if info:
                pool.append((provider_name, model))
            else:
                logger.warning(
                    "Pool %s: references unregistered provider '%s', skipped",
                    env_key, provider_name,
                )
        return pool if pool else list(default)

    # ── Default pool builders ──

    def _default_heavy_pool(self) -> List[PoolSpec]:
        pool: List[PoolSpec] = []
        gm = self._registry.get("gemini")
        if gm and gm.enabled:
            model = gm.model or "gemini-3-flash-preview"
            pool.append(("gemini", model))
        ds = self._registry.get("deepseek")
        if ds and ds.enabled and not self._strict_heavy:
            model = ds.model or "deepseek-chat"
            pool.append(("deepseek", model))
        return pool

    def _default_light_pool(self) -> List[PoolSpec]:
        pool: List[PoolSpec] = []
        ds = self._registry.get("deepseek")
        if ds and ds.enabled:
            model = ds.model or "deepseek-chat"
            pool.append(("deepseek", model))
        return pool

    def _default_fast_pool(self) -> List[PoolSpec]:
        pool: List[PoolSpec] = []
        orr = self._registry.get("openrouter")
        if orr and orr.enabled:
            model = orr.model or "deepseek-chat"
            pool.append(("openrouter", model))
        ds = self._registry.get("deepseek")
        if ds and ds.enabled:
            model = ds.model or "deepseek-chat"
            pool.append(("deepseek", model))
        return pool

    def _resolve_entry(self, provider_name: str, model: str) -> Optional[PoolEntry]:
        info = self._registry.get(provider_name)
        if info is None or not info.enabled:
            return None
        resolved_model = (model or info.model or "").strip()
        if not resolved_model:
            logger.warning("Pool entry %s has no model configured, skipped", provider_name)
            return None
        return (info.client, resolved_model, f"{provider_name}/{resolved_model}")

    # ── Pool access ──

    def get_pool(self, name: str) -> List[PoolEntry]:
        with self._lock:
            specs = list(self._pool_specs.get(name, []))

        pool: List[PoolEntry] = []
        seen = set()
        for provider_name, model in specs:
            entry = self._resolve_entry(provider_name, model)
            if entry is None:
                continue
            key = self._pool_entry_key(entry)
            if key in seen:
                continue
            seen.add(key)
            pool.append(entry)
        return pool

    def get_all_pools(self) -> Dict[str, List[PoolEntry]]:
        with self._lock:
            names = list(self._pool_specs.keys())
        return {name: self.get_pool(name) for name in names}

    def list_models_for_provider(self, provider_name: str) -> List[str]:
        with self._lock:
            specs = [spec for pool in self._pool_specs.values() for spec in pool]

        models: List[str] = []
        seen = set()
        for spec_provider, spec_model in specs:
            if spec_provider != provider_name or not spec_model or spec_model in seen:
                continue
            seen.add(spec_model)
            models.append(spec_model)

        info = self._registry.get(provider_name)
        default_model = (info.model if info else "") or ""
        if default_model and default_model not in seen:
            models.append(default_model)
        return models

    # ── Pool composition (preferred provider + fallback) ──

    def resolve_pool(self, use_heavy: bool) -> Tuple[List[PoolEntry], str]:
        pool_name = "Heavy" if use_heavy else "Light"
        pool = self.get_pool(pool_name)
        if pool:
            return pool, pool_name
        if use_heavy:
            raise RuntimeError(
                "Heavy pool is empty: no heavy models configured "
                "(need GEMINI_API_KEY or POOL_HEAVY)"
            )
        raise RuntimeError(
            "Light pool is empty: no light models configured "
            "(need DEEPSEEK_API_KEY or POOL_LIGHT)"
        )

    def resolve_preferred(
        self,
        preferred_provider: Optional[str],
        preferred_model: Optional[str],
    ) -> Optional[PoolEntry]:
        provider_name = (preferred_provider or "").strip()
        if not provider_name or provider_name == "auto":
            return None
        info = self._registry.get(provider_name)
        if info is None:
            raise RuntimeError(f"Unregistered provider: {provider_name}")
        if not info.enabled:
            raise RuntimeError(f"Provider '{provider_name}' is disabled")
        model = (preferred_model or info.model or "").strip()
        if not model or model == "auto":
            raise RuntimeError(
                f"Provider {provider_name} has no default model and none specified"
            )
        return (info.client, model, f"{provider_name}/{model}")

    @staticmethod
    def _pool_entry_key(entry: PoolEntry) -> Tuple[str, str]:
        _, model, display = entry
        return (display.split("/", 1)[0], model)

    def compose_pool(
        self,
        use_heavy: bool,
        preferred_provider: Optional[str] = None,
        preferred_model: Optional[str] = None,
    ) -> Tuple[List[PoolEntry], str]:
        direct_entry = self.resolve_preferred(preferred_provider, preferred_model)
        if direct_entry is None:
            return self.resolve_pool(use_heavy)

        pool = [direct_entry]
        pool_name = f"Preferred({direct_entry[2]})"

        try:
            fallback_pool, fallback_name = self.resolve_pool(use_heavy)
        except Exception as exc:
            logger.warning(
                "Preferred model %s has no fallback pool: %s",
                direct_entry[2], exc,
            )
            return pool, pool_name

        seen = {self._pool_entry_key(direct_entry)}
        for entry in fallback_pool:
            key = self._pool_entry_key(entry)
            if key not in seen:
                seen.add(key)
                pool.append(entry)

        if len(pool) > 1:
            pool_name = f"{pool_name} -> {fallback_name}"
        return pool, pool_name

    # ── Runtime reconfiguration ──

    def reconfigure_pool(self, name: str, entries: List[PoolEntry]) -> None:
        specs: List[PoolSpec] = []
        for _, model, display in entries:
            provider = (display or "").split("/", 1)[0].strip()
            if not provider:
                logger.warning("Pool %s: entry '%s' missing provider name, skipped", name, display)
                continue
            specs.append((provider, model))
        with self._lock:
            self._pool_specs[name] = specs

    def replace_provider_model(self, provider: str, model: str) -> None:
        with self._lock:
            for pool_name, specs in self._pool_specs.items():
                updated = []
                for spec_provider, spec_model in specs:
                    if spec_provider == provider:
                        updated.append((spec_provider, model))
                    else:
                        updated.append((spec_provider, spec_model))
                self._pool_specs[pool_name] = updated

    def add_model(
        self, pool_name: str, provider: str, model: str, priority: int = -1
    ) -> None:
        info = self._registry.get(provider)
        if info is None:
            raise KeyError(f"Provider '{provider}' not registered")
        spec: PoolSpec = (provider, model)
        with self._lock:
            pool = self._pool_specs.setdefault(pool_name, [])
            if priority < 0 or priority >= len(pool):
                pool.append(spec)
            else:
                pool.insert(priority, spec)

    def remove_model(self, pool_name: str, provider: str, model: str) -> bool:
        with self._lock:
            pool = self._pool_specs.get(pool_name, [])
            for i, (spec_provider, spec_model) in enumerate(pool):
                if spec_provider == provider and spec_model == model:
                    pool.pop(i)
                    return True
        return False

    def log_init(self) -> None:
        """Log pool initialization summary."""
        print("[AIClient] Multi-model pool initialization complete")
        providers = self._registry.list_names()
        print(f"[AIClient] Registered providers ({len(providers)}): {providers}")
        for pool_name in ("Heavy", "Light", "Fast"):
            pool = self.get_pool(pool_name)
            names = [e[2] for e in pool]
            print(f"[AIClient] {pool_name} pool ({len(names)}): {names}")
