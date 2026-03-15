# Data Format Audit

Date: 2026-03-15

## Goal

This document audits the current project data formats for future LLM integration and defines the first stable export layer.

## Current Stack

- Backend: FastAPI + SQLAlchemy + SQLite
- Rendering: Jinja templates + browser-side fetch
- Validation: Pydantic v2
- Current risk: persistence-layer data is still heterogeneous even though API response typing is now fully covered

## Dataset Snapshot

Sampled from `data/learning.db`:

| Table | Count |
| --- | ---: |
| `daily_uploads` | 69 |
| `chapters` | 247 |
| `concept_mastery` | 847 |
| `test_records` | 166 |
| `quiz_sessions` | 49 |
| `learning_sessions` | 83 |
| `question_records` | 1216 |
| `wrong_answers_v2` | 274 |
| `wrong_answer_retries` | 136 |

## API Contract Coverage

Router scan result:

- Total routes: `86`
- Routes with `response_model`: `86`
- Routes without `response_model`: `0`
- Typed coverage: `100.0%`

Highest-risk routers:

| Router | Total | Typed | Untyped |
| --- | ---: | ---: | ---: |
| `learning_tracking.py` | 15 | 15 | 0 |
| `wrong_answers_v2.py` | 16 | 16 | 0 |
| `quiz.py` | 7 | 7 | 0 |
| `quiz_batch.py` | 6 | 6 | 0 |
| `quiz_new.py` | 5 | 5 | 0 |
| `history.py` | 3 | 3 | 0 |

Note:

- The new `routers/llm.py` adds typed export endpoints, and the legacy quiz family is now fully typed.
- `learning_tracking.py`, `wrong_answers_v2.py`, `history.py`, `dashboard.py`, `graph.py`, and quiz variation endpoints are all response-typed now.
- The main remaining risks are no longer missing response schemas, but inconsistent historical field values and mixed storage shapes in the database layer.

## Real Enum Values

Distinct values found in the live database:

- `learning_sessions.session_type`: `detail_practice`, `exam`
- `learning_sessions.status`: `completed`, `in_progress`
- `question_records.question_type`: `A1`, `A2`, `A3`, `X`
- `question_records.difficulty`: `基础`, `提高`, `难题`
- `question_records.confidence`: `<empty>`, `sure`, `unsure`, `no`
- `wrong_answers_v2.severity_tag`: `critical`, `stubborn`, `landmine`, `normal`
- `wrong_answers_v2.mastery_status`: `active`, `archived`
- `wrong_answer_retries.confidence`: `sure`, `unsure`, `no`
- `test_records.confidence`: `<null>`, `sure`, `unsure`

Important data-quality issue:

- `question_records.confidence` is mostly empty right now.
- Current counts: `<empty>=1114`, `sure=68`, `no=18`, `unsure=16`.
- LLM-side consumers must not assume confidence is always present.
- New writes are now coerced to `sure / unsure / no`, and legacy reads are normalized at response/analytics time.
- Historical blank rows are not rewritten in-place yet; that should be treated as a separate data migration decision.
- A dry-run/apply migration script is now available at `migrate_confidence_contracts.py`.

## JSON Columns

High-value JSON columns in active use:

| Model | Field | Shape | Notes |
| --- | --- | --- | --- |
| `DailyUpload` | `ai_extracted` | object | upload parsing result |
| `Chapter` | `concepts` | array<object> | concept list |
| `TestRecord` | `ai_options` | object | option map |
| `TestRecord` | `weak_points` | array<string> | AI weaknesses |
| `FeynmanSession` | `dialogue` | array<object> | transcript |
| `QuizSession` | `questions` | array<object> | legacy exam question snapshots |
| `QuizSession` | `answers` | array<object> | legacy exam answer snapshots |
| `LearningActivity` | `data` | object | event payload |
| `QuestionRecord` | `options` | object | option map |
| `QuestionRecord` | `answer_changes` | array<object> | answer revision trail |
| `WrongAnswerV2` | `options` | object | active wrong-answer options |
| `WrongAnswerV2` | `linked_record_ids` | array<int> | linked source records |
| `WrongAnswerV2` | `variant_data` | object | transformed question cache |
| `WrongAnswerV2` | `parent_ids` | array<int> | fusion parent ids |
| `WrongAnswerV2` | `fusion_data` | object | fusion diagnosis cache |
| `WrongAnswerRetry` | `ai_evaluation` | object | rationale judgement |

## Contract Problems

### 1. One concept, multiple shapes

Question-like payloads currently exist in at least four shapes:

- `question_records`
- `wrong_answers_v2`
- `quiz_sessions.questions`
- `test_records`

Differences include:

- `question` vs `question_text` naming
- option maps with different guarantees
- mixed answer naming
- missing or inconsistent `fingerprint`
- different difficulty / confidence conventions

### 2. Frontend payloads are mixed with analysis payloads

Several endpoints return data optimized for charts or page rendering, not for machine consumption. This is acceptable for UI, but bad for LLM ingestion because:

- display labels and raw metrics are mixed together
- time values are inconsistently serialized
- nested objects have no versioning

### 3. Historical storage drift still exists

`schemas.py` and `api_contracts.py` now cover current responses, but the underlying stored data still has legacy variation. This means:

- response typing alone does not fix old rows already persisted with blank or irregular values
- JSON columns still allow free-form internal writes unless write paths are normalized
- future migrations should target storage canonicalization, not just response validation

## Canonical LLM Contract Added

This audit introduces a new stable export layer:

- `utils/data_contracts.py`
- `GET /api/llm/audit`
- `GET /api/llm/wrong-answers`
- `GET /api/llm/sessions`
- `GET /api/llm/context`

Schema version:

- `llm-ready.v1`

Main design choices:

- stable snake_case fields
- normalized option order `A-E`
- explicit chapter reference object
- explicit session stats object
- explicit SRS state for wrong answers
- normalized enum codes while preserving raw labels where needed
- ISO datetime/date serialization

## Persistence Canonicalization Status

Write-side guards are now partially in place for newly written records:

- `quiz_sessions.questions`
- `quiz_sessions.answers`
- `test_records.ai_options`
- `test_records.weak_points`
- `wrong_answers.options`
- `wrong_answers.weak_points`
- `learning_activities.data`
- `question_records.options`
- `question_records.answer_changes`
- `wrong_answers_v2.linked_record_ids`
- `wrong_answers_v2.options`
- `wrong_answers_v2.parent_ids`
- `wrong_answers_v2.variant_data`
- `wrong_answers_v2.fusion_data`
- `wrong_answer_retries.ai_evaluation`

Historical backfill is now scripted via:

- `python migrate_json_contracts.py`

This shifts the primary risk from "new writes keep drifting" to "historical rows may still contain legacy shapes until migrations are applied".

## Recommended Usage

For future LLM integration:

1. Use `/api/llm/context` for broad session + wrong-answer + analytics context.
2. Use `/api/llm/wrong-answers` when the model only needs remediation data.
3. Use `/api/llm/sessions` for learning-trace analysis.
4. Use `/api/llm/audit` to verify route coverage and data-quality status before rollout.

Do not feed the current UI endpoints directly to the model as a primary contract.

## Next Migration Steps

1. Decide whether to run `python migrate_confidence_contracts.py --apply --rewrite-empty` after taking a DB backup.
2. Run `python migrate_json_contracts.py --apply` on a backup copy first and record the diff summary.
3. Add version markers to AI-generated JSON blobs such as `variant_data` and `fusion_data`.
4. Expand contract tests further to cover `quiz_batch`, `wrong_answers_v2`, `learning_tracking/stats`, and import flows end-to-end.
5. Consider centralizing JSON-column normalization in model-level hooks if write paths keep growing.
