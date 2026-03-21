"""Thin AIClient orchestrator — delegates to specialized modules."""

from __future__ import annotations

import logging
import os
import threading
import time as _time
from typing import Any, AsyncIterator, Dict, List, Optional

from services.api_hub.provider_registry import ProviderRegistry
from services.api_hub.pool_manager import PoolManager
from services.api_hub.health_monitor import HealthMonitor
from services.api_hub.cost_tracker import CostTracker
from services.api_hub import retry_engine, stream_handler, json_handler
from services.api_hub._types import PoolEntry
from services.llm_audit import (
    create_llm_call_context,
    derive_llm_call_context,
    get_llm_audit_request_context,
)

logger = logging.getLogger(__name__)


def _read_positive_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


class AIClient:
    """AI client — multi-model pool routing (modular facade)."""

    def __init__(self):
        self.strict_heavy = (
            os.getenv("STRICT_HEAVY_MODEL") or ""
        ).strip().lower() in ("1", "true", "yes")

        # 1. Register all available providers
        self.registry = ProviderRegistry()
        self.registry.register_from_env()

        # 2. Parse three task pools
        self.pools = PoolManager(self.registry, strict_heavy=self.strict_heavy)
        self.pools.load_from_env()

        # 3. Backward-compat attributes (for code referencing ds_client / gm_client)
        ds = self.registry.get_client_and_model("deepseek")
        self.ds_client = ds[0] if ds else None
        self.ds_model = ds[1] if ds else "deepseek-chat"
        gm = self.registry.get_client_and_model("gemini")
        self.gm_client = gm[0] if gm else None
        self.gm_model = gm[1] if gm else "gemini-3-flash-preview"

        runtime_db_factory = None
        try:
            from database.domains import RuntimeSessionLocal

            runtime_db_factory = RuntimeSessionLocal
        except Exception as e:
            logger.warning("Runtime DB init failed, API Hub persistence disabled: %s", e)

        self.health_window_seconds = _read_positive_int_env(
            "API_HUB_HEALTH_WINDOW_SECONDS",
            120,
            minimum=30,
        )
        self.health_failure_threshold = _read_positive_int_env(
            "API_HUB_FAILURE_THRESHOLD",
            2,
            minimum=1,
        )

        # 4. Health monitor & cost tracker
        self.health = HealthMonitor(
            window_seconds=self.health_window_seconds,
            failure_threshold=self.health_failure_threshold,
            db_session_factory=runtime_db_factory,
        )
        self._health_callback = self.health.make_callback()
        self.cost_tracker = CostTracker(db_session_factory=runtime_db_factory)
        if runtime_db_factory is not None:
            self.cost_tracker.load_prices_from_db()
        self._usage_callback = self._make_usage_callback()

        self.pools.log_init()

    def _make_usage_callback(self):
        def _cb(
            *,
            provider: str,
            model: str,
            usage: Optional[Dict[str, Any]],
            elapsed_ms: int,
            status: str,
            pool_name: str,
            audit_context: Optional[Dict[str, Any]],
        ) -> None:
            usage_data = dict(usage or {})
            prompt_tokens = int(usage_data.get("prompt_tokens") or 0)
            completion_tokens = int(usage_data.get("completion_tokens") or 0)
            total_tokens = int(usage_data.get("total_tokens") or 0) or (prompt_tokens + completion_tokens)
            request_context = get_llm_audit_request_context()
            metadata = dict(audit_context or {})
            self.cost_tracker.record_usage(
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                elapsed_ms=elapsed_ms,
                status=status,
                pool_name=pool_name,
                caller=str(metadata.get("operation") or ""),
                request_path=str(request_context.get("http_path") or ""),
                logical_call_id=str(metadata.get("logical_call_id") or ""),
            )

        return _cb

    # ── Public API (backward-compatible signatures) ──

    def _filter_unhealthy_pool(
        self,
        pool: List[PoolEntry],
        *,
        pool_name: str,
        preferred_provider: Optional[str] = None,
    ) -> List[PoolEntry]:
        if len(pool) <= 1:
            return pool

        protected_provider = (preferred_provider or "").strip()
        filtered: List[PoolEntry] = []
        skipped: List[str] = []
        for index, entry in enumerate(pool):
            provider = entry[2].split("/", 1)[0]
            if index == 0 and protected_provider and provider == protected_provider:
                filtered.append(entry)
                continue
            if self.health.is_healthy(provider):
                filtered.append(entry)
            else:
                skipped.append(entry[2])

        if filtered:
            if skipped:
                logger.warning(
                    "Skipping unhealthy providers for %s: %s",
                    pool_name,
                    ", ".join(skipped),
                )
            return filtered

        if skipped:
            logger.warning(
                "All providers for %s are currently unhealthy; failing open with full pool",
                pool_name,
            )
        return pool

    def _compose_call_pool(
        self,
        *,
        use_heavy: bool,
        preferred_provider: Optional[str] = None,
        preferred_model: Optional[str] = None,
    ) -> tuple[List[PoolEntry], str]:
        pool, pool_name = self.pools.compose_pool(
            use_heavy=use_heavy,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
        )
        filtered_pool = self._filter_unhealthy_pool(
            pool,
            pool_name=pool_name,
            preferred_provider=preferred_provider,
        )
        return filtered_pool, pool_name

    async def generate_content(
        self,
        prompt: str,
        max_tokens: int = 4000,
        temperature: float = 0.3,
        timeout: int = 120,
        use_heavy: bool = False,
        preferred_provider: Optional[str] = None,
        preferred_model: Optional[str] = None,
        audit_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate text content. use_heavy=True routes to Heavy pool."""
        pool, pool_name = self._compose_call_pool(
            use_heavy=use_heavy,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
        )
        messages = [{"role": "user", "content": prompt}]
        call_context = audit_context or create_llm_call_context(
            call_kind="content",
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            use_heavy=use_heavy,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
        )
        return await retry_engine.call_pool(
            pool=pool,
            pool_name=pool_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            audit_context=call_context,
            health_callback=self._health_callback,
            usage_callback=self._usage_callback,
        )

    async def generate_content_stream(
        self,
        prompt: str,
        max_tokens: int = 4000,
        temperature: float = 0.3,
        timeout: int = 120,
        use_heavy: bool = False,
        preferred_provider: Optional[str] = None,
        preferred_model: Optional[str] = None,
        audit_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        """Stream text content with pool fallback."""
        pool, pool_name = self.pools.compose_pool(
            use_heavy=use_heavy,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
        )
        messages = [{"role": "user", "content": prompt}]
        call_context = audit_context or create_llm_call_context(
            call_kind="stream",
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            use_heavy=use_heavy,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
        )
        deadline = _time.time() + timeout
        emitted_any = False

        async for chunk in stream_handler.generate_content_stream(
            pool=pool,
            pool_name=pool_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            audit_context=call_context,
            health_callback=self._health_callback,
            usage_callback=self._usage_callback,
        ):
            if chunk:
                emitted_any = True
                yield chunk

        if emitted_any:
            return

        # All stream attempts failed without emitting — fallback to non-streaming
        logger.warning("%s pool stream all failed, falling back to non-streaming", pool_name)
        fallback_text = await self.generate_content(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=max(15, int(deadline - _time.time())),
            use_heavy=use_heavy,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            audit_context=derive_llm_call_context(
                call_context,
                call_kind="content",
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=max(15, int(deadline - _time.time())),
                use_heavy=use_heavy,
                preferred_provider=preferred_provider,
                preferred_model=preferred_model,
                phase="stream_fallback_text",
            ),
        )
        if fallback_text:
            yield fallback_text

    async def generate_json(
        self,
        prompt: str,
        schema: Dict,
        max_tokens: int = 4000,
        temperature: float = 0.2,
        timeout: int = 150,
        use_heavy: bool = False,
        preferred_provider: Optional[str] = None,
        preferred_model: Optional[str] = None,
        audit_context: Optional[Dict[str, Any]] = None,
    ) -> Dict:
        """Generate JSON with two-prompt retry and Fast pool fallback."""
        async def _call_pool_with_tracking(**kwargs):
            kwargs.setdefault("health_callback", self._health_callback)
            kwargs.setdefault("usage_callback", self._usage_callback)
            return await retry_engine.call_pool(**kwargs)

        return await json_handler.generate_json(
            prompt=prompt,
            schema=schema,
            generate_content_fn=self.generate_content,
            call_pool_fn=_call_pool_with_tracking,
            fast_pool=self._filter_unhealthy_pool(
                self.pools.get_pool("Fast"),
                pool_name="Fast",
            ),
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            use_heavy=use_heavy,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            audit_context=audit_context,
            create_context_fn=create_llm_call_context,
            derive_context_fn=derive_llm_call_context,
        )

    # ── Backward-compat internal access ──

    @property
    def _providers(self) -> Dict:
        """Backward-compat: return dict of name -> (client, model)."""
        result = {}
        for info in self.registry.list_all():
            result[info.name] = (info.client, info.model)
        return result

    @property
    def _heavy_pool(self):
        return self.pools.get_pool("Heavy")

    @property
    def _light_pool(self):
        return self.pools.get_pool("Light")

    @property
    def _fast_pool(self):
        return self.pools.get_pool("Fast")


# ── Thread-safe singleton ──

_lock = threading.Lock()
_ai_client: Optional[AIClient] = None


def get_ai_client() -> AIClient:
    global _ai_client
    if _ai_client is None:
        with _lock:
            if _ai_client is None:
                _ai_client = AIClient()
    return _ai_client
