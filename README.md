# outlabs-taskq

Postgres-native durable task queue for Python services (Outlabs / Diverse / QDarte).

**Status:** pre-alpha — design complete (spec v1.6, [ADR-001..011](docs/adr/README.md) accepted; both review rounds folded in; protocol v1 + 0.1 function manifest canonical). **Stage 1 in progress** — see the live [`TASKS.md`](TASKS.md) board for current counts and work; stage strategy lives in the [build plan](docs/Task%20Queue%20Build%20Plan.md).

SQL functions in schema `taskq` are the contract. The Python package provides the installer, typed client, worker runtime, and an optional FastAPI facade. `outlabs-auth` is an optional adapter, not a hard dependency.

## Docs

Start here:

| Doc | What it is |
|---|---|
| [`docs/adr/`](docs/adr/README.md) | **Accepted decisions (ADR-001..011) — override conflicting passages elsewhere** |
| [`docs/design-review/`](docs/design-review/README.md) | Seven-doc design review (2026-07-18) — provenance for the ADRs |
| [`TASKS.md`](TASKS.md) | **Live execution tracker — start here to contribute** |
| [`docs/Task Queue Build Plan.md`](docs/Task%20Queue%20Build%20Plan.md) | The stage-by-stage build sequence + exit gates |
| [`docs/Task Queue Transport Protocol v1.md`](docs/Task%20Queue%20Transport%20Protocol%20v1.md) | **Canonical wire contract** (ADR-005 satisfied) |
| [`docs/Task Queue 0.1 Function Manifest.md`](docs/Task%20Queue%200.1%20Function%20Manifest.md) | **Canonical 0.1 SQL surface** — migration 0001 derives from this |
| [`docs/Task Queue — Unified Design Spec.md`](docs/Task%20Queue%20%E2%80%94%20Unified%20Design%20Spec.md) | Destination design (SQL-first), v1.6 |
| [`docs/Task Queue Library Extraction Design Brief.md`](docs/Task%20Queue%20Library%20Extraction%20Design%20Brief.md) | Package boundaries + optional outlabs-auth |
| [`docs/Task Queue Authorization & Queue Permissions.md`](docs/Task%20Queue%20Authorization%20%26%20Queue%20Permissions.md) | Per-queue permissions + outlabs-auth adapter + provisioning DX |
| [`docs/Task Queue Test & Benchmark Harness.md`](docs/Task%20Queue%20Test%20%26%20Benchmark%20Harness.md) | Own test suites, CI matrix, benchmark scenarios + regression gates |
| [`docs/Task Queue Growth, Topology & Live Visibility.md`](docs/Task%20Queue%20Growth%2C%20Topology%20%26%20Live%20Visibility.md) | Retention at scale, optional dedicated queue DB, stats API + SSE (partly proposals) |
| [`docs/taskq-borrowed-features/`](docs/taskq-borrowed-features/README.md) | Normative product features to implement (01–14) |
| [`docs/Task Queue Peer Patterns Research.md`](docs/Task%20Queue%20Peer%20Patterns%20Research.md) | Provenance from ten surveyed open-source queue systems (described generically, not named) |
| [`docs/Task Queue Gap Analysis.md`](docs/Task%20Queue%20Gap%20Analysis.md) | Cross-repo defect inventory |
| [`docs/Task Queue Staging Cutover Runbook.md`](docs/Task%20Queue%20Staging%20Cutover%20Runbook.md) | Diverse/QDarte cutover ops |

## Install (once implemented)

```bash
pip install outlabs-taskq           # core: SQL client + worker
pip install outlabs-taskq[http]     # + FastAPI facade / HTTP client
pip install outlabs-taskq[outlabs]  # + outlabs-auth adapter
```

## Package layout (target)

```
src/taskq/
  sql/           # installer + schema
  client.py      # asyncio SQL client
  worker.py      # claim / heartbeat / settle loop
  models.py      # pydantic contracts (only copy)
  http/          # optional FastAPI facade
  cli.py
```

## Consumers

- `qdarteAPI` / `qdarte-workers`
- `diverse-data-api` / `diverse-data-workers`
- `outlabsAPI` (planned third host — embedded topology, replaces its legacy broker lanes; extraction brief §2.3)

## Development gates

Protect `main` with pull requests, require branches to be current, and require every Stage-1 CI check: `lint`; both `import-isolation` and `unit` Python lanes; both PostgreSQL `sql-contract` lanes; `races`; `migrations`; and `bench-smoke`. Do not bypass a failed required check except through the repository's explicit break-glass process. Later-stage `crash` and `facade` jobs become required when their board tasks land.

## License

MIT — see [LICENSE](LICENSE).
