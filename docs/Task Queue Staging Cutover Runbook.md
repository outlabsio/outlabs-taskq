# Task Queue Staging Cutover Runbook

Purpose: prove the first Diverse `taskq` lane in staging before any production
cutover. Production remains default-off until qdarte completes a 24-hour
production taskq observation gate and Diverse has a clean staging evidence
packet.

## Staging Gates

Start closed by default:

```bash
DIVERSE_TASKQ_ENABLED=false
DIVERSE_TASKQ_ENV=staging
DIVERSE_TASKQ_QUEUES=
DIVERSE_TASKQ_JOB_TYPES=
DIVERSE_TASKQ_ROLLBACK=false
```

Open only the first court lane in staging:

```bash
DIVERSE_TASKQ_ENABLED=true
DIVERSE_TASKQ_ENV=staging
DIVERSE_TASKQ_QUEUES=courts,_system
DIVERSE_TASKQ_JOB_TYPES=missouri_casenet,taskq.janitor
DIVERSE_TASKQ_ROLLBACK=false
```

Rollback:

```bash
DIVERSE_TASKQ_ROLLBACK=true
```

or clear either allowlist. New producers must return to the legacy
`scrape_jobs` path; existing taskq rows drain or are operator-cancelled/redriven.

## Evidence and Facade Routes

Cutover status:

```text
GET /api/v1/taskq/cutover-status
```

This route returns sanitized gate state only: environment, env match, rollback,
cutover active, and queue/job-type allowlists.

Schema status:

```text
GET /api/v1/taskq/schema-status
```

This route is intentionally read-only and not cutover-gated. It reports whether
the expected taskq schema, tables, functions, seed queues, and janitor schedule
exist. It also checks cutover-critical function signatures, including the
`fail_job` retryable-flag signature required by the court adapter. It should be
callable while production taskq is disabled so final-gate evidence can prove
"schema installed, mutations refused."

Mutation facade, all gated by `DIVERSE_TASKQ_*` and `JOB_WRITE`:

```text
POST /api/v1/taskq/jobs
POST /api/v1/taskq/jobs/claim
POST /api/v1/taskq/jobs/{job_id}/heartbeat
POST /api/v1/taskq/jobs/{job_id}/complete
POST /api/v1/taskq/jobs/{job_id}/fail
POST /api/v1/taskq/jobs/{job_id}/release
POST /api/v1/taskq/queues/{queue}/pause
POST /api/v1/taskq/queues/{queue}/resume
POST /api/v1/taskq/tick
POST /api/v1/taskq/jobs/{job_id}/redrive
POST /api/v1/taskq/jobs/{job_id}/cancel
```

Settlement payloads may include `queue` and `job_type` **as assertions only**
(ADR-006, 2026-07-18): the facade resolves the authoritative queue/job_type from
the job row (`get_authorization_projection`) before authorizing, and rejects a
mismatching assertion with 409/422. Caller-supplied fields are never the
authorization source — the earlier form of this runbook used them for the lane
allowlist, which let a caller pick its own authorization lane. `claim` accepts
`job_id` for targeted staging recovery. Route shapes here are the Diverse legacy
compatibility prefix; the versioned protocol (ADR-005) is canonical once
published.

## Canary Command

Use the public API canary before and after the temporary staging env flip:

```bash
DATA_API_KEY=... uv run python scripts/taskq_staging_canary.py closed
```

Closed mode requires:

- `/api/v1/taskq/schema-status` is `ready=true`
- `signatures.fail_job_retryable=true`
- `/api/v1/taskq/cutover-status` has `cutover_active=false`
- taskq enqueue is refused with HTTP 403

After temporarily opening only `courts/missouri_casenet` in staging, run:

```bash
DATA_API_KEY=... uv run python scripts/taskq_staging_canary.py open --destructive
```

Open mode creates one staging-only legacy canary scrape job, enqueues a taskq
job pointing at it, targeted-claims the taskq job, heartbeats it, fails it with
`retryable=false`, and verifies that a legacy `job_results` summary row was
persisted with taskq metadata. After the open canary passes, restore the gates
to default-closed and rerun closed mode.

## First Lane

First lane: one court-scraper platform in staging, with `queue=courts` and
`job_type=missouri_casenet` unless the side-effect disposition picks a safer
singleton lane.

Before enabling it, write a side-effect disposition covering:

- producer and enqueue path
- handler/runtime path
- app-table writes and per-result records
- progress/checkpoint mapping
- proxy/domain signals
- schedule/planner ownership
- retry/cancel/redrive behavior
- rollback path

Initial disposition for the generic facade:

- Producer: disabled by default. Staging producers may call `POST /api/v1/taskq/jobs`
  only when `courts/missouri_casenet` is allowlisted.
- Handler/runtime: workers claim via `POST /api/v1/taskq/jobs/claim`, with optional
  targeted `job_id`. No worker process should bypass the API with direct DB access.
- App-table writes: court case ingestion remains inside the existing worker-side
  court ingest path. Queue accounting summaries can be attached to an explicit
  legacy `scrape_jobs.id` by sending `legacy_scrape_job_id` plus `results` or
  `partial_results` on taskq settle calls.
- Legacy result FKs: a pure `taskq.jobs.id` is not treated as a drop-in
  `scrape_jobs.id`. The staging bridge only writes legacy `job_results` when
  the task payload explicitly names the legacy job id; persisted rows include
  `meta_json.taskq.{job_id,attempt_id,queue,job_type}` and duplicate settle
  retries are skipped for the same taskq job/attempt.
- Progress/checkpoints: workers send heartbeat `progress` JSON; durable domain
  checkpoints stay in the existing court tables until the adapter mapping is
  reviewed lane-by-lane.
- Proxy/domain signals: keep the current worker-owned proxy and domain signal writes
  unchanged for the first staging slice.
- Schedule/planner ownership: API owns `taskq.tick`; the installed janitor schedule
  is evidence/metadata until an API-hosted runner invokes it.
- Retry/cancel/redrive: taskq owns retry budget, lease expiry reaping, operator
  cancel, and redrive inside the `taskq` schema. Worker failures must send
  `retryable=false` for terminal/decode/provider-contract errors; taskq then
  records `status=failed`, `outcome=non_retryable` immediately instead of
  burning the retry budget.
- Rollback: set `DIVERSE_TASKQ_ROLLBACK=true` or clear allowlists. Existing legacy
  `scrape_jobs` remains the production path until staging evidence is accepted.

## Production Toggle Preflight

Run this immediately before any production taskq enablement. Do not skip it
because staging passed earlier.

Executable gate:

```bash
DATA_API_KEY=... DATABASE_URL=postgresql://... \
  uv run python scripts/taskq_production_preflight.py \
    --qdarte-production-started-at 2026-07-09T20:00:00Z \
    --qdarte-observation-accepted
```

Before 24 hours have elapsed from the confirmed qdarte production taskq start
timestamp, the command must fail with the qdarte observation gate not elapsed.
After the 24-hour mark, it still fails unless an operator passes
`--qdarte-observation-accepted`, the public staging API is still default-closed
and schema-ready, and the first-lane taskq table has zero queued/running/blocked
rows. Record the real qdarte production start timestamp when it is confirmed;
do not infer it from the final-gate packet alone.

1. Confirm the qdarte production observation gate is green. The canonical spec
   now requires the accepted qdarte final-gate packet plus 24 hours of clean
   qdarte production taskq runtime before Diverse production enablement. Current
   accepted packet: `final-gate-20260709-9cd22a3-tq-clean`; production
   observation start timestamp is recorded by the operator once qdarte taskq is
   live in production.
2. Confirm the Diverse evidence packet is still current: GitHub Verify green on
   the deployed API commit, worker staging commit pushed, and this runbook's
   deployed-staging evidence accepted.
3. Rerun the public staging closed canary with production still untouched:

   ```bash
   DATA_API_KEY=... uv run python scripts/taskq_staging_canary.py closed
   ```

4. Confirm staging remains default-closed before the flip:

   ```bash
   docker inspect diverse-data-api-staging --format '{{json .Config.Env}}' \
     | python -c 'import json,sys; print([e for e in json.load(sys.stdin) if e.startswith("DIVERSE_TASKQ")])'
   ```

5. Confirm the first-lane taskq table has no queued/running rows:

   ```sql
   SELECT status, outcome, count(*)
   FROM taskq.jobs
   WHERE queue = 'courts'
     AND job_type = 'missouri_casenet'
     AND status IN ('queued', 'running', 'blocked')
   GROUP BY status, outcome;
   ```

6. Enable only the first production lane:

   ```bash
   DIVERSE_TASKQ_ENABLED=true
   DIVERSE_TASKQ_ENV=production
   DIVERSE_TASKQ_QUEUES=courts,_system
   DIVERSE_TASKQ_JOB_TYPES=missouri_casenet,taskq.janitor
   DIVERSE_TASKQ_ROLLBACK=false
   ```

7. Keep rollback ready before starting any worker:

   ```bash
   DIVERSE_TASKQ_ROLLBACK=true
   ```

   or clear `DIVERSE_TASKQ_QUEUES` / `DIVERSE_TASKQ_JOB_TYPES`.

## Current Scaffold Checkpoint

2026-07-09:

- `DIVERSE_TASKQ_*` config gates added, default-off.
- `/api/v1/taskq/cutover-status` added.
- `/api/v1/taskq/schema-status` added as read-only schema proof.
- `taskq` schema migration added on Alembic head `e1a2b3c4d5f6`.
- Gated mutation facade added for enqueue, claim, heartbeat, complete, fail,
  release, pause/resume, tick, redrive, and cancel.
- `diverse-data-workers` queue client now has typed taskq methods for status,
  enqueue, claim, settle, pause/resume, tick, redrive, and cancel.
- `diverse-data-workers` has a staging-only court adapter behind `worker --taskq`.
  It claims `queue=courts`, executes the existing court batch path, heartbeats via
  taskq, and settles with optional legacy result summaries.
- `taskq.fail_job` supports terminal failure via `retryable=false`; the worker
  adapter sends that for unknown-platform, decode, and non-retryable batch errors.

Local staging DB evidence:

- Applied Alembic head `e1a2b3c4d5f6` to
  `postgresql://postgres:duckdb@127.0.0.1:5434/diverse_analytics_staging`.
- Verified `taskq` schema, all six tables, all 13 functions, queues
  `courts/_system`, and schedule `taskq.janitor`.
- Ran SQL smoke through installed functions:
  enqueue `courts/missouri_casenet` -> targeted claim by `job_id` -> heartbeat
  -> complete -> tick. Final job state: `succeeded/succeeded`.

Local staging HTTP evidence:

- Rebuilt `api-staging` from the clean verified worktree and confirmed
  `environment=staging`.
- Authenticated `GET /api/v1/taskq/cutover-status` returned default-closed
  state: `taskq_enabled=false`, `cutover_active=false`.
- Authenticated `GET /api/v1/taskq/schema-status` returned `ready=true`.
- Authenticated enqueue while disabled returned 403
  `taskq cutover is not active for this environment`.
- Temporarily opened only `courts/missouri_casenet` locally and ran HTTP smoke:
  enqueue -> targeted claim by `job_id` -> heartbeat -> complete -> tick.
  Result: `claim_count=1`, heartbeat `ok`, complete `ok`, `tick_reaped=0`.
- Restored local staging to default-closed after the smoke.

Local staging bridge evidence:

- Seeded a legacy `scrape_jobs` row, enqueued a taskq `courts/missouri_casenet`
  job with `legacy_scrape_job_id`, claimed it, completed it through taskq, then
  persisted one legacy `job_results` summary through the API service bridge.
- Verified duplicate persistence for the same taskq job/attempt is skipped.
- Smoke result:
  `settle_status=ok`, `persisted=true`, `duplicate_skipped=true`,
  `legacy_result_count=1`.

Local staging terminal-failure evidence:

- Applied Alembic head `f6e7d8c9b0a1` and verified a claimed taskq job failed
  with `retryable=false` goes terminal immediately:
  `settle_status=non_retryable`, `job_status=failed`,
  `outcome=non_retryable`, `failure_count=1`.
- Updated schema readiness to expose `signatures.fail_job_retryable=true`; local
  staging reports `ready=true` and no `signature:*` missing entries.

Local staging canary evidence:

- Rebuilt `api-staging` from `78910c3` and verified Alembic current
  `f6e7d8c9b0a1`.
- Ran `scripts/taskq_staging_canary.py closed` against `http://127.0.0.1:8051`;
  it passed with `cutover_active=false`, `signatures.fail_job_retryable=true`,
  and enqueue refused with HTTP 403.
- Recreated local `api-staging` with a temporary `/tmp` compose override opening
  only `courts/missouri_casenet`; ran
  `scripts/taskq_staging_canary.py open --destructive`. It passed:
  targeted claim, heartbeat `ok`, terminal fail `non_retryable`, and one legacy
  `job_results` row with taskq metadata.
- Recreated local `api-staging` without the override and reran closed mode; it
  passed, proving rollback/default-closed behavior after an open canary.

Deployed staging evidence:

- GitHub Verify is green at `bf09b6d` after the worker seed canary helper and
  final evidence update.
- `https://diverse-data-staging.outlabs.io/` reports `environment=staging`.
- Unauthenticated `/api/v1/taskq/cutover-status` returns 401, proving the route
  is deployed behind auth rather than absent.
- Authenticated `/api/v1/taskq/cutover-status` returns default-closed state:
  `taskq_enabled=false`, `cutover_active=false`, `env_matches=true`.
- Authenticated `/api/v1/taskq/schema-status` returns `ready=true` with all
  expected tables, functions, queues, and `taskq.janitor` present.
- Authenticated enqueue while disabled returns 403
  `taskq cutover is not active for this environment`.
- `scripts/taskq_staging_canary.py closed` passes against
  `https://diverse-data-staging.outlabs.io`: `taskq_enabled=false`,
  `cutover_active=false`, `queues=[]`, `job_types=[]`,
  `signatures.fail_job_retryable=true`, and enqueue refused with HTTP 403.
- Temporarily opened only `courts/missouri_casenet` via the local staging compose
  taskq override and ran
  `scripts/taskq_staging_canary.py open --destructive` against
  `https://diverse-data-staging.outlabs.io`. It passed:
  `taskq_job_id=8cbca08c-44ec-4925-9259-4ede4bde837b`,
  `attempt_id=e7078a67-516d-4885-ab22-e46b82dd9fe6`,
  terminal fail `non_retryable`, and one legacy bridge result row.
- Temporarily reopened only `courts/missouri_casenet`, seeded a deterministic
  worker canary with `scripts/taskq_staging_canary.py seed-worker --destructive`,
  then ran the real worker path:
  `uv run python -m diverse_data_workers --profile staging-render worker
  --platform missouri_casenet --taskq --taskq-queue courts --taskq-batch 1
  --once --worker-id taskq-worker-canary-20260709T193900`. The worker verified
  `actual_api_env=staging`, claimed
  `taskq_job_id=1570c4fa-49b1-4d2f-9197-32d0ccf9f236`, failed before any vendor
  call with `Job has neither requests_json nor counties`, and exited after one
  job. The taskq row settled as `status=failed`, `outcome=non_retryable`,
  `attempt_count=1`, `failure_count=1`.
- Restored deployed staging to default-closed and reran closed mode; it passed.
  Container inspection confirmed no `DIVERSE_TASKQ_*` env vars remain on
  `diverse-data-api-staging`.
- Follow-up observation at `2026-07-09 16:43:34 -03` passed with the gate still
  closed: public closed canary passed, container inspection returned no
  `DIVERSE_TASKQ*` env vars, and `taskq.jobs` had no queued/running rows
  (`failed/non_retryable=3`, `succeeded/succeeded=2`, all expected canaries).
  API logs for the observation window showed only expected taskq read routes and
  the closed-mode 403 mutation refusal; no taskq errors or tracebacks.
- Diverse staging packet status: technically clean for the first court lane.
  Production flip remains blocked by the canonical spec until qdarte completes
  its required 24-hour production observation gate; do not enable production
  taskq for Diverse before that external gate is green.
- Focused verification:

```bash
uv run pytest tests/test_platform_config.py tests/test_taskq_routes.py -q
uv run ruff check src/diverse_data_api/platform/config.py \
  src/diverse_data_api/app/api.py \
  src/diverse_data_api/domains/queue/contracts.py \
  src/diverse_data_api/domains/queue/taskq_api.py \
  src/diverse_data_api/domains/queue/taskq_service.py \
  tests/test_platform_config.py tests/test_taskq_routes.py \
  alembic/versions/20260709_e1a2b3c4d5f6_add_taskq_schema.py
```
