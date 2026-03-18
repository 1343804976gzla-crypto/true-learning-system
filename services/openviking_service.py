from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import httpx
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class OpenVikingConfig:
    enabled: bool
    url: str
    api_key: str | None
    agent_id: str | None
    timeout: float
    default_limit: int
    default_target_uri: str


def get_openviking_config() -> OpenVikingConfig:
    return OpenVikingConfig(
        enabled=_env_flag("OPENVIKING_ENABLED", default=False),
        url=(os.getenv("OPENVIKING_URL") or "http://localhost:1933").strip(),
        api_key=(os.getenv("OPENVIKING_API_KEY") or "").strip() or None,
        agent_id=(os.getenv("OPENVIKING_AGENT_ID") or "").strip() or None,
        timeout=max(_env_float("OPENVIKING_TIMEOUT", default=15.0), 1.0),
        default_limit=max(_env_int("OPENVIKING_SEARCH_LIMIT", default=5), 1),
        default_target_uri=(os.getenv("OPENVIKING_TARGET_URI") or "").strip(),
    )


def is_openviking_enabled() -> bool:
    config = get_openviking_config()
    return bool(config.enabled and config.url)


def _build_headers(config: OpenVikingConfig) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if config.api_key:
        headers["X-API-Key"] = config.api_key
    if config.agent_id:
        headers["X-OpenViking-Agent"] = config.agent_id
    return headers


def _extract_result_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("OpenViking returned an invalid payload.")
    if payload.get("status") == "error":
        error_info = payload.get("error") or {}
        message = str(error_info.get("message") or error_info.get("code") or "OpenViking returned an error.")
        raise ValueError(message[:300])
    result = payload.get("result")
    if result is None:
        return {}
    if not isinstance(result, dict):
        raise ValueError("OpenViking returned an invalid result payload.")
    return result


def _sync_request(
    method: str,
    path: str,
    *,
    params: Dict[str, Any] | None = None,
    json_body: Dict[str, Any] | None = None,
    request_timeout: float | None = None,
) -> Dict[str, Any]:
    config = get_openviking_config()
    if not bool(config.enabled and config.url):
        raise RuntimeError("OpenViking is disabled.")

    with httpx.Client(
        base_url=config.url.rstrip("/"),
        headers=_build_headers(config),
        timeout=max(float(request_timeout or config.timeout), 1.0),
    ) as client:
        response = client.request(method.upper(), path, params=params, json=json_body)
        response.raise_for_status()
        return _extract_result_payload(response.json())


def openviking_stat(uri: str, *, missing_ok: bool = False) -> Dict[str, Any] | None:
    try:
        return _sync_request("GET", "/api/v1/fs/stat", params={"uri": uri})
    except httpx.HTTPStatusError as exc:
        if missing_ok and exc.response is not None and exc.response.status_code == 404:
            return None
        raise


def openviking_mkdir(uri: str) -> Dict[str, Any]:
    return _sync_request("POST", "/api/v1/fs/mkdir", json_body={"uri": uri})


def openviking_remove_uri(
    uri: str,
    *,
    recursive: bool = False,
    missing_ok: bool = True,
) -> Dict[str, Any] | None:
    if missing_ok and openviking_stat(uri, missing_ok=True) is None:
        return None
    try:
        return _sync_request(
            "DELETE",
            "/api/v1/fs",
            params={"uri": uri, "recursive": "true" if recursive else "false"},
        )
    except httpx.HTTPStatusError as exc:
        if missing_ok and exc.response is not None:
            if exc.response.status_code == 404:
                return None
            body = exc.response.text.lower()
            if "not found" in body or "no such file" in body:
                return None
        raise


def openviking_add_resource(
    *,
    path: str,
    to: str | None = None,
    parent: str | None = None,
    reason: str = "",
    instruction: str = "",
    wait: bool = False,
    timeout: float | None = None,
    strict: bool = True,
    directly_upload_media: bool = True,
    preserve_structure: bool | None = None,
    request_timeout: float | None = None,
) -> Dict[str, Any]:
    request_body: Dict[str, Any] = {
        "path": path,
        "reason": reason,
        "instruction": instruction,
        "wait": wait,
        "timeout": timeout,
        "strict": strict,
        "directly_upload_media": directly_upload_media,
    }
    if to:
        request_body["to"] = to
    if parent:
        request_body["parent"] = parent
    if preserve_structure is not None:
        request_body["preserve_structure"] = preserve_structure
    return _sync_request(
        "POST",
        "/api/v1/resources",
        json_body=request_body,
        request_timeout=request_timeout,
    )


def _normalize_relations(value: Any) -> List[Dict[str, str]]:
    relations: List[Dict[str, str]] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        uri = str(item.get("uri") or "").strip()
        if not uri:
            continue
        relations.append(
            {
                "uri": uri,
                "abstract": str(item.get("abstract") or "").strip(),
            }
        )
    return relations


def _normalize_contexts(items: Any, context_type: str) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        uri = str(item.get("uri") or "").strip()
        if not uri:
            continue
        normalized.append(
            {
                "context_type": context_type,
                "uri": uri,
                "level": int(item.get("level") or 0),
                "score": float(item.get("score") or 0.0),
                "category": str(item.get("category") or "").strip(),
                "match_reason": str(item.get("match_reason") or "").strip(),
                "abstract": str(item.get("abstract") or "").strip(),
                "overview": str(item.get("overview") or "").strip(),
                "relations": _normalize_relations(item.get("relations")),
            }
        )
    return normalized


def _disabled_payload(query: str, target_uri: str) -> Dict[str, Any]:
    return {
        "status": "disabled",
        "enabled": False,
        "query": query,
        "target_uri": target_uri,
        "count": 0,
        "total": 0,
        "items": [],
        "memories": [],
        "resources": [],
        "skills": [],
        "query_plan": {},
        "error": "OpenViking is disabled.",
    }


def _error_payload(query: str, target_uri: str, error_message: str) -> Dict[str, Any]:
    return {
        "status": "error",
        "enabled": True,
        "query": query,
        "target_uri": target_uri,
        "count": 0,
        "total": 0,
        "items": [],
        "memories": [],
        "resources": [],
        "skills": [],
        "query_plan": {},
        "error": error_message,
    }


async def search_openviking_context(
    *,
    query: str,
    target_uri: str = "",
    limit: int | None = None,
) -> Dict[str, Any]:
    clean_query = " ".join((query or "").split())
    config = get_openviking_config()
    resolved_target_uri = target_uri.strip() or config.default_target_uri
    resolved_limit = max(int(limit or config.default_limit), 1)

    if not clean_query:
        return _error_payload("", resolved_target_uri, "OpenViking query is empty.")
    if not is_openviking_enabled():
        return _disabled_payload(clean_query, resolved_target_uri)

    request_body: Dict[str, Any] = {
        "query": clean_query,
        "target_uri": resolved_target_uri,
        "limit": resolved_limit,
    }

    try:
        async with httpx.AsyncClient(
            base_url=config.url.rstrip("/"),
            headers=_build_headers(config),
            timeout=config.timeout,
        ) as client:
            response = await client.post("/api/v1/search/search", json=request_body)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        message = str(exc)[:300] or "OpenViking request failed."
        logger.warning("OpenViking search failed: %s", message)
        return _error_payload(clean_query, resolved_target_uri, message)

    if not isinstance(payload, dict):
        return _error_payload(clean_query, resolved_target_uri, "OpenViking returned an invalid payload.")
    if payload.get("status") == "error":
        error_info = payload.get("error") or {}
        message = str(error_info.get("message") or error_info.get("code") or "OpenViking returned an error.")[:300]
        logger.warning("OpenViking returned error payload: %s", message)
        return _error_payload(clean_query, resolved_target_uri, message)

    result = payload.get("result") or {}
    memories = _normalize_contexts(result.get("memories"), "memory")
    resources = _normalize_contexts(result.get("resources"), "resource")
    skills = _normalize_contexts(result.get("skills"), "skill")
    items = [*memories, *resources, *skills][:resolved_limit]

    return {
        "status": "ok",
        "enabled": True,
        "query": clean_query,
        "target_uri": resolved_target_uri,
        "count": len(items),
        "total": int(result.get("total") or len(memories) + len(resources) + len(skills)),
        "items": items,
        "memories": memories,
        "resources": resources,
        "skills": skills,
        "query_plan": result.get("query_plan") or {},
        "error": None,
    }
