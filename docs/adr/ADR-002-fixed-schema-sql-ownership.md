# ADR-002 — Fixed `taskq` schema; SQL functions own correctness

**Status:** Accepted 2026-07-18
**Resolves:** D-09; reaffirms Unified Spec §0/§1/§4

## Context

The SQL contract, role grants, and (per ADR-010) pinned `search_path` all reference schema `taskq` literally, while parts of the Python surface implied an arbitrary `schema=` option (extraction brief §4.4, spec §14 sketch). A parameterized schema would make every migration, grant, verification, and — critically — every `SET search_path` dynamic, multiplying the security and test matrix for a need no host has.

## Decision

1. The schema is **fixed at `taskq`** for all `0.x` releases. `schema=` is removed from the public constructor promise.
2. PL/pgSQL functions in `taskq` remain the correctness contract: claiming, fencing, budgets, and transitions are decided in SQL. Python (and any other client) maps typed outcomes; it never reimplements transitions.
3. No application role holds DML on taskq tables (ADR-010 defines the roles).
4. Multiple installations per database are reconsidered only with a real customer need, full parameterization, and a dedicated security matrix — as a `1.x` question.

## Consequences

- Extraction brief §4.4 and spec §14 constructor sketches amended.
- ADR-010's `SET search_path = pg_catalog, taskq, pg_temp` is safe to pin literally.
- Hosts wanting isolation between projects use separate databases (already the deployment reality).
