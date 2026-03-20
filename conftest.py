from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pytest

_fd, _db_path = tempfile.mkstemp(prefix="tls-pytest-", suffix=".db")
os.close(_fd)

# Keep pytest runs isolated from the user's real learning.db.
os.environ["DATABASE_PATH"] = str(Path(_db_path).resolve())
os.environ["CONTENT_DATABASE_PATH"] = str(Path(_db_path).resolve())
os.environ["AGENT_DATABASE_PATH"] = str(Path(_db_path).resolve())
os.environ["RUNTIME_DATABASE_PATH"] = str(Path(_db_path).resolve())
os.environ["REVIEW_DATABASE_PATH"] = str(Path(_db_path).resolve())
os.environ["OPENVIKING_SYNC_ENABLED"] = "false"
os.environ["SINGLE_USER_MODE"] = "true"
os.environ["AGENT_DUPLICATE_WAIT_TIMEOUT_SECONDS"] = "300"

import agent_models  # noqa: F401
import knowledge_upload_models  # noqa: F401

from learning_tracking_models import create_learning_tracking_tables
from knowledge_upload_models import create_knowledge_upload_tables
from models import Base, content_db_engine, engine, runtime_db_engine, init_db
from database.domains import agent_engine, review_engine
from services.data_identity import clear_identity_caches_for_tests


init_db()
create_learning_tracking_tables()
create_knowledge_upload_tables()
clear_identity_caches_for_tests()

_TEST_DEF_PATTERN = re.compile(r"^(?:async\s+def|def)\s+test_|^class\s+Test", re.MULTILINE)


def pytest_ignore_collect(collection_path: Path, config) -> bool:
    path = Path(str(collection_path))
    if path.suffix != ".py" or not path.name.startswith("test_"):
        return False

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="utf-8", errors="ignore")

    return _TEST_DEF_PATTERN.search(content) is None


@pytest.fixture(autouse=True)
def reset_test_database():
    from services import agent_runtime, data_identity

    clear_identity_caches_for_tests()
    engine.dispose()
    content_db_engine.dispose()
    runtime_db_engine.dispose()
    agent_engine.dispose()
    review_engine.dispose()
    db_file = Path(_db_path)
    if db_file.exists():
        db_file.unlink()
    init_db()
    create_learning_tracking_tables()
    create_knowledge_upload_tables()
    data_identity._IDENTITY_SCHEMA_READY = False
    agent_runtime._AGENT_SCHEMA_READY = False
    agent_runtime.ensure_agent_schema()
    clear_identity_caches_for_tests()
    yield
    engine.dispose()
    content_db_engine.dispose()
    runtime_db_engine.dispose()
    agent_engine.dispose()
    review_engine.dispose()
