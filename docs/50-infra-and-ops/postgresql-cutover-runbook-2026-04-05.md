# PostgreSQL Cutover Runbook

Date: 2026-04-05

See also:

- `docs/50-infra-and-ops/100-student-rollout-plan-2026-04-05.md`
- `docs/50-infra-and-ops/postgresql-docker-bootstrap-2026-04-04.md`

## Goal

Move the rollout environment from split SQLite files to PostgreSQL with a repeatable cutover sequence and a clear rollback point.

## Current rehearsal baseline

Latest dry-run inventory:

- report: `data/logs/cutover-dry-run-2026-04-05.json`
- domains scanned: `core`, `content`, `runtime`, `review`, `agent`, `legacy`
- tables discovered: `50`
- total source rows: `13948`
- `learning.db` is effectively empty for the current application domains

Latest local live-target rehearsal:

- import report: `data/logs/cutover-import-2026-04-05-live-rehearsal.json`
- verify report: `data/logs/cutover-verify-2026-04-05-live-rehearsal.json`
- imported rows: `13921`
- filtered legacy orphan rows: `27`
- filtered tables: `learning_activities`, `daily_review_paper_items`, `agent_memories`, `workflows`, `shared_access_log`, `workflow_artifacts`
- merged audit rows imported: `4889`

Primary live data currently sits in:

- `data/content_knowledge.db`
- `data/learning_runtime.db`
- `data/wrong_answer_review.db`
- `data/agent.db`

## Preconditions

Before running the import:

- PostgreSQL target is reachable
- schema has already been created with `alembic upgrade head`
- student writes are paused during the final import window
- `.env` for the rollout environment already contains the target PostgreSQL URL

Recommended minimum rollout env:

```env
DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
ALEMBIC_DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
CONTENT_DATABASE_PATH=
LEGACY_DATABASE_PATH=
AGENT_DATABASE_PATH=
RUNTIME_DATABASE_PATH=
REVIEW_DATABASE_PATH=
CONTENT_DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
LEGACY_DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
AGENT_DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
RUNTIME_DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
REVIEW_DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
DB_AUTO_CREATE_SCHEMA=false
SINGLE_USER_MODE=false
AUTH_REQUIRE_LOGIN_FOR_STUDENT_ROUTES=true
AUTH_COOKIE_SECURE=true
```

Important:

- leaving `CONTENT_DATABASE_PATH`, `RUNTIME_DATABASE_PATH`, `REVIEW_DATABASE_PATH`, `AGENT_DATABASE_PATH`, or `LEGACY_DATABASE_PATH` populated will keep those domains on SQLite even if `DATABASE_URL` points to PostgreSQL

## Step 1: Snapshot the SQLite sources

Create fresh backups before touching PostgreSQL:

```powershell
python scripts\backup_all_databases.py --include-shadow --output-dir data\backups\cutover-2026-04-05
```

Keep the generated backup directory until the rollout has been stable for at least one full day.

## Step 2: Rehearse inventory only

Run a no-write dry-run first:

```powershell
python scripts\migrate_sqlite_to_postgres.py --dry-run --merge-audit --report-out data\logs\cutover-dry-run.json
```

Do not proceed if:

- any expected source database is missing
- expected tables disappear from the report
- row totals change unexpectedly without a known reason

## Step 3: Prepare the PostgreSQL target

If you are using the Docker deployment path:

```powershell
docker compose up -d postgres
docker compose up migrate
```

If the target is an external PostgreSQL instance, run Alembic against that target before the import window.

## Step 4: Execute the import

For a clean rehearsal or a one-time cutover target, truncate first:

```powershell
python scripts\migrate_sqlite_to_postgres.py --target-url "postgresql+psycopg://tls:change-me@localhost:15432/true_learning_system" --truncate-target --merge-audit --report-out data\logs\cutover-import.json
```

Notes:

- `--truncate-target` is recommended for rehearsal environments so old rows do not mask import mistakes
- `--merge-audit` should stay on if you want split SQLite audit rows merged into the single PostgreSQL `audit_change_log`
- the importer advances PostgreSQL sequences after copy so integer primary keys continue safely

## Step 5: Verify row-count parity

Run verification immediately after the import:

```powershell
python scripts\verify_postgres_cutover.py --target-url "postgresql+psycopg://tls:change-me@localhost:15432/true_learning_system" --merge-audit --allow-filtered-report data\logs\cutover-import.json --report-out data\logs\cutover-verify.json
```

The cutover is not accepted unless:

- verification exits with code `0`
- the key student tables match expected counts
- no target table is reported missing
- any filtered rows are explicitly recorded in the import report instead of being silently lost

Key tables to inspect first:

- `users`
- `auth_sessions`
- `concept_mastery`
- `concept_links`
- `learning_sessions`
- `question_records`
- `wrong_answers_v2`
- `wrong_answer_retries`
- `batch_exam_states`

## Step 6: Flip the rollout app to PostgreSQL

After verification passes, start the rollout stack with the PostgreSQL-backed env:

```powershell
docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d
```

Confirm these values are active in the rollout env before allowing students in:

- `SINGLE_USER_MODE=false`
- `AUTH_REQUIRE_LOGIN_FOR_STUDENT_ROUTES=true`
- `DATABASE_URL` points at PostgreSQL
- `ALEMBIC_DATABASE_URL` points at PostgreSQL
- `AUTH_COOKIE_SECURE=true` when traffic is behind HTTPS

## Step 7: Smoke test before opening access

Minimum smoke test:

1. register or log in as a non-admin student account
2. open the dashboard and one chapter page
3. generate one quiz and submit answers
4. confirm wrong-answer review and learning tracking load correctly
5. refresh the browser and confirm the session survives

Recommended spot checks after the smoke test:

- new rows land in PostgreSQL, not only in SQLite
- anonymous access to student pages redirects to `/login`
- login API is rate-limited after repeated failures

## Rollback

If import verification fails or smoke testing exposes a blocker:

1. stop new student traffic
2. restore the previous app env values
3. restart the app against the original SQLite setup
4. keep the failed PostgreSQL target for investigation instead of reusing it blindly

Do not delete the SQLite backups until the rollback decision is closed.

## Exit criteria for the first 100-student rollout

Treat the cutover as ready only when all of the following are true:

- import report exists
- verification report exists
- smoke test passed on the PostgreSQL-backed app
- HTTPS proxy is active for the rollout hostname
- at least one rehearsal has been completed end-to-end before the live student wave
