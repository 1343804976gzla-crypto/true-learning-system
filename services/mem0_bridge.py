from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEM0_REFERENCE_ROOT = Path(
    os.getenv("MEM0_REFERENCE_ROOT") or (Path.home() / "reference-projects" / "mem0")
)
MEM0_PYTHON = Path(
    os.getenv("MEM0_PYTHON") or (MEM0_REFERENCE_ROOT / ".venv" / "Scripts" / "python.exe")
)
MEM0_WORKER = PROJECT_ROOT / "scripts" / "mem0_bridge_worker.py"
DEFAULT_MEM0_AGENT_ID = (os.getenv("MEM0_AGENT_ID") or "true-learning-system").strip() or "true-learning-system"


def _bridge_mode() -> str:
    return (os.getenv("MEM0_BRIDGE_ENABLED") or "auto").strip().lower()


def _scope_user_id(user_id: str | None, device_id: str | None) -> str:
    normalized_user_id = str(user_id or "").strip()
    if normalized_user_id:
        return normalized_user_id
    normalized_device_id = str(device_id or "").strip()
    if normalized_device_id:
        return f"device::{normalized_device_id}"
    return ""


def is_mem0_bridge_available() -> bool:
    mode = _bridge_mode()
    if mode in {"0", "false", "no", "off", "disabled"}:
        return False
    return MEM0_REFERENCE_ROOT.exists() and MEM0_PYTHON.exists() and MEM0_WORKER.exists()


def get_mem0_bridge_status() -> Dict[str, Any]:
    return {
        "available": is_mem0_bridge_available(),
        "mode": _bridge_mode(),
        "reference_root": str(MEM0_REFERENCE_ROOT),
        "python_path": str(MEM0_PYTHON),
        "worker_path": str(MEM0_WORKER),
        "agent_id": DEFAULT_MEM0_AGENT_ID,
    }


def _run_worker(command: str, payload: Dict[str, Any], *, timeout: int = 90) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="tls-mem0-bridge-") as temp_dir:
        temp_root = Path(temp_dir)
        payload_path = temp_root / "payload.json"
        result_path = temp_root / "result.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        completed = subprocess.run(
            [
                str(MEM0_PYTHON),
                str(MEM0_WORKER),
                command,
                str(PROJECT_ROOT),
                str(payload_path),
                str(result_path),
            ],
            cwd=str(MEM0_REFERENCE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
        if completed.returncode != 0:
            error_text = (completed.stderr or completed.stdout or f"mem0 bridge exited with code {completed.returncode}").strip()
            raise RuntimeError(error_text[:800] or "mem0 bridge failed")
        if not result_path.exists():
            raise RuntimeError("mem0 bridge did not produce a result file")
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("mem0 bridge returned invalid JSON") from exc
        return data if isinstance(data, dict) else {}


def store_mem0_memory_records(
    records: List[Dict[str, Any]],
    *,
    user_id: str | None,
    device_id: str | None,
    session_id: str | None,
) -> List[Dict[str, Any]]:
    if not is_mem0_bridge_available():
        return []

    scope_user_id = _scope_user_id(user_id, device_id)
    normalized_records = [dict(item or {}) for item in records if str((item or {}).get("summary") or "").strip()]
    if not scope_user_id or not normalized_records:
        return []

    payload = {
        "scope_user_id": scope_user_id,
        "agent_id": DEFAULT_MEM0_AGENT_ID,
        "session_id": session_id,
        "device_id": str(device_id or "").strip() or None,
        "records": normalized_records,
    }
    try:
        result = _run_worker("add", payload, timeout=120)
    except Exception as exc:
        logger.warning("Mem0 bridge add failed: %s", str(exc)[:300])
        return []
    return list(result.get("results") or [])


def search_mem0_memories(
    query: str,
    *,
    user_id: str | None,
    device_id: str | None,
    session_id: str | None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    if not is_mem0_bridge_available():
        return []

    scope_user_id = _scope_user_id(user_id, device_id)
    normalized_query = " ".join(str(query or "").split())
    if not scope_user_id or not normalized_query:
        return []

    payload = {
        "scope_user_id": scope_user_id,
        "agent_id": DEFAULT_MEM0_AGENT_ID,
        "session_id": session_id,
        "device_id": str(device_id or "").strip() or None,
        "query": normalized_query,
        "limit": max(1, min(int(limit or 5), 10)),
    }
    try:
        result = _run_worker("search", payload, timeout=90)
    except Exception as exc:
        logger.warning("Mem0 bridge search failed: %s", str(exc)[:300])
        return []
    return list(result.get("results") or [])
