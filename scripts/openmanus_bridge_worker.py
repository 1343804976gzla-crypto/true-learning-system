from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from pathlib import Path
from typing import Any, Dict, List


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


def _configure_runtime_env() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")


def _normalize_text(value: Any, *, limit: int = 4000) -> str:
    normalized = " ".join(str(value or "").split())
    return normalized[:limit].strip()


def _tool_name_from_call(tool_call: Any) -> str:
    function = getattr(tool_call, "function", None)
    if function is not None:
        return _normalize_text(getattr(function, "name", ""), limit=120)
    if isinstance(tool_call, dict):
        return _normalize_text(((tool_call.get("function") or {}).get("name") or ""), limit=120)
    return ""


def _build_prompt(query: str) -> str:
    guidance = (
        "You are being invoked as a read-only sub-agent for True Learning System. "
        "Prefer direct reasoning. Only use tools when they are strictly necessary. "
        "Do not modify local files. Do not ask the human for follow-up input. "
        "Answer in Chinese unless the request clearly requires another language. "
        "After your final answer, terminate successfully."
    )
    return f"{guidance}\n\n[Request]\n{query}"


def _install_logger_shim() -> None:
    from loguru import logger as loguru_logger

    loguru_logger.remove()
    loguru_logger.add(sys.stderr, level=os.getenv("OPENMANUS_BRIDGE_LOG_LEVEL", "WARNING"))

    logger_module = types.ModuleType("app.logger")

    def define_log_level(print_level: str = "WARNING", logfile_level: str = "WARNING", name: str | None = None):
        del logfile_level, name
        loguru_logger.remove()
        loguru_logger.add(sys.stderr, level=print_level)
        return loguru_logger

    logger_module.logger = loguru_logger
    logger_module.define_log_level = define_log_level
    sys.modules["app.logger"] = logger_module


async def _run_openmanus(payload: Dict[str, Any]) -> Dict[str, Any]:
    project_root = Path(payload["project_root"]).resolve()
    openmanus_root = Path(payload["openmanus_root"]).resolve()
    query = _normalize_text(payload.get("query") or "", limit=2400)
    max_steps = max(1, min(int(payload.get("max_steps") or 4), 8))
    if not query:
        raise RuntimeError("OpenManus query is empty")

    _load_env_file(project_root)
    _configure_runtime_env()

    sys.path.insert(0, str(openmanus_root))
    _install_logger_shim()

    from app.agent.manus import Manus
    from app.tool import Terminate, ToolCollection

    agent = await Manus.create(max_steps=max_steps)
    agent.available_tools = ToolCollection(Terminate())
    agent.special_tool_names = [Terminate().name]
    agent.max_steps = max_steps
    try:
        run_result = await agent.run(_build_prompt(query))
        assistant_messages: List[str] = []
        tool_names: List[str] = []
        seen_tools: set[str] = set()

        for message in agent.memory.messages:
            role = str(getattr(message, "role", ""))
            if role == "assistant":
                content = _normalize_text(getattr(message, "content", ""), limit=2000)
                if content:
                    assistant_messages.append(content)
                for tool_call in list(getattr(message, "tool_calls", None) or []):
                    tool_name = _tool_name_from_call(tool_call)
                    if tool_name and tool_name not in seen_tools:
                        seen_tools.add(tool_name)
                        tool_names.append(tool_name)
            elif role == "tool":
                tool_name = _normalize_text(getattr(message, "name", ""), limit=120)
                if tool_name and tool_name not in seen_tools:
                    seen_tools.add(tool_name)
                    tool_names.append(tool_name)

        answer = assistant_messages[-1] if assistant_messages else _normalize_text(run_result, limit=2000)
        return {
            "status": "completed",
            "query": query,
            "answer": answer,
            "tool_names": tool_names,
            "steps_executed": int(getattr(agent, "current_step", 0) or 0),
            "message_count": len(agent.memory.messages),
            "assistant_message_count": len(assistant_messages),
            "run_result": _normalize_text(run_result, limit=1200),
            "count": 1 if answer else 0,
        }
    finally:
        await agent.cleanup()


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit("usage: openmanus_bridge_worker.py <command> <project_root> <payload_path> <result_path>")

    command = sys.argv[1].strip().lower()
    project_root = Path(sys.argv[2]).resolve()
    payload_path = Path(sys.argv[3]).resolve()
    result_path = Path(sys.argv[4]).resolve()

    payload = json.loads(payload_path.read_text(encoding="utf-8-sig"))
    payload["project_root"] = str(project_root)
    payload["openmanus_root"] = str(Path.cwd().resolve())

    if command != "run":
        raise SystemExit(f"unsupported command: {command}")

    result = asyncio.run(_run_openmanus(payload))
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
