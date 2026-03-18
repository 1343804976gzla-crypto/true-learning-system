"""
Converge repository to a single SQLite database.

Strategy:
1) Resolve active DB from DATABASE_PATH in .env (fallback: data/learning.db).
2) If root learning.db exists and is different:
   - rename root learning.db to a timestamped legacy backup

This removes the ambiguous duplicate path entirely, so the app only uses the
DATABASE_PATH target and old snapshots can no longer silently drift.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv


def resolve_active_db(project_dir: Path) -> Path:
    load_dotenv(project_dir / ".env")
    setting = (os.getenv("DATABASE_PATH") or "").strip()

    if setting.startswith("sqlite:///"):
        return Path(setting.replace("sqlite:///", "", 1)).resolve()
    if setting:
        p = Path(setting)
        if not p.is_absolute():
            p = (project_dir / p).resolve()
        return p
    return (project_dir / "data" / "learning.db").resolve()


def main() -> None:
    project_dir = Path(__file__).resolve().parent
    active = resolve_active_db(project_dir)
    root = (project_dir / "learning.db").resolve()

    if not active.exists():
        raise FileNotFoundError(f"Active DB missing: {active}")

    print(f"[active] {active}")
    print(f"[root  ] {root}")

    if not root.exists():
        print("[skip] root learning.db does not exist.")
        return
    if root == active:
        print("[skip] root DB is already the active DB.")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root_bak = root.with_suffix(root.suffix + f".legacy.detached.{ts}")

    try:
        os.chmod(root, 0o666)
    except Exception:
        pass

    root.rename(root_bak)
    print(f"[detach-root] {root_bak}")
    print("[done] single-db convergence applied; only the active DB remains in use.")


if __name__ == "__main__":
    main()
