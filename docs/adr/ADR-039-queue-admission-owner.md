# ADR-039 — Immutable queue admission owner

Status: implemented in migration 0046 (SQL contract 0.6.10; candidate only, not published).

No live verification claim is made in this ADR; downstream API/runtime integration is outside this TaskQ-only PR scope.

TaskQ can bind a queue once to an existing database role that is already a
member of `taskq_producer`. Binding requires target attestation, operator
privilege, and an empty queue. The binding cannot be cleared or changed. This
role binding is not a tenant, API-key, or host-domain authorization identity;
those checks remain in the application or host.

`jobs` has a SECURITY DEFINER trigger. It checks `session_user` (not
`current_user`) so the bound producer role survives SECURITY DEFINER entry
points. There is no runner admission bypass: `taskq_runner` is not admitted by
this owner check, including for workflow continuations. Terminal jobs on bound
queues cannot transition back to any non-terminal state. This blocks SQL, CLI,
HTTP, single, and bulk generic terminal redrive at the database boundary,
while unfinished retry transitions remain governed by existing settlement rules.

Binding does not create or name an application role. Operators must provision
and grant the existing producer role separately. The SQL binding function is
one-shot: a repeated raw bind, including the same values, returns `TQ409`; it
is not an upsert. A host bootstrap may be idempotent by reading and accepting
the already-matching binding before calling the function, but no generic profile
rebind or clear operation exists. Profile reads expose owner and actual active
depth through a locked-safe SQL surface.

Rollout: apply migration only after runtime containing this contract is
available. Verify role membership, target attestation, trigger and function
catalog parity before opening a bound queue. Rollback is pause-and-forward;
there is no downgrade that clears ownership.
