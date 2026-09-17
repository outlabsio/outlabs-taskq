# ADR-038 — Transaction-bound terminal host-effect fence

Status: implemented candidate, not released
Date: 2026-09-10
SQL contract: 0.6.9; additive migration `0045_terminal_effect_fence`

## Problem

A co-resident domain importer must durably record native terminal failure without
racing operator redrive. `get_job` does not lock; queue pause does not fence
administrative redrive. `lock_active_effect_attempt` correctly excludes terminal
jobs. Direct privileges on TaskQ tables are not an acceptable workaround.

## Decision

Add one SQL-only function, analogous to ADR-036:

```sql
taskq.lock_terminal_effect_job(
  p_job_id uuid, p_queue text, p_job_type text,
  p_expected_environment text, p_expected_installation_id uuid,
  p_allow_production boolean DEFAULT false
)
RETURNS TABLE(
  status text, outcome text, finished_at timestamptz,
  payload jsonb, workflow_id uuid
)
```

Only `taskq_producer` receives EXECUTE. The function is owned by `taskq_owner`,
SECURITY DEFINER, VOLATILE, with pinned `pg_catalog, taskq, pg_temp` search path
and no PUBLIC EXECUTE. Existing table/role grants and historical SQL stay intact.
On SQL contract 0.6.11, corrective migration 0047 first calls the shared
queue-admission owner guard; 0047 requires the already-applied 0.6.10 state. A
producer on a bound queue must be a member of that immutable owner role; denial
is `TQ425` before any payload-bearing job lookup or job-row lock. Migration 0047 replaces the 0045 body after the 0046 guard exists,
preserving function identity and leaving 0.6.9 installs with their original
unbound behavior. Migration 0046 remains unchanged history. Continuation
provenance checks are not a bypass for this direct fence call.
Required installation UUID plus environment/production opt-in use existing target
attestation. Database-role ownership and target identity are not a domain tenant
or API-key identity. TaskQ is not the authority for a host's domain tenant: the
trusted host MUST verify its tenant, owning job, generation/checkpoint, admitted
payload, and its authenticated caller's permission before publishing a domain
mutation.
No runner/observer/operator/housekeeper payload access is added.

The exact job UUID/queue/type must be succeeded, failed, or cancelled. A missing,
nonterminal, or mismatched job returns no row. The selected row is locked FOR
UPDATE through the caller's transaction, serializing with redrive and retention.
At READ COMMITTED, a redrive that wins first causes the waiting predicate to be
rechecked and return no row. An earlier observer result never acts as authority.
At stronger isolation, a serialization failure must roll back/retry the entire
host transaction. The result contains admitted payload but no errors, results,
headers, progress, attempt tokens, or worker identity.

`taskq.sql.lock_terminal_effect_job(connection, ...)` exposes the borrowed
SQLAlchemy transaction adapter and immutable `TerminalEffectJob`. It refuses a
connection without a transaction and never commits/closes the caller's connection.
This is not a new HTTP command, runner mutation, queue framework, or authorization
provider. The lock is a snapshot-to-domain-publication fence, not a permanent
ban on redrive AFTER the host transaction commits. TaskQ does not reserve or
release the host's domain state and does not choose that recovery policy; hosts
must retain their own failed-state guard and provide a separate deliberate
recovery policy.

## Compatibility and verification

Package source baseline: `0.1.0a38` / `5a622cd530c88db9aa92ad9e8e64c31aa883e4df`.
The candidate package is `0.1.0a39` and unpublished; artifact publication and explicit
downstream pins remain release gates. The candidate runtime accepts 0.6.9, 0.6.10, and 0.6.11 alongside its prior
contracts. Deploy the reviewed runtime everywhere BEFORE forward-only migrations
0045/0046/0047; 0045 requires 0.6.8, 0046 requires 0.6.9, and 0047 requires
0.6.10, so a37 installations also need unchanged 0044 before this chain. A 0.6.9
installation remains valid and unbound-compatible; applying 0046 then 0047
installs owner enforcement without changing the 0045 function identity.

`tests/test_contract_0_6_9.py` proves exact identity, environment/install rejection,
capability-role denials, absence of direct producer table privileges, both redrive
race orders, caller-transaction adapter behavior and full catalog verification.
Existing migrations were not edited. No production target, credentials, package
publish, or deployment was involved. Disposable PostgreSQL role/race/catalog
verification remains a release gate; no live verification claim is made here.
Downstream API integration is out of scope and uses no TaskQ source overlay in
this candidate.
