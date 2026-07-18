# outlabs-taskq — AGENTS.md

Postgres-native task queue library. **Docs first** — implementation is pre-alpha.

## Source of truth

| Concern | Doc |
|---|---|
| **Accepted decisions (win on conflict)** | `docs/adr/` (ADR-001..010) |
| Protocol / SQL contract | `docs/Task Queue — Unified Design Spec.md` (v1.5) |
| Package + auth boundaries | `docs/Task Queue Library Extraction Design Brief.md` |
| Queue-scoped authorization + provisioning | `docs/Task Queue Authorization & Queue Permissions.md` |
| Own tests / CI / benchmarks | `docs/Task Queue Test & Benchmark Harness.md` |
| Product features to build | `docs/taskq-borrowed-features/` (01–14) |
| Peer provenance | `docs/Task Queue Peer Patterns Research.md` |

## Hard rules

1. SQL functions in schema `taskq` are the contract. Python is a client. Schema name is FIXED (`taskq`, ADR-002).
2. Core package must import without FastAPI and without `outlabs-auth`.
3. Optional extras: `outlabs-taskq[http]`, `outlabs-taskq[outlabs]`.
4. No silent enqueue success (`None` / null). Typed `EnqueueResult` only.
5. Do not reimplement claim/fencing/budget in Python.
6. Every SQL function: owned by `taskq_owner`, pinned `search_path`, PUBLIC execute revoked, granted to a capability role (ADR-010).
7. Never authorize a job mutation from caller-supplied queue/job_type — authoritative row lookup only (ADR-006). Accepted work is never silently dropped (ADR-007).

## Consumers

- `diverse-data-api` / `diverse-data-workers`
- `qdarteAPI` / `qdarte-workers` / `qdarte-runtime`
- `outlabsAPI` (planned — embedded worker topology, feature 14)

## Local layout

```
docs/                 design + feature specs (canonical)
src/taskq/            Python package (skeleton)
tests/
```
