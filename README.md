# outlabs-taskq

Postgres-native durable task queue for Python services.

**Status:** alpha — **`0.1.0a30`** uses SQL contract **`0.6.6`** and Protocol revision **`1.0.17`**. The resource-oriented, non-interactive CLI is a complete operator and coding-agent surface over direct PostgreSQL and HTTP. Migrations `0022`–`0042` add the per-queue flow-control plane — counters and health, rate/cap/key limits, schedule smear, circuit breaker, priority aging, and an operator audit log — all off by default.

SQL functions in schema `taskq` are the contract. The Python package provides the installer, typed client, worker runtime, and an optional FastAPI facade. `outlabs-auth` is an optional adapter, not a hard dependency. Queue storage may be co-resident with the host database or dedicated; the HTTP facade may use OutLabsAuth, a host-supplied/remote authorizer, or simple packaged credentials, while trusted direct-SQL deployments use PostgreSQL capability roles.

## Docs

Start here:

| Doc | What it is |
|---|---|
| [`docs/CLI.md`](docs/CLI.md) | Operator/agent CLI contract, contexts, output, safety, and command catalog |
| [`docs/CLI-MIGRATION.md`](docs/CLI-MIGRATION.md) | Intentional alpha grammar migration; there are no compatibility aliases |
| [`docs/adr/`](docs/adr/README.md) | Accepted reusable architecture decisions |
| [`docs/Task Queue 0.1 Function Manifest.md`](docs/Task%20Queue%200.1%20Function%20Manifest.md) | **Canonical 0.1 SQL surface** — migration 0001 derives from this |
| [`docs/Task Queue Stage 2A Typed Enqueue Specification.md`](docs/Task%20Queue%20Stage%202A%20Typed%20Enqueue%20Specification.md) | Typed enqueue contract |
| [`docs/Task Queue Stage 2B Worker Runtime Specification.md`](docs/Task%20Queue%20Stage%202B%20Worker%20Runtime%20Specification.md) | Worker runtime behavior |
| [`docs/Task Queue Stage 3 FastAPI and Authorization Specification.md`](docs/Task%20Queue%20Stage%203%20FastAPI%20and%20Authorization%20Specification.md) | Optional HTTP and authorization integration |
| [`docs/RELEASE-0.1.0a30.md`](docs/RELEASE-0.1.0a30.md) | 0.1.0a30 paused-queue worker backoff — package-only, no schema change (contract 0.6.6) |
| [`docs/RELEASE-0.1.0a29.md`](docs/RELEASE-0.1.0a29.md) | 0.1.0a29 worker-presence runtime compatibility fix — package-only, no schema change (contract 0.6.6) |
| [`docs/RELEASE-0.1.0a28.md`](docs/RELEASE-0.1.0a28.md) | 0.1.0a28 `taskq auth` CLI serialization fix — package-only, no schema change (contract 0.6.6) |
| [`docs/RELEASE-0.1.0a27.md`](docs/RELEASE-0.1.0a27.md) | 0.1.0a27 flow-control plane (contract 0.6.6) release notes and rollout |
| [`docs/RELEASE-0.1.0a26.md`](docs/RELEASE-0.1.0a26.md) | 0.1.0a26 production-integration closeout and rollout checklist |
| [`docs/RELEASE-0.1.0a25.md`](docs/RELEASE-0.1.0a25.md) | 0.1.0a25 complete operator read model and CLI foundation |
| [`docs/RELEASE-0.1.0a24.md`](docs/RELEASE-0.1.0a24.md) | Historical unreleased a24 candidate record |
| [`docs/RELEASE-0.1.0a22.md`](docs/RELEASE-0.1.0a22.md) | 0.1.0a22 standalone scheduler release notes and rollout checklist |
| [`docs/TaskQ Standalone Scheduler Specification.md`](docs/TaskQ%20Standalone%20Scheduler%20Specification.md) | Owner-approved standalone scheduler, target-attestation, manifest, and evidence contract |
| [`docs/RELEASE-0.1.0a21.md`](docs/RELEASE-0.1.0a21.md) | 0.1.0a21 compatible OutLabs Auth prerelease range and checklist |
| [`docs/RELEASE-0.1.0a20.md`](docs/RELEASE-0.1.0a20.md) | 0.1.0a20 OutLabs Auth a27 compatibility release and checklist |
| [`docs/RELEASE-0.1.0a19.md`](docs/RELEASE-0.1.0a19.md) | 0.1.0a19 compatibility fix, hardening changes, and release checklist |

## Install

Install the exact published prerelease selected by the consumer lockfile:

```bash
pip install outlabs-taskq==0.1.0a30
```

## Credential handling

Keep DSNs and HTTP credentials out of shell history and process arguments. Supply them through the
process environment using a secret manager, container secret, or service supervisor, then omit the
DSN from commands. A context stores only the environment-variable name:

```bash
# TASKQ_STAGING_DSN is supplied by the deployment environment.
taskq --context staging db plan -o json
taskq --context staging db verify

# A context file is optional when the caller supplies target identity explicitly.
taskq --dsn-env TASKQ_STAGING_DSN \
  --expected-environment staging --actor operator:release-agent \
  db verify -o json
```

`taskq db migrate` requires a superuser or a managed database-owner role with
`CREATEROLE`. On PostgreSQL 16/18, the installer bootstraps `taskq_owner` and
retains owner membership on that migration role so later owner-only binding
and upgrades work. This must be a dedicated owner/migration credential: never
give it to an API, worker, or scheduler. Runtime logins receive only the
required TaskQ capability roles.

Migration `0019` deliberately stops at an unbound target before scheduler
activation. Inspect and bind the safe fingerprint, then resume migration:

```bash
PLAN=$(taskq --context staging db plan -o json)
# Review PLAN and extract data.plan_digest.
taskq --context staging --yes db migrate --plan-digest "$PLAN_DIGEST"
taskq --context staging target show -o json
taskq --context staging --yes target bind staging \
  --expected-installation-id "$TASKQ_INSTALLATION_ID" \
  --expected-binding-version 0
taskq --context staging db plan -o json
taskq --context staging --yes db migrate --plan-digest "$PLAN_DIGEST"
taskq --context staging db verify
```

Run the framework-neutral scheduler with static target expectations. The
bounded mode is first-class for platform timers and scale-to-zero databases:

```bash
taskq --context staging scheduler run
taskq --context staging scheduler once -o json
taskq --context staging scheduler doctor -o json

taskq --context staging schedule manifest plan examples/schedules.minimal.yaml -o json
taskq --context staging schedule manifest apply examples/schedules.minimal.yaml \
  --plan-digest "$PLAN_DIGEST"
```

Activating or resuming an interval schedule is `from now`: the first scheduler
pass initializes its recurrence and the first job becomes due after one full
interval. Activation does not enqueue an immediate job. Size attended pilot
windows accordingly, or use an explicit one-shot job when an immediate canary
is required.

The scheduler is a clock, not a task executor. Run one supervised scheduler per
database/environment, but place workers according to the application's existing
operating model. A single host-native worker can use a combined registry and
subscribe to multiple queues; TaskQ does not require one container per worker,
queue, task, or schedule. Prefer existing local worker hosts unless a particular
task has a documented cloud availability, latency, or network requirement.

### Concurrency model

TaskQ does not serialize independent jobs. A job without a `concurrency_key` may
run alongside any other claim, up to the explicit `WorkerOptions.concurrency`
and any configured queue flow cap. Worker concurrency defaults to one as a safe
deployment setting; applications that have parallel-safe handlers must size it
deliberately.

Use a `concurrency_key` only to name a real shared resource such as a browser
session, licensed renderer, or rate-limited provider. Jobs with different keys
do not block each other. A named key with no registered limit is conservatively
capped at one; raise that cross-worker cap with `set_concurrency_limit` when the
resource safely supports more than one user. Do not stamp unrelated stages with
one host-wide key: process lanes and task-name claim filters provide isolation,
while resource keys provide cross-worker mutual exclusion.

Priority controls which eligible job is claimed first; it does not create
parallel capacity and cannot bypass worker, queue, flow-key, or concurrency-key
limits. Keep ordering policy and resource capacity as separate decisions.

Every recurring source manifest must set `catchup: fire_once` or
`catchup: fire_all` explicitly. Do not rely on the current `skip` default: the
released SQL contract intentionally accepts only zero occurrences for that
policy, so continuous polling advances without enqueueing. A corrected
`skip_missed` semantic requires a new SQL migration and compatibility audit; it
is not being patched only in Python.

Worker services may still use their runtime settings. Operator CLI commands require an explicit
context or complete connection flags; they never infer a current context or endpoint. HTTP contexts
store a bearer-token environment-variable name, never a literal token or arbitrary header value.
OutLabs IAM provisioning reads `TASKQ_AUTH_DSN`.

Use `https://` for traffic that leaves a protected private network. The mounted facade intentionally
publishes its public wire contract at `/taskq/openapi.json`; deployments that do not want that route
reachable should gate it at the host application or reverse proxy.

## Package layout

```
src/taskq/
  sql/           # migrations 0001-0021, runner/verifier, manifest, SQL transport
  scheduler.py   # standalone runtime, bounded mode, doctor, YAML plan/apply
  protocol.py    # closed command/outcome/error single-source (Tier-0 parity-tested)
  continuations.py # pure policy compiler, negotiation, and derived identities
  registry.py    # typed Task[In, Out] registry
  client.py      # TaskQ facade: transactional typed enqueue
  transport.py   # TaskqTransport protocol
  worker.py      # supervisor + fair poll/presence/shutdown service
  settings.py    # secret-safe worker environment/CLI configuration
  testing.py     # fake client, enqueue assertions, direct work, inline and drain helpers
  cli/            # Click registry, contexts, transports, safety, output, errors
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

Protect `main` with pull requests, require branches to be current, and require the CI gates that run on pull requests: `lint`, `dependency-audit`, both `import-isolation` and `unit` Python lanes, `built-artifacts`, both PostgreSQL `sql-contract` lanes, both `fresh-cluster-security` lanes, `races`, `stage3-audit`, `migrations`, `bench-smoke`, and both PostgreSQL `load-smoke` lanes. The scheduled/dispatchable `million-row-plans` job keeps structural plans honest without charging every pull request. Do not bypass a failed required check except through the repository's explicit break-glass process.

## License

MIT — see [LICENSE](LICENSE).
