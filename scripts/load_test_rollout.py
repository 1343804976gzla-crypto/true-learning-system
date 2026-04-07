from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass(frozen=True)
class LoadTestAccount:
    email: str
    password: str
    display_name: str | None = None


@dataclass(frozen=True)
class StepResult:
    step: str
    ok: bool
    status_code: int
    elapsed_ms: float
    detail: str
    account_email: str
    iteration: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a rollout-oriented load test against the student-facing auth, stats, "
            "history, wrong-answer, and tracking flows."
        )
    )
    parser.add_argument("--base-url", default="https://localhost", help="Base application URL.")
    parser.add_argument(
        "--accounts-file",
        default="",
        help="Path to a JSON or CSV file containing test accounts.",
    )
    parser.add_argument("--email", default="", help="Single test account email.")
    parser.add_argument("--password", default="", help="Single test account password.")
    parser.add_argument("--display-name", default="", help="Optional single account display name.")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Maximum concurrent student journeys. Default: 5.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="How many full journeys to run per account. Default: 1.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=20.0,
        help="Per-request timeout in seconds. Default: 20.",
    )
    parser.add_argument(
        "--skip-write-flow",
        action="store_true",
        help="Skip the tracking write flow and run read-only checks.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification for local/self-signed rehearsal environments.",
    )
    parser.add_argument(
        "--report-out",
        default="",
        help="Optional JSON output path for the full rollout load-test report.",
    )
    parser.add_argument(
        "--failure-limit",
        type=int,
        default=20,
        help="Maximum failures to keep in the final report. Default: 20.",
    )
    return parser.parse_args()


def _normalize_base_url(value: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if not normalized:
        raise ValueError("base URL is required")
    if "://" not in normalized:
        raise ValueError("base URL must include a scheme such as https://")
    return normalized


def _derive_base_origin(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("base URL must include a scheme and hostname")
    return f"{parsed.scheme}://{parsed.netloc}"


def _build_http_transport(*, insecure: bool) -> httpx.BaseTransport:
    return httpx.HTTPTransport(retries=0, verify=not insecure)


def _load_accounts_from_payload(payload: Any) -> list[LoadTestAccount]:
    raw_accounts = payload.get("accounts") if isinstance(payload, dict) else payload
    if not isinstance(raw_accounts, list):
        raise ValueError("accounts payload must be a list or an object with an 'accounts' list")

    accounts: list[LoadTestAccount] = []
    for index, item in enumerate(raw_accounts, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"account #{index} must be an object")
        email = str(item.get("email") or "").strip()
        password = str(item.get("password") or "").strip()
        display_name = str(item.get("display_name") or "").strip() or None
        if not email:
            raise ValueError(f"account #{index} is missing email")
        if not password:
            raise ValueError(f"account #{index} is missing password")
        accounts.append(LoadTestAccount(email=email, password=password, display_name=display_name))
    if not accounts:
        raise ValueError("at least one account is required")
    return accounts


def _load_accounts_from_csv(path: Path) -> list[LoadTestAccount]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [dict(row) for row in reader]
    return _load_accounts_from_payload(rows)


def _load_accounts(args: argparse.Namespace) -> list[LoadTestAccount]:
    if args.accounts_file:
        path = Path(args.accounts_file).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"accounts file not found: {path}")
        if path.suffix.lower() == ".csv":
            return _load_accounts_from_csv(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _load_accounts_from_payload(payload)

    email = str(args.email or "").strip()
    password = str(args.password or "").strip()
    display_name = str(args.display_name or "").strip() or None
    if not email or not password:
        raise ValueError("either --accounts-file or both --email and --password are required")
    return [LoadTestAccount(email=email, password=password, display_name=display_name)]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if percentile <= 0:
        return ordered[0]
    if percentile >= 100:
        return ordered[-1]
    rank = max(0, math.ceil((percentile / 100.0) * len(ordered)) - 1)
    return ordered[min(rank, len(ordered) - 1)]


def _round_ms(value: float) -> float:
    return round(float(value or 0.0), 1)


def _truncate_detail(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _extract_error_detail(response: httpx.Response, payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("detail", "message", "error"):
            value = payload.get(key)
            if value:
                return _truncate_detail(value)
    return _truncate_detail(response.text)


def _json_or_none(response: httpx.Response) -> Any:
    content_type = str(response.headers.get("content-type") or "").lower()
    if "application/json" not in content_type:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _request_json(
    client: httpx.Client,
    *,
    step: str,
    method: str,
    path: str,
    account: LoadTestAccount,
    iteration: int,
    expected_statuses: tuple[int, ...] = (200,),
    json_body: dict[str, Any] | None = None,
    same_origin: str | None = None,
) -> tuple[StepResult, Any]:
    started = time.perf_counter()
    try:
        request_headers = {"Accept": "application/json"}
        if same_origin and method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            request_headers["Origin"] = same_origin

        response = client.request(
            method,
            path,
            json=json_body,
            headers=request_headers,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        payload = _json_or_none(response)
        ok = response.status_code in expected_statuses
        detail = "" if ok else _extract_error_detail(response, payload)
        return (
            StepResult(
                step=step,
                ok=ok,
                status_code=int(response.status_code),
                elapsed_ms=elapsed_ms,
                detail=detail,
                account_email=account.email,
                iteration=iteration,
            ),
            payload,
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return (
            StepResult(
                step=step,
                ok=False,
                status_code=0,
                elapsed_ms=elapsed_ms,
                detail=_truncate_detail(exc),
                account_email=account.email,
                iteration=iteration,
            ),
            None,
        )


def _append_or_stop(results: list[StepResult], result: StepResult) -> bool:
    results.append(result)
    return result.ok


def _run_student_journey(
    *,
    base_url: str,
    timeout_seconds: float,
    insecure: bool,
    skip_write_flow: bool,
    account: LoadTestAccount,
    iteration: int,
) -> list[StepResult]:
    results: list[StepResult] = []
    transport = _build_http_transport(insecure=insecure)
    same_origin = _derive_base_origin(base_url)
    with httpx.Client(
        base_url=base_url,
        follow_redirects=False,
        timeout=timeout_seconds,
        transport=transport,
    ) as client:
        health_result, _ = _request_json(
            client,
            step="health",
            method="GET",
            path="/health",
            account=account,
            iteration=iteration,
            same_origin=same_origin,
        )
        if not _append_or_stop(results, health_result):
            return results

        ready_result, _ = _request_json(
            client,
            step="ready",
            method="GET",
            path="/ready",
            account=account,
            iteration=iteration,
            same_origin=same_origin,
        )
        if not _append_or_stop(results, ready_result):
            return results

        me_before_result, _ = _request_json(
            client,
            step="auth_me_before",
            method="GET",
            path="/api/auth/me",
            account=account,
            iteration=iteration,
            same_origin=same_origin,
        )
        if not _append_or_stop(results, me_before_result):
            return results

        login_result, login_payload = _request_json(
            client,
            step="login",
            method="POST",
            path="/api/auth/login",
            account=account,
            iteration=iteration,
            json_body={
                "email": account.email,
                "password": account.password,
            },
            same_origin=same_origin,
        )
        if not _append_or_stop(results, login_result):
            return results
        if not isinstance(login_payload, dict) or not bool(login_payload.get("authenticated")):
            results.append(
                StepResult(
                    step="login_state",
                    ok=False,
                    status_code=200,
                    elapsed_ms=0.0,
                    detail="login response did not authenticate the session",
                    account_email=account.email,
                    iteration=iteration,
                )
            )
            return results

        me_after_result, me_after_payload = _request_json(
            client,
            step="auth_me_after",
            method="GET",
            path="/api/auth/me",
            account=account,
            iteration=iteration,
            same_origin=same_origin,
        )
        if not _append_or_stop(results, me_after_result):
            return results
        if not isinstance(me_after_payload, dict) or not bool(me_after_payload.get("authenticated")):
            results.append(
                StepResult(
                    step="auth_me_state",
                    ok=False,
                    status_code=200,
                    elapsed_ms=0.0,
                    detail="session is not authenticated after login",
                    account_email=account.email,
                    iteration=iteration,
                )
            )
            return results

        for step_name, path in (
            ("auth_sessions", "/api/auth/sessions"),
            ("stats", "/api/stats"),
            ("tracking_stats", "/api/tracking/stats?period=all"),
            ("history_stats", "/api/history/stats"),
            ("wrong_answer_stats", "/api/wrong-answers/stats"),
        ):
            step_result, _ = _request_json(
                client,
                step=step_name,
                method="GET",
                path=path,
                account=account,
                iteration=iteration,
                same_origin=same_origin,
            )
            if not _append_or_stop(results, step_result):
                return results

        if skip_write_flow:
            return results

        session_title = f"rollout-load-test-{iteration}-{uuid.uuid4().hex[:8]}"
        start_result, start_payload = _request_json(
            client,
            step="tracking_session_start",
            method="POST",
            path="/api/tracking/session/start",
            account=account,
            iteration=iteration,
            json_body={
                "session_type": "detail_practice",
                "title": session_title,
            },
            same_origin=same_origin,
        )
        if not _append_or_stop(results, start_result):
            return results

        session_id = str((start_payload or {}).get("session_id") or "").strip()
        if not session_id:
            results.append(
                StepResult(
                    step="tracking_session_state",
                    ok=False,
                    status_code=200,
                    elapsed_ms=0.0,
                    detail="tracking session start did not return a session_id",
                    account_email=account.email,
                    iteration=iteration,
                )
            )
            return results

        question_result, _ = _request_json(
            client,
            step="tracking_question",
            method="POST",
            path=f"/api/tracking/session/{session_id}/question",
            account=account,
            iteration=iteration,
            json_body={
                "question_index": 0,
                "question_type": "A1",
                "difficulty": "basic",
                "question_text": "Load test question",
                "options": {"A": "Option A", "B": "Option B"},
                "correct_answer": "A",
                "user_answer": "A",
                "is_correct": True,
                "confidence": "sure",
                "explanation": "Load test explanation",
                "key_point": "load-test",
                "time_spent_seconds": 3,
            },
            same_origin=same_origin,
        )
        if not _append_or_stop(results, question_result):
            return results

        complete_result, _ = _request_json(
            client,
            step="tracking_complete",
            method="POST",
            path=f"/api/tracking/session/{session_id}/complete",
            account=account,
            iteration=iteration,
            json_body={
                "score": 100,
                "total_questions": 1,
            },
            same_origin=same_origin,
        )
        if not _append_or_stop(results, complete_result):
            return results

        detail_result, _ = _request_json(
            client,
            step="tracking_session_detail",
            method="GET",
            path=f"/api/tracking/session/{session_id}",
            account=account,
            iteration=iteration,
            same_origin=same_origin,
        )
        _append_or_stop(results, detail_result)
        return results


def _summarize_step_results(results: list[StepResult]) -> list[dict[str, Any]]:
    grouped: dict[str, list[StepResult]] = {}
    for result in results:
        grouped.setdefault(result.step, []).append(result)

    summary: list[dict[str, Any]] = []
    for step, items in grouped.items():
        elapsed_values = [item.elapsed_ms for item in items]
        ok_count = sum(1 for item in items if item.ok)
        failure_count = len(items) - ok_count
        status_codes = Counter(str(item.status_code) for item in items)
        summary.append(
            {
                "step": step,
                "requests": len(items),
                "successes": ok_count,
                "failures": failure_count,
                "success_rate": round((ok_count / len(items)) * 100.0, 1) if items else 0.0,
                "min_ms": _round_ms(min(elapsed_values) if elapsed_values else 0.0),
                "avg_ms": _round_ms(sum(elapsed_values) / len(elapsed_values) if elapsed_values else 0.0),
                "p50_ms": _round_ms(_percentile(elapsed_values, 50)),
                "p95_ms": _round_ms(_percentile(elapsed_values, 95)),
                "max_ms": _round_ms(max(elapsed_values) if elapsed_values else 0.0),
                "status_codes": dict(sorted(status_codes.items())),
            }
        )
    return summary


def _build_report(
    *,
    args: argparse.Namespace,
    base_url: str,
    accounts: list[LoadTestAccount],
    results: list[StepResult],
    started_at: float,
    finished_at: float,
) -> dict[str, Any]:
    ok_count = sum(1 for item in results if item.ok)
    failures = [item for item in results if not item.ok]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "account_count": len(accounts),
        "concurrency": int(args.concurrency),
        "rounds": int(args.rounds),
        "write_flow_enabled": not bool(args.skip_write_flow),
        "timeout_seconds": float(args.timeout_seconds),
        "insecure_tls": bool(args.insecure),
        "overall": {
            "steps": len(results),
            "successes": ok_count,
            "failures": len(failures),
            "success_rate": round((ok_count / len(results)) * 100.0, 1) if results else 0.0,
            "wall_time_ms": _round_ms((finished_at - started_at) * 1000.0),
        },
        "steps": _summarize_step_results(results),
        "failures": [asdict(item) for item in failures[: max(1, int(args.failure_limit))]],
    }
    return report


def _print_report(report: dict[str, Any]) -> None:
    overall = report["overall"]
    print("")
    print("Rollout load test complete")
    print(
        "overall: "
        f"steps={overall['steps']} "
        f"successes={overall['successes']} "
        f"failures={overall['failures']} "
        f"success_rate={overall['success_rate']}% "
        f"wall_time_ms={overall['wall_time_ms']}"
    )
    print("")
    for step in report["steps"]:
        statuses = ",".join(f"{code}:{count}" for code, count in step["status_codes"].items())
        print(
            f"{step['step']}: "
            f"count={step['requests']} "
            f"success_rate={step['success_rate']}% "
            f"p50={step['p50_ms']}ms "
            f"p95={step['p95_ms']}ms "
            f"statuses={statuses}"
        )

    failures = report.get("failures") or []
    if failures:
        print("")
        print("failures:")
        for item in failures:
            print(
                f"- account={item['account_email']} "
                f"iteration={item['iteration']} "
                f"step={item['step']} "
                f"status={item['status_code']} "
                f"detail={item['detail']}"
            )


def _write_report(path_value: str, payload: dict[str, Any]) -> Path:
    path = Path(path_value).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    args = _parse_args()
    base_url = _normalize_base_url(args.base_url)
    accounts = _load_accounts(args)
    if args.concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if args.rounds < 1:
        raise ValueError("rounds must be at least 1")
    if args.timeout_seconds <= 0:
        raise ValueError("timeout-seconds must be greater than 0")

    started_at = time.perf_counter()
    all_results: list[StepResult] = []
    with ThreadPoolExecutor(max_workers=int(args.concurrency)) as executor:
        futures = [
            executor.submit(
                _run_student_journey,
                base_url=base_url,
                timeout_seconds=float(args.timeout_seconds),
                insecure=bool(args.insecure),
                skip_write_flow=bool(args.skip_write_flow),
                account=account,
                iteration=iteration,
            )
            for iteration in range(1, int(args.rounds) + 1)
            for account in accounts
        ]
        for future in as_completed(futures):
            all_results.extend(future.result())
    finished_at = time.perf_counter()

    report = _build_report(
        args=args,
        base_url=base_url,
        accounts=accounts,
        results=all_results,
        started_at=started_at,
        finished_at=finished_at,
    )
    _print_report(report)

    if args.report_out:
        report_path = _write_report(args.report_out, report)
        print("")
        print(f"report: {report_path}")

    return 0 if report["overall"]["failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
