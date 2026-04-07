from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts._postgres_cutover import (  # noqa: E402
    DOMAIN_ORDER,
    discover_source_tables,
    normalize_row_for_target,
    require_postgres_target_url,
    resolve_source_databases,
    table_row_count,
    write_json_report,
)

DEFAULT_DEVICE_ID = "local-default"


@dataclass
class TableCopyResult:
    domain: str
    table_name: str
    source_count: int
    attempted_rows: int
    inserted_rows: int
    filtered_rows: int = 0
    skipped: bool = False
    reason: str = ""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import split SQLite runtime data into a PostgreSQL target database."
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
        help="Comma-separated domains to import. Default imports all standard domains.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows per insert batch. Default: 500.",
    )
    parser.add_argument(
        "--truncate-target",
        action="store_true",
        help="Truncate target tables before import. Recommended for a clean cutover rehearsal.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect source tables and counts without writing to PostgreSQL.",
    )
    parser.add_argument(
        "--merge-audit",
        action="store_true",
        help="Merge per-domain audit_change_log tables into the single PostgreSQL audit_change_log table.",
    )
    parser.add_argument(
        "--report-out",
        default="",
        help="Optional JSON file path for the import report.",
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


def _reflect_target_tables(engine: Engine) -> dict[str, Table]:
    metadata = MetaData()
    metadata.reflect(bind=engine)
    return {table.name: table for table in metadata.sorted_tables}


def _reflect_source_tables(engine: Engine) -> list[Table]:
    table_names = discover_source_tables(engine)
    metadata = MetaData()
    metadata.reflect(bind=engine, only=table_names)
    return [table for table in metadata.sorted_tables if table.name != "audit_change_log"]


def _normalize_identity(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _build_storage_scope_key(*, user_id: Any = None, device_id: Any = None) -> str:
    normalized_user = _normalize_identity(user_id)
    normalized_device = _normalize_identity(device_id)
    if normalized_user:
        return f"user:{normalized_user}"
    return f"device:{normalized_device or DEFAULT_DEVICE_ID}"


def _prepare_payload(row: dict[str, Any], target_table: Table) -> dict[str, Any]:
    payload = normalize_row_for_target(row, target_table)
    target_column_names = {column.name for column in target_table.columns}

    if "scope_key" not in target_column_names:
        return payload

    user_id = payload.get("user_id", row.get("user_id"))
    device_id = payload.get("device_id", row.get("device_id"))
    normalized_user = _normalize_identity(user_id)
    normalized_device = _normalize_identity(device_id)

    if not normalized_user and not normalized_device and "device_id" in target_column_names:
        normalized_device = DEFAULT_DEVICE_ID
        payload["device_id"] = normalized_device

    payload["scope_key"] = _build_storage_scope_key(
        user_id=normalized_user,
        device_id=normalized_device,
    )
    return payload


def _load_foreign_key_reference_sets(target_engine: Engine, target_table: Table) -> dict[str, tuple[str, set[Any]]]:
    reference_sets: dict[str, tuple[str, set[Any]]] = {}
    with target_engine.connect() as connection:
        for constraint in target_table.foreign_key_constraints:
            elements = list(constraint.elements)
            if len(elements) != 1:
                continue
            element = elements[0]
            local_column = element.parent.name
            remote_column = element.column.name
            remote_table = element.column.table.name
            values = {
                row[0]
                for row in connection.exec_driver_sql(
                    f'SELECT "{remote_column}" FROM "{remote_table}"'
                ).fetchall()
            }
            reference_sets[local_column] = (f"{remote_table}.{remote_column}", values)
    return reference_sets


def _filter_payload_for_foreign_keys(
    payload: list[dict[str, Any]],
    reference_sets: dict[str, tuple[str, set[Any]]],
) -> tuple[list[dict[str, Any]], int]:
    if not payload or not reference_sets:
        return payload, 0

    filtered: list[dict[str, Any]] = []
    dropped = 0
    for item in payload:
        keep = True
        for column_name, (_, allowed_values) in reference_sets.items():
            value = item.get(column_name)
            if value is None:
                continue
            if value not in allowed_values:
                keep = False
                dropped += 1
                break
        if keep:
            filtered.append(item)
    return filtered, dropped


def _table_insert_statement(target_table: Table) -> Any:
    statement = pg_insert(target_table)
    primary_key_columns = [column.name for column in target_table.primary_key.columns]
    if primary_key_columns:
        statement = statement.on_conflict_do_nothing(index_elements=primary_key_columns)
    return statement


def _copy_table(
    *,
    domain: str,
    source_engine: Engine,
    target_engine: Engine,
    source_table: Table,
    target_table: Table,
    batch_size: int,
) -> TableCopyResult:
    source_count = table_row_count(source_engine, source_table.name)
    if source_count == 0:
        return TableCopyResult(
            domain=domain,
            table_name=source_table.name,
            source_count=0,
            attempted_rows=0,
            inserted_rows=0,
        )

    selected_columns = [
        source_table.c[column.name]
        for column in target_table.columns
        if column.name in source_table.c
    ]
    if not selected_columns:
        return TableCopyResult(
            domain=domain,
            table_name=source_table.name,
            source_count=source_count,
            attempted_rows=0,
            inserted_rows=0,
            skipped=True,
            reason="no_shared_columns",
        )

    statement = select(*selected_columns)
    if source_table.primary_key.columns:
        statement = statement.order_by(*source_table.primary_key.columns)

    insert_statement = _table_insert_statement(target_table)
    attempted_rows = 0
    initial_target_count = table_row_count(target_engine, target_table.name)
    filtered_rows = 0
    foreign_key_reference_sets = _load_foreign_key_reference_sets(target_engine, target_table)

    with source_engine.connect() as source_connection, target_engine.begin() as target_connection:
        result = source_connection.execution_options(stream_results=True).execute(statement)
        while True:
            batch = result.fetchmany(max(1, batch_size))
            if not batch:
                break
            payload = [
                _prepare_payload(dict(row._mapping), target_table)
                for row in batch
            ]
            payload, dropped_rows = _filter_payload_for_foreign_keys(payload, foreign_key_reference_sets)
            filtered_rows += dropped_rows
            if not payload:
                continue
            attempted_rows += len(payload)
            target_connection.execute(insert_statement, payload)

    inserted_rows = max(0, table_row_count(target_engine, target_table.name) - initial_target_count)

    return TableCopyResult(
        domain=domain,
        table_name=source_table.name,
        source_count=source_count,
        attempted_rows=attempted_rows,
        inserted_rows=inserted_rows,
        filtered_rows=filtered_rows,
    )


def _truncate_target_tables(target_engine: Engine, table_names: list[str]) -> None:
    if not table_names:
        return
    joined = ", ".join(f'"{name}"' for name in table_names)
    with target_engine.begin() as connection:
        connection.exec_driver_sql(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE")


def _sync_serial_sequences(target_engine: Engine, target_tables: dict[str, Table], table_names: list[str]) -> None:
    with target_engine.begin() as connection:
        for table_name in table_names:
            table = target_tables.get(table_name)
            if table is None:
                continue
            pk_columns = list(table.primary_key.columns)
            if len(pk_columns) != 1:
                continue
            pk_column = pk_columns[0]
            try:
                python_type = pk_column.type.python_type
            except Exception:
                continue
            if python_type is not int:
                continue
            sequence_name = connection.exec_driver_sql(
                "SELECT pg_get_serial_sequence(%s, %s)",
                (table_name, pk_column.name),
            ).scalar()
            if not sequence_name:
                continue
            max_value = connection.exec_driver_sql(
                f'SELECT COALESCE(MAX("{pk_column.name}"), 0) FROM "{table_name}"'
            ).scalar()
            current_value = int(max_value or 0)
            if current_value <= 0:
                connection.exec_driver_sql(
                    "SELECT setval(%s::regclass, %s, %s)",
                    (sequence_name, 1, False),
                )
            else:
                connection.exec_driver_sql(
                    "SELECT setval(%s::regclass, %s, %s)",
                    (sequence_name, current_value, True),
                )


def _copy_merged_audit(
    *,
    selected_domains: list[str],
    databases: list[Any],
    target_engine: Engine,
    target_tables: dict[str, Table],
    batch_size: int,
) -> TableCopyResult:
    target_table = target_tables.get("audit_change_log")
    if target_table is None:
        return TableCopyResult(
            domain="audit",
            table_name="audit_change_log",
            source_count=0,
            attempted_rows=0,
            inserted_rows=0,
            skipped=True,
            reason="target_table_missing",
        )

    selected_column_names = [column.name for column in target_table.columns if column.name != "id"]
    insert_statement = pg_insert(target_table)
    attempted_rows = 0
    source_count = 0
    initial_target_count = table_row_count(target_engine, target_table.name)

    for database in databases:
        if database.domain not in selected_domains or not database.path.exists():
            continue
        source_engine = create_engine(f"sqlite:///{database.path.as_posix()}")
        try:
            source_tables = set(discover_source_tables(source_engine))
            if "audit_change_log" not in source_tables:
                continue
            source_count += table_row_count(source_engine, "audit_change_log")
            source_table = Table("audit_change_log", MetaData(), autoload_with=source_engine)
            statement = select(
                *[
                    source_table.c[column_name]
                    for column_name in selected_column_names
                    if column_name in source_table.c
                ]
            )
            with source_engine.connect() as source_connection, target_engine.begin() as target_connection:
                result = source_connection.execution_options(stream_results=True).execute(statement)
                while True:
                    batch = result.fetchmany(max(1, batch_size))
                    if not batch:
                        break
                    payload = [
                        normalize_row_for_target(dict(row._mapping), target_table)
                        for row in batch
                    ]
                    attempted_rows += len(payload)
                    target_connection.execute(insert_statement, payload)
        finally:
            source_engine.dispose()

    inserted_rows = max(0, table_row_count(target_engine, target_table.name) - initial_target_count)

    return TableCopyResult(
        domain="audit",
        table_name="audit_change_log",
        source_count=source_count,
        attempted_rows=attempted_rows,
        inserted_rows=inserted_rows,
        filtered_rows=0,
    )


def _count_merged_audit_sources(selected_domains: list[str], databases: list[Any]) -> int:
    total = 0
    for database in databases:
        if database.domain not in selected_domains or not database.path.exists():
            continue
        source_engine = create_engine(f"sqlite:///{database.path.as_posix()}")
        try:
            source_tables = set(discover_source_tables(source_engine))
            if "audit_change_log" in source_tables:
                total += table_row_count(source_engine, "audit_change_log")
        finally:
            source_engine.dispose()
    return total


def main() -> int:
    args = _parse_args()
    selected_domains = _selected_domains(args.domains)
    databases = resolve_source_databases(source_dir=args.source_dir, overrides=_source_overrides(args))
    report: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "dry-run" if args.dry_run else "import",
        "domains": selected_domains,
        "batch_size": int(args.batch_size),
        "truncate_target": bool(args.truncate_target),
        "merge_audit": bool(args.merge_audit),
        "sources": [],
        "tables": [],
    }

    target_engine: Engine | None = None
    target_tables: dict[str, Table] = {}
    if not args.dry_run:
        target_engine = create_engine(require_postgres_target_url(args.target_url))
        target_tables = _reflect_target_tables(target_engine)
        if not target_tables:
            raise RuntimeError("target database has no tables; run Alembic migrations first")

    try:
        if not args.dry_run and args.truncate_target and target_engine is not None:
            truncate_table_names: list[str] = []
            for database in databases:
                if database.domain not in selected_domains or not database.path.exists():
                    continue
                source_engine = create_engine(f"sqlite:///{database.path.as_posix()}")
                try:
                    for source_table in _reflect_source_tables(source_engine):
                        if source_table.name in target_tables:
                            truncate_table_names.append(source_table.name)
                finally:
                    source_engine.dispose()
            if args.merge_audit and "audit_change_log" in target_tables:
                truncate_table_names.append("audit_change_log")
            _truncate_target_tables(target_engine, list(dict.fromkeys(truncate_table_names)))
            print(f"[truncate] cleared {len(set(truncate_table_names))} target tables")

        imported_table_names: list[str] = []
        for database in databases:
            if database.domain not in selected_domains:
                continue
            source_entry: dict[str, Any] = {
                "domain": database.domain,
                "path": str(database.path),
                "exists": database.path.exists(),
                "tables": [],
            }
            report["sources"].append(source_entry)
            if not database.path.exists():
                source_entry["error"] = "missing"
                continue

            source_engine = create_engine(f"sqlite:///{database.path.as_posix()}")
            try:
                source_tables = _reflect_source_tables(source_engine)
                source_entry["tables"] = [table.name for table in source_tables]
                for source_table in source_tables:
                    source_count = table_row_count(source_engine, source_table.name)
                    if args.dry_run:
                        report["tables"].append(
                            {
                                "domain": database.domain,
                                "table": source_table.name,
                                "source_count": source_count,
                                "attempted_rows": 0,
                                "inserted_rows": 0,
                                "filtered_rows": 0,
                                "skipped": False,
                                "reason": "",
                            }
                        )
                        print(f"[dry-run] {database.domain}.{source_table.name}: {source_count} rows")
                        continue

                    target_table = target_tables.get(source_table.name)
                    if target_table is None:
                        result = TableCopyResult(
                            domain=database.domain,
                            table_name=source_table.name,
                            source_count=source_count,
                            attempted_rows=0,
                            inserted_rows=0,
                            skipped=True,
                            reason="target_table_missing",
                        )
                    else:
                        result = _copy_table(
                            domain=database.domain,
                            source_engine=source_engine,
                            target_engine=target_engine,
                            source_table=source_table,
                            target_table=target_table,
                            batch_size=max(1, args.batch_size),
                        )
                        imported_table_names.append(source_table.name)

                    report["tables"].append(
                        {
                            "domain": result.domain,
                            "table": result.table_name,
                            "source_count": result.source_count,
                            "attempted_rows": result.attempted_rows,
                            "inserted_rows": result.inserted_rows,
                            "filtered_rows": result.filtered_rows,
                            "skipped": result.skipped,
                            "reason": result.reason,
                        }
                    )
                    status = "skip" if result.skipped else "copy"
                    suffix = f" ({result.reason})" if result.reason else ""
                    filtered_suffix = f" filtered={result.filtered_rows}" if result.filtered_rows else ""
                    print(
                        f"[{status}] {result.domain}.{result.table_name}: "
                        f"source={result.source_count} attempted={result.attempted_rows} inserted={result.inserted_rows}{filtered_suffix}{suffix}"
                    )
            finally:
                source_engine.dispose()

        if args.merge_audit:
            if args.dry_run:
                audit_source_count = _count_merged_audit_sources(selected_domains, databases)
                report["tables"].append(
                    {
                        "domain": "audit",
                        "table": "audit_change_log",
                        "source_count": audit_source_count,
                        "attempted_rows": 0,
                        "inserted_rows": 0,
                        "filtered_rows": 0,
                        "skipped": False,
                        "reason": "",
                    }
                )
                print(f"[dry-run] audit.audit_change_log: {audit_source_count} rows")
            elif target_engine is not None:
                audit_result = _copy_merged_audit(
                    selected_domains=selected_domains,
                    databases=databases,
                    target_engine=target_engine,
                    target_tables=target_tables,
                    batch_size=max(1, args.batch_size),
                )
                report["tables"].append(
                    {
                        "domain": audit_result.domain,
                        "table": audit_result.table_name,
                        "source_count": audit_result.source_count,
                        "attempted_rows": audit_result.attempted_rows,
                        "inserted_rows": audit_result.inserted_rows,
                        "filtered_rows": audit_result.filtered_rows,
                        "skipped": audit_result.skipped,
                        "reason": audit_result.reason,
                    }
                )
                status = "skip" if audit_result.skipped else "copy"
                suffix = f" ({audit_result.reason})" if audit_result.reason else ""
                filtered_suffix = f" filtered={audit_result.filtered_rows}" if audit_result.filtered_rows else ""
                print(
                    f"[{status}] audit.audit_change_log: "
                    f"source={audit_result.source_count} attempted={audit_result.attempted_rows} inserted={audit_result.inserted_rows}{filtered_suffix}{suffix}"
                )

        if not args.dry_run and target_engine is not None:
            _sync_serial_sequences(target_engine, target_tables, list(dict.fromkeys(imported_table_names)))

        totals = {
            "table_count": len(report["tables"]),
            "source_rows": sum(int(item["source_count"]) for item in report["tables"]),
            "attempted_rows": sum(int(item["attempted_rows"]) for item in report["tables"]),
            "inserted_rows": sum(int(item["inserted_rows"]) for item in report["tables"]),
            "filtered_rows": sum(int(item.get("filtered_rows", 0)) for item in report["tables"]),
            "skipped_tables": sum(1 for item in report["tables"] if item["skipped"]),
        }
        report["totals"] = totals
        print(json.dumps({"totals": totals}, ensure_ascii=False, indent=2))
        if args.report_out:
            report_path = write_json_report(args.report_out, report)
            print(f"[report] {report_path}")
        return 0
    finally:
        if target_engine is not None:
            target_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
