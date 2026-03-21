"""Streaming generation with thread-based SSE bridge and pool fallback."""

from __future__ import annotations

import asyncio
import logging
import threading
import time as _time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from services.api_hub._types import PoolEntry
from services.api_hub.retry_engine import get_client_base_url
from services.llm_audit import log_llm_attempt

logger = logging.getLogger(__name__)


def _extract_stream_delta(chunk: Any) -> str:
    """Extract text delta from a streaming chunk."""
    try:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            return ""
        delta = getattr(choices[0], "delta", None)
        content = getattr(delta, "content", None) if delta is not None else None
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
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


async def call_model_stream(
    client: Any,
    model: str,
    provider_name: str,
    messages: list,
    max_tokens: int,
    temperature: float,
    timeout: int,
) -> AsyncIterator[str]:
    """Stream text from a single model using a thread-based bridge."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Tuple[str, object | None]] = asyncio.Queue()

    def _push(kind: str, payload: object | None = None) -> None:
        asyncio.run_coroutine_threadsafe(queue.put((kind, payload)), loop)

    def _worker() -> None:
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
            for chunk in stream:
                delta_text = _extract_stream_delta(chunk)
                if delta_text:
                    _push("delta", delta_text)
        except Exception as exc:
            _push("error", exc)
        finally:
            _push("done")

    threading.Thread(
        target=_worker,
        name=f"ai-stream-{provider_name}",
        daemon=True,
    ).start()

    deadline = loop.time() + timeout
    while True:
        remaining = max(1.0, deadline - loop.time())
        try:
            kind, payload = await asyncio.wait_for(queue.get(), timeout=remaining)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"{provider_name} stream timeout ({timeout}s)"
            ) from exc

        if kind == "delta":
            yield str(payload or "")
            continue
        if kind == "error":
            if isinstance(payload, Exception):
                raise payload
            raise RuntimeError(f"{provider_name} stream call failed")
        break


async def generate_content_stream(
    pool: List[PoolEntry],
    pool_name: str,
    messages: list,
    max_tokens: int,
    temperature: float,
    timeout: int,
    audit_context: Optional[Dict[str, Any]] = None,
    health_callback: Any = None,
    usage_callback: Any = None,
) -> AsyncIterator[str]:
    """Stream text through a model pool with fallback.

    If all stream attempts fail without emitting data, yields nothing
    (caller should handle fallback to non-streaming).
    """
    deadline = _time.time() + timeout
    last_error: Optional[Exception] = None

    for index, (client, model, display) in enumerate(pool):
        remaining = deadline - _time.time()
        if remaining < 10:
            break

        per_model_time = max(15, int(remaining / max(1, len(pool) - index)))
        emitted = False
        output_chars = 0
        started = _time.time()
        logger.info(
            "=== %s pool stream call: %s, allocated timeout %ds ===",
            pool_name, display, per_model_time,
        )

        try:
            async for chunk in call_model_stream(
                client=client,
                model=model,
                provider_name=display,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=per_model_time,
            ):
                if chunk:
                    emitted = True
                    output_chars += len(chunk)
                    yield chunk

            log_llm_attempt(
                provider=display.split("/", 1)[0],
                model=model,
                provider_display=display,
                base_url=get_client_base_url(client),
                pool_name=pool_name,
                pool_index=index + 1,
                pool_size=len(pool),
                attempt=1,
                status="success",
                elapsed_ms=int((_time.time() - started) * 1000),
                output_chars=output_chars,
                audit_context=audit_context,
            )
            elapsed_ms = int((_time.time() - started) * 1000)
            if usage_callback:
                try:
                    usage_callback(
                        provider=display.split("/", 1)[0],
                        model=model,
                        usage={
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                        },
                        elapsed_ms=elapsed_ms,
                        status="success",
                        pool_name=pool_name,
                        audit_context=audit_context,
                    )
                except Exception as callback_exc:
                    logger.warning("usage callback failed for %s: %s", display, callback_exc)
            if health_callback:
                try:
                    health_callback(display.split("/", 1)[0], True, elapsed_ms)
                except Exception as callback_exc:
                    logger.warning("health callback failed for %s: %s", display, callback_exc)
            return

        except Exception as exc:
            last_error = exc
            elapsed_ms = int((_time.time() - started) * 1000)
            log_llm_attempt(
                provider=display.split("/", 1)[0],
                model=model,
                provider_display=display,
                base_url=get_client_base_url(client),
                pool_name=pool_name,
                pool_index=index + 1,
                pool_size=len(pool),
                attempt=1,
                status="error",
                elapsed_ms=elapsed_ms,
                output_chars=output_chars,
                error=exc,
                audit_context=audit_context,
            )
            if usage_callback:
                try:
                    usage_callback(
                        provider=display.split("/", 1)[0],
                        model=model,
                        usage={
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                        },
                        elapsed_ms=elapsed_ms,
                        status="error",
                        pool_name=pool_name,
                        audit_context=audit_context,
                    )
                except Exception as callback_exc:
                    logger.warning("usage callback failed for %s: %s", display, callback_exc)
            if health_callback:
                try:
                    health_callback(display.split("/", 1)[0], False, elapsed_ms)
                except Exception as callback_exc:
                    logger.warning("health callback failed for %s: %s", display, callback_exc)
            logger.error(
                "❌ %s stream failed: %s: %s",
                display, type(exc).__name__, str(exc)[:120],
            )
            if emitted:
                raise
            continue

    if last_error is not None:
        return

    raise RuntimeError(f"{pool_name} pool stream all failed (unknown error)")
