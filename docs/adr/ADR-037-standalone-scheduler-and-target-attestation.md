# ADR-037 — Standalone scheduler and database-attested target identity

**Status:** Accepted 2026-08-03

**Owner approval:** 2026-08-03

**SQL contracts:** 0.2.7 backing / 0.3.0 activation
**Migrations:** `0019_scheduler_target_identity.sql`, `0020_standalone_scheduler.sql`
## Context

TaskQ already has database-time recurrence evaluation, leased and fenced
schedule claims, a permanent occurrence identity, and atomic occurrence-to-job
enqueue. The scheduling loop nevertheless lives inside the optional FastAPI
runtime. API lifecycle, multiprocess composition, deployment overlap, and
scheduler health are therefore coupled.

The package's current `environment`, `expected_environment`, and
`allow_production` settings are local assertions. They cannot prove what
database a DSN actually names. A process may label a production DSN
`development`; manifest apply is an especially likely accidental mutation and
does not pass through schedule claim/fire.

The first standalone-scheduler review returned PASS WITH CONDITIONS. It
required target enforcement on all schedule writes/runtime, non-tautological
pin provenance, and an overlap/audit model grounded in TaskQ's real job state
machine. The owner accepted the adjudicated design and opened implementation.

## Decision

### Process boundary

`taskq scheduler` is a framework-neutral supervised process. It evaluates due
claims and atomically enqueues ordinary TaskQ jobs; it never imports application
routers or executes handlers. `taskq scheduler --once` is a first-class bounded
mode and is preferred for scale-to-zero databases. FastAPI's embedded loop is a
one-release compatibility adapter over the same core and attestation contract.

### Staged identity contract

Migration 0019 is additive and changes SQL metadata to 0.2.7 without activating
new runtime semantics. It creates one owner-only target-identity row with:

- an immutable-until-rotation installation UUID;
- environment `unbound` initially;
- positive binding version;
- bound timestamp/actor; and
- a private attestation secret.

It also creates an append-only binding audit and hardened functions to read the
safe identity, bind/rebind it, establish a transaction-local attestation, and
verify that attestation. No capability role may bind or rotate identity.

Migration 0020 refuses unless the target is bound. It installs the v1 scheduler
contract, activates `scheduler_v2` and `target_attestation`, and moves metadata
to 0.3.0. Applying all pending migrations to an unbound installation therefore
commits 0019, stops safely at 0020, and directs the operator to bind before
rerunning migration. It never guesses an existing database's environment.

A true production disaster-recovery restore retains its installation identity
and deployment pin. A staging/development clone is rebound by an owner/admin
operation that rotates the installation UUID and increments the binding
version. Rotation invalidates every prior attestation and deployment pin.

### Attestation mechanics and provenance

The caller supplies expected environment, optional expected installation UUID,
and production opt-in from static trusted deployment configuration. The
package must never fill expected values by reading the target. Production
requires both `allow_production=true` and an installation UUID pin; non-production
requires an environment match and may also pin the UUID.

`taskq.attest_target(...)` validates those values and installs a transaction-local
opaque MAC bound to the PostgreSQL backend, transaction, installation UUID,
environment, and binding version. The secret remains owner-only.
`taskq.require_target_attestation()` recomputes and verifies the MAC. A caller
cannot forge an attestation by setting a custom GUC, and a token cannot survive
transaction or identity rotation.

After activation, every scheduler mutation requires that attestation in the
same transaction: put, retire, pause/resume, claim, fire, error, tick, janitor,
and manifest apply. The compatibility adapter uses the same transport hook.
Direct-SQL worker claim establishes the same attestation before `claim_jobs`;
HTTP workers retain their authenticated remote-target boundary.

Read-only `target show`, `scheduler doctor`, and manifest `plan` may inspect the
safe fingerprint without attesting and never claim, enqueue, or mutate.

Target refusal is a stable non-retryable validation outcome with a bounded
reason (`target_unbound`, `environment_mismatch`, `installation_mismatch`,
`production_not_allowed`, `production_pin_required`, or
`target_attestation_required`). Credentials, DSNs, raw SQL, the attestation
secret, and the complete installation UUID are absent from logs and diagnostics.

### Schedule decisions and overlap

The occurrence invariant becomes one durable outcome per selected nominal
occurrence, not necessarily one job. `schedule_occurrences` gains outcome and
decision identity with nullable job ID. An immutable `schedule_decisions` row
records every evaluation/action using database `as_of`, definition version,
cursor range, action token, result, and bounded summary.

This split prevents `skip` after long downtime from manufacturing one row per
missed minute. Initialization, skip advancement, re-anchor, and errors are
decision summaries. Fired, late-rejected, and overlap-rejected candidates are
occurrence rows.

V1 overlap is `forbid` or `allow`, defaulting to `forbid` for new definitions.
`forbid` checks every earlier occurrence for the same schedule whose job still
exists in `blocked`, `queued`, or `running` inside the fire transaction.
Terminal or janitor-removed jobs do not block; redrive can make an older job
active again. This is distinct from `concurrency_key`, which buffers jobs and
serializes execution.

V1 catch-up names remain `skip`, `fire_once`, and `fire_all`. V1 resume is
explicit `from now`; it records a re-anchor decision. Invalid deterministic
calendar evaluation auto-pauses after three consecutive errors. Transient
database/runtime errors do not increment that counter.

### Desired state and scope

Manifest keys are immutable schedule identities. YAML requires `task`, `queue`,
and exactly one recurrence; UTC, `skip`, and `forbid` are the visible defaults.
Definitions carry namespace, source, display name, and a canonical definition
hash. Apply may mutate only matching namespace/source ownership. Missing owned
keys are drift. V1 has no bulk prune; retirement is explicit, per-key, and
version-checked.

Jitter, `buffer_one`, destructive overlap, historical resume/backfill, natural
language cron, trigger plugins, holiday calendars, DAG orchestration, and a
cross-company global scheduler are outside v1.

### Compatibility

The 0.2.7 backing step changes no existing function behavior. The 0.3.0
activation is intentionally fail-closed: old runtimes do not support it, and
new direct-SQL transports establish attestation before guarded functions.
Runtime compatibility remains a closed set, not a version range. A deployment
first installs 0.2.7-capable code, binds identity, then activates 0.3.0 and the
new runtime. Contract metadata is rechecked after reconnect.

## Consequences

- Scheduler lifecycle and observability are independent from FastAPI.
- A wrong DSN cannot be made safe merely by changing a local environment label.
- Manifest writes receive the same target guard as schedule firing.
- Upgrades and clones have an explicit stopped checkpoint instead of an unsafe
  inferred environment.
- Active/active requires no new leader service; existing claims, fencing, and
  occurrence uniqueness remain the coordination mechanism and must pass the
  hard-kill/race evidence.
- The package gains schema and operational surface, but v1 scope is bounded and
  its minimal manifest remains small.
