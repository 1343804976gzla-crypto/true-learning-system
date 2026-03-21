"""Shared type aliases and dataclasses for the API Hub."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import openai

# Core type alias: (client, model, display_name)
PoolEntry = Tuple[openai.OpenAI, str, str]


@dataclass
class ProviderInfo:
    """Registered provider metadata."""

    name: str
    client: openai.OpenAI
    model: str
    base_url: str
    enabled: bool = True

    def pool_entry(self, display: Optional[str] = None) -> PoolEntry:
        return (self.client, self.model, display or f"{self.name}/{self.model}")


@dataclass
class PoolConfig:
    """Parsed pool configuration."""

    name: str
    entries: List[PoolEntry] = field(default_factory=list)


@dataclass
class CallResult:
    """Result of a single model call."""

    text: str
    provider: str
    model: str
    elapsed_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: Optional[str] = None
    response_id: Optional[str] = None


@dataclass
class HealthStatus:
    """Health status snapshot for a provider."""

    provider: str
    healthy: bool
    success_rate: float = 1.0
    avg_latency_ms: int = 0
    sample_count: int = 0
    last_error: Optional[str] = None
    last_success_at: Optional[float] = None
    last_failure_at: Optional[float] = None


# Audit callback type
AuditCallback = Any  # Callable signature varies; kept loose for compatibility
