from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPENMANUS_REFERENCE_ROOT = Path(
    os.getenv("OPENMANUS_REFERENCE_ROOT") or (Path.home() / "reference-projects" / "OpenManus")
)
OPENMANUS_PYTHON = Path(
    os.getenv("OPENMANUS_PYTHON") or (OPENMANUS_REFERENCE_ROOT / ".venv" / "Scripts" / "python.exe")
)
OPENMANUS_WORKER = PROJECT_ROOT / "scripts" / "openmanus_bridge_worker.py"


def _load_project_env_values() -> Dict[str, str]:
    values: Dict[str, str] = {}
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in (
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL",
        "OPENMANUS_DAYTONA_API_KEY",
        "OPENMANUS_DAYTONA_SERVER_URL",
        "OPENMANUS_DAYTONA_TARGET",
    ):
        if key not in values and os.getenv(key):
            values[key] = str(os.getenv(key) or "").strip()
    return values


def _load_existing_openmanus_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        return {}
    try:
        with config_path.open("rb") as handle:
            return tomllib.load(handle)
    except Exception:
        logger.warning("Failed to parse existing OpenManus config at %s", config_path)
        return {}


def _quote_toml(value: str) -> str:
    escaped = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_openmanus_config(env_values: Dict[str, str], existing: Dict[str, Any]) -> str:
    deepseek_model = env_values.get("DEEPSEEK_MODEL") or "deepseek-chat"
    deepseek_base_url = env_values.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1"
    deepseek_api_key = env_values.get("DEEPSEEK_API_KEY") or "DUMMY_KEY"

    daytona_existing = dict(existing.get("daytona") or {})
    daytona_api_key = env_values.get("OPENMANUS_DAYTONA_API_KEY") or str(daytona_existing.get("daytona_api_key") or "DUMMY_DAYTONA_KEY")
    daytona_server_url = env_values.get("OPENMANUS_DAYTONA_SERVER_URL") or str(daytona_existing.get("daytona_server_url") or "https://app.daytona.io/api")
    daytona_target = env_values.get("OPENMANUS_DAYTONA_TARGET") or str(daytona_existing.get("daytona_target") or "us")

    lines = [
        "[llm]",
        f"model = {_quote_toml(deepseek_model)}",
        f"base_url = {_quote_toml(deepseek_base_url)}",
        f"api_key = {_quote_toml(deepseek_api_key)}",
        "max_tokens = 4096",
        "temperature = 0.0",
        'api_type = "openai"',
        'api_version = ""',
        "",
        "[llm.vision]",
        f"model = {_quote_toml(deepseek_model)}",
        f"base_url = {_quote_toml(deepseek_base_url)}",
        f"api_key = {_quote_toml(deepseek_api_key)}",
        "max_tokens = 4096",
        "temperature = 0.0",
        'api_type = "openai"',
        'api_version = ""',
        "",
        "[mcp]",
        'server_reference = "app.mcp.server"',
        "",
        "[runflow]",
        "use_data_analysis_agent = false",
        "",
        "[daytona]",
        f"daytona_api_key = {_quote_toml(daytona_api_key)}",
        f"daytona_server_url = {_quote_toml(daytona_server_url)}",
        f"daytona_target = {_quote_toml(daytona_target)}",
    ]
    return "\n".join(lines) + "\n"


def is_openmanus_bridge_available() -> bool:
    return OPENMANUS_REFERENCE_ROOT.exists() and OPENMANUS_PYTHON.exists()


def _is_openmanus_run_available() -> bool:
    return is_openmanus_bridge_available() and OPENMANUS_WORKER.exists()


def _run_worker(command: str, payload: Dict[str, Any], *, timeout: int = 180) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="tls-openmanus-bridge-") as temp_dir:
        temp_root = Path(temp_dir)
        payload_path = temp_root / "payload.json"
        result_path = temp_root / "result.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        completed = subprocess.run(
            [
                str(OPENMANUS_PYTHON),
                str(OPENMANUS_WORKER),
                command,
                str(PROJECT_ROOT),
                str(payload_path),
                str(result_path),
            ],
            cwd=str(OPENMANUS_REFERENCE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
        if completed.returncode != 0:
            error_text = (
                completed.stderr
                or completed.stdout
                or f"openmanus bridge exited with code {completed.returncode}"
            ).strip()
            raise RuntimeError(error_text[:1200] or "openmanus bridge failed")
        if not result_path.exists():
            raise RuntimeError("openmanus bridge did not produce a result file")
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("openmanus bridge returned invalid JSON") from exc
        return data if isinstance(data, dict) else {}


def sync_openmanus_config() -> Dict[str, Any]:
    config_path = OPENMANUS_REFERENCE_ROOT / "config" / "config.toml"
    status = {
        "available": is_openmanus_bridge_available(),
        "config_path": str(config_path),
        "reference_root": str(OPENMANUS_REFERENCE_ROOT),
        "python_path": str(OPENMANUS_PYTHON),
        "synced": False,
    }
    if not status["available"]:
        return status

    env_values = _load_project_env_values()
    existing = _load_existing_openmanus_config(config_path)
    rendered = _render_openmanus_config(env_values, existing)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(rendered, encoding="utf-8")
    status.update(
        {
            "synced": True,
            "uses_tls_env": bool(env_values.get("DEEPSEEK_API_KEY")),
            "model": env_values.get("DEEPSEEK_MODEL") or "deepseek-chat",
            "base_url": env_values.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1",
            "synced_at": datetime.now().isoformat(),
        }
    )
    return status


def get_openmanus_bridge_status(sync: bool = False) -> Dict[str, Any]:
    if sync:
        status = sync_openmanus_config()
        env_values = _load_project_env_values()
    else:
        config_path = OPENMANUS_REFERENCE_ROOT / "config" / "config.toml"
        env_values = _load_project_env_values()
        status = {
            "available": is_openmanus_bridge_available(),
            "config_path": str(config_path),
            "reference_root": str(OPENMANUS_REFERENCE_ROOT),
            "python_path": str(OPENMANUS_PYTHON),
            "synced": False,
            "uses_tls_env": bool(env_values.get("DEEPSEEK_API_KEY")),
            "model": env_values.get("DEEPSEEK_MODEL") or "deepseek-chat",
            "base_url": env_values.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1",
        }
    status["has_real_llm_key"] = bool(env_values.get("DEEPSEEK_API_KEY"))
    status["worker_path"] = str(OPENMANUS_WORKER)
    status["run_available"] = _is_openmanus_run_available()
    return status


def run_openmanus_consult(
    query: str,
    *,
    max_steps: int = 4,
    timeout_seconds: int | None = None,
) -> Dict[str, Any]:
    normalized_query = " ".join(str(query or "").split())
    if not normalized_query:
        raise RuntimeError("OpenManus query is empty")
    if not _is_openmanus_run_available():
        raise RuntimeError("OpenManus bridge is not available")

    sync_openmanus_config()
    result = _run_worker(
        "run",
        {
            "query": normalized_query,
            "max_steps": max(1, min(int(max_steps or 4), 8)),
        },
        timeout=int(timeout_seconds or 180),
    )
    if not str(result.get("answer") or "").strip():
        raise RuntimeError("OpenManus returned an empty answer")
    return result
