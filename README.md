# outlabs-taskq

Postgres-native durable task queue for Python services (Outlabs / Diverse / QDarte).

**Status:** pre-alpha — design complete (spec v1.5, [ADR-001..010](docs/adr/README.md) accepted 2026-07-18); package skeleton only. Next: Stage 0 exit (versioned transport protocol) → secure SQL kernel per the [delivery roadmap](docs/design-review/06-delivery-roadmap.md).

SQL functions in schema `taskq` are the contract. The Python package provides the installer, typed client, worker runtime, and an optional FastAPI facade. `outlabs-auth` is an optional adapter, not a hard dependency.

## Docs

Start here:

| Doc | What it is |
|---|---|
| [`docs/adr/`](docs/adr/README.md) | **Accepted decisions (ADR-001..010) — override conflicting passages elsewhere** |
| [`docs/design-review/`](docs/design-review/README.md) | Seven-doc design review (2026-07-18) — provenance for the ADRs |
| [`docs/Task Queue — Unified Design Spec.md`](docs/Task%20Queue%20%E2%80%94%20Unified%20Design%20Spec.md) | Canonical protocol (SQL-first), v1.5 |
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
- `outlabsAPI` (planned third host — embedded topology, replaces its RabbitMQ lanes; extraction brief §2.3)

## License

MIT — see [LICENSE](LICENSE).
