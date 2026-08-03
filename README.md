# outlabs-taskq

Postgres-native durable task queue for Python services.

**Status:** alpha — current package version **`0.1.0a21`** uses SQL contract **`0.2.6`**. The SQL kernel, typed client, worker and CLI, testing helpers, optional FastAPI facade, authorization boundary, durable admission, atomic follow-ups, workflows, schedules, projections, and worker presence are implemented.

SQL functions in schema `taskq` are the contract. The Python package provides the installer, typed client, worker runtime, and an optional FastAPI facade. `outlabs-auth` is an optional adapter, not a hard dependency. Queue storage may be co-resident with the host database or dedicated; the HTTP facade may use OutLabsAuth, a host-supplied/remote authorizer, or simple packaged credentials, while trusted direct-SQL deployments use PostgreSQL capability roles.

## Docs

Start here:

| Doc | What it is |
|---|---|
| [`docs/adr/`](docs/adr/README.md) | Accepted reusable architecture decisions |
| [`docs/Task Queue 0.1 Function Manifest.md`](docs/Task%20Queue%200.1%20Function%20Manifest.md) | **Canonical 0.1 SQL surface** — migration 0001 derives from this |
| [`docs/Task Queue Stage 2A Typed Enqueue Specification.md`](docs/Task%20Queue%20Stage%202A%20Typed%20Enqueue%20Specification.md) | Typed enqueue contract |
| [`docs/Task Queue Stage 2B Worker Runtime Specification.md`](docs/Task%20Queue%20Stage%202B%20Worker%20Runtime%20Specification.md) | Worker runtime behavior |
| [`docs/Task Queue Stage 3 FastAPI and Authorization Specification.md`](docs/Task%20Queue%20Stage%203%20FastAPI%20and%20Authorization%20Specification.md) | Optional HTTP and authorization integration |
| [`docs/RELEASE-0.1.0a21.md`](docs/RELEASE-0.1.0a21.md) | 0.1.0a21 compatible OutLabs Auth prerelease range and checklist |
| [`docs/RELEASE-0.1.0a20.md`](docs/RELEASE-0.1.0a20.md) | 0.1.0a20 OutLabs Auth a27 compatibility release and checklist |
| [`docs/RELEASE-0.1.0a19.md`](docs/RELEASE-0.1.0a19.md) | 0.1.0a19 compatibility fix, hardening changes, and release checklist |

## Install

Install the immutable wheel from the latest prerelease:

```bash
pip install https://github.com/outlabsio/outlabs-taskq/releases/download/v0.1.0a21/outlabs_taskq-0.1.0a21-py3-none-any.whl
# extras:
# pip install "outlabs-taskq[http] @ https://github.com/outlabsio/outlabs-taskq/releases/download/v0.1.0a21/outlabs_taskq-0.1.0a21-py3-none-any.whl"
# pip install "outlabs-taskq[outlabs] @ https://github.com/outlabsio/outlabs-taskq/releases/download/v0.1.0a21/outlabs_taskq-0.1.0a21-py3-none-any.whl"
```

## Credential handling

Keep DSNs and HTTP credentials out of shell history and process arguments. Supply them through the
process environment using a secret manager, container secret, or service supervisor, then omit the
DSN from migration and verification commands:

```bash
# TASKQ_DSN is supplied by the deployment environment.
taskq migrate
taskq verify
```

Workers read `TASKQ_DSN` for direct SQL, or `TASKQ_HTTP_BASE_URL` with exactly one of
`TASKQ_HTTP_BEARER_TOKEN` or the `TASKQ_HTTP_HEADER_NAME`/`TASKQ_HTTP_HEADER_VALUE` pair. OutLabs IAM
provisioning reads `TASKQ_AUTH_DSN`. Command-line credential arguments remain available for
compatibility, but password-bearing DSNs and explicit token/value flags emit a secret-free warning.

Use `https://` for traffic that leaves a protected private network. The mounted facade intentionally
publishes its public wire contract at `/taskq/openapi.json`; deployments that do not want that route
reachable should gate it at the host application or reverse proxy.

## Package layout

```
src/taskq/
  sql/           # migrations 0001-0018, runner/verifier, manifest, SQL transport
  protocol.py    # closed command/outcome/error single-source (Tier-0 parity-tested)
  continuations.py # pure policy compiler, negotiation, and derived identities
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

## Atomic native follow-ups

A handler declares its finite child graph in the registry and returns typed children with its
successful result. The parent settlement and every child insert commit together; the worker never
receives a generic producer client.

```python
from taskq import Complete, Followup, FollowupTarget, Task, TaskRegistry

child = Task(
    name="listing.enrich",
    queue="enrichment",
    input_model=EnrichInput,
    output_model=EnrichOutput,
    handler=enrich,
)

async def discover(payload: DiscoverInput) -> Complete:
    return Complete(
        result={"accepted": True},
        followups=(
            Followup(
                step="enrich",
                job_type=child.name,
                queue=child.queue,
                payload={"listing_id": payload.listing_id},
            ),
        ),
    )

parent = Task(
    name="listing.discover",
    queue="discovery",
    input_model=DiscoverInput,
    output_model=DiscoverOutput,
    followup_targets=(FollowupTarget(queue=child.queue, job_type=child.name),),
    handler=discover,
)

registry = TaskRegistry((parent, child))
```

Worker construction rejects missing or queue-mismatched target declarations. HTTP completion
authorizes the parent queue before decoding the body, then authorizes every distinct child queue
before SQL; direct SQL retains the trusted runner-role boundary.

## Development gates

Protect `main` with pull requests, require branches to be current, and require the CI gates that run on pull requests: `lint`, `dependency-audit`, both `import-isolation` and `unit` Python lanes, `built-artifacts`, both PostgreSQL `sql-contract` lanes, both `fresh-cluster-security` lanes, `races`, `stage3-audit`, `migrations`, and `bench-smoke`. The scheduled/dispatchable `million-row-plans` job keeps structural plans honest without charging every pull request. Do not bypass a failed required check except through the repository's explicit break-glass process.

## License

MIT — see [LICENSE](LICENSE).
