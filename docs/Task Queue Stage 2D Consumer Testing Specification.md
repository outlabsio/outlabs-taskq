# taskq — Stage 2D Consumer Testing Specification

> **Status:** S2-06 implementation specification — frozen 2026-07-18
> **Authority:** Tier 3. The Transport Protocol v1, Function Manifest 0.1.2, and ADRs win every conflict.
> **Depends on:** completed Stage 2A–2C runtime, including the round-4 remediation and completion evidence.

## 1. Purpose and boundary

S2-06 ships a small consumer-facing `taskq.testing` surface for unit tests and transactional PostgreSQL integration tests. It makes enqueue assertions, direct handler execution, inline execution, and bounded queue drains deterministic without turning an in-memory double into a second implementation of the SQL kernel.

The invariant is:

> The fake path is fast and typed; the PostgreSQL path is authoritative. A passing fake test is never represented as protocol proof.

This slice adds no SQL, migrations, HTTP, FastAPI, authorization, embedded production worker, listener, CLI, or Stage-3 code. It does not modify Tier-0 contracts. Consumer helpers may call existing public contract functions and, for inspection only, read durable rows through an explicitly privileged test transaction. They never write tables directly.

`taskq.testing` ships in the core artifact but is not imported by `taskq.__init__`. Importing core must not import `pytest`, SQLAlchemy test plugins, or optional integrations. Helpers fail with `AssertionError` so they work under any test runner.

## 2. Public surface

```python
from taskq.testing import (
    DrainReport,
    EnqueuedJob,
    FakeTaskQClient,
    InlineRecorder,
    RecordedEnqueue,
    RecordedSettlement,
    drain,
    inline_mode,
    require_enqueued,
    work,
)
```

`FakeTaskQClient` is a transport-shaped unit-test double. The name describes its intended use behind `TaskQ`; it is not a database client and does not claim conformance to every `TaskqTransport` command. It implements the producer and runner commands needed by `TaskQ`, `WorkerSupervisor`, `inline_mode`, and `drain`. Every other command fails immediately with `TaskqConfigError("unsupported by FakeTaskQClient")` rather than inventing SQL behavior.

The record and report types are frozen, fence-free Pydantic models with `extra="forbid"`:

```python
class RecordedEnqueue:
    job_id: UUID
    queue: str
    job_type: str
    payload: dict[str, Any]
    headers: dict[str, Any]
    idempotency_key: str | None
    status: Literal["created", "existed"]

class RecordedSettlement:
    job_id: UUID
    queue: str
    job_type: str
    command: Literal["complete", "fail", "snooze", "release", "cancel_running"]
    intent: HandlerResult | None       # release has no handler intent
    outcome: Literal["ok", "retry_scheduled", "dead"]
    cause: str | None                  # release-only safe cause

class DrainReport:
    claimed: int
    completed: int
    retried: int
    snoozed: int
    cancelled: int
    released: int
    failed: int
    capped: bool
```

`EnqueuedJob` contains safe enqueue-visible fields only: `job_id`, `queue`, `job_type`, `payload`, `headers`, `idempotency_key`, `status`, and `scheduled_at`. Attempt tokens, lease owners, and fences are absent from all public records, errors, representations, and logs.

## 3. Fake client contract

`FakeTaskQClient(*, queues=(), clock=None)` creates no tasks, threads, engines, or connections. It owns an in-memory ordered ledger and exposes immutable snapshots through `enqueues`, `settlements`, and `pending` properties.

The fake implements only these semantics:

1. `enqueue` and `enqueue_many` validate existing command models and return the real typed `created`/`existed` result variants. Active `(queue, idempotency_key)` values deduplicate; no key always creates.
2. `claim` returns FIFO due jobs for a known queue, creates opaque internal attempts, and returns real typed claim models. Unknown queues and empty queues use the real closed outcomes.
3. heartbeat and settlement methods enforce only internal attempt identity and a one-terminal-settlement rule. They return real typed outcomes and record the semantic settlement intent. They do not model database clocks, concurrency caps, retry budgets, event conservation, advisory locks, privileges, or transaction isolation.
4. retryable fail, snooze, and release make a job pending again; complete, non-retryable fail, and cancel make it terminal. This is sufficient for deterministic handler and drain tests, not a replacement for SQL transition tests.
5. `assert_enqueued(job_type, *, count=1, where=None)` uses the same matcher grammar as `require_enqueued` and raises `AssertionError` with fence-free diagnostics.
6. `aclose()` is idempotent. Use after close fails loudly.

Queue discovery is explicit: constructor queues plus queues named by successful enqueues are known. Fake time is injectable and test-local; it is never evidence for database-clock behavior.

## 4. Replacement scope

`TaskQ.replace_client(client)` is a synchronous context manager:

```python
with tq.replace_client(FakeTaskQClient()) as fake:
    await application_call()
    fake.assert_enqueued("mail.send", count=1)
```

It swaps only `tq.transport`, yields the exact supplied client, and restores the exact prior transport in `finally`, including when the body raises or is cancelled. It owns and closes neither transport. Nested replacement on the same `TaskQ` is rejected. Because replacement mutates one facade, concurrent use of that facade outside the context is unsupported and documented as a test-only boundary.

No optional module is imported to implement the context manager.

## 5. Enqueue assertions

```python
job = await require_enqueued(
    source,
    job_type="mail.send",
    where={"payload.recipient": "person@example.test"},
    unique_skipped=False,
)
```

`source` is one of `TaskQ`, `FakeTaskQClient`, SQLAlchemy `AsyncConnection`, or `AsyncSession`.

- Fake sources inspect their immutable ledger.
- SQL sources perform a parameter-bound `SELECT` against `taskq.jobs` inside the caller's exact current transaction. This is a test-only inspection read and therefore requires a database-owner or explicitly test-privileged connection; application capability credentials are not widened.
- `where` permits equality matchers on safe scalar columns and dotted `payload.*` or `headers.*` JSON paths. Empty path components, arrays, operators, SQL fragments, and fence/lease/attempt fields are rejected.
- Zero or multiple matches raise `AssertionError`; one match returns `EnqueuedJob`.
- `unique_skipped=False` additionally requires the caller-supplied enqueue result to be `created`. Because a connection cannot infer that result, this option accepts an `EnqueueResult` value rather than consulting database state; omission means no disposition assertion.

All SQL is fixed text with bound values. The helper never commits, rolls back, closes, or changes role on a caller-owned transaction.

## 6. Direct handler work

```python
settle = await work(
    connection,
    task=registered_task,
    payload={"recipient": "person@example.test"},
    progress=None,
)
```

`task` must be a registered-style `Task` with a handler; raw callables are rejected because their durable name, queue, input/output models, retry policy, and dispatch arity are otherwise ambiguous.

- With `connection=None`, `work` builds a fence-free synthetic `JobContext`, validates the payload through the task, invokes sync/async handlers with the same registry-frozen arity, and normalizes output, explicit intents, and exceptions through the worker's shared normalization function. It returns a typed `HandlerResult`.
- With an `AsyncConnection` or `AsyncSession`, `work` uses existing enqueue, claim, heartbeat, and settle functions through a connection-bound SQL transport inside the caller's transaction. The queue must already exist and the credential must be able to perform the required producer and runner calls. It inserts and claims one uniquely keyed job, invokes the normal `WorkerSupervisor`, and returns the single recorded semantic settlement intent. It never listens or sleeps for polling.
- `unique_mode="normal"` is the default. `unique_mode="isolated"` generates a per-call idempotency key; it does not alter queue policy or bypass SQL uniqueness rules.
- Caller transaction ownership is absolute: no implicit begin beyond SQLAlchemy autobegin, commit, rollback, close, engine disposal, background listener, or post-return task.

The execution path is shared with production code rather than copying result/exception normalization into `taskq.testing`.

## 7. Inline mode

```python
async with inline_mode(tq, follow=False, max_jobs=100) as recorder:
    await application_call()
    assert recorder.settled("mail.send")[0].is_complete
```

`inline_mode` installs a fresh fake client with the `TaskQ` registry and executes each newly created enqueue immediately through `work`. It still returns the real typed `EnqueueResult`. Existing deduplicated enqueues do not execute twice.

The yielded `InlineRecorder` provides immutable enqueue and settlement snapshots plus filtered `enqueued(job_type)` and `settled(job_type)` methods. It exposes no attempts or fences.

Followups are always recorded. With `follow=False` they are not executed. With `follow=True`, a followup is validated through the registered task and enqueued through the same inline boundary. The cumulative execution count may not exceed `max_jobs`; `None`, booleans, zero, and values above 10,000 are rejected. Hitting the cap raises `AssertionError` with a runaway-followup message. Context exit waits for all inline execution and restores the original transport even after errors or cancellation.

Inline execution is testing-only. It is not a production mode and must never be selected by settings or environment variables.

## 8. Bounded drain

```python
report = await drain(tq, queue="mail", max_jobs=100, connection=connection)
```

`drain` runs claim → `WorkerSupervisor` → settlement sequentially until a claim returns empty/paused or the cap is reached.

- A fake-backed `TaskQ` uses the fake runner commands.
- A SQL-backed `TaskQ` without `connection` uses the normal transport transaction boundaries.
- Supplying `connection` binds every SQL command to the caller's exact transaction through a connection-bound adapter; it is valid only for `SqlTaskqTransport`.
- Claims use batch 1 and worker concurrency 1. Notifications, presence, poll sleeps, and the worker service are not started.
- `max_jobs` defaults to 100 and must be an integer from 1 through 10,000. `None` and booleans are rejected. If work is still immediately claimable at the cap, `drain` raises `AssertionError`; it never silently returns a partial success.
- A retry or snooze counts as one processed job. Future-due work ends the drain as empty; the helper does not advance database time.
- Fatal worker reports raise `TaskqError`; ordinary typed handler outcomes populate `DrainReport`.

Every started handler, heartbeat, settlement retry, and supervisor task is joined before return or raise. Caller-owned transports and SQL transactions remain open.

## 9. PostgreSQL mark and fixture boundary

The package registers the `taskq_sql` pytest marker in project configuration for its own suite. Consumer projects may register the same marker and compose their own database fixture; taskq does not install a global pytest plugin, create databases at import time, or guess credentials.

The documented fixture pattern is a small consumer fixture that yields `FakeTaskQClient` or wraps `inline_mode`. This keeps fixture scope and application lifecycle under the consumer's control while the shipped helpers remain test-runner neutral.

## 10. Packaging and safety

1. `import taskq` remains core-only and does not import `taskq.testing`.
2. `import taskq.testing` works from source, wheel, and sdist with core dependencies only; it never imports FastAPI, HTTP, OutLabs, or pytest.
3. Public models and assertion output are fence-free. Internal attempt values use the same redaction discipline as production transport types.
4. There is no production configuration switch for fake or inline execution.
5. No helper performs direct table DML, schema mutation, queue provisioning, or role changes.

## 11. Acceptance matrix and commit slices

### S2-06A — fake client and replacement boundary

- typed created/existed enqueue records, FIFO claim, heartbeat, every settlement intent, and unsupported-command failure;
- active-key dedup and terminal/retry state transitions without pretending to model SQL policy;
- `TaskQ.replace_client` restoration on normal exit, exception, and async cancellation, plus nested/concurrent-use guard;
- `assert_enqueued` count and matcher success/failure with fence-free diagnostics;
- core imports remain isolated.

### S2-06B — consumer helpers

- fake and live-transaction `require_enqueued`, including missing, ambiguous, nested matcher, injection-shaped input, and transaction rollback vectors;
- synthetic and real-transaction `work` mapping output, `Complete`, `Snooze`, retryable/non-retryable exception, sync/async arity, and no-handler rejection;
- inline typed enqueue/settlement, dedup, followup record-only/execution, cap failure, exception/cancellation restoration, and no leaked task;
- fake and PostgreSQL drains for empty, complete, retry, snooze, fatal, cap, rollback, and resource cleanup.

### S2-06-AUDIT — permanent completion evidence

- identical full suite on PostgreSQL 16.14 and 18.3, with the million-row plan gate unchanged;
- repeated inline cancellation/followup and drain cap/cleanup races;
- source plus wheel/sdist import and smoke checks on Python 3.12/3.13 with core, HTTP, and OutLabs extras;
- exact diff confirms no SQL migration, Tier-0, Tier-4, HTTP, OutLabs, FastAPI, listener, CLI, or Stage-3 change;
- docs show the fake-versus-PostgreSQL proof boundary and consumer fixture recipes.

## 12. Exit gate

S2-06 is complete when every acceptance row is permanent, both PostgreSQL majors pass the identical suite, artifacts contain the testing module without optional imports, resource ledgers return to baseline, the board records exact evidence, and Stage 3 remains untouched. The next action is the Stage-3 gate and round-5 review boundary; S2-06 does not open or implement Stage 3 itself.
