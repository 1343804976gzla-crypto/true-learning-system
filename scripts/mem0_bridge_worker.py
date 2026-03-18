from __future__ import annotations

import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict, List


def _configure_mem0_runtime_env(project_root: Path) -> Path:
    runtime_root = project_root / "data" / "mem0_runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MEM0_DIR", str(runtime_root))
    os.environ.setdefault("MEM0_TELEMETRY", "false")
    return runtime_root


def _load_env_file(project_root: Path) -> None:
    env_path = project_root / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _build_mem0_config(project_root: Path) -> Dict[str, Any]:
    qdrant_path = project_root / "data" / "mem0_qdrant"
    runtime_root = _configure_mem0_runtime_env(project_root)
    qdrant_path.mkdir(parents=True, exist_ok=True)

    deepseek_api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    siliconflow_api_key = (os.getenv("SILICONFLOW_API_KEY") or "").strip()
    if not deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，无法初始化 Mem0 LLM")
    if not siliconflow_api_key:
        raise RuntimeError("SILICONFLOW_API_KEY 未配置，无法初始化 Mem0 embedder")

    return {
        "history_db_path": str(runtime_root / "history.db"),
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "tls_agent_memory",
                "path": str(qdrant_path),
                "embedding_model_dims": 1024,
                "on_disk": True,
            },
        },
        "llm": {
            "provider": "deepseek",
            "config": {
                "model": (os.getenv("DEEPSEEK_MODEL") or "deepseek-chat").strip(),
                "api_key": deepseek_api_key,
                "deepseek_base_url": (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1").strip(),
                "temperature": 0.0,
                "max_tokens": 256,
            },
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": (os.getenv("MEM0_EMBEDDING_MODEL") or "BAAI/bge-m3").strip(),
                "api_key": siliconflow_api_key,
                "openai_base_url": (os.getenv("SILICONFLOW_BASE_URL") or "https://api.siliconflow.cn/v1").strip(),
                "embedding_dims": 1024,
            },
        },
    }


def _close_mem0(memory: Any) -> None:
    for attr_name in ("vector_store", "_telemetry_vector_store"):
        client = getattr(getattr(memory, attr_name, None), "client", None)
        if client is not None and hasattr(client, "close"):
            with suppress(Exception):
                client.close()


def _add_records(memory: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    scope_user_id = str(payload.get("scope_user_id") or "").strip()
    agent_id = str(payload.get("agent_id") or "").strip() or None
    session_id = str(payload.get("session_id") or "").strip() or None
    device_id = str(payload.get("device_id") or "").strip() or None

    results: List[Dict[str, Any]] = []
    for record in list(payload.get("records") or []):
        summary = " ".join(str((record or {}).get("summary") or "").split())
        if not summary:
            continue
        memory_type = " ".join(str((record or {}).get("memory_type") or "mem0_memory").split()) or "mem0_memory"
        memory_label = " ".join(str((record or {}).get("memory_label") or memory_type).split()) or memory_type
        metadata = {
            "origin": "true-learning-system",
            "memory_type": memory_type,
            "memory_label": memory_label,
            "session_id": session_id,
            "device_id": device_id,
            "source_message_ids": list((record or {}).get("source_message_ids") or []),
        }
        add_result = memory.add(
            [{"role": "user", "content": summary}],
            user_id=scope_user_id,
            agent_id=agent_id,
            infer=False,
            metadata=metadata,
        )
        results.extend(list((add_result or {}).get("results") or []))
    return {"results": _json_safe(results)}


def _search_records(memory: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    scope_user_id = str(payload.get("scope_user_id") or "").strip()
    agent_id = str(payload.get("agent_id") or "").strip() or None
    query = " ".join(str(payload.get("query") or "").split())
    limit = max(1, min(int(payload.get("limit") or 5), 10))
    search_result = memory.search(
        query,
        user_id=scope_user_id,
        agent_id=agent_id,
        limit=limit,
        rerank=False,
    )
    return {"results": _json_safe((search_result or {}).get("results") or [])}


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit("usage: mem0_bridge_worker.py <command> <project_root> <payload_path> <result_path>")

    command = sys.argv[1].strip().lower()
    project_root = Path(sys.argv[2]).resolve()
    payload_path = Path(sys.argv[3]).resolve()
    result_path = Path(sys.argv[4]).resolve()

    _load_env_file(project_root)
    _configure_mem0_runtime_env(project_root)

    payload = json.loads(payload_path.read_text(encoding="utf-8-sig"))
    from mem0 import Memory

    memory = Memory.from_config(_build_mem0_config(project_root))
    try:
        if command == "add":
            result = _add_records(memory, payload)
        elif command == "search":
            result = _search_records(memory, payload)
        else:
            raise RuntimeError(f"unsupported command: {command}")
    finally:
        _close_mem0(memory)

    result_path.write_text(json.dumps(_json_safe(result), ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
