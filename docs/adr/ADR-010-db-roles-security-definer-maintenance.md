# ADR-010 — Database roles, SECURITY DEFINER hardening, maintenance split

**Status:** Accepted 2026-07-18
**Resolves:** D-02, D-03; amends Unified Spec §4 (role model), §13.5 (janitor)

## Context

Two verified defects. First, the spec's `SECURITY DEFINER` functions pin no `search_path` and never revoke PostgreSQL's default `EXECUTE ... TO PUBLIC` — the textbook definer-function escalation surface (attacker-writable schema shadows an unqualified reference; any database user can call privileged functions). Second, §13.5 scheduled `REINDEX INDEX CONCURRENTLY` from inside the janitor: concurrent reindex cannot run in a transaction block, and a PL/pgSQL function always is one — as designed, the step can never execute.

## Decision

**Roles (capability model, replacing the single `taskq_worker`):**

| Role | Capability |
|---|---|
| `taskq_owner` | `NOLOGIN`; owns schema + all objects; never used by an application |
| `taskq_producer` | enqueue + its typed results |
| `taskq_runner` | claim, heartbeat, fenced settlement verbs |
| `taskq_observer` | safe views/read functions; no mutation |
| `taskq_operator` | pause/resume, cancel, redrive, expire, transactional maintenance functions |

Deployment credentials are memberships (a facade DB user typically holds producer+runner+observer; the CLI operator role adds operator). Where the spec text says `taskq_worker`, read "the capability role granted that function family"; the umbrella name may persist as a legacy grant during host migration only.

**Function hardening contract (every taskq function, enforced by migration + tests):** owned by `taskq_owner`; `SET search_path = pg_catalog, taskq, pg_temp` (safe because the schema is fixed, ADR-002); fully schema-qualified references anyway; `REVOKE EXECUTE ... FROM PUBLIC` in the same migration that creates it; `GRANT EXECUTE` to the smallest capability role; no identifier ever interpolated from caller input; privilege-regression tests run as untrusted roles (harness T2), including shadow-object attempts.

**Maintenance split:** `taskq.janitor(...)` keeps only **transactional** work — reaping backstop, terminalization sweeps, retention deletes, event pruning, (0.3+) archive movement and partition rotation. Out-of-transaction maintenance — `REINDEX ... CONCURRENTLY`, future PG19 `REPACK CONCURRENTLY` — moves to an external **`taskq maintenance`** CLI/daemon: admin credentials, autocommit, per-run advisory lock, bloat/age thresholds, logged plan, `--dry-run`, explicit opt-in for expensive operations, safe under double invocation. The `taskq_index_bytes` alert (§12.2) remains the trigger signal; the weekly cadence becomes this command's default schedule (host cron/Coolify).

## Consequences

- Spec §4 role block and §13.5 amended; the janitor's savepoint-per-pass structure is unchanged for what remains.
- 0.1 janitor trigger per ADR-009 (hardwired tick pass) — reaping never depends on the maintenance CLI.
- Every new privileged surface added later must pass the same hardening checklist before merge.
