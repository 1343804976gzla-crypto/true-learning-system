# Rollout Final Rehearsal Checklist

Date: 2026-04-06

See also:

- `docs/50-infra-and-ops/100-student-rollout-plan-2026-04-05.md`
- `docs/50-infra-and-ops/https-reverse-proxy-runbook-2026-04-05.md`
- `docs/50-infra-and-ops/postgresql-cutover-runbook-2026-04-05.md`
- `docs/50-infra-and-ops/backup-and-recovery-runbook-2026-04-05.md`
- `docs/50-infra-and-ops/student-pilot-load-test-runbook-2026-04-05.md`
- `docs/50-infra-and-ops/local-rollout-rehearsal-results-2026-04-06.md`

## Goal

Provide one simple final-host rehearsal sequence before opening the rollout environment to real student traffic.

This checklist is the short operator version:

- verify env and auth/cookie posture
- verify HTTPS proxy and app readiness
- verify login/session/smoke behavior
- verify backup/recovery readiness
- record a clear go or no-go decision

## When to use this checklist

Use this document when all major code changes for the rollout branch are already merged and you are validating the real deployment host or the final staging host that mirrors it closely.

If you are still rehearsing SQLite-to-PostgreSQL import itself, complete `postgresql-cutover-runbook-2026-04-05.md` first, then return here.

## Stop rules

Do not open student traffic if any of the following is true:

- `python scripts\verify_rollout_auth_config.py --json` reports a failure
- `GET /ready` returns anything other than `200`
- login fails for a known-good student account
- cross-site login is not rejected
- backup or restore readiness is unknown
- pilot load test shows repeated `5xx`, `401`, or `403` errors on known-good flows

## Evidence to keep

Save the following before making the go/no-go call:

- rendered `docker compose` config used for the rehearsal
- output of `python scripts\verify_rollout_auth_config.py --json`
- output of `pwsh scripts\smoke_test_production.ps1`
- latest backup file path or backup job record
- pilot load-test report path if a pilot load rehearsal was run

## Checklist

### 1. Freeze the rehearsal window

Confirm:

- the target hostname is final or final-like
- the `.env` file for this host is the rollout version
- student writes are paused if you are doing PostgreSQL cutover in the same window

Recommended env items to verify first:

- `SINGLE_USER_MODE=false`
- `AUTH_REQUIRE_LOGIN_FOR_STUDENT_ROUTES=true`
- `AUTH_COOKIE_SECURE=true`
- `AUTH_ENABLE_CSRF_PROTECTION=true`
- `DATABASE_URL` points to PostgreSQL
- `ALEMBIC_DATABASE_URL` points to PostgreSQL
- split-domain `*_DATABASE_PATH` values are empty or explicitly overridden
- split-domain `*_DATABASE_URL` values point to the same PostgreSQL target
- `TLS_PUBLIC_HOSTNAME` matches the rollout hostname

Run:

```powershell
python scripts\verify_rollout_auth_config.py --json
```

Pass condition:

- no failed checks

### 2. Start the rollout stack and verify the proxy path

Run:

```powershell
docker compose -f docker-compose.yml -f docker-compose.proxy.yml config
docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d
curl http://127.0.0.1:18000/health
curl -I https://your-domain.example.com/health
```

Pass conditions:

- app is reachable on the internal host-only port
- public hostname responds over HTTPS
- direct public access does not depend on exposing the app container itself

### 3. Verify readiness before student traffic

Run:

```powershell
curl http://127.0.0.1:18000/ready
curl https://your-domain.example.com/ready
```

Pass conditions:

- both endpoints return `200`
- no database readiness error appears in logs

If PostgreSQL cutover was part of this window, also confirm:

- import report exists
- verify report exists
- key student tables match the expected counts

### 4. Run the browser-like smoke test

Run with a real non-admin student account:

```powershell
pwsh scripts\smoke_test_production.ps1 `
  -BaseUrl https://your-domain.example.com `
  -Email student@example.com `
  -Password 'change-me'
```

Local self-signed HTTPS rehearsal:

```powershell
.\scripts\smoke_test_production.ps1 `
  -BaseUrl https://localhost:18443 `
  -Email student@example.com `
  -Password 'change-me' `
  -Insecure
```

Pass conditions:

- same-origin login succeeds
- `/api/auth/sessions` is reachable after login
- student dashboard/history endpoints load
- cookie is marked `Secure`
- cross-site login probe is rejected with `403 csrf validation failed`

### 5. Confirm backup and rollback readiness

If a fresh PostgreSQL backup has not been created in the current window, run one now:

```powershell
.\scripts\backup_postgres.ps1
```

Minimum confirmation:

- you know the latest backup file path
- the operator on duty knows which restore command will be used if rollback is needed
- the SQLite cutover backups are still retained if this is the first PostgreSQL rollout day

Optional spot check:

```powershell
.\scripts\restore_postgres.ps1 -InputFile .\data\backups\postgres\your-latest.dump
```

Run the restore only against a safe rehearsal target, never against the live database by accident.

### 6. Run the pilot load rehearsal when promotion is the goal

Use this step before moving from internal rehearsal to 10-student or 30-student rollout.

Example:

```powershell
python scripts\load_test_rollout.py `
  --base-url "https://your-domain.example.com" `
  --accounts-file ".\pilot-accounts.json" `
  --concurrency 10 `
  --rounds 3 `
  --report-out ".\data\logs\pilot-load-test-10-students.json"
```

Local self-signed HTTPS rehearsal:

```powershell
python scripts\load_test_rollout.py `
  --base-url "https://localhost:18443" `
  --email "student@example.com" `
  --password "change-me" `
  --concurrency 1 `
  --rounds 1 `
  --insecure `
  --report-out ".\data\logs\pilot-load-test-local-https.json"
```

Pass conditions:

- `ready` success rate is `100%`
- known-good logins succeed
- no repeated `5xx` on read or write flows
- latency stays within the thresholds defined in `student-pilot-load-test-runbook-2026-04-05.md`

### 7. Record the decision

Write down:

- date and hostname
- operator
- git commit or image tag
- result of env verification
- result of smoke test
- result of backup check
- result of pilot load rehearsal if run
- final decision: `go` or `no-go`

Recommended rule:

- only call `go` when every required step above passed in the same host/env
- otherwise call `no-go`, keep the environment for investigation, and do not partially open student traffic
