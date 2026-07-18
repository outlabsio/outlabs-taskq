# ADR-004 — Ordered migrations are canonical; the snapshot is generated

**Status:** Accepted 2026-07-18
**Resolves:** D-07; amends borrowed-feature 13 and extraction brief §6.2/§8.2

## Context

Feature 13 described both a full `schema.sql` ("idempotent install target") and `migrations/` — two things called canonical. A full schema file is not naturally safe to rerun against a live install, and host integration (Alembic is synchronous; the taskq client is async) was left to improvisation.

## Decision

1. **Ordered, immutable package migrations are the single source of truth** for install and upgrade.
2. `taskq.schema_migrations` records id, package version, checksum, and applied timestamp. `taskq migrate` applies missing migrations under an advisory lock; `taskq verify` compares objects, signatures, ownership, privileges, and checksums **without changing state**; lock-recovery tooling ships beside them.
3. `schema.sql` becomes a **generated snapshot** — for review, diffing, and clean test fixtures. It never upgrades a live database.
4. Host Alembic revisions call a **supported synchronous adapter** (or execute a version-pinned migration bundle); no ad-hoc async-from-sync bridging.
5. Application startup **verifies** compatibility (against `taskq.meta.contract_version` + the feature-12 matrix) and never silently migrates production.
6. Rolling upgrades follow the extraction brief §8.2 sequence: package supporting contracts N and N+1 → fleets upgrade → pre-migration → flip → post-migration → floor raise. Versioned function names during rollout per feature 13.

## Consequences

- Feature 13 §2/§3 amended (snapshot demoted, sync adapter added); harness gains suite T8 (migration/compatibility).
- There is no automatic downgrade; recovery is restore/forward-fix, rehearsed in T8.
