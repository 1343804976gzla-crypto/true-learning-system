from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.llm_audit import (  # noqa: E402
    iter_llm_audit_events,
    resolve_llm_audit_log_path,
    summarize_llm_audit_events,
)


def _join(values: list[str], *, limit: int = 3) -> str:
    if not values:
        return "-"
    clipped = values[:limit]
    suffix = "" if len(values) <= limit else f" (+{len(values) - limit})"
    return ", ".join(clipped) + suffix


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize local LLM audit events.")
    parser.add_argument("--since-minutes", type=int, default=60)
    parser.add_argument("--request-id", type=str, default=None)
    parser.add_argument("--path-contains", type=str, default=None)
    parser.add_argument("--request-limit", type=int, default=10)
    parser.add_argument("--event-limit", type=int, default=20)
    args = parser.parse_args()

    audit_path = resolve_llm_audit_log_path()
    events = list(
        iter_llm_audit_events(
            path=audit_path,
            since_minutes=args.since_minutes,
            request_id=args.request_id,
            path_contains=args.path_contains,
        )
    )

    print(f"Audit file: {audit_path}")
    if not events:
        print("No audit events matched.")
        return 0

    summary = summarize_llm_audit_events(events)
    print(f"Matched events: {summary['total_events']}")
    print()

    print("Requests:")
    for item in summary["requests"][: max(1, args.request_limit)]:
        print(
            f"- {item['request_id']} "
            f"{item.get('http_method') or '-'} {item.get('http_path') or '-'} "
            f"events={item['events']} success={item['successes']} error={item['errors']} "
            f"tokens={item['total_tokens']} providers={_join(item['providers'])}"
        )
        print(
            f"  referer={item.get('referer') or '-'} "
            f"ops={_join(item['operations'], limit=2)} "
            f"window={item.get('started_at') or '-'} -> {item.get('ended_at') or '-'}"
        )
    print()

    print("Providers:")
    for item in summary["providers"][:10]:
        print(
            f"- {(item.get('provider') or '-')}/{(item.get('model') or '-')} "
            f"events={item['events']} success={item['successes']} error={item['errors']} "
            f"tokens={item['total_tokens']}"
        )
    print()

    print("Recent events:")
    for event in events[-max(1, args.event_limit) :]:
        print(
            f"- {event.get('created_at') or '-'} "
            f"{event.get('status') or '-'} "
            f"{event.get('http_method') or '-'} {event.get('http_path') or '-'} "
            f"{event.get('provider_display') or '-'} "
            f"phase={event.get('phase') or '-'} "
            f"tokens={event.get('total_tokens') or 0} "
            f"elapsed_ms={event.get('elapsed_ms') or 0}"
        )
        if event.get("error_type") or event.get("error_message"):
            print(
                f"  error={(event.get('error_type') or '-')}: "
                f"{event.get('error_message') or '-'}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
