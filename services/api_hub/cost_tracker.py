"""Token cost calculation, usage recording, and budget alerts."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Default price table: (provider, model) -> {input_per_1k, output_per_1k} in USD
DEFAULT_PRICES: Dict[Tuple[str, str], Dict[str, float]] = {
    ("deepseek", "deepseek-chat"): {"input": 0.0014, "output": 0.0028},
    ("deepseek", "deepseek-reasoner"): {"input": 0.0055, "output": 0.0219},
    ("gemini", "gemini-3-flash-preview"): {"input": 0.00, "output": 0.00},
    ("gemini", "gemini-3.1-pro-preview"): {"input": 0.00125, "output": 0.005},
    ("openrouter", "deepseek-chat"): {"input": 0.0014, "output": 0.0028},
    ("siliconflow", "deepseek-chat"): {"input": 0.001, "output": 0.002},
    ("qingyun", "gemini-3-flash-preview"): {"input": 0.00, "output": 0.00},
}


class CostTracker:
    """Track token usage and calculate costs."""

    def __init__(self, db_session_factory=None):
        self._prices = dict(DEFAULT_PRICES)
        self._db_factory = db_session_factory
        self._budget_alert_usd: Optional[float] = None
        self._lock = threading.Lock()

    def calculate_cost(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Calculate cost in USD for a given call."""
        key = (provider, model)
        with self._lock:
            prices = self._prices.get(key)
        if prices is None:
            return 0.0
        input_cost = (prompt_tokens / 1000) * prices["input"]
        output_cost = (completion_tokens / 1000) * prices["output"]
        return round(input_cost + output_cost, 6)

    def record_usage(
        self,
        provider: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        elapsed_ms: int = 0,
        status: str = "success",
        pool_name: str = "",
        caller: str = "",
        request_path: str = "",
        logical_call_id: str = "",
    ) -> None:
        """Record a usage entry to the database."""
        if self._db_factory is None:
            return

        cost = self.calculate_cost(provider, model, prompt_tokens, completion_tokens)

        try:
            from services.api_hub.models import ApiHubUsage

            db: Session = self._db_factory()
            try:
                entry = ApiHubUsage(
                    logical_call_id=logical_call_id,
                    provider=provider,
                    model=model,
                    pool_name=pool_name,
                    status=status,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens or (prompt_tokens + completion_tokens),
                    cost_usd=cost,
                    elapsed_ms=elapsed_ms,
                    caller=caller,
                    request_path=request_path,
                )
                db.add(entry)
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning("Failed to record usage: %s", e)

        if self._budget_alert_usd is not None:
            daily = self.get_daily_cost()
            if daily >= self._budget_alert_usd:
                logger.warning(
                    "BUDGET ALERT: Daily cost $%.4f exceeds limit $%.4f",
                    daily, self._budget_alert_usd,
                )

    def get_summary(
        self,
        period: str = "24h",
        group_by: str = "provider",
    ) -> Dict[str, Any]:
        """Get usage summary grouped by provider or pool."""
        if self._db_factory is None:
            return {}

        try:
            from services.api_hub.models import ApiHubUsage
            from sqlalchemy import func

            hours = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}.get(period, 24)
            cutoff = _utc_now() - timedelta(hours=hours)

            db: Session = self._db_factory()
            try:
                group_col = getattr(ApiHubUsage, group_by, ApiHubUsage.provider)
                rows = (
                    db.query(
                        group_col,
                        func.count().label("calls"),
                        func.sum(ApiHubUsage.prompt_tokens).label("prompt_tokens"),
                        func.sum(ApiHubUsage.completion_tokens).label("completion_tokens"),
                        func.sum(ApiHubUsage.total_tokens).label("total_tokens"),
                        func.sum(ApiHubUsage.cost_usd).label("total_cost"),
                        func.avg(ApiHubUsage.elapsed_ms).label("avg_latency"),
                    )
                    .filter(ApiHubUsage.created_at >= cutoff.isoformat())
                    .group_by(group_col)
                    .all()
                )
                return {
                    row[0]: {
                        "calls": row[1],
                        "prompt_tokens": row[2] or 0,
                        "completion_tokens": row[3] or 0,
                        "total_tokens": row[4] or 0,
                        "total_cost": round(row[5] or 0, 6),
                        "avg_latency_ms": int(row[6] or 0),
                    }
                    for row in rows
                }
            finally:
                db.close()
        except Exception as e:
            logger.warning("Failed to get summary: %s", e)
            return {}

    def get_daily_cost(self) -> float:
        """Get total cost for the current day."""
        summary = self.get_summary(period="24h", group_by="provider")
        return sum(v.get("total_cost", 0) for v in summary.values())

    def get_timeline(self, period: str = "24h") -> Dict[str, Any]:
        """Get usage timeline grouped into time buckets."""
        if self._db_factory is None:
            return {}

        try:
            from services.api_hub.models import ApiHubUsage
            from sqlalchemy import func

            hours = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}.get(period, 24)
            cutoff = _utc_now() - timedelta(hours=hours)
            bucket_length = 13 if hours <= 168 else 10

            db: Session = self._db_factory()
            try:
                bucket_expr = func.substr(ApiHubUsage.created_at, 1, bucket_length)
                rows = (
                    db.query(
                        bucket_expr.label("bucket"),
                        func.count().label("calls"),
                        func.sum(ApiHubUsage.total_tokens).label("total_tokens"),
                        func.sum(ApiHubUsage.cost_usd).label("total_cost"),
                    )
                    .filter(ApiHubUsage.created_at >= cutoff.isoformat())
                    .group_by(bucket_expr)
                    .order_by(bucket_expr)
                    .all()
                )
                timeline: Dict[str, Any] = {}
                for bucket, calls, total_tokens, total_cost in rows:
                    label = str(bucket or "")
                    if bucket_length == 13 and label:
                        label = f"{label}:00"
                    label = label.replace("T", " ")
                    timeline[label] = {
                        "calls": calls or 0,
                        "total_tokens": total_tokens or 0,
                        "total_cost": round(total_cost or 0, 6),
                    }
                return timeline
            finally:
                db.close()
        except Exception as e:
            logger.warning("Failed to get timeline: %s", e)
            return {}

    def set_budget_alert(self, daily_limit_usd: float) -> None:
        self._budget_alert_usd = daily_limit_usd

    def update_price(
        self,
        provider: str,
        model: str,
        input_per_1k: float,
        output_per_1k: float,
    ) -> None:
        """Update price for a provider/model pair (runtime + optional DB persist)."""
        with self._lock:
            self._prices[(provider, model)] = {
                "input": input_per_1k,
                "output": output_per_1k,
            }

        if self._db_factory is not None:
            try:
                from services.api_hub.models import ApiHubPrice

                db: Session = self._db_factory()
                try:
                    existing = (
                        db.query(ApiHubPrice)
                        .filter_by(provider=provider, model=model)
                        .first()
                    )
                    if existing:
                        existing.input_per_1k = input_per_1k
                        existing.output_per_1k = output_per_1k
                        existing.updated_at = _utc_now().isoformat()
                    else:
                        db.add(
                            ApiHubPrice(
                                provider=provider,
                                model=model,
                                input_per_1k=input_per_1k,
                                output_per_1k=output_per_1k,
                            )
                        )
                    db.commit()
                finally:
                    db.close()
            except Exception as e:
                logger.warning("Failed to persist price update: %s", e)

    def get_prices(self) -> Dict[str, Dict[str, float]]:
        """Return current price table as {provider/model: {input, output}}."""
        with self._lock:
            return {
                f"{p}/{m}": dict(v)
                for (p, m), v in self._prices.items()
            }

    def load_prices_from_db(self) -> None:
        """Load custom prices from database, overriding defaults."""
        if self._db_factory is None:
            return
        try:
            from services.api_hub.models import ApiHubPrice

            db: Session = self._db_factory()
            try:
                rows = db.query(ApiHubPrice).all()
                with self._lock:
                    for row in rows:
                        self._prices[(row.provider, row.model)] = {
                            "input": row.input_per_1k,
                            "output": row.output_per_1k,
                        }
            finally:
                db.close()
        except Exception as e:
            logger.warning("Failed to load prices from DB: %s", e)
