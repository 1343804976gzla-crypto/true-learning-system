"""Sliding-window health tracker per provider."""

from __future__ import annotations

import logging
import threading
import time as _time
from collections import deque
from typing import Dict, Optional

from services.api_hub._types import HealthStatus

logger = logging.getLogger(__name__)


class HealthMonitor:
    """Track provider health using a sliding time window."""

    def __init__(
        self,
        window_seconds: int = 120,
        failure_threshold: int = 5,
        db_session_factory=None,
    ):
        self._window = window_seconds
        self._threshold = failure_threshold
        self._db_factory = db_session_factory
        self._records: Dict[str, deque] = {}  # provider -> deque of (timestamp, success, latency_ms)
        self._lock = threading.Lock()

    def record(self, provider: str, success: bool, latency_ms: int = 0) -> None:
        """Record a call result for a provider."""
        now = _time.time()
        with self._lock:
            if provider not in self._records:
                self._records[provider] = deque()
            self._records[provider].append((now, success, latency_ms))
            self._prune(provider, now)
            snapshot = self._build_status_locked(provider)
        self._persist_status(snapshot)

    def _prune(self, provider: str, now: float) -> None:
        """Remove records outside the sliding window (must hold lock)."""
        q = self._records.get(provider)
        if q is None:
            return
        cutoff = now - self._window
        while q and q[0][0] < cutoff:
            q.popleft()

    def _build_status_locked(self, provider: str) -> HealthStatus:
        q = self._records.get(provider)
        if not q:
            return HealthStatus(provider=provider, healthy=True)

        successes = sum(1 for _, s, _ in q if s)
        failures = len(q) - successes
        rate = successes / len(q) if q else 1.0
        latencies = [lat for _, s, lat in q if s and lat > 0]
        avg_lat = int(sum(latencies) / len(latencies)) if latencies else 0

        last_success = None
        last_failure = None
        for ts, success, _ in reversed(q):
            if success and last_success is None:
                last_success = ts
            if not success and last_failure is None:
                last_failure = ts
            if last_success is not None and last_failure is not None:
                break

        healthy = failures < self._threshold
        return HealthStatus(
            provider=provider,
            healthy=healthy,
            success_rate=round(rate, 3),
            avg_latency_ms=avg_lat,
            sample_count=len(q),
            last_success_at=last_success,
            last_failure_at=last_failure,
        )

    def _persist_status(self, snapshot: HealthStatus) -> None:
        if self._db_factory is None or snapshot.sample_count <= 0:
            return
        try:
            from services.api_hub.models import ApiHubHealthLog

            status = "healthy"
            if not snapshot.healthy:
                status = "down" if snapshot.success_rate <= 0 else "degraded"

            db = self._db_factory()
            try:
                db.add(ApiHubHealthLog(
                    provider=snapshot.provider,
                    status=status,
                    success_rate=snapshot.success_rate,
                    avg_latency_ms=snapshot.avg_latency_ms,
                    sample_count=snapshot.sample_count,
                ))
                db.commit()
            finally:
                db.close()
        except Exception as exc:
            logger.warning("Failed to persist health snapshot for %s: %s", snapshot.provider, exc)

    def is_healthy(self, provider: str) -> bool:
        return self.get_status(provider).healthy

    def get_status(self, provider: str) -> HealthStatus:
        now = _time.time()
        with self._lock:
            self._prune(provider, now)
            return self._build_status_locked(provider)

    def get_all_status(self) -> Dict[str, HealthStatus]:
        with self._lock:
            providers = list(self._records.keys())
        return {p: self.get_status(p) for p in providers}

    def make_callback(self):
        """Return a callback function suitable for retry_engine health_callback param."""
        def _cb(provider: str, success: bool, latency_ms: int) -> None:
            self.record(provider, success, latency_ms)
        return _cb
