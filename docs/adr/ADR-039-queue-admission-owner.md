# ADR-039 — Immutable queue admission owner

Status: base ownership contract implemented in migration 0046; corrective behavior implemented in migration 0047 (SQL contract 0.6.11; candidate only, not published).

No live verification claim is made in this ADR; downstream API/runtime integration is outside this TaskQ-only PR scope.

TaskQ can bind a queue once to an existing database role that is already a
member of `taskq_producer`. Binding requires target attestation, operator
privilege, and a queue with no jobs or admissions, including retained
admission receipts. The binding cannot be cleared or changed. This
role binding is not a tenant, API-key, or host-domain authorization identity;
those checks remain in the application or host.

`jobs` has a SECURITY DEFINER trigger. It checks `session_user` (not
`current_user`) so the bound producer role survives SECURITY DEFINER entry
points. Runner settlement has no blanket admission bypass. Its private
continuation insert is admitted only when the locked, succeeded parent proves
that its bound source owner is non-null and exactly equals the target queue's
immutable owner, with the engine-reserved `chain:<parent UUID>:<step>` key and
workflow identity derived from that parent. This permits detached and workflow
member followups on same-owner queues, while rejecting foreign-owner and
unbound-parent to bound-target continuations. Public entrypoints cannot create
rows in the reserved `chain:` namespace; `try_enqueue` may return an existing
key replay before enqueue validation. Caller-supplied parent/workflow values or
GUC state cannot provide this provenance. Terminal jobs on bound
queues cannot transition back to any non-terminal state. This blocks SQL, CLI,
HTTP, single, and bulk generic terminal redrive at the database boundary,
while unfinished retry transitions remain governed by existing settlement rules.
The terminal host-effect fence also calls the shared owner guard before payload
lookup or job-row locking: bound queues deny a non-owner producer with `TQ425`,
while unbound queues retain generic-producer behavior. Continuation provenance
cannot bypass this direct fence check.

Binding does not create or name an application role. Operators must provision
and grant the existing producer role separately. The SQL binding function is
one-shot: a repeated raw bind, including the same values, returns `TQ409`; it
is not an upsert. A host bootstrap may be idempotent by reading and accepting
the already-matching binding before calling the function, but no generic profile
rebind or clear operation exists. Profile reads expose owner and actual active
depth through a locked-safe SQL surface.

Rollout: apply migration only after runtime containing this contract is
available. For existing 0046 installations, apply corrective migration 0047
and verify its 0.6.11 metadata transition, role membership, target attestation,
trigger and function catalog parity before opening a bound queue. Rollback is pause-and-forward;
there is no downgrade that clears ownership.
