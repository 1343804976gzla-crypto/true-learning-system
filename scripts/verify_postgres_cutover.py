from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, create_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts._postgres_cutover import (  # noqa: E402
    DOMAIN_ORDER,
    discover_source_tables,
    require_postgres_target_url,
    resolve_source_databases,
    table_row_count,
    write_json_report,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify row-count parity between split SQLite sources and PostgreSQL target."
    )
    parser.add_argument(
        "--target-url",
        default="",
        help="Target PostgreSQL URL. Defaults to DATABASE_URL or ALEMBIC_DATABASE_URL.",
    )
    parser.add_argument(
        "--source-dir",
        default=str(PROJECT_ROOT / "data"),
        help="Directory containing the source SQLite files.",
    )
    parser.add_argument("--source-core-path", default="", help="Override source core DB path.")
    parser.add_argument("--source-content-path", default="", help="Override source content DB path.")
    parser.add_argument("--source-runtime-path", default="", help="Override source runtime DB path.")
    parser.add_argument("--source-review-path", default="", help="Override source review DB path.")
    parser.add_argument("--source-agent-path", default="", help="Override source agent DB path.")
    parser.add_argument("--source-legacy-path", default="", help="Override source legacy DB path.")
    parser.add_argument(
        "--domains",
        default=",".join(DOMAIN_ORDER),
        help="Comma-separated domains to verify. Default verifies all standard domains.",
    )
    parser.add_argument(
        "--merge-audit",
        action="store_true",
        help="Also verify merged audit_change_log row counts.",
    )
    parser.add_argument(
        "--report-out",
        default="",
        help="Optional JSON file path for the verification report.",
    )
    parser.add_argument(
        "--allow-filtered-report",
        default="",
        help="Optional import report path. When provided, target counts may be lower by the table's filtered_rows value.",
    )
    return parser.parse_args()


def _selected_domains(raw_value: str) -> list[str]:
    requested = [item.strip().lower() for item in str(raw_value or "").split(",") if item.strip()]
    invalid = [item for item in requested if item not in DOMAIN_ORDER]
    if invalid:
        raise ValueError(f"unknown domains: {', '.join(invalid)}")
    return requested or list(DOMAIN_ORDER)


def _source_overrides(args: argparse.Namespace) -> dict[str, str]:
    return {
        "core": args.source_core_path,
        "content": args.source_content_path,
        "runtime": args.source_runtime_path,
        "review": args.source_review_path,
        "agent": args.source_agent_path,
        "legacy": args.source_legacy_path,
    }


def _load_filtered_rows(path: str) -> dict[tuple[str, str], int]:
    report_path = Path(path).resolve()
    if not report_path.exists():
        raise FileNotFoundError(f"filtered report not found: {report_path}")
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    filtered_rows: dict[tuple[str, str], int] = {}
    for item in payload.get("tables", []):
        domain = str(item.get("domain") or "").strip().lower()
        table = str(item.get("table") or "").strip()
        if not domain or not table:
            continue
        filtered_rows[(domain, table)] = int(item.get("filtered_rows") or 0)
    return filtered_rows


def main() -> int:
    args = _parse_args()
    selected_domains = _selected_domains(args.domains)
    databases = resolve_source_databases(source_dir=args.source_dir, overrides=_source_overrides(args))
    target_engine = create_engine(require_postgres_target_url(args.target_url))
    mismatches = 0
    filtered_rows_by_table = _load_filtered_rows(args.allow_filtered_report) if args.allow_filtered_report else {}

    report: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "domains": selected_domains,
        "merge_audit": bool(args.merge_audit),
        "allow_filtered_report": str(Path(args.allow_filtered_report).resolve()) if args.allow_filtered_report else "",
        "tables": [],
    }

    try:
        target_metadata = MetaData()
        target_metadata.reflect(bind=target_engine)
        target_table_names = set(target_metadata.tables.keys())

        for database in databases:
            if database.domain not in selected_domains:
                continue
            if not database.path.exists():
                report["tables"].append(
                    {
                        "domain": database.domain,
                        "table": "",
                        "source_count": 0,
                        "target_count": 0,
                        "match": False,
                        "reason": "source_missing",
                    }
                )
                mismatches += 1
                continue

            source_engine = create_engine(f"sqlite:///{database.path.as_posix()}")
            try:
                for table_name in discover_source_tables(source_engine):
                    if table_name == "audit_change_log":
                        continue
                    source_count = table_row_count(source_engine, table_name)
                    filtered_rows = int(filtered_rows_by_table.get((database.domain, table_name), 0))
                    expected_target_count = max(0, source_count - filtered_rows)
                    if table_name not in target_table_names:
                        report["tables"].append(
                            {
                                "domain": database.domain,
                                "table": table_name,
                                "source_count": source_count,
                                "target_count": 0,
                                "expected_target_count": expected_target_count,
                                "filtered_rows": filtered_rows,
                                "match": False,
                                "reason": "target_table_missing",
                            }
                        )
                        mismatches += 1
                        print(f"[mismatch] {database.domain}.{table_name}: target table missing")
                        continue
                    target_count = table_row_count(target_engine, table_name)
                    matched = expected_target_count == target_count
                    report["tables"].append(
                        {
                            "domain": database.domain,
                            "table": table_name,
                            "source_count": source_count,
                            "target_count": target_count,
                            "expected_target_count": expected_target_count,
                            "filtered_rows": filtered_rows,
                            "match": matched,
                            "reason": "",
                        }
                    )
                    if matched:
                        if filtered_rows:
                            print(
                                f"[ok] {database.domain}.{table_name}: "
                                f"source={source_count} target={target_count} filtered={filtered_rows}"
                            )
                        else:
                            print(f"[ok] {database.domain}.{table_name}: {source_count}")
                    else:
                        mismatches += 1
                        print(
                            f"[mismatch] {database.domain}.{table_name}: "
                            f"source={source_count} expected={expected_target_count} target={target_count}"
                        )
            finally:
                source_engine.dispose()

        if args.merge_audit:
            source_audit_total = 0
            for database in databases:
                if database.domain not in selected_domains or not database.path.exists():
                    continue
                source_engine = create_engine(f"sqlite:///{database.path.as_posix()}")
                try:
                    source_tables = set(discover_source_tables(source_engine))
                    if "audit_change_log" in source_tables:
                        source_audit_total += table_row_count(source_engine, "audit_change_log")
                finally:
                    source_engine.dispose()

            if "audit_change_log" not in target_table_names:
                report["tables"].append(
                    {
                        "domain": "audit",
                        "table": "audit_change_log",
                        "source_count": source_audit_total,
                        "target_count": 0,
                        "expected_target_count": source_audit_total,
                        "filtered_rows": 0,
                        "match": False,
                        "reason": "target_table_missing",
                    }
                )
                mismatches += 1
                print("[mismatch] audit.audit_change_log: target table missing")
            else:
                target_audit_total = table_row_count(target_engine, "audit_change_log")
                matched = source_audit_total == target_audit_total
                report["tables"].append(
                    {
                        "domain": "audit",
                        "table": "audit_change_log",
                        "source_count": source_audit_total,
                        "target_count": target_audit_total,
                        "expected_target_count": source_audit_total,
                        "filtered_rows": 0,
                        "match": matched,
                        "reason": "",
                    }
                )
                if matched:
                    print(f"[ok] audit.audit_change_log: {source_audit_total}")
                else:
                    mismatches += 1
                    print(
                        f"[mismatch] audit.audit_change_log: "
                        f"source={source_audit_total} target={target_audit_total}"
                    )

        totals = {
            "table_count": len(report["tables"]),
            "matched_tables": sum(1 for item in report["tables"] if item["match"]),
            "mismatched_tables": sum(1 for item in report["tables"] if not item["match"]),
        }
        report["totals"] = totals
        print(json.dumps({"totals": totals}, ensure_ascii=False, indent=2))
        if args.report_out:
            report_path = write_json_report(args.report_out, report)
            print(f"[report] {report_path}")
        return 0 if mismatches == 0 else 1
    finally:
        target_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
