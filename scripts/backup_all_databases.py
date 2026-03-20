from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.audit import audit_targets


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create timestamped backups for all active SQLite databases.")
    parser.add_argument("--include-shadow", action="store_true", help="Include the shadow learning.db file in the backup set.")
    parser.add_argument("--output-dir", type=str, default="", help="Optional directory to place all backup files.")
    return parser.parse_args()


def _backup_sqlite(source: Path, target: Path) -> None:
    source_conn = sqlite3.connect(str(source))
    target_conn = sqlite3.connect(str(target))
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()


def main() -> int:
    args = _parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    custom_output_dir = Path(args.output_dir).resolve() if args.output_dir else None

    created: list[Path] = []
    for target in audit_targets():
        if target.name == "shadow" and not args.include_shadow:
            continue
        if target.path is None or not target.path.exists():
            print(f"[skip] {target.name}: path missing")
            continue

        backup_dir = custom_output_dir or (target.path.parent / "backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{target.path.stem}.backup-{timestamp}{target.path.suffix}"
        _backup_sqlite(target.path, backup_path)
        created.append(backup_path)
        print(f"[backup] {target.name}: {backup_path}")

    if not created:
        print("[done] no backup files created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
