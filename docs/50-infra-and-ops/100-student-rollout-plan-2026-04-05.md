# 100 Student Rollout Plan

Date: 2026-04-05

## Goal

Move `true-learning-system` from the current private/single-user posture into a controlled deployment that can support about 100 student accounts.

This document is the execution version of the plan: what to do first, what to defer, and what counts as "ready".

Operator handoff for the final-host rehearsal now lives in:

- `docs/50-infra-and-ops/rollout-final-rehearsal-checklist-2026-04-06.md`

## Current baseline

Already in place:

- server-side auth/session foundation exists
- main multi-user actor-scope isolation has been introduced across the core quiz/challenge/fusion/tracking paths
- student-route auth gating, session revocation, and env-driven CORS hardening now exist
- auth rate limiting/lockout and a queryable auth audit trail now exist
- internal `/api/llm/*` contract and audit routes can now be login-gated and optionally admin-only in rollout envs
- batch quiz generation now has a concurrency/capacity guard instead of unlimited parallel generation
- readiness probe, smoke script, and PostgreSQL backup/restore scripts now exist
- a formal student-pilot load-test script and runbook now exist
- baseline app-layer security headers now exist in addition to the proxy config
- PostgreSQL runtime configuration and Alembic baseline exist
- Docker Compose already includes `postgres`, `migrate`, `app`, and `neo4j`

Still blocking a 100-student rollout:

- local runtime still defaults to split SQLite files and `SINGLE_USER_MODE=true`
- PostgreSQL cutover tooling has now passed one local live-target rehearsal, but the final rollout host/env has not been rehearsed yet
- auth enforcement and internal-route admin gating can now be enabled, but the rollout env still has not been switched over
- password reset/admin reset tooling now exists, but the final host still needs one full operator rehearsal
- reverse proxy / HTTPS assets exist now, but the rollout hostname/env is not configured yet
- the new load-test tooling still has not been executed on the final rollout host/env, and the final-host smoke/recovery rehearsal still has not been executed

## Rollout scope

Phase 1 student release should keep only the essential modules:

- login
- chapter/content learning
- quiz and batch quiz
- wrong answers and retry
- learning tracking and progress board

Defer from the first 100-student release unless specifically needed:

- public-facing Agent workflows
- Telegram gateway
- Graphiti/Neo4j-enhanced features
- low-traffic compatibility pages that do not clearly add student value

## Execution order

1. PostgreSQL cutover rehearsal
2. auth enforcement on student-facing routes
3. deployment hardening with HTTPS and secure cookies
4. capacity controls for AI-heavy quiz generation
5. phased rollout and monitoring

## P0: PostgreSQL Cutover

Goal:

- make PostgreSQL the only write target for the rollout environment
- stop treating split SQLite files as the live production source of truth

Artifacts added in this round:

- `scripts/migrate_sqlite_to_postgres.py`
- `scripts/verify_postgres_cutover.py`

Recommended rehearsal flow:

```powershell
python scripts\migrate_sqlite_to_postgres.py --dry-run --report-out data\logs\cutover-dry-run.json
python scripts\migrate_sqlite_to_postgres.py --target-url "postgresql+psycopg://..." --truncate-target --report-out data\logs\cutover-import.json
python scripts\verify_postgres_cutover.py --target-url "postgresql+psycopg://..." --report-out data\logs\cutover-verify.json
```

Acceptance criteria:

- source row counts are inventoried cleanly
- import completes without missing target tables
- verification script shows row-count parity for key tables
- PostgreSQL sequences are advanced correctly after import

Key tables to watch first:

- `users`
- `auth_sessions`
- `concept_mastery`
- `concept_links`
- `learning_sessions`
- `question_records`
- `wrong_answers_v2`
- `wrong_answer_retries`
- `batch_exam_states`

## P0: Auth Enforcement

Goal:

- student data must resolve from the authenticated server session, not from client-declared identity alone

Required changes:

- add a shared dependency for student-facing routes that requires an authenticated user
- redirect or reject anonymous access on:
  - dashboard home
  - tracking pages
  - quiz submit/read flows
  - wrong-answer views
- keep `device_id` only as an auxiliary marker, not the primary account identity

Current implementation note:

- student-route auth gating can now be enabled with `AUTH_REQUIRE_LOGIN_FOR_STUDENT_ROUTES=true`
- internal audit/export routes such as `/api/llm/*` can be restricted with `AUTH_REQUIRE_ADMIN_FOR_INTERNAL_ROUTES=true`
- auth-side mutation events can now be queried from `GET /api/auth/audit`

Acceptance criteria:

- a student cannot access another student's state by changing headers or local storage
- anonymous users are routed to `/login` or receive `401` on API calls
- student-facing pages behave correctly after login refresh

## P1: Deployment Hardening

Goal:

- convert the current internal/private deployment posture into a controlled rollout environment

Required changes:

- add `nginx` or `caddy` in front of `app`
- enable HTTPS
- set `AUTH_COOKIE_SECURE=true`
- replace open CORS with explicit origins
- add request-size limits and basic abuse throttling for auth endpoints

Acceptance criteria:

- app is accessed only through HTTPS in the rollout environment
- auth cookie is `Secure`
- direct app port does not need public exposure

## P1: Capacity Controls

Goal:

- avoid having 100 students trigger uncontrolled real-time LLM generation spikes

Required changes:

- prefer cached or pre-generated quiz payloads for repeated content
- queue or throttle expensive generation paths
- keep heavy/long-running AI flows off the hot path for the first rollout
- add basic response-time and generation-success monitoring

Current implementation note:

- batch quiz generation now has a process-local concurrency guard driven by:
  - `BATCH_GENERATION_MAX_CONCURRENCY`
  - `BATCH_GENERATION_QUEUE_TIMEOUT_SECONDS`
- staged student-pilot load testing can now be executed with:
  - `scripts/load_test_rollout.py`
  - `docs/50-infra-and-ops/student-pilot-load-test-runbook-2026-04-05.md`

Operational posture for first release:

- keep `Graphiti` optional rather than mandatory
- treat AI-heavy batch generation as constrained capacity, not unlimited capacity
- measure separately:
  - login success rate
  - quiz generation success rate
  - p95 response time
  - database error rate

## Rollout ladder

Use a staged release instead of going directly to 100 students.

1. Internal rehearsal with copied production-like data
2. 10 students closed beta
3. 30 students limited rollout
4. 100 students full rollout

Promotion rule between stages:

- no unresolved data-isolation issue
- no blocking auth/session issue
- no sustained database error spike
- quiz generation success rate remains acceptable during the stage window

## Immediate next build

After the current auth/proxy/cutover groundwork, the next implementation session should do:

1. execute one final PostgreSQL cutover rehearsal on the actual rollout host/env
2. flip a staging-style env to `SINGLE_USER_MODE=false` and `AUTH_REQUIRE_LOGIN_FOR_STUDENT_ROUTES=true`
3. run a 10-student pilot with `scripts/load_test_rollout.py`, smoke checks, and basic latency/error monitoring

Use `docs/50-infra-and-ops/rollout-final-rehearsal-checklist-2026-04-06.md` as the single-page execution order for that final rehearsal.
