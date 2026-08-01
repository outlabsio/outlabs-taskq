# ADR-036 — Trusted host-effect fence

**Status:** accepted  
**Date:** 2026-07-30  
**SQL contract:** 0.2.6  
**Migration:** `0018_trusted_effect_fence.sql`

## Context

Some co-resident hosts execute a domain mutation through their own database
transaction while a TaskQ attempt is live. The mutation must be rejected after
lease loss and must serialize with settlement, but host roles must not receive
direct privileges on `taskq.jobs`.

Reading `taskq.jobs ... FOR UPDATE` from host code breaks TaskQ's table-privilege
wall. Merely reading an observer projection is also insufficient: it neither
binds the current attempt and worker nor holds the job-row lock through the
domain transaction.

## Decision

SQL contract 0.2.6 adds exactly one domain-neutral function:

```sql
taskq.lock_active_effect_attempt(
  p_job_id uuid,
  p_attempt_id uuid,
  p_worker_id text,
  p_queue text,
  p_job_type text
)
RETURNS TABLE(payload jsonb, workflow_id uuid, workflow_counts jsonb)
```

The function is `SECURITY DEFINER`, owned by `taskq_owner`, has the canonical
pinned search path, and is executable only by `taskq_producer`. It locks the
matching job row and returns one row only while all of these are true:

- the job is `running`;
- `current_attempt_id`, `worker_id`, `queue`, and `job_type` exactly match;
- the database-clock lease is still live; and
- cancellation has not been requested.

Otherwise it returns zero rows. Inputs are strict and bounded using the
existing job/worker/queue/job-type domains. The projection contains the
admitted payload, workflow id, and an exact status-count object for that
workflow. For a detached job, `workflow_counts` is null. For a workflow member
it contains non-negative `blocked`, `queued`, `running`, `succeeded`, `failed`,
and `cancelled` counts computed under the same statement snapshot. This lets a
host make a workflow-aware domain effect without a second direct read from
TaskQ relations. Headers, fence material, progress, result, and error are never
returned.

The caller must invoke the function inside the same transaction as its domain
effect. Holding the returned row lock makes settlement, cancellation, expiry,
and another effect transaction serialize on the job row. A host may then bind
the requested domain subject to the admitted payload before writing.

`taskq_producer` is selected because the co-resident API already owns atomic
publication and the effect endpoint is part of that trusted host boundary.
Possession of an attempt id remains necessary. The function does not grant
claim, heartbeat, release, settlement, operator, observer-page, or direct table
access.

## Compatibility

This is an additive direct-SQL contract patch. It adds no HTTP route, wire
field, TQ outcome, capability flag, migration ordering exception, or
authentication-provider dependency. Protocol document revision remains
1.0.15 and wire major remains 1. Runtimes that do not use trusted host effects
may ignore the function.

## Required evidence

- fresh and 0.2.5→0.2.6 migration on PostgreSQL 16 and 18;
- exact function identity, owner, volatility, security and pinned search path;
- producer success and every other capability-role denial;
- no direct/effective table privileges for any capability role;
- active-match, detached and workflow-count projections, expired, cancelled,
  mismatched-attempt, worker, queue and job-type vectors;
- a two-session race proving the effect lock blocks settlement until the effect
  transaction commits; and
- installed wheel/sdist migration and manifest parity.
