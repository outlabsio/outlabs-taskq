# TaskQ Standalone Scheduler Specification

**Status:** owner-approved implementation contract

**Date:** 2026-08-03
**Decision:** [ADR-037](adr/ADR-037-standalone-scheduler-and-target-attestation.md)

## 1. V1 acceptance surface

The implementation is complete only when all of these exist and are evidenced:

1. `taskq scheduler` and bounded `taskq scheduler --once`, sharing one
   framework-neutral runtime with the temporary FastAPI compatibility adapter.
2. Database-attested target identity, explicit bind/rotation, safe fingerprint,
   and function-level enforcement for every schedule mutation/runtime path.
3. A minimal versioned YAML manifest, deterministic JSON/human `plan`,
   source-scoped idempotent `apply`, and explicit per-key retirement.
4. Durable decision and selected-occurrence outcomes with `skip`, `fire_once`,
   `fire_all`, maximum lateness, `forbid`, and `allow`.
5. Deterministic-definition auto-pause after three consecutive errors and
   audited pause/resume `from now`.
6. Advancement health, scheduler/maintenance timing, safe structured logs, and
   operator read models without payload PII.
7. Fresh/upgrade PostgreSQL 16/18, kill/race, wrong-target, migration,
   manifest-ownership, packaging, and coding-agent ergonomics evidence.

## 2. Minimal manifest

```yaml
version: 1
namespace: qdarte
source: deployment

schedules:
  intake-submission-review-pull:
    task: intake.submission_review_pull
    queue: intake
    interval_seconds: 900
```

Canonical defaults, fully expanded by `plan`:

```yaml
timezone: UTC                 # cron only
catchup: skip
max_catchup: 1
overlap: forbid
max_lateness_seconds: null
state: active
payload: {}
```

Catch-up policy is evaluated against the database clock:

- `skip` fires one ordinarily due occurrence when no later occurrence is also
  due. If two or more occurrences accumulated while the clock was unavailable,
  it discards that backlog and advances to the next future instant.
- `fire_once` fires the latest due occurrence, whether one or many accumulated.
- `fire_all` fires due occurrences in order, bounded by `max_catchup`.

The distinction matters for continuous clocks: polling a few milliseconds after
an instant is normal operation, not catch-up. Consequently the default `skip`
policy must still emit an ordinary recurring run; it is not a mute schedule.

An advanced definition may supply ordinary TaskQ target options, but the
compiler emits exactly the existing `ScheduleJobTarget` shape. The scheduler
does not import the task registry. An application-side typed compiler may
derive `queue` from a registry before producing the canonical manifest.

## 3. Stable identity and ownership

The database stable name is `<namespace>.<manifest-key>`. Namespace, source,
and key use the existing lower-case schedule-name grammar; the combined name
remains within 120 UTF-8 bytes. Display name is mutable and never participates
in idempotency.

The canonical definition hash is SHA-256 of sorted compact JSON after defaults
and target/recurrence normalization. Database `put_schedule` stores namespace,
source, display name, and hash. Updating an existing row with a different
namespace/source fails `TQ409 schedule_owner_mismatch` before mutation.

`plan` lists owned schedules through a bounded cursor, reports create/update/
unchanged/drift, warns on likely key rename, and never mutates. `apply` rechecks
versions and applies create/update/unchanged. Drift remains active and produces
a non-destructive report. `retire KEY` is a separate version-checked command.

## 4. Target identity lifecycle

### 4.1 Safe read model

`taskq.get_target_identity()` returns:

- full installation UUID to read-only capability-role and owner connections;
- environment;
- binding version;
- bound timestamp;
- SQL contract version; and
- capabilities.

Human output abbreviates the UUID. JSON output contains the UUID so deployment
automation can pin it without scraping. Neither output contains hostnames,
credentials, secrets, or DSN text.

### 4.2 Bind and rotate

`taskq target bind ENV --actor ACTOR` requires an owner/admin connection and an
expected current installation UUID. Initial bind retains the seeded UUID and
increments binding version. Rebinding a bound target requires `--rotate`, a
reason, and expected binding version; it generates a new UUID. Every operation
appends a binding audit row.

Allowed built-in environment names are `development`, `staging`, `production`,
and `test`. A custom value may use lower-case `[a-z0-9][a-z0-9_-]{0,62}`.

### 4.3 Runtime settings

All direct-SQL scheduler/worker configurations include:

```text
TASKQ_EXPECTED_ENV
TASKQ_EXPECTED_INSTALLATION_ID   # required for production
TASKQ_ALLOW_PRODUCTION=false     # explicit true required for production
```

The expected fields are never populated from `get_target_identity`. Doctor may
compare and report them, but runtime startup and transport construction use only
static settings supplied before the database connection.

## 5. Scheduler modes

Continuous mode polls with bounded jitter/backoff, responds to SIGINT/SIGTERM,
and reports advancement health. It begins no new claim after shutdown and lets
the current SQL transaction finish.

`--once` repeatedly claims/evaluates until nothing is due or either
`--max-batches` or `--max-runtime-seconds` is exhausted. A successful pass,
including nothing due, exits 0. Target/config refusal exits 2; unavailable or
version/capability failure exits 3; unexpected internal failure exits 1. JSON
output distinguishes `nothing_due`, `fired`, and `budget_exhausted`; budget
exhaustion is exit 3 so an external timer alerts instead of silently leaving
unknown backlog.

Recommended deployment:

- always-on PostgreSQL: continuous supervised process;
- scale-to-zero PostgreSQL: platform/OS timer plus `--once` at the business
  cadence; and
- one logical scheduler per database/environment until active/active evidence
  passes.

The scheduler is only a clock: it evaluates recurrence and durably enqueues
ordinary TaskQ jobs. It never imports application registries or executes task
handlers. Worker placement is therefore an independent application decision.
One host-native or otherwise consolidated worker process may subscribe to
multiple queues through one combined registry; TaskQ does not require a worker
container per queue, task, or schedule. Keep workers on existing local worker
hosts by default. Add a cloud worker only when the task has an explicit
availability, latency, or private-network requirement that a local worker
cannot meet.

## 6. Decision and occurrence records

Every claim action inserts one immutable decision keyed by schedule and action
token. Fields include schedule, definition version, database `as_of`, cursor
from/to, action, selected count, jobs enqueued, bounded summary, scheduler
identity, and created time.

Every selected due instant inserts one immutable occurrence keyed by schedule
and due instant with decision ID, outcome (`fired`, `late_skipped`, or
`overlap_skipped`), nullable job ID, and created time. The existing uniqueness
continues to fence duplicate fire.

Within one bounded `fire_all` batch, `overlap: forbid` evaluates each nominal
instant in order. The first fired occurrence creates a queued job in the same
transaction, so every later occurrence in that batch observes the active job
and records `overlap_skipped`. Use `overlap: allow` when every bounded catch-up
occurrence must enqueue; execution-level `concurrency_key` remains the option
for queueing all occurrences while serializing their handlers.

The fire transaction order is:

1. lock and fence the schedule;
2. validate action bounds;
3. for each selected due instant, converge on the occurrence identity;
4. reject stale by maximum lateness, else evaluate overlap, else enqueue;
5. inject immutable schedule headers;
6. write the decision and advance the cursor atomically.

Schedule headers are package-owned and cannot be overridden:

```json
{
  "taskq_schedule": {
    "schedule_id": "uuid",
    "schedule_key": "namespace.key",
    "occurrence_id": "uuid",
    "definition_version": 1,
    "scheduled_for": "RFC3339 UTC",
    "enqueued_at": "RFC3339 UTC",
    "lateness_seconds": 0,
    "backfilled": false
  }
}
```

## 7. Overlap and error semantics

`forbid` searches occurrence-linked jobs for the schedule in
`blocked|queued|running`. It does not treat a skipped occurrence with NULL job
as the predecessor. An absent row after janitor or any terminal status does not
block. A redriven earlier job blocks again. The outcome is observable and does
not enqueue.

`allow` performs no scheduler-level active-job check. Applications that need
every occurrence but serialized effects use `concurrency_key` and an
appropriate concurrency limit.

Only errors classified by the evaluator as deterministic definition/calendar
errors increment `consecutive_definition_errors`. On the third, the same fenced
error transaction pauses the schedule, stores bounded reason/actor/time, writes
the decision, and clears the claim. Definition change resets the counter.

## 8. Retention

Decision and occurrence rows have a fixed v1 retention floor of 90 days.
Janitor removes a row only when it is older than the cutoff and its due/cursor
position is strictly behind the schedule cursor. Occurrence rows whose linked
job still exists are retained. A prune-followed-by-refire test must prove that
fire bounds, not retained history alone, prevent an old occurrence from being
proposed again.

## 9. Required evidence

- fresh and upgrade migrations on PostgreSQL 16 and 18, including the unbound
  stop/bind/resume flow;
- exact manifest, grant, owner, SECURITY DEFINER, pinned search path, RLS/table
  privilege, and installed artifact parity;
- static pin, forged GUC, echo-back client, environment mismatch, production
  opt-in, clone rotation, and DR-retain drills;
- concurrent claim/fire, expiry/reclaim, open transaction across expiry,
  response loss, and real-process kill boundaries;
- overlap across every active/terminal/deleted/redriven/intervening-skipped
  state and separate `concurrency_key` serialization;
- DST gap/fold crossed with all catch-up policies;
- source ownership, collision, rename, identical reapply, drift, and retirement;
- auto-pause classification/reset and ledger retention/no-refire; and
- wheel/sdist plus a fresh coding-agent minimal/advanced manifest exercise.
