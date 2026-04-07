# True Learning System Production Next Steps Roadmap

Date: 2026-04-04

## Purpose

This document records the remaining work needed to move the project from the current "production-ready foundation" state into a real deployable public multi-user system.

It is intended to be an execution checklist, not only a review summary.

## Current baseline

Already in place:

- multi-user actor-scope isolation for the main quiz/challenge/fusion/tracking paths
- server-side login/session foundation
- PostgreSQL runtime configuration support
- Alembic scaffold and baseline revision
- Docker Compose services for `postgres`, `migrate`, `app`, `neo4j`
- SQLite-to-PostgreSQL migration and verification scripts now exist
- HTTPS reverse proxy assets and secure-header defaults now exist
- auth audit, CSRF checks, emergency admin reset tooling, and rollout auth verification tooling now exist
- PostgreSQL backup/restore scripts, readiness probes, and production smoke test tooling now exist
- student-pilot load-test tooling and a final-host rehearsal checklist now exist
- a real local HTTP/HTTPS rollout rehearsal has now been completed and logged

Still missing:

- final rollout-host PostgreSQL rehearsal and env flip
- final rollout-host HTTPS/cookie/auth rehearsal
- self-service password reset flow
- final audit of older compatibility pages and low-traffic legacy endpoints
- final backup/recovery rehearsal and pilot load validation on the rollout host

## Execution order

1. SQLite to PostgreSQL cutover
2. Reverse proxy / HTTPS / cookie hardening
3. Auth hardening and abuse controls
4. Final legacy endpoint audit
5. Backup, monitoring, and release rehearsal

## P0: SQLite To PostgreSQL Cutover

Status: `in progress`

Goal:

- move all production data from split SQLite files into PostgreSQL
- make PostgreSQL the only write target in deployed environments

Tasks:

- write a migration/import script that reads the current SQLite domain databases and inserts into PostgreSQL in dependency order
- define import order clearly:
  - core/auth
  - content
  - runtime
  - review
  - agent
- handle identity columns and scoped unique keys explicitly during import
- support dry-run mode, row-count report, and idempotent retry behavior where practical
- support pre-import backup snapshot and post-import verification report
- test import against a copy of current real data, not only empty dev data

Completed in the current branch:

- `scripts/migrate_sqlite_to_postgres.py` now exists
- `scripts/verify_postgres_cutover.py` now exists
- a local live-target rehearsal report and verify report now exist
- `docs/50-infra-and-ops/postgresql-cutover-runbook-2026-04-05.md` now documents the repeatable flow

Still open inside this workstream:

- execute one final cutover rehearsal on the actual rollout host/env
- switch the rollout env to PostgreSQL as the only write target
- confirm the PostgreSQL-backed env passes smoke checks on the final host

Suggested file targets:

- `scripts/migrate_sqlite_to_postgres.py`
- `scripts/verify_postgres_cutover.py`
- `docs/50-infra-and-ops/postgres-cutover-runbook-YYYY-MM-DD.md`

Acceptance criteria:

- row counts per table match expected values
- key scoped tables verify cleanly:
  - `concept_mastery`
  - `concept_links`
  - `wrong_answers_v2`
  - `learning_sessions`
  - `question_records`
  - `users`
  - `auth_sessions`
- app boots with `DATABASE_URL=postgresql+psycopg://...`
- target regression suite still passes on PostgreSQL

## P0: Reverse Proxy And HTTPS

Status: `in progress`

Goal:

- put the app behind a proper reverse proxy with TLS
- make auth cookies safe for internet-facing deployment

Tasks:

- add `nginx` or `caddy` service for TLS termination and upstream proxying
- configure secure headers:
  - `Strict-Transport-Security`
  - `X-Content-Type-Options`
  - `X-Frame-Options`
  - `Referrer-Policy`
  - `Content-Security-Policy` baseline if feasible
- set production cookie settings:
  - `AUTH_COOKIE_SECURE=true`
  - `AUTH_COOKIE_SAMESITE=lax` or stricter if flow allows
  - `AUTH_COOKIE_DOMAIN` when needed
- support forwarded headers correctly in FastAPI/uvicorn deployment
- separate public port exposure from internal service ports

Completed in the current branch:

- `docker-compose.proxy.yml` and `deploy/caddy/Caddyfile` now exist
- `.env.example` now includes secure-cookie and CSRF-related rollout defaults
- `docs/50-infra-and-ops/https-reverse-proxy-runbook-2026-04-05.md` now documents the proxy validation flow

Still open inside this workstream:

- bind the real rollout hostname and DNS to the target host
- execute the HTTPS smoke path on the final rollout host
- confirm the public host is only opened through the proxy path

Suggested file targets:

- `docker-compose.yml`
- `deploy/nginx/` or `deploy/caddy/`
- `.env.example`
- `docs/50-infra-and-ops/https-reverse-proxy-runbook-YYYY-MM-DD.md`

Acceptance criteria:

- HTTPS access works end-to-end
- login cookie is `Secure`
- direct app container port does not need to be internet-exposed

## P1: Auth Hardening

Status: `in progress`

Goal:

- make login/session handling resilient enough for public multi-user use

Tasks:

- add logout-all / session revocation support
- add refresh/renewal policy review and hard expiration rules
- add password reset flow or explicit admin reset procedure
- add login/register rate limiting
- add account lockout or progressive throttling for repeated failures
- add audit visibility for auth events
- review CSRF exposure for any cookie-authenticated write endpoints

Completed in the current branch:

- logout-all and single-session revocation support now exist
- login/register rate limiting and lockout behavior now exist
- auth event trail is now queryable from `GET /api/auth/audit`
- auth mutation audit snapshots now exclude `password_hash` and `session_token_hash`
- authenticated `/api/agent/*` requests now ignore spoofed `user_id` / `device_id` values from query/body and bind to the request actor instead
- browser-origin CSRF checks now protect unsafe cookie-authenticated writes on `/api/auth/*` and authenticated protected APIs
- an operator runbook and script now exist for emergency password resets:
  - `scripts/admin_reset_password.py`
  - `docs/50-infra-and-ops/admin-password-reset-runbook-2026-04-06.md`
- rollout rehearsal tooling now exists for auth/cookie posture:
  - `scripts/verify_rollout_auth_config.py`
  - `scripts/smoke_test_production.ps1`

Still open inside this workstream:

- user self-service password reset flow
- rollout-host rehearsal for trusted origin/CORS settings
- rollout-host secure cookie/TLS rehearsal

Suggested file targets:

- `services/auth_service.py`
- `routers/auth.py`
- `auth_models.py`
- `database/audit.py`
- `docs/50-infra-and-ops/auth-hardening-plan-YYYY-MM-DD.md`

Acceptance criteria:

- active sessions can be listed and revoked
- repeated brute-force attempts are throttled
- auth event trail is queryable

## P1: Final Legacy Endpoint Audit

Status: `not started`

Goal:

- remove or harden the remaining endpoints that may still aggregate globally

Priority audit targets:

- old dashboard/compatibility pages in `main.py`
- older history/mixed-content endpoints
- low-traffic legacy pages that still call `db.query(...).all()` without actor scope
- any endpoint that resolves records by global ids and then writes

Current note:

- dormant legacy quiz files such as `routers/quiz_old.py` and `routers/quiz_new.py` still contain unscoped global-id writes, but they are not currently mounted in `main.py`; do not re-enable them without an actor-scope retrofit

Tasks:

- search all routers/services for:
  - raw `db.query(...).all()`
  - `count()` over actor-owned tables without scope
  - writes after unscoped lookups
- either:
  - add actor scope
  - add auth gate
  - or retire the endpoint if no longer needed

Suggested file targets:

- `main.py`
- `routers/history.py`
- `routers/quiz_old.py`
- `routers/quiz_new.py`
- `routers/llm.py`

Acceptance criteria:

- a documented endpoint audit list exists
- high-risk remaining legacy routes are either fixed or explicitly retired

## P2: Operations And Recovery

Status: `in progress`

Goal:

- make the deployment recoverable and operable by routine procedures

Tasks:

- add PostgreSQL backup script and restore test
- define Neo4j backup expectations if Graphiti remains enabled
- add health and readiness checks for full stack
- define log location and retention strategy
- define upgrade sequence:
  - backup
  - migrate
  - deploy
  - smoke test
  - rollback
- add a post-deploy smoke test script

Completed in the current branch:

- `scripts/backup_postgres.ps1` now exists
- `scripts/restore_postgres.ps1` now exists
- `scripts/smoke_test_production.ps1` now exists
- `docs/50-infra-and-ops/backup-and-recovery-runbook-2026-04-05.md` now exists
- `docs/50-infra-and-ops/rollout-final-rehearsal-checklist-2026-04-06.md` now exists
- `docs/50-infra-and-ops/student-pilot-load-test-runbook-2026-04-05.md` now exists

Still open inside this workstream:

- execute one full backup/recovery/smoke rehearsal on the final rollout host
- run the first pilot load rehearsal against the final host/env
- define routine log retention and monitoring escalation for real operators

Suggested file targets:

- `scripts/backup_postgres.ps1`
- `scripts/restore_postgres.ps1`
- `scripts/smoke_test_production.ps1`
- `docs/50-infra-and-ops/backup-and-recovery-runbook-YYYY-MM-DD.md`

Acceptance criteria:

- backup/restore has been rehearsed once
- smoke test script exists and can validate login + quiz + tracking + wrong-answer flows

## Recommended next implementation session

If continuing immediately, the next concrete build should be:

1. execute the final-host checklist in `docs/50-infra-and-ops/rollout-final-rehearsal-checklist-2026-04-06.md`
2. run one real-host PostgreSQL cutover rehearsal and confirm `/ready` plus smoke checks
3. run a 10-student pilot with `scripts/load_test_rollout.py`
4. make the go/no-go decision from the recorded rehearsal evidence

## Related documents

- `docs/50-infra-and-ops/production-readiness-update-2026-04-04.md`
- `docs/50-infra-and-ops/postgresql-docker-bootstrap-2026-04-04.md`
- `docs/50-infra-and-ops/rollout-final-rehearsal-checklist-2026-04-06.md`
- `docs/50-infra-and-ops/local-rollout-rehearsal-results-2026-04-06.md`
