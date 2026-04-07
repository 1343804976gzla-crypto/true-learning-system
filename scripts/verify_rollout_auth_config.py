from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _normalize_origin(value: str | None) -> str | None:
    candidate = str(value or "").strip().rstrip("/")
    if not candidate:
        return None
    if candidate == "*":
        return candidate
    if "://" in candidate:
        return candidate.lower()
    return f"https://{candidate}".lower()


def _hostname_is_local(hostname: str | None) -> bool:
    candidate = str(hostname or "").strip().lower()
    return candidate in {"", "localhost", "127.0.0.1", "::1"}


def _resolve_cors_origins(env: Mapping[str, str]) -> list[str]:
    explicit_origins = [
        origin
        for origin in (_normalize_origin(item) for item in _split_csv(env.get("CORS_ALLOW_ORIGINS")))
        if origin
    ]
    if explicit_origins:
        return list(dict.fromkeys(explicit_origins))

    if _is_truthy(env.get("CORS_ALLOW_ALL_ORIGINS") or "false"):
        return ["*"]

    defaults = [
        "http://localhost",
        "http://127.0.0.1",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://localhost",
        "https://127.0.0.1",
    ]
    public_hostname = str(env.get("TLS_PUBLIC_HOSTNAME") or "").strip().rstrip("/")
    if public_hostname and not _hostname_is_local(public_hostname):
        if "://" in public_hostname:
            defaults.append(public_hostname.lower())
        else:
            defaults.extend([f"https://{public_hostname}".lower(), f"http://{public_hostname}".lower()])
    return list(dict.fromkeys(defaults))


@dataclass
class CheckResult:
    status: str
    key: str
    message: str


@dataclass
class VerificationReport:
    mode: str
    hostname: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [item for item in self.checks if item.status == "fail"]

    @property
    def warnings(self) -> list[CheckResult]:
        return [item for item in self.checks if item.status == "warn"]

    @property
    def ok(self) -> bool:
        return not self.failures


def verify_rollout_auth_config(env: Mapping[str, str] | None = None) -> VerificationReport:
    current_env = dict(env or os.environ)
    hostname = str(current_env.get("TLS_PUBLIC_HOSTNAME") or "").strip()
    mode = "local-rehearsal" if _hostname_is_local(hostname) else "public-rollout"
    report = VerificationReport(mode=mode, hostname=hostname or "localhost")

    def add(status: str, key: str, message: str) -> None:
        report.checks.append(CheckResult(status=status, key=key, message=message))

    if _is_truthy(current_env.get("AUTH_REQUIRE_LOGIN_FOR_STUDENT_ROUTES") or "false"):
        add("pass", "AUTH_REQUIRE_LOGIN_FOR_STUDENT_ROUTES", "student routes require login")
    else:
        add("fail", "AUTH_REQUIRE_LOGIN_FOR_STUDENT_ROUTES", "student-route auth gate is disabled")

    if _is_truthy(current_env.get("AUTH_ENABLE_CSRF_PROTECTION") or "true"):
        add("pass", "AUTH_ENABLE_CSRF_PROTECTION", "CSRF protection is enabled")
    else:
        add("fail", "AUTH_ENABLE_CSRF_PROTECTION", "CSRF protection is disabled")

    cookie_secure = _is_truthy(current_env.get("AUTH_COOKIE_SECURE") or "false")
    if cookie_secure:
        add("pass", "AUTH_COOKIE_SECURE", "auth cookie is marked Secure")
    elif mode == "public-rollout":
        add("fail", "AUTH_COOKIE_SECURE", "public rollout requires AUTH_COOKIE_SECURE=true")
    else:
        add("warn", "AUTH_COOKIE_SECURE", "local rehearsal is using an insecure cookie")

    cookie_samesite = str(current_env.get("AUTH_COOKIE_SAMESITE") or "lax").strip().lower() or "lax"
    if cookie_samesite in {"lax", "strict"}:
        add("pass", "AUTH_COOKIE_SAMESITE", f"cookie SameSite policy is {cookie_samesite}")
    else:
        add("fail", "AUTH_COOKIE_SAMESITE", "cookie SameSite must be lax or strict")

    if _is_truthy(current_env.get("CORS_ALLOW_ALL_ORIGINS") or "false"):
        add("fail", "CORS_ALLOW_ALL_ORIGINS", "wildcard CORS is enabled")
    else:
        add("pass", "CORS_ALLOW_ALL_ORIGINS", "wildcard CORS is disabled")

    resolved_origins = _resolve_cors_origins(current_env)
    if "*" in resolved_origins:
        add("fail", "CORS_ALLOW_ORIGINS", "resolved CORS origin list still contains *")
    else:
        add("pass", "CORS_ALLOW_ORIGINS", f"resolved CORS origins: {', '.join(resolved_origins)}")

    if mode == "public-rollout":
        expected_origin = _normalize_origin(hostname)
        if expected_origin and expected_origin in resolved_origins:
            add("pass", "TLS_PUBLIC_HOSTNAME", f"public hostname {expected_origin} is covered by CORS defaults")
        else:
            add("fail", "TLS_PUBLIC_HOSTNAME", "public hostname is not covered by resolved CORS origins")
    else:
        add("warn", "TLS_PUBLIC_HOSTNAME", "TLS_PUBLIC_HOSTNAME is still local; this is not a public rollout hostname")

    if _is_truthy(current_env.get("SECURITY_HEADERS_ENABLED") or "true"):
        add("pass", "SECURITY_HEADERS_ENABLED", "app-layer security headers are enabled")
    else:
        add("fail", "SECURITY_HEADERS_ENABLED", "security headers are disabled")

    hsts_enabled = _is_truthy(current_env.get("SECURITY_HEADERS_HSTS_ENABLED") or "true")
    if mode == "public-rollout":
        if hsts_enabled:
            add("pass", "SECURITY_HEADERS_HSTS_ENABLED", "HSTS is enabled for public rollout")
        else:
            add("fail", "SECURITY_HEADERS_HSTS_ENABLED", "public rollout should enable HSTS")
    elif hsts_enabled:
        add("pass", "SECURITY_HEADERS_HSTS_ENABLED", "HSTS is enabled")
    else:
        add("warn", "SECURITY_HEADERS_HSTS_ENABLED", "HSTS is disabled during local rehearsal")

    forwarded_allow_ips = str(current_env.get("UVICORN_FORWARDED_ALLOW_IPS") or "").strip()
    if forwarded_allow_ips:
        add("pass", "UVICORN_FORWARDED_ALLOW_IPS", f"forwarded allow list configured as {forwarded_allow_ips}")
    else:
        add("warn", "UVICORN_FORWARDED_ALLOW_IPS", "forwarded allow list is empty")

    return report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify auth/cookie/CORS settings for rollout.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    return parser.parse_args(argv)


def _render_text(report: VerificationReport) -> str:
    lines = [
        f"mode: {report.mode}",
        f"hostname: {report.hostname}",
        f"failures: {len(report.failures)}",
        f"warnings: {len(report.warnings)}",
    ]
    for item in report.checks:
        lines.append(f"[{item.status}] {item.key}: {item.message}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = verify_rollout_auth_config()
    payload = {
        "mode": report.mode,
        "hostname": report.hostname,
        "ok": report.ok,
        "failures": len(report.failures),
        "warnings": len(report.warnings),
        "checks": [asdict(item) for item in report.checks],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_render_text(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
