# outlabs-taskq 0.1.0a40 release notes

**Base release:** 0.1.0a39  
**SQL contract:** 0.6.12  
**Protocol document:** 1.0.18  
**Packaged migrations:** 0001–0048  

## Shared-system correction

This release retains the terminal host-effect fence and queue admission owner
introduced by migrations 0045/0046, while forward migrations 0047/0048 close
the cross-consumer paths found during whole-system review.

- Owner checks now cover idempotent and workflow-member replays before returning
  an existing job.
- A separate least-privilege housekeeper can fire a schedule into a bound queue
  using schedule-occurrence provenance created in the same transaction.
- Owner identity is stored by PostgreSQL role OID, so role rename is safe and a
  dropped role fails closed.
- Normal admission takes a `FOR KEY SHARE` queue row lock. It remains compatible
  with ordinary queue configuration and pause updates while serializing against
  the explicit `FOR UPDATE` used by bind, adopt, and rotate.
- Operators can adopt an existing paused, quiesced queue or rotate a missing or
  compromised owner. Retained terminal jobs and receipts do not prevent
  recovery; active jobs, reservations, and schedules do.
- Owner changes and their actor/reason are appended to `queue_audit`.

TaskQ owns the database capability boundary only. Host tenant, user, API-key,
and business authorization remain the consumer's responsibility.

## Required rollout order

1. Build the reviewed a40 wheel and source distribution once. Record their
   hashes and retain the prior artifact.
2. Pause affected queues and their schedules. Deploy a40 to every API, worker,
   scheduler, operator, migrator, and maintenance process while the database is
   still on its current supported contract. Confirm no older process can
   reconnect.
3. Run `taskq db plan`, review the exact target and digest, apply migrations
   through 0048 with the owner credential, and require `taskq db verify` to
   succeed.
4. For a new empty queue, call the one-shot bind. For a queue with retained
   terminal history, use adoption. Use rotation only for deliberate owner
   credential recovery. Keep schedules paused until the resulting owner identity
   and audit row are verified.
5. Prove owner and non-owner LOGIN behavior, separate-housekeeper schedule fire,
   worker claim/settlement, replay denial, and host authorization on the exact
   deployment artifact.
6. Resume one queue and one bounded canary at a time. Confirm terminal counts,
   schedule occurrences, workflow identity, and consumer side effects before
   widening traffic.

Do not migrate first. Runtimes older than this candidate do not recognize SQL
contract 0.6.12 and may not map `TQ425` correctly.

## First consumer rollout

The first planned rollout is the Diverse Data API and its HTTP-only
`diverse-data-workers` fleet. The API is the sole TaskQ database, migration,
facade, producer, operator, and maintenance authority. Workers keep only
lane-scoped HTTP credentials and never receive TaskQ database roles. Publication
and both consumer lock regenerations precede promotion. The API runtime rolls
out first against the old explicitly supported contract; compatible workers
are then prepared and all older processes are fenced before migration.

The initial local-wheel checks proved broad Python compatibility on older
consumer snapshots, but they are not the final consumer release gate. The
current rollout commits must pass their complete suites after locking the
published artifact and must record compatible-behind API boot, exact
post-migration attestation, queue-owner adoption, owner/non-owner and HTTP
authorization checks, separate-housekeeper fire, worker settlement, and a
bounded canary. See
[`evidence/diverse-data-a40-pre-rollout-2026-09-18.md`](evidence/diverse-data-a40-pre-rollout-2026-09-18.md).

## Recovery and rollback

Before migration, rollback can restore the previous artifact. After migration,
pause queues and schedules and recover forward with a40; there is no supported
schema downgrade or ownership clear. If the bound role was dropped or must be
replaced, keep the queue paused and quiesced, attest the target, and rotate to a
new dedicated producer role. Preserve the audit evidence and rerun catalog and
LOGIN-boundary verification before resuming.

Package publication does not authorize a production migration, queue binding,
or deployment; those remain consumer rollout decisions with their own gates.
