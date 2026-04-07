# True Learning System Production Readiness Update

Date: 2026-04-04

See also:

- `docs/50-infra-and-ops/postgresql-docker-bootstrap-2026-04-04.md`
- `docs/50-infra-and-ops/production-next-steps-roadmap-2026-04-04.md`

## What changed in this round

- Added login/session scaffolding on the server side so the project is no longer only relying on client-declared identity.
- Changed `wrong_answers_v2` from global fingerprint uniqueness to scoped uniqueness:
  - old: `question_fingerprint`
  - new: `(scope_key, question_fingerprint)`
- Added review-db migration support in `services/data_identity.py` for the `wrong_answers_v2` scoped schema.
- Added content-db migration support in `services/data_identity.py` for:
  - `concept_mastery`
  - `concept_links`
- Scoped the fusion flow end-to-end:
  - `routers/fusion.py`
  - `services/fusion_service.py`
- Scoped the graph and Feynman flows:
  - `routers/graph.py`
  - `routers/feynman.py`
- Scoped the challenge flow end-to-end:
  - `routers/challenge.py`
- Scoped the remaining legacy quiz entrypoints and legacy upload concept writes:
  - `routers/quiz.py`
  - `routers/quiz_fast.py`
  - `routers/quiz_concurrent.py`
  - `routers/upload.py`
- Scoped the remaining high-risk batch-submit concept/follow-up writes:
  - `routers/quiz_batch.py`
  - `services/batch_exam_submit.py`
- Scoped the remaining high-risk concept routes still living in `main.py`:
  - `/chapter/{chapter_id}`
  - `/quiz/{concept_id}`
  - `/feynman/{concept_id}`
  - `/api/stats`
  - `/api/chapters`
  - `/api/chapter/{chapter_id}`
  - `/api/concept/{concept_id}`
- Scoped the remaining high-risk dashboard and tracking flows:
  - `/` dashboard home page
  - `routers/learning_tracking.py` session write endpoints
  - `routers/learning_tracking.py` review-data assembly
  - `routers/learning_tracking.py` knowledge-tree fallback chapter resolution
- Added production database bootstrap work:
  - `database/domains.py` now accepts generic `DATABASE_URL` values, not only SQLite file paths
  - `database/audit.py` now creates audit tables through SQLAlchemy metadata so startup works on PostgreSQL
  - `DB_AUTO_CREATE_SCHEMA` can disable runtime `create_all()` paths for production
  - Alembic scaffold added under `alembic/`
  - baseline revision added for the current schema snapshot
  - `docker-compose.yml` now includes `postgres` and a one-shot `migrate` service
  - `.env.example` now includes PostgreSQL/Alembic production variables
- Added regression tests for:
  - fusion actor-scope isolation
  - wrong-answer scoped uniqueness
  - actor-scope base regressions
  - auth endpoint baseline
  - graph actor-scope isolation
  - Feynman actor-scope isolation
  - content-schema scoped migration/backfill
  - challenge actor-scope isolation
  - main.py concept-route actor-scope isolation
  - legacy quiz actor-scope isolation
  - quiz-fast actor-scope isolation
  - quiz-v2 actor-scope isolation
  - legacy upload concept-creation isolation
  - batch exam submit actor-scope isolation
  - dashboard home page actor-scope isolation
  - tracking session write isolation and knowledge-tree fallback isolation
  - database URL normalization / Alembic metadata bootstrap

## What is now safer

- Different users/devices can now keep the same wrong-answer fingerprint without colliding in `wrong_answers_v2`.
- Different users/devices can now keep the same `concept_id` without colliding in `concept_mastery`.
- Different users/devices can now keep the same concept-link pair without colliding in `concept_links`.
- Fusion endpoints no longer read, mutate, or deduplicate against another actor's wrong-answer records.
- Graph endpoints no longer read another actor's concept mastery rows or custom links.
- Feynman start no longer resolves a concept from another actor's scope.
- Challenge endpoints no longer read, mutate, retry, or aggregate another actor's wrong-answer records.
- Legacy quiz single-question routes now resolve `ConceptMastery` and `TestRecord` inside the current actor scope instead of by global `concept_id` / `test_id`.
- `quiz_fast` and `quiz_v2` now repair/seed chapter concepts per actor scope, and their submit/result endpoints reject cross-actor session access by validating scoped `TestRecord` ownership.
- Legacy `/api/upload` no longer skips `ConceptMastery` creation just because another actor already owns the same `concept_id` in the same chapter.
- Batch exam submit now resolves auto-created `ConceptMastery` rows and `WrongAnswerV2` follow-up rows inside the current actor scope instead of deduplicating globally.
- The remaining concept detail pages and compatibility APIs in `main.py` no longer resolve another actor's `ConceptMastery` or `TestRecord` rows.
- Dashboard home page no longer aggregates another actor's `LearningSession`, `DailyUpload`, `WrongAnswerV2`, or question-record stats.
- Learning-tracking session write endpoints now reject cross-actor session IDs instead of allowing writes by raw `session_id`.
- New `LearningSession`, `LearningActivity`, and `QuestionRecord` rows created by tracking flows now persist `user_id/device_id`, which prevents new runtime data from being written without ownership metadata.
- Knowledge-tree fallback chapter resolution no longer borrows another actor's `WrongAnswerV2` fingerprint mapping.
- Legacy `wrong_answers_v2` rows with missing `scope_key` are backfilled row-by-row from `user_id/device_id` instead of being forced into one default bucket.
- Legacy `concept_mastery` and `concept_links` rows are now rebuilt/backfilled into scoped storage schemas on SQLite startup.
- The application can now be configured against PostgreSQL with one `DATABASE_URL` instead of being hard-wired to SQLite file paths.
- Production deployment can now run schema migration before app startup instead of relying only on runtime `create_all()`.
- Runtime schema auto-creation can now be disabled explicitly with `DB_AUTO_CREATE_SCHEMA=false`, which is the safer production posture.

## Validation completed

```powershell
python -m py_compile database\domains.py database\audit.py database\alembic_support.py auth_models.py knowledge_upload_models.py learning_tracking_models.py services\agent_session_serializers.py services\data_identity.py services\quiz_scope.py routers\challenge.py routers\graph.py routers\feynman.py routers\fusion.py routers\quiz.py routers\quiz_fast.py routers\quiz_concurrent.py routers\upload.py routers\quiz_batch.py routers\learning_tracking.py services\batch_exam_submit.py main.py test_database_domains_config.py test_challenge_endpoints.py test_main_concept_scope.py test_graph_endpoints.py test_feynman_endpoints.py test_content_scope_schema.py test_fusion_endpoints.py test_wrong_answer_scope_schema.py test_actor_scope_regressions.py test_auth_endpoints.py test_quiz_scope_endpoints.py test_quiz_batch_generate_endpoint.py alembic\env.py alembic\versions\20260404_000001_baseline_schema.py
pytest -q test_database_domains_config.py test_quiz_scope_endpoints.py test_challenge_endpoints.py test_main_concept_scope.py test_graph_endpoints.py test_feynman_endpoints.py test_content_scope_schema.py test_fusion_endpoints.py test_wrong_answer_scope_schema.py test_actor_scope_regressions.py test_auth_endpoints.py test_quiz_batch_generate_endpoint.py
```

Result:

- `48 passed`

Additional note:

- `pytest -q test_backfill_concept_mastery.py` currently fails at collection because the repo is missing the imported module `backfill_concept_mastery`; this is a pre-existing repository issue, not caused by this round of scope changes.

## Remaining release blockers

1. The biggest remaining data-isolation work is now outside the core quiz/challenge/fusion/batch submit paths.
   Remaining candidates should be audited in older dashboards, mixed-content history pages, and any low-traffic compatibility routes that still aggregate globally.
2. The dashboard/home page and learning-tracking high-risk entrypoints are safer now, but older mixed-content views and low-traffic compatibility pages still need a final pass before public deployment.
3. PostgreSQL/Alembic bootstrap now exists, but there is still no finished cutover/import path from the current split SQLite files into PostgreSQL.
   That data migration path must be completed and rehearsed before production cutover.
4. Auth is scaffolded, but production hardening is still incomplete.
   Missing areas include password reset, refresh-token/session revocation, rate limiting, CSRF/session policy review, and reverse-proxy security headers.
5. Docker Compose now boots PostgreSQL and runs Alembic, but TLS termination, reverse proxy, and secret management are still not production-complete.

## Recommended next execution order

1. Audit the remaining dashboard/history compatibility pages and retire or harden the ones that still aggregate globally.
2. Implement and rehearse SQLite-to-PostgreSQL import/cutover against a copy of current data.
3. Add reverse proxy / HTTPS / secure cookie settings and harden auth/session management before exposing the system publicly.
