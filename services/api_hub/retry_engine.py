"""Retry logic, transient error detection, and pool traversal."""

from __future__ import annotations

import asyncio
import logging
import time as _time
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.api_hub._types import PoolEntry
from services.llm_audit import extract_response_usage, log_llm_attempt

logger = logging.getLogger(__name__)


# ── Utility helpers ──


def is_transient_error(exc: Exception) -> bool:
    """Classify whether an exception is a retryable transient error."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    status = getattr(exc, "status_code", None)
    if status in {408, 409, 429, 500, 502, 503, 504}:
        return True
    msg = str(exc).lower()
    keywords = (
        "429", "rate limit", "timeout", "timed out", "temporarily",
        "overload", "upstream", "connection", "try again",
        "稍后再试", "负载", "超时",
    )
    return any(k in msg for k in keywords)


def extract_text_content(response: Any) -> str:
    """Extract text from an OpenAI-compatible chat completion response."""
    try:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) if message is not None else None
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                else:
                    text = getattr(item, "text", None)
                if text:
                    parts.append(str(text))
            return "".join(parts)
    except Exception:
        return ""
    return ""


def get_client_base_url(client: Any) -> Optional[str]:
    """Extract base_url string from an OpenAI client."""
    base_url = getattr(client, "base_url", None)
    return str(base_url) if base_url is not None else None


def _invoke_optional_callback(
    callback: Optional[Callable],
    callback_name: str,
    provider_name: str,
    *args: Any,
    **kwargs: Any,
) -> None:
    if callback is None:
        return
    try:
        callback(*args, **kwargs)
    except Exception as exc:
        logger.warning("%s failed for %s: %s", callback_name, provider_name, exc)


# ── Core retry with audit ──


async def call_model_with_audit(
    client: Any,
    model: str,
    provider_name: str,
    messages: list,
    max_tokens: int,
    temperature: float,
    timeout: int,
    pool_name: str,
    pool_index: int,
    pool_size: int,
    audit_context: Optional[Dict[str, Any]] = None,
    health_callback: Optional[Callable] = None,
    usage_callback: Optional[Callable[..., None]] = None,
) -> str:
    """Call a single model with limited retries (max 2) and audit logging.

    timeout: total time budget for this model (seconds), shared across retries.
    health_callback: optional callable(provider, success, latency_ms) for health tracking.
    """
    provider_key = provider_name.split("/", 1)[0]
    max_attempts = 2
    model_deadline = _time.time() + timeout
    last_error: Optional[Exception] = None
    last_elapsed_ms = 0

    for attempt in range(1, max_attempts + 1):
        remaining = model_deadline - _time.time()
        if remaining < 5:
            logger.debug(
                "%s time budget exhausted (%.0fs), stopping retries",
                provider_name, remaining,
            )
            break

        attempt_timeout = max(10, int(remaining))
        started = _time.time()
        try:
            def _call():
                return client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )

            loop = asyncio.get_event_loop()
            response = await asyncio.wait_for(
                loop.run_in_executor(None, _call),
                timeout=attempt_timeout,
            )
            response_text = extract_text_content(response)
            elapsed_ms = int((_time.time() - started) * 1000)

            finish_reason = None
            try:
                choices = getattr(response, "choices", None) or []
                if choices:
                    finish_reason = getattr(choices[0], "finish_reason", None)
            except Exception:
                pass

            log_llm_attempt(
                provider=provider_key,
                model=model,
                provider_display=provider_name,
                base_url=get_client_base_url(client),
                pool_name=pool_name,
                pool_index=pool_index,
                pool_size=pool_size,
                attempt=attempt,
                status="success",
                elapsed_ms=elapsed_ms,
                output_chars=len(response_text),
                finish_reason=str(finish_reason) if finish_reason is not None else None,
                usage=extract_response_usage(response),
                response_id=getattr(response, "id", None),
                audit_context=audit_context,
            )

            _invoke_optional_callback(
                usage_callback,
                "usage callback",
                provider_name,
                provider=provider_key,
                model=model,
                usage=extract_response_usage(response),
                elapsed_ms=elapsed_ms,
                status="success",
                pool_name=pool_name,
                audit_context=audit_context,
            )
            _invoke_optional_callback(
                health_callback,
                "health callback",
                provider_name,
                provider_key,
                True,
                elapsed_ms,
            )

            return response_text

        except asyncio.TimeoutError:
            last_error = TimeoutError(
                f"{provider_name} request timeout ({attempt_timeout}s)"
            )
        except Exception as e:
            last_error = e

        elapsed_ms = int((_time.time() - started) * 1000)
        last_elapsed_ms = elapsed_ms
        log_llm_attempt(
            provider=provider_key,
            model=model,
            provider_display=provider_name,
            base_url=get_client_base_url(client),
            pool_name=pool_name,
            pool_index=pool_index,
            pool_size=pool_size,
            attempt=attempt,
            status="error",
            elapsed_ms=elapsed_ms,
            output_chars=0,
            error=last_error,
            audit_context=audit_context,
        )

        if (
            attempt < max_attempts
            and last_error
            and is_transient_error(last_error)
        ):
            wait_s = attempt
            logger.info(
                "%s transient error, retry %d in %ds: %s",
                provider_name, attempt, wait_s, last_error,
            )
            await asyncio.sleep(wait_s)
            continue
        break

    if last_error is not None:
        _invoke_optional_callback(
            usage_callback,
            "usage callback",
            provider_name,
            provider=provider_key,
            model=model,
            usage={
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            elapsed_ms=last_elapsed_ms,
            status="error",
            pool_name=pool_name,
            audit_context=audit_context,
        )
        _invoke_optional_callback(
            health_callback,
            "health callback",
            provider_name,
            provider_key,
            False,
            last_elapsed_ms,
        )

    if last_error is None:
        raise RuntimeError(f"{provider_name} call failed (unknown error)")
    raise last_error


# ── Pool traversal ──


async def call_pool(
    pool: List[PoolEntry],
    pool_name: str,
    messages: list,
    max_tokens: int = 4000,
    temperature: float = 0.3,
    timeout: int = 120,
    audit_context: Optional[Dict[str, Any]] = None,
    health_callback: Optional[Callable] = None,
    usage_callback: Optional[Callable[..., None]] = None,
) -> str:
    """Iterate through a model pool, trying each entry with time-budget splitting.

    Falls back to the next model on failure. Raises the last exception if all fail.
    """
    if not pool:
        raise RuntimeError(f"{pool_name} pool is empty, no available models")

    deadline = _time.time() + timeout
    last_error: Optional[Exception] = None

    logger.info("=== %s pool call started ===", pool_name)
    logger.info("Pool size: %d, total timeout: %ds", len(pool), timeout)

    for i, (client, model, display) in enumerate(pool):
        remaining = deadline - _time.time()
        if remaining < 10:
            logger.warning(
                "%s pool: time budget exhausted (%.0fs), skipping remaining %d models",
                pool_name, remaining, len(pool) - i,
            )
            break

        models_left = len(pool) - i
        per_model_time = max(15, int(remaining / models_left))

        logger.info(
            "Trying model %d/%d: %s, allocated timeout: %ds",
            i + 1, len(pool), display, per_model_time,
        )

        try:
            start = _time.time()
            result = await call_model_with_audit(
                client=client,
                model=model,
                provider_name=display,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=per_model_time,
                pool_name=pool_name,
                pool_index=i + 1,
                pool_size=len(pool),
                audit_context=audit_context,
                health_callback=health_callback,
                usage_callback=usage_callback,
            )
            elapsed = _time.time() - start
            logger.info(
                "✅ %s succeeded, elapsed: %.1fs, output: %d chars",
                display, elapsed, len(result),
            )
            if i > 0:
                logger.info("%s pool: model #%d %s took over", pool_name, i + 1, display)
            return result
        except Exception as e:
            last_error = e
            elapsed = _time.time() - start
            logger.error(
                "❌ %s failed (%.1fs): %s: %s",
                display, elapsed, type(e).__name__, str(e)[:100],
            )
            if i < len(pool) - 1:
                logger.info("Switching to next model...")
                continue
            break

    if last_error is not None:
        logger.error("%s pool all failed, last error: %s", pool_name, type(last_error).__name__)
        raise last_error
    raise RuntimeError(f"{pool_name} pool all failed (unknown error)")
