# Local Rollout Rehearsal Results

Date: 2026-04-06

See also:

- `docs/50-infra-and-ops/rollout-final-rehearsal-checklist-2026-04-06.md`
- `docs/50-infra-and-ops/https-reverse-proxy-runbook-2026-04-05.md`
- `docs/50-infra-and-ops/postgresql-cutover-runbook-2026-04-05.md`
- `docs/50-infra-and-ops/backup-and-recovery-runbook-2026-04-05.md`

## Goal

Record the real local rehearsal that was executed on 2026-04-06 before attempting the final rollout host.

This was not the final public-host rehearsal.
It was a local operator rehearsal to prove that:

- PostgreSQL-backed startup works with the current branch
- auth gating and CSRF posture work with both HTTP and HTTPS local rehearsal
- the smoke script and pilot load-test script work on Windows PowerShell
- backup scripting works on the current machine

## Rehearsal scope

Executed locally with:

- PostgreSQL container: `true-learning-system-postgres`
- local HTTP app rehearsal: `http://127.0.0.1:18100`
- local HTTPS proxy rehearsal: `https://localhost:18443`
- local HTTPS proxy container: `tls-local-rehearsal-caddy`
- local HTTPS app container: `tls-local-rehearsal-app`

The HTTPS rehearsal used a local Caddy certificate and the smoke script was run with `-Insecure` because Windows PowerShell 5.1 does not trust the local rehearsal certificate by default.

## What passed

### 1. PostgreSQL-backed startup

Confirmed:

- app startup completed successfully
- `GET /health` returned `200`
- `GET /ready` returned `200`

Important verification:

- the rehearsal env had to clear split-domain `*_DATABASE_PATH` values
- otherwise the app silently stayed in a mixed mode where only the core domain used PostgreSQL

### 2. Student-route auth gate

Confirmed:

- anonymous `GET /api/stats` returned `401`
- anonymous `GET /history` redirected to `/login`
- register/login/session listing worked after enabling `AUTH_REQUIRE_LOGIN_FOR_STUDENT_ROUTES=true`

### 3. CSRF posture

Confirmed:

- cross-site login probe returned `403`
- response body included `{"detail":"csrf validation failed"}`
- same-origin login succeeded

### 4. HTTPS local rehearsal

Confirmed:

- Caddy redirected local HTTP to HTTPS
- `https://localhost:18443/health` returned `200`
- login cookie included `Secure`
- HTTPS smoke test passed end to end

### 5. Pilot load-test rehearsal

Confirmed:

- local HTTP pilot rehearsal passed:
  - `data/logs/pilot-load-test-local-rehearsal-2026-04-06.json`
- local HTTPS pilot rehearsal passed:
  - `data/logs/pilot-load-test-local-https-rehearsal-2026-04-06.json`

### 6. PostgreSQL backup

Confirmed:

- `scripts/backup_postgres.ps1 -UseDockerExec` completed successfully
- generated backup:
  - `data/backups/postgres/true_learning_system.pg-backup-20260406_104035.dump`

## Real problems found during rehearsal

These were not theoretical review findings.
They were hit during the actual local rehearsal and then fixed.

1. The running app image did not actually contain `psycopg`, even though `requirements.txt` already required it.
2. `database/audit.py` used `exec_driver_sql(...)` with `:named` placeholders, which failed under PostgreSQL/psycopg during auth audit writes.
3. `scripts/smoke_test_production.ps1` did not correctly read HTTP error payloads on Windows PowerShell 5.1, so the CSRF check falsely failed even when the backend returned the right `403` JSON.
4. `scripts/smoke_test_production.ps1` also needed `UseBasicParsing` and an explicit insecure TLS path for local self-signed HTTPS rehearsal.
5. `scripts/load_test_rollout.py` did not send same-origin `Origin` headers for unsafe requests and did not pass `--insecure` TLS settings down to the transport layer.
6. `scripts/backup_postgres.ps1` and `scripts/restore_postgres.ps1` used a `Host` parameter name that conflicted with PowerShell's built-in `$Host` variable.
7. the same backup/restore scripts also assumed `ProcessStartInfo.ArgumentList` always existed, which was not safe in the current Windows PowerShell runtime.
8. `docker-compose.yml` only forced `DATABASE_URL` to PostgreSQL and would otherwise leave other domains on SQLite if `*_DATABASE_PATH` stayed populated.

## Artifacts

Relevant local rehearsal artifacts:

- `data/logs/pilot-load-test-local-rehearsal-2026-04-06.json`
- `data/logs/pilot-load-test-local-https-rehearsal-2026-04-06.json`
- `data/backups/postgres/true_learning_system.pg-backup-20260406_104035.dump`

## Validation after fixes

```powershell
python -m pytest tests\infra -q
```

Result:

- `78 passed`

## Remaining gap before public rollout

This local rehearsal reduced risk, but it did not replace the final rollout-host rehearsal.

Still required:

- run the same checklist on the actual rollout host/env
- verify the real public hostname and DNS path
- verify HTTPS without `-Insecure`
- run the first staged student pilot against the final host/env
- record the final go/no-go decision with the real host evidence
