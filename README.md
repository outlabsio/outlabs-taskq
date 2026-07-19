# outlabs-taskq

Postgres-native durable task queue for Python services (Outlabs / Diverse / QDarte).

**Status:** pre-alpha — design complete (spec v1.6, [ADR-001..017](docs/adr/README.md) accepted; five review rounds processed; protocol v1 document revision 1.0.4 + 0.1.2 function manifest canonical). **Stages 1 through 3 are independently accepted; the S4-00 first-host specification is frozen and round-6-review-gated, with no Stage-4 host implementation started** — the SQL kernel, typed client, worker/CLI, consumer testing helpers, generated HTTP clients, mounted FastAPI facade, authorization boundary, long-poll hub, composable runtime, and explicit OutLabs authorizer/provisioning tools are implemented. See the live [`TASKS.md`](TASKS.md) board for current counts and work.

SQL functions in schema `taskq` are the contract. The Python package provides the installer, typed client, worker runtime, and an optional FastAPI facade. `outlabs-auth` is an optional adapter, not a hard dependency.

## Docs

Start here:

| Doc | What it is |
|---|---|
| [`docs/adr/`](docs/adr/README.md) | **Accepted decisions (ADR-001..017) — override conflicting passages elsewhere** |
| [`docs/design-review/`](docs/design-review/README.md) | Seven-doc design review (2026-07-18) — provenance for the ADRs |
| [`TASKS.md`](TASKS.md) | **Live execution tracker — start here to contribute** |
| [`docs/Task Queue Build Plan.md`](docs/Task%20Queue%20Build%20Plan.md) | The stage-by-stage build sequence + exit gates |
| [`docs/Task Queue Transport Protocol v1.md`](docs/Task%20Queue%20Transport%20Protocol%20v1.md) | **Canonical wire contract** (ADR-005 satisfied) |
| [`docs/Task Queue 0.1 Function Manifest.md`](docs/Task%20Queue%200.1%20Function%20Manifest.md) | **Canonical 0.1 SQL surface** — migration 0001 derives from this |
| [`docs/Task Queue — Unified Design Spec.md`](docs/Task%20Queue%20%E2%80%94%20Unified%20Design%20Spec.md) | Destination design (SQL-first), v1.6 |
| [`docs/Task Queue Library Extraction Design Brief.md`](docs/Task%20Queue%20Library%20Extraction%20Design%20Brief.md) | Package boundaries + optional outlabs-auth |
| [`docs/Task Queue Authorization & Queue Permissions.md`](docs/Task%20Queue%20Authorization%20%26%20Queue%20Permissions.md) | Per-queue permissions + outlabs-auth adapter + provisioning DX |
| [`docs/Task Queue Stage 4 outlabsAPI Dogfood Specification.md`](docs/Task%20Queue%20Stage%204%20outlabsAPI%20Dogfood%20Specification.md) | Round-6-accepted first-host lanes, dependency/database preflight, runtime/IAM integration, deploy/failure/rollback gates |
| [`docs/Task Queue Test & Benchmark Harness.md`](docs/Task%20Queue%20Test%20%26%20Benchmark%20Harness.md) | Own test suites, exact CI matrix, and report-only benchmark scenarios |
| [`docs/Task Queue Growth, Topology & Live Visibility.md`](docs/Task%20Queue%20Growth%2C%20Topology%20%26%20Live%20Visibility.md) | Retention at scale, optional dedicated queue DB, stats API + SSE (partly proposals) |
| [`docs/taskq-borrowed-features/`](docs/taskq-borrowed-features/README.md) | Normative product features to implement (01–14) |
| [`docs/Task Queue Peer Patterns Research.md`](docs/Task%20Queue%20Peer%20Patterns%20Research.md) | Provenance from ten surveyed open-source queue systems (described generically, not named) |
| [`docs/Task Queue Gap Analysis.md`](docs/Task%20Queue%20Gap%20Analysis.md) | Cross-repo defect inventory |
| [`docs/Task Queue Staging Cutover Runbook.md`](docs/Task%20Queue%20Staging%20Cutover%20Runbook.md) | Diverse/QDarte cutover ops |

## Install

```bash
pip install outlabs-taskq           # core: SQL client + worker
pip install outlabs-taskq[http]     # + FastAPI facade / HTTP client
pip install outlabs-taskq[outlabs]  # + outlabs-auth adapter
```

## Package layout

```
src/taskq/
  sql/           # migrations 0001-0003, runner/verifier, manifest, SQL transport
  protocol.py    # closed command/outcome/error single-source (Tier-0 parity-tested)
  registry.py    # typed Task[In, Out] registry
  client.py      # TaskQ facade: transactional typed enqueue
  transport.py   # TaskqTransport protocol
  worker.py      # supervisor + fair poll/presence/shutdown service
  settings.py    # secret-safe worker environment/CLI configuration
  testing.py     # fake client, enqueue assertions, direct work, inline and drain helpers
  cli.py         # migrate / verify / worker
  http/          # optional clients, mounted facade, composable runtime/lifespan
```

## Consumer testing

Fast unit tests can replace one facade without starting a worker or database:

```python
from taskq.testing import FakeTaskQClient

with tq.replace_client(FakeTaskQClient()) as fake:
    await application_call()
    fake.assert_enqueued("mail.send", where={"payload.recipient": "me@example.test"})
```

Inline mode executes registered handlers immediately; bounded drain tests queued behavior without sleeps:

```python
from taskq.testing import drain, inline_mode

async with inline_mode(tq) as recorder:
    await application_call()
    assert recorder.settled("mail.send")[0].is_complete

report = await drain(tq, queue="mail", max_jobs=100)
assert report.completed == 1
```

These are consumer-test conveniences, not production modes. The fake intentionally does not model PostgreSQL fencing, privileges, budgets, or transaction isolation. Use a scratch PostgreSQL transaction with `work`, `require_enqueued`, or `drain(..., connection=connection)` when those contracts matter; every helper preserves caller transaction ownership and makes runaway caps fail loudly.

## Consumers

- `qdarteAPI` / `qdarte-workers`
- `diverse-data-api` / `diverse-data-workers`
- `outlabsAPI` (planned third host — embedded topology, replaces its legacy broker lanes; extraction brief §2.3)

## Development gates

Protect `main` with pull requests, require branches to be current, and require every Stage-1+2D CI check: `lint`; both `import-isolation` and `unit` Python lanes (including testing and core/HTTP/outlabs boundaries); `built-artifacts`; both PostgreSQL `sql-contract` full-suite lanes; `races`; `migrations`; and `bench-smoke`. The scheduled/dispatchable `million-row-plans` job keeps structural plans honest without charging every pull request. Do not bypass a failed required check except through the repository's explicit break-glass process. Later-stage `crash` and `facade` jobs become required when their board tasks land.

## License

MIT — see [LICENSE](LICENSE).
