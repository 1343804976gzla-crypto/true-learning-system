"""Provider registration and credential management."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import openai
from dotenv import load_dotenv

from services.api_hub._types import PoolEntry, ProviderInfo

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

# Provider definitions: name -> (env_prefix, default_base_url)
DEFAULT_PROVIDER_DEFS: Dict[str, Tuple[str, str]] = {
    "deepseek":    ("DEEPSEEK",    "https://api.deepseek.com/v1"),
    "gemini":      ("GEMINI",      "https://api.qingyuntop.top/v1"),
    "siliconflow": ("SILICONFLOW", "https://api.siliconflow.cn/v1"),
    "openrouter":  ("OPENROUTER",  "https://openrouter.ai/api/v1"),
    "qingyun":     ("QINGYUN",     "https://api.qingyuntop.top/v1"),
}


class ProviderRegistry:
    """Thread-safe registry for LLM providers."""

    def __init__(self, provider_defs: Optional[Dict[str, Tuple[str, str]]] = None):
        self._defs = provider_defs or dict(DEFAULT_PROVIDER_DEFS)
        self._providers: Dict[str, ProviderInfo] = {}
        self._model_update_listeners: List[Callable[[str, str], None]] = []
        self._lock = threading.Lock()

    # ── Bulk registration from environment ──

    def register_from_env(self) -> None:
        """Scan environment variables and register all configured providers."""
        for name, (prefix, default_url) in self._defs.items():
            api_key = (os.getenv(f"{prefix}_API_KEY") or "").strip()
            base_url = (os.getenv(f"{prefix}_BASE_URL") or default_url).strip()
            model = (os.getenv(f"{prefix}_MODEL") or "").strip()
            if api_key:
                self.register(name, api_key, base_url, model)

        # Backward compat: qingyun falls back to GEMINI_* env vars
        if "qingyun" not in self._providers:
            qy_key = (os.getenv("QINGYUN_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
            qy_url = (
                os.getenv("QINGYUN_BASE_URL")
                or os.getenv("GEMINI_BASE_URL")
                or self._defs.get("qingyun", ("", ""))[1]
            ).strip()
            qy_model = (os.getenv("QINGYUN_MODEL") or "").strip()
            if qy_key:
                self.register("qingyun", qy_key, qy_url, qy_model)

        # Backward compat: FAST_FALLBACK_* as openrouter alias
        if "openrouter" not in self._providers:
            fast_key = (os.getenv("FAST_FALLBACK_API_KEY") or "").strip()
            fast_url = (os.getenv("FAST_FALLBACK_BASE_URL") or "").strip()
            fast_model = (os.getenv("FAST_FALLBACK_MODEL") or "").strip()
            if fast_key and fast_url:
                self.register("openrouter", fast_key, fast_url, fast_model)

    # ── Individual registration ──

    def register(
        self,
        name: str,
        api_key: str,
        base_url: str,
        model: str,
        enabled: bool = True,
    ) -> None:
        """Register or update a single provider."""
        client = openai.OpenAI(api_key=api_key, base_url=base_url)
        info = ProviderInfo(
            name=name,
            client=client,
            model=model,
            base_url=base_url,
            enabled=enabled,
        )
        with self._lock:
            self._providers[name] = info

    # ── Query ──

    def get(self, name: str) -> Optional[ProviderInfo]:
        with self._lock:
            return self._providers.get(name)

    def list_all(self) -> List[ProviderInfo]:
        with self._lock:
            return list(self._providers.values())

    def list_names(self) -> List[str]:
        with self._lock:
            return list(self._providers.keys())

    def add_model_update_listener(self, callback: Callable[[str, str], None]) -> None:
        self._model_update_listeners.append(callback)

    def is_available(self, name: str) -> bool:
        with self._lock:
            info = self._providers.get(name)
            return info is not None and info.enabled

    # ── Runtime management ──

    def update_key(self, name: str, new_key: str) -> None:
        """Rotate API key without restart."""
        with self._lock:
            info = self._providers.get(name)
            if info is None:
                raise KeyError(f"Provider '{name}' not registered")
            new_client = openai.OpenAI(api_key=new_key, base_url=info.base_url)
            info.client = new_client

    def enable(self, name: str) -> None:
        with self._lock:
            info = self._providers.get(name)
            if info:
                info.enabled = True

    def disable(self, name: str) -> None:
        with self._lock:
            info = self._providers.get(name)
            if info:
                info.enabled = False

    def update_model(self, name: str, new_model: str) -> None:
        with self._lock:
            info = self._providers.get(name)
            if info is None:
                raise KeyError(f"Provider '{name}' not registered")
            info.model = new_model
        for callback in list(self._model_update_listeners):
            try:
                callback(name, new_model)
            except Exception as exc:
                logger.warning("provider model update listener failed for %s: %s", name, exc)

    # ── Backward-compat helpers ──

    def get_client_and_model(self, name: str) -> Optional[Tuple[openai.OpenAI, str]]:
        """Return (client, model) tuple for backward compatibility with old _providers dict."""
        with self._lock:
            info = self._providers.get(name)
            if info is None:
                return None
            return (info.client, info.model)

    def get_credential(self, service_name: str) -> Dict[str, str]:
        """Unified credential access for non-LLM services."""
        prefix = service_name.upper()
        return {
            "api_key": (os.getenv(f"{prefix}_API_KEY") or "").strip(),
            "base_url": (os.getenv(f"{prefix}_BASE_URL") or "").strip(),
            "model": (os.getenv(f"{prefix}_MODEL") or "").strip(),
        }
