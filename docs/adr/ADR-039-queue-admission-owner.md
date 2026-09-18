# ADR-039 — Queue admission owner

Status: candidate implemented through migration 0048 (SQL contract 0.6.12; not published)

No live or production verification claim is made in this ADR. Consumer and
release-train validation are release gates for the candidate.

## Decision

TaskQ can bind a queue to an existing PostgreSQL role that is already a member
of `taskq_producer`. This is a database capability boundary. It does not replace
a host's tenant, user, API-key, route, or business authorization.

The default binding operation is one-shot and requires target attestation,
operator privilege, an empty job table for the queue, no admission state, and
no active schedule targeting the queue. A repeated bind, including the same
role, returns `TQ409`. A host bootstrap may read and accept an already-matching
binding, but it must not treat bind as an upsert.

The authoritative owner identity is the PostgreSQL role OID. The original role
name remains as a diagnostic snapshot. A role rename therefore preserves the
boundary; a missing role fails closed with `TQ425`. The observer identity read
returns the current name, OID, presence, maximum depth, and active depth.

## Admission boundary

Every direct admission and replay path checks membership using `session_user`
before returning or creating a job. This includes reservation lifecycle,
`enqueue`, `try_enqueue`, `enqueue_many`, idempotency replay, and workflow-step
replay. SECURITY DEFINER entry points cannot substitute their owner as the
caller identity.

Job inserts also pass a SECURITY DEFINER trigger. Runner continuations are
admitted only when the succeeded parent proves the same owner OID and the
engine-derived parent, workflow, policy, step, and `chain:` key are consistent.
Caller-supplied parent/workflow fields or GUC state do not provide provenance.

A separate housekeeper login may fire a schedule into a bound queue. The
schedule trigger snapshots the target queue owner OID. `fire_schedule` creates
the occurrence first, and the job trigger accepts the insert only when that
matching occurrence is uncommitted in the current transaction, its job is not
yet attached, and schedule identity, owner, queue, and job type all agree.
Headers alone cannot manufacture this proof. The occurrence and job still
commit or roll back atomically.

Normal admissions take a `FOR KEY SHARE` lock on the queue row. Owner mutation
explicitly takes `FOR UPDATE`. This closes bind/admission races while allowing
concurrent producers and ordinary pause or configuration updates.

Terminal jobs on bound queues cannot transition back to a nonterminal state.
This blocks generic SQL, CLI, HTTP, single, and bulk redrive at the database
boundary. Unfinished retry transitions retain their existing settlement rules.
The terminal host-effect fence checks ownership before payload lookup or job-row
locking. Continuation and schedule provenance cannot bypass that direct call.

## Adoption and recovery

Strict bind remains the safe primitive for a new empty queue. Existing queues
need a controlled adoption path because retained terminal jobs and admitted or
cancelled receipts are durable history rather than active work.

`adopt_queue_admission_owner` can bind an unbound queue with retained history;
`rotate_queue_admission_owner` can replace a bound owner, including recovery
after the old role was dropped. Both require:

- a dedicated target role that is already a `taskq_producer` member;
- operator privilege and target attestation;
- a paused queue;
- no blocked, queued, or running job;
- no reserved admission;
- no active schedule targeting the queue; and
- a nonempty actor and reason recorded in `queue_audit` with old/new OIDs.

There is no generic clear operation. Ownership recovery is deliberate,
auditable, paused, and forward-only.

## Rollout

Deploy package 0.1.0a40 to every API, worker, scheduler, operator, migrator, and
maintenance process before applying migrations 0045–0048. The candidate runtime
accepts the old and new contracts during that staged transition. Confirm no old
runtime can reconnect, then migrate, verify the closed catalog, bind or recover
while queues and schedules remain paused, and run owner/non-owner LOGIN tests on
the deployment artifact before a bounded canary.

Rollback before migration may restore the prior artifact. After migration,
pause and recover forward on a compatible runtime; no downgrade clears the
owner or reverts contract metadata.

## Verification

The contract suite covers fresh installs and an immutable 0046 upgrade, real
LOGIN roles, every admission lifecycle and replay path, bind/admission races,
queue-configuration concurrency, role rename, adoption, rotation, terminal
redrive, runner continuations, separate-housekeeper schedule firing, target
attestation, exact grants, and catalog verification. PostgreSQL 16, 17, and 18,
built-artifact, release-train, and canonical consumer checks remain required
before publication or deployment.
