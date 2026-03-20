from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import threading
from contextvars import ContextVar
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]
_request_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "llm_audit_request_context",
    default=None,
)
_write_lock = threading.Lock()
_SKIP_CALLSITE_MODULE_PREFIXES = (
    "services.ai_client",
    "services.llm_audit",
    "asyncio",
    "contextvars",
)


def _env_flag(name: str, default: bool = True) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def llm_audit_enabled() -> bool:
    return _env_flag("LLM_AUDIT_ENABLED", default=True)


def _sanitize_text(value: Any, *, limit: int = 240) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...(+{len(text) - limit} chars)"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def resolve_llm_audit_log_path() -> Path:
    raw = (os.getenv("LLM_AUDIT_LOG_PATH") or "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
    else:
        path = (BASE_DIR / "data" / "llm_audit.jsonl").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def new_llm_audit_id(prefix: str = "llm") -> str:
    return f"{prefix}_{uuid4().hex}"


def set_llm_audit_request_context(
    *,
    request_id: str,
    method: str,
    path: str,
    query: str | None,
    referer: str | None,
    user_agent: str | None,
    user_id: str | None,
    device_id: str | None,
) -> Any:
    context = {
        "request_id": request_id,
        "http_method": _sanitize_text(method, limit=16),
        "http_path": _sanitize_text(path, limit=240),
        "http_query": _sanitize_text(query, limit=512),
        "referer": _sanitize_text(referer, limit=512),
        "user_agent": _sanitize_text(user_agent, limit=240),
        "actor_user_id": _sanitize_text(user_id, limit=120),
        "actor_device_id": _sanitize_text(device_id, limit=120),
    }
    return _request_context.set(context)


def reset_llm_audit_request_context(token: Any) -> None:
    _request_context.reset(token)


def get_llm_audit_request_context() -> dict[str, Any]:
    return dict(_request_context.get() or {})


def infer_llm_call_site(default: str = "unknown") -> str:
    frame = inspect.currentframe()
    try:
        if frame is None:
            return default
        current = frame.f_back
        while current is not None:
            module_name = str(current.f_globals.get("__name__") or "")
            if not module_name.startswith(_SKIP_CALLSITE_MODULE_PREFIXES):
                return f"{module_name}:{current.f_code.co_name}"
            current = current.f_back
    finally:
        del frame
    return default


def _hash_messages(messages: Iterable[dict[str, Any]]) -> str | None:
    try:
        serialized = json.dumps(list(messages), ensure_ascii=False, sort_keys=True)
    except Exception:
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _count_message_chars(messages: Iterable[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            total += len(content)
            continue
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    total += len(str(item.get("text") or ""))
                else:
                    total += len(str(item or ""))
    return total


def create_llm_call_context(
    *,
    call_kind: str,
    messages: Iterable[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    timeout: int,
    use_heavy: bool,
    preferred_provider: str | None,
    preferred_model: str | None,
    operation: str | None = None,
    phase: str = "main",
    logical_call_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = {
        "logical_call_id": logical_call_id or new_llm_audit_id("llm"),
        "call_kind": call_kind,
        "phase": phase,
        "operation": operation or infer_llm_call_site(),
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "timeout_s": int(timeout),
        "use_heavy": bool(use_heavy),
        "preferred_provider": _sanitize_text(preferred_provider, limit=80),
        "preferred_model": _sanitize_text(preferred_model, limit=160),
        "message_count": len(list(messages)) if not isinstance(messages, list) else len(messages),
        "prompt_chars": _count_message_chars(messages),
        "prompt_sha256": _hash_messages(messages),
    }
    if metadata:
        for key, value in metadata.items():
            if value is None:
                continue
            context[key] = value
    return context


def derive_llm_call_context(
    parent_context: dict[str, Any] | None,
    *,
    call_kind: str | None = None,
    messages: Iterable[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout: int | None = None,
    use_heavy: bool | None = None,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    phase: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parent = dict(parent_context or {})
    if messages is None:
        prompt_chars = parent.get("prompt_chars")
        prompt_hash = parent.get("prompt_sha256")
        message_count = parent.get("message_count")
    else:
        prompt_chars = _count_message_chars(messages)
        prompt_hash = _hash_messages(messages)
        message_count = len(list(messages)) if not isinstance(messages, list) else len(messages)

    context = {
        "logical_call_id": str(parent.get("logical_call_id") or new_llm_audit_id("llm")),
        "call_kind": call_kind or str(parent.get("call_kind") or "content"),
        "phase": phase or str(parent.get("phase") or "main"),
        "operation": str(parent.get("operation") or infer_llm_call_site()),
        "max_tokens": int(max_tokens if max_tokens is not None else parent.get("max_tokens") or 0),
        "temperature": float(
            temperature if temperature is not None else parent.get("temperature") or 0.0
        ),
        "timeout_s": int(timeout if timeout is not None else parent.get("timeout_s") or 0),
        "use_heavy": bool(use_heavy if use_heavy is not None else parent.get("use_heavy")),
        "preferred_provider": _sanitize_text(
            preferred_provider if preferred_provider is not None else parent.get("preferred_provider"),
            limit=80,
        ),
        "preferred_model": _sanitize_text(
            preferred_model if preferred_model is not None else parent.get("preferred_model"),
            limit=160,
        ),
        "message_count": message_count,
        "prompt_chars": prompt_chars,
        "prompt_sha256": prompt_hash,
    }
    for key, value in parent.items():
        if key not in context and value is not None:
            context[key] = value
    if metadata:
        for key, value in metadata.items():
            if value is None:
                continue
            context[key] = value
    return context


def extract_response_usage(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }
    getter = usage.get if isinstance(usage, dict) else lambda key: getattr(usage, key, None)
    return {
        "prompt_tokens": getter("prompt_tokens"),
        "completion_tokens": getter("completion_tokens"),
        "total_tokens": getter("total_tokens"),
    }


def _extract_status_code(error: Exception | None) -> int | None:
    if error is None:
        return None
    status_code = getattr(error, "status_code", None)
    if status_code is None:
        return None
    try:
        return int(status_code)
    except Exception:
        return None


def _json_safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
            continue
        if isinstance(value, (list, dict)):
            safe[key] = value
            continue
        safe[key] = _sanitize_text(value, limit=240)
    return safe


def log_llm_attempt(
    *,
    provider: str,
    model: str,
    provider_display: str,
    base_url: str | None,
    pool_name: str,
    pool_index: int,
    pool_size: int,
    attempt: int,
    status: str,
    elapsed_ms: int,
    output_chars: int,
    finish_reason: str | None = None,
    usage: dict[str, int | None] | None = None,
    response_id: str | None = None,
    error: Exception | None = None,
    audit_context: dict[str, Any] | None = None,
) -> None:
    if not llm_audit_enabled():
        return

    try:
        usage_payload = usage or {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }
        request_context = get_llm_audit_request_context()
        payload = {
            "event_type": "llm_attempt",
            "created_at": _now_iso(),
            "provider": _sanitize_text(provider, limit=64),
            "model": _sanitize_text(model, limit=160),
            "provider_display": _sanitize_text(provider_display, limit=240),
            "base_url": _sanitize_text(base_url, limit=240),
            "pool_name": _sanitize_text(pool_name, limit=80),
            "pool_index": int(pool_index),
            "pool_size": int(pool_size),
            "attempt": int(attempt),
            "status": status,
            "elapsed_ms": int(elapsed_ms),
            "output_chars": int(output_chars),
            "finish_reason": _sanitize_text(finish_reason, limit=80),
            "prompt_tokens": usage_payload.get("prompt_tokens"),
            "completion_tokens": usage_payload.get("completion_tokens"),
            "total_tokens": usage_payload.get("total_tokens"),
            "response_id": _sanitize_text(response_id, limit=120),
            "error_type": type(error).__name__ if error is not None else None,
            "error_message": _sanitize_text(str(error), limit=400) if error is not None else None,
            "status_code": _extract_status_code(error),
        }
        payload.update(_json_safe_payload(request_context))
        payload.update(_json_safe_payload(dict(audit_context or {})))
        _append_jsonl(payload)
    except Exception as exc:
        logger.warning("failed to write llm audit event: %s", exc)


def _append_jsonl(payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path = resolve_llm_audit_log_path()
    with _write_lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")


def iter_llm_audit_events(
    *,
    path: str | Path | None = None,
    since_minutes: int | None = None,
    request_id: str | None = None,
    path_contains: str | None = None,
) -> Iterator[dict[str, Any]]:
    source = Path(path).resolve() if path else resolve_llm_audit_log_path()
    if not source.exists():
        return

    cutoff: datetime | None = None
    if since_minutes is not None:
        cutoff = datetime.now() - timedelta(minutes=max(0, since_minutes))

    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            created_at = event.get("created_at")
            if cutoff is not None and created_at:
                try:
                    if datetime.fromisoformat(str(created_at)) < cutoff:
                        continue
                except ValueError:
                    pass
            if request_id and str(event.get("request_id") or "") != request_id:
                continue
            if path_contains and path_contains not in str(event.get("http_path") or ""):
                continue
            yield event


def summarize_llm_audit_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    request_summary: dict[str, dict[str, Any]] = {}
    provider_summary: dict[str, dict[str, Any]] = {}
    event_list = list(events)

    for event in event_list:
        request_key = str(event.get("request_id") or "<unknown>")
        request_entry = request_summary.setdefault(
            request_key,
            {
                "request_id": request_key,
                "http_method": event.get("http_method"),
                "http_path": event.get("http_path"),
                "referer": event.get("referer"),
                "operations": set(),
                "providers": set(),
                "events": 0,
                "successes": 0,
                "errors": 0,
                "total_tokens": 0,
                "started_at": event.get("created_at"),
                "ended_at": event.get("created_at"),
            },
        )
        request_entry["events"] += 1
        request_entry["successes"] += 1 if event.get("status") == "success" else 0
        request_entry["errors"] += 1 if event.get("status") != "success" else 0
        request_entry["total_tokens"] += int(event.get("total_tokens") or 0)
        if event.get("operation"):
            request_entry["operations"].add(str(event["operation"]))
        if event.get("provider_display"):
            request_entry["providers"].add(str(event["provider_display"]))
        request_entry["ended_at"] = event.get("created_at") or request_entry["ended_at"]

        provider_key = f"{event.get('provider') or '<unknown>'}/{event.get('model') or '<unknown>'}"
        provider_entry = provider_summary.setdefault(
            provider_key,
            {
                "provider": event.get("provider"),
                "model": event.get("model"),
                "events": 0,
                "successes": 0,
                "errors": 0,
                "total_tokens": 0,
            },
        )
        provider_entry["events"] += 1
        provider_entry["successes"] += 1 if event.get("status") == "success" else 0
        provider_entry["errors"] += 1 if event.get("status") != "success" else 0
        provider_entry["total_tokens"] += int(event.get("total_tokens") or 0)

    requests = []
    for item in request_summary.values():
        item["operations"] = sorted(item["operations"])
        item["providers"] = sorted(item["providers"])
        requests.append(item)
    requests.sort(key=lambda item: item.get("ended_at") or "", reverse=True)

    providers = list(provider_summary.values())
    providers.sort(key=lambda item: (item["total_tokens"], item["events"]), reverse=True)

    return {
        "total_events": len(event_list),
        "requests": requests,
        "providers": providers,
    }
