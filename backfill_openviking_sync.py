from __future__ import annotations

import argparse
import json

from services.openviking_sync import (
    backfill_openviking_records,
    bulk_import_openviking_exports,
    get_openviking_sync_config,
    install_openviking_sync_hooks,
    list_supported_openviking_models,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill True Learning System records into OpenViking.")
    parser.add_argument("--model", action="append", dest="models", help="Limit to one or more model names.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum records per model.")
    parser.add_argument("--batch-size", type=int, default=None, help="Batch size for export and upload.")
    parser.add_argument("--export-only", action="store_true", help="Only write local export files, skip OpenViking upload.")
    parser.add_argument("--bulk-import", action="store_true", help="Upload exported per-table directories to OpenViking in bulk.")
    parser.add_argument("--list-models", action="store_true", help="List supported model names and exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.list_models:
        for model in list_supported_openviking_models():
            print(model.__name__)
        return 0

    install_openviking_sync_hooks()
    config = get_openviking_sync_config()

    counts = backfill_openviking_records(
        model_names=args.models,
        limit_per_model=args.limit,
        batch_size=args.batch_size,
        export_only=args.export_only or args.bulk_import,
    )

    if args.bulk_import:
        counts = bulk_import_openviking_exports(model_names=args.models)

    summary = {
        "export_dir": str(config.export_dir),
        "root_uri": config.root_uri,
        "upload_enabled": bool(config.upload_enabled and not args.export_only),
        "bulk_import": bool(args.bulk_import),
        "counts": counts,
        "total": sum(counts.values()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
