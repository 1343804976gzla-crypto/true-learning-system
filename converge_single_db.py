"""
Converge repository to a single SQLite database.

Strategy:
1) Resolve active DB from DATABASE_PATH in .env (fallback: data/learning.db).
2) If root learning.db exists and is different:
   - backup root DB
   - replace root learning.db with a COPY of active DB (snapshot)
   - set root DB as read-only (best effort), so accidental writes are blocked.

This avoids silent divergence while keeping compatibility for legacy scripts
that may still point to ./learning.db.
"""

from __future__ import annotations

import os
import shutil
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
    root_bak = root.with_suffix(root.suffix + f".legacy.bak.{ts}")
    shutil.copy2(root, root_bak)
    print(f"[backup-root] {root_bak}")

    # Ensure root is writable before overwrite
    try:
        os.chmod(root, 0o666)
    except Exception:
        pass

    shutil.copy2(active, root)
    print("[sync] root learning.db replaced by active DB snapshot.")

    # Best effort: mark as read-only to prevent accidental divergence
    try:
        os.chmod(root, 0o444)
        print("[lock] root learning.db set to read-only.")
    except Exception as e:
        print(f"[warn] failed to set read-only: {e}")

    print("[done] single-db convergence applied.")


if __name__ == "__main__":
    main()

