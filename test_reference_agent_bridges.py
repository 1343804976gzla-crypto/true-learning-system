from pathlib import Path
from types import SimpleNamespace

from services import agent_memory
from services import mem0_bridge, openmanus_bridge


def test_mem0_bridge_scopes_device_identity_when_user_missing():
    assert mem0_bridge._scope_user_id(None, "device-123") == "device::device-123"
    assert mem0_bridge._scope_user_id("user-abc", "device-123") == "user-abc"


def test_render_openmanus_config_uses_tls_llm_values():
    rendered = openmanus_bridge._render_openmanus_config(
        {
            "DEEPSEEK_MODEL": "deepseek-chat",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
            "DEEPSEEK_API_KEY": "secret-key",
        },
        {},
    )

    assert 'model = "deepseek-chat"' in rendered
    assert 'base_url = "https://api.deepseek.com/v1"' in rendered
    assert 'api_key = "secret-key"' in rendered
    assert '[mcp]' in rendered
    assert '[daytona]' in rendered


def test_openmanus_bridge_sync_writes_config(tmp_path, monkeypatch):
    reference_root = tmp_path / "OpenManus"
    config_dir = reference_root / "config"
    config_dir.mkdir(parents=True)
    python_path = reference_root / ".venv" / "Scripts"
    python_path.mkdir(parents=True)
    (python_path / "python.exe").write_text("", encoding="utf-8")
    (config_dir / "config.toml").write_text(
        "[daytona]\ndaytona_api_key = \"existing\"\ndaytona_server_url = \"https://app.daytona.io/api\"\ndaytona_target = \"us\"\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(openmanus_bridge, "OPENMANUS_REFERENCE_ROOT", reference_root)
    monkeypatch.setattr(openmanus_bridge, "OPENMANUS_PYTHON", python_path / "python.exe")
    monkeypatch.setattr(
        openmanus_bridge,
        "_load_project_env_values",
        lambda: {
            "DEEPSEEK_MODEL": "deepseek-chat",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
            "DEEPSEEK_API_KEY": "real-key",
        },
    )

    status = openmanus_bridge.sync_openmanus_config()
    written = (config_dir / "config.toml").read_text(encoding="utf-8")

    assert status["available"] is True
    assert status["synced"] is True
    assert 'api_key = "real-key"' in written
    assert 'server_reference = "app.mcp.server"' in written
    assert 'daytona_api_key = "existing"' in written


def test_get_openmanus_bridge_status_does_not_rewrite_config(tmp_path, monkeypatch):
    reference_root = tmp_path / "OpenManus"
    config_dir = reference_root / "config"
    config_dir.mkdir(parents=True)
    python_path = reference_root / ".venv" / "Scripts"
    python_path.mkdir(parents=True)
    (python_path / "python.exe").write_text("", encoding="utf-8")
    worker_path = reference_root / "worker.py"
    worker_path.write_text("", encoding="utf-8")
    config_path = config_dir / "config.toml"
    original = (
        "[llm]\n"
        'model = "existing-model"\n'
        'base_url = "https://existing.example/v1"\n'
        'api_key = "existing-key"\n'
    )
    config_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(openmanus_bridge, "OPENMANUS_REFERENCE_ROOT", reference_root)
    monkeypatch.setattr(openmanus_bridge, "OPENMANUS_PYTHON", python_path / "python.exe")
    monkeypatch.setattr(openmanus_bridge, "OPENMANUS_WORKER", worker_path)
    monkeypatch.setattr(
        openmanus_bridge,
        "_load_project_env_values",
        lambda: {
            "DEEPSEEK_MODEL": "deepseek-chat",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
            "DEEPSEEK_API_KEY": "real-key",
        },
    )

    status = openmanus_bridge.get_openmanus_bridge_status()

    assert status["available"] is True
    assert status["synced"] is False
    assert status["has_real_llm_key"] is True
    assert config_path.read_text(encoding="utf-8") == original


def test_openmanus_bridge_run_consult_uses_worker_payload(monkeypatch):
    worker_path = Path("worker.py")
    captured = {}

    monkeypatch.setattr(openmanus_bridge, "OPENMANUS_WORKER", worker_path)
    monkeypatch.setattr(openmanus_bridge, "_is_openmanus_run_available", lambda: True)
    monkeypatch.setattr(openmanus_bridge, "sync_openmanus_config", lambda: {"available": True, "synced": True})

    def _fake_run_worker(command, payload, *, timeout=180):
        captured["command"] = command
        captured["payload"] = payload
        captured["timeout"] = timeout
        return {
            "status": "completed",
            "answer": "OpenManus result",
            "count": 1,
        }

    monkeypatch.setattr(openmanus_bridge, "_run_worker", _fake_run_worker)

    result = openmanus_bridge.run_openmanus_consult(
        "  build a study plan  ",
        max_steps=3,
        timeout_seconds=45,
    )

    assert captured["command"] == "run"
    assert captured["payload"] == {
        "query": "build a study plan",
        "max_steps": 3,
    }
    assert captured["timeout"] == 45
    assert result["answer"] == "OpenManus result"


def test_search_long_term_memories_uses_mem0_when_local_memory_is_empty(monkeypatch):
    class _EmptyQuery:
        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def all(self):
            return []

    monkeypatch.setattr(agent_memory, "_memory_scope_query", lambda db, **kwargs: _EmptyQuery())
    monkeypatch.setattr(
        agent_memory,
        "search_mem0_memories",
        lambda *args, **kwargs: [
            {
                "id": "remote-1",
                "memory": "review linear algebra tomorrow morning",
                "metadata": {
                    "memory_type": "user_goal",
                    "memory_label": "goal",
                    "session_id": "remote-session",
                },
                "created_at": "2026-03-17T11:00:00",
                "score": 0.91,
            }
        ],
    )

    results = agent_memory.search_long_term_memories(
        object(),
        session=SimpleNamespace(id="session-1", user_id="user-1", device_id=None),
        query="what should I review tomorrow",
        limit=5,
    )

    assert len(results) == 1
    assert results[0]["id"] == "remote-1"
    assert results[0]["memory_type"] == "user_goal"
    assert results[0]["summary"] == "review linear algebra tomorrow morning"
