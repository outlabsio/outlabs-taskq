# outlabs-taskq 0.1.0a37 release notes

**Base release:** 0.1.0a36  
**SQL contract:** 0.6.7  
**Protocol document:** 1.0.18  
**Packaged migrations:** 0001–0043

a37 adds set-based bulk admission for dependency-free workflow members and
closes the queue-counter depth-check implementation gap. It is an additive SQL
contract change; existing single and ordinary bulk request shapes remain valid.

## Workflow-safe bulk admission

`EnqueueManyItem` now accepts the paired `workflow_id` and `step_key` fields.
One `enqueue_many` call may carry up to 1,000 dependency-free members for one
planning workflow and one queue. TaskQ locks the workflow once, validates every
member before writing, resolves exact step replays before sealed/depth checks,
reserves the lifetime member count once, inserts jobs and events set-wise, and
emits one queue notification.

The contract preserves:

- deterministic workflow intent hashes and exact replay outcomes;
- unique workflow steps and active idempotency keys;
- declared-queue, sealed-membership, and lifetime-member-limit fences;
- caller-owned transaction commit, rollback, and savepoint behavior;
- ordered typed results, one per input;
- whole-call rollback for validation, conflict, backpressure, or invariant
  failures.

Dependencies remain outside the bulk surface. DAG edges continue to use
single `enqueue`; workflow bulk is the fast path for independent batch members.

The typed `TaskQ.enqueue_many` facade adds common workflow, retry, concurrency,
headers, TTL, and flow-key overrides. A non-SQL transport receives the same
validated `EnqueueManyItem` values; a caller-owned SQLAlchemy session or
connection is still used directly without TaskQ committing it.

## Counter-backed depth checks

Single and bulk enqueue now read `queue_counters.blocked + queued` when the
`queue_counters` capability is active. This implements the O(1) contract
specified when queue counters shipped. The historical `OFFSET` probe remains
only as a compatibility fallback for a pre-counter schema.

This matters for large caller-owned transactions: the old single path repeated
an increasingly expensive depth scan after every member. It could become
quadratic in the number of uncommitted admissions even though it was not
blocked on a lock or provider limit.

## Performance and conformance evidence

The PostgreSQL contract suite admits 10,000 one-job workflow members as ten
1,000-item calls and requires completion inside a 30-second production budget.
The development PostgreSQL 16 run completed the measured admission body in
9.29 seconds. The production workload that motivated the change took roughly
20–22 minutes for the same member count through 10,000 serial calls.

Regression coverage includes:

- exact ordered create and replay results, including replay after sealing;
- mismatched step intent and duplicate-key conflicts;
- member-limit and max-depth rollback with no partial rows or counter drift;
- caller-owned transaction propagation through the typed facade;
- the real 10,000-member PostgreSQL performance gate;
- fresh/upgrade migration, exact manifest, role/grant, packaging, and HTTP
  catalog parity through SQL contract 0.6.7 / Protocol 1.0.18.

## Compatibility and rollout

The a37 runtime accepts SQL contracts 0.6.6 and 0.6.7. Older runtimes do not
accept 0.6.7, so use this order on every shared installation:

1. Stop new large planners and leave their target queues paused.
2. Pin API, scheduler, worker, migration, operator, and load-lab processes to
   the exact a37 artifact; verify they are healthy against the existing 0.6.6
   database.
3. Apply migration `0043_workflow_bulk_admission` once with the dedicated
   owner/migration credential.
4. Run `taskq db verify`, confirm contract 0.6.7 and capability
   `workflow_bulk_admission`, then canary a bounded bulk plan.
5. Resume ordinary planning only after admission duration, queue counters,
   workflow counts, and concurrent worker health are verified.

Migration 0043 is forward-only. Rolling an application process back to a36
after the database reaches 0.6.7 is not supported; restore the complete a36
application/database pair from the rehearsed rollback boundary if required.

Production mutation still requires the exact installation identity,
`--allow-production`, and `--yes`. Runtime credentials remain separate from
the owner/migration credential.
