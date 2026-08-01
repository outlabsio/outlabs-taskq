# ADR-030 — Workflow counters preserve cancellation lock order

**Status:** Accepted 2026-07-23
**Resolves:** S5-QD-FR-CQ-06
**Amends:** ADR-029 decision 4; Function Manifest §18.3

## Context

ADR-029 requires exact bounded workflow-state counts without a request-time
member scan. Its initial Manifest shape gave the private counter row a foreign
key to the workflow.

The first implementation run reached ADR-026's existing held-open settlement
versus concurrent cancellation history. Updating the counter during settlement
caused PostgreSQL referential-integrity machinery to retain a key-share lock on
the parent workflow. The operator's required `SELECT ... FOR UPDATE` then
waited instead of recording cancellation intent while the job row was
SKIP-LOCKED. `pg_blocking_pids` and `pg_locks` identified the settlement
transaction and workflow tuple. Relaxing that race would regress accepted
cancellation semantics.

A scratch-only prototype removed only the counter foreign key, created counter
rows from workflow lifecycle, and made job transitions update-only. The exact
race passed in 0.20 seconds while queued→running→succeeded counts stayed exact.

## Decision

1. `taskq.workflow_member_counts` is owner-private and keyed by workflow UUID,
   but it has **no foreign key** to `taskq.workflows`. This is deliberate lock
   isolation, not relaxed application integrity.
2. Owner-private `taskq.manage_workflow_member_counts()` and its workflow
   lifecycle trigger create the zero counter row with workflow creation and
   remove it with a successful workflow deletion. Migration 0011 backfills
   every existing workflow before either trigger activates.
3. `taskq.update_workflow_member_counts()` performs UPDATE-only old/new bucket
   transitions. It never inserts through referential integrity during a job
   state change. A missing invariant row while the workflow exists is TQ500 and
   rolls back the originating job mutation.
4. Both private functions are `VOLATILE SECURITY DEFINER`, owner-owned,
   path-pinned and PUBLIC-revoked. No application role receives table or
   function authority.
5. The verifier asserts the exact table, both functions and both trigger
   definitions. Race evidence must include held-open settlement versus
   cancellation plus concurrent counter transitions; arithmetic equality is
   checked against raw jobs.
6. Protocol 1.0.13, SQL contract 0.2.3, migration identity 0011, all public
   composites/functions/routes/capabilities and migration 0012's activation
   sequence remain unchanged.

## Consequences

- Exact workflow counts no longer add a lock edge from job settlement to the
  parent workflow.
- Counter integrity is owned by one private lifecycle invariant rather than a
  foreign key whose locking semantics conflict with cancellation.
- Any future public workflow deletion must preserve lifecycle ordering and add
  its own count-row deletion race; none exists in 0.2.3.
