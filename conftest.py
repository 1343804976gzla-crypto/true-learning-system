from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


_fd, _db_path = tempfile.mkstemp(prefix="tls-pytest-", suffix=".db")
os.close(_fd)

# Keep pytest runs isolated from the user's real learning.db.
os.environ["DATABASE_PATH"] = str(Path(_db_path).resolve())
os.environ["OPENVIKING_SYNC_ENABLED"] = "false"

from learning_tracking_models import create_learning_tracking_tables
from models import init_db


init_db()
create_learning_tracking_tables()

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
