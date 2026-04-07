# PostgreSQL + Docker Bootstrap

Date: 2026-04-04

See also:

- `docs/50-infra-and-ops/production-readiness-update-2026-04-04.md`
- `docs/50-infra-and-ops/production-next-steps-roadmap-2026-04-04.md`

## Goal

Move the server runtime away from split SQLite files and into a PostgreSQL-backed deployment flow with an explicit migration step.

## What is in place now

- `database/domains.py` accepts `postgresql+psycopg://...` URLs.
- `alembic/` contains the first migration scaffold and baseline revision.
- `docker-compose.yml` now includes:
  - `postgres`
  - `migrate`
  - `app`
  - `neo4j`
- `.env.example` includes PostgreSQL and Alembic variables.
- `DB_AUTO_CREATE_SCHEMA=false` can disable runtime `create_all()` behavior in production.

## Recommended production env

Use these values at minimum:

```env
DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
ALEMBIC_DATABASE_URL=postgresql+psycopg://tls:change-me@postgres:5432/true_learning_system
DB_AUTO_CREATE_SCHEMA=false
SINGLE_USER_MODE=false
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax
```

If all domain-specific database URLs are left empty, they will fall back to `DATABASE_URL`.

## Bootstrap commands

```powershell
docker compose build
docker compose up migrate
docker compose up -d app neo4j
```

Or start the whole stack together:

```powershell
docker compose up -d
```

The `app` service is configured to wait for:

- PostgreSQL health
- Neo4j health
- successful completion of the one-shot `migrate` service

## Current gap before real cutover

This round adds PostgreSQL runtime and migration scaffolding, but it does not yet migrate existing SQLite data into PostgreSQL.

Before public cutover, still required:

1. Export/import current SQLite data into PostgreSQL.
2. Rehearse rollback on a copy of real data.
3. Put the app behind HTTPS reverse proxy.
4. Turn on secure cookie settings and secret management.
