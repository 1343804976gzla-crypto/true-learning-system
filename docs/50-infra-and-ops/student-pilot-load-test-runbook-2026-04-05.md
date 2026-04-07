# Student Pilot Load Test Runbook

Date: 2026-04-05

## Purpose

Provide a repeatable load-test script for the 10-student and 30-student pilot stages before a broader rollout.

This runbook is intentionally focused on the first public student release surface:

- login and session checks
- student stats/history/wrong-answer reads
- a lightweight tracking write flow:
  - start session
  - submit one question
  - complete session

It does not try to saturate heavy LLM generation by default. That remains a separate controlled rehearsal because batch quiz generation is capacity-constrained on purpose.

## Script

- `scripts/load_test_rollout.py`

The script measures:

- per-step success rate
- per-step p50 / p95 latency
- status-code distribution
- failure samples with account, round, step, and error detail

## Account file format

JSON example:

```json
{
  "accounts": [
    {
      "email": "student01@example.com",
      "password": "change-me-01",
      "display_name": "Student 01"
    },
    {
      "email": "student02@example.com",
      "password": "change-me-02"
    }
  ]
}
```

CSV example:

```csv
email,password,display_name
student01@example.com,change-me-01,Student 01
student02@example.com,change-me-02,Student 02
```

## Recommended pilot commands

Local or staging with self-signed TLS:

```powershell
python scripts\load_test_rollout.py `
  --base-url "https://localhost" `
  --accounts-file ".\pilot-accounts.json" `
  --concurrency 5 `
  --rounds 2 `
  --insecure `
  --report-out ".\data\logs\pilot-load-test.json"
```

10-student pilot:

```powershell
python scripts\load_test_rollout.py `
  --base-url "https://rollout.example.com" `
  --accounts-file ".\pilot-accounts.json" `
  --concurrency 10 `
  --rounds 3 `
  --report-out ".\data\logs\pilot-load-test-10-students.json"
```

30-student limited rollout:

```powershell
python scripts\load_test_rollout.py `
  --base-url "https://rollout.example.com" `
  --accounts-file ".\pilot-accounts-30.json" `
  --concurrency 15 `
  --rounds 3 `
  --report-out ".\data\logs\pilot-load-test-30-students.json"
```

Read-only rehearsal:

```powershell
python scripts\load_test_rollout.py `
  --base-url "https://rollout.example.com" `
  --accounts-file ".\pilot-accounts.json" `
  --concurrency 10 `
  --rounds 2 `
  --skip-write-flow `
  --report-out ".\data\logs\pilot-load-test-readonly.json"
```

## Expected steps

Each virtual student journey runs:

1. `GET /health`
2. `GET /ready`
3. `GET /api/auth/me`
4. `POST /api/auth/login`
5. `GET /api/auth/me`
6. `GET /api/auth/sessions`
7. `GET /api/stats`
8. `GET /api/tracking/stats?period=all`
9. `GET /api/history/stats`
10. `GET /api/wrong-answers/stats`
11. Optional write flow:
    `POST /api/tracking/session/start`
    `POST /api/tracking/session/{id}/question`
    `POST /api/tracking/session/{id}/complete`
    `GET /api/tracking/session/{id}`

## Interpretation

Minimum expectations for a pilot promotion decision:

- `ready` success rate stays at `100%`
- `login` success rate stays at `100%` for known-good accounts
- no sustained `5xx` responses on student read paths
- tracking write flow completes without `401`, `403`, or `5xx`
- p95 latency stays operationally acceptable for the pilot stage

Suggested first-stage review thresholds:

- `ready` p95 below `1000 ms`
- auth and read endpoints p95 below `1500 ms`
- tracking write endpoints p95 below `2500 ms`
- overall failure count stays at `0`

If the environment is intentionally cold or under shared debugging load, record that fact next to the report before making rollout decisions.

## Notes

- The script exits with code `1` when any step fails.
- For local rehearsal against self-signed HTTPS, use `--insecure`.
- Do not use this script as the primary batch-quiz stress tool. Heavy generation should remain separately throttled and monitored because the rollout environment is intentionally not configured for unlimited concurrent LLM generation.
