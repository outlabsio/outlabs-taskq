# taskq — Stage 2A typed-enqueue implementation specification

> **Status:** Tier-3 implementation design — 2026-07-18. This document specifies S2-01..03 only. The Tier-0 Transport Protocol v1 and 0.1 Function Manifest, followed by accepted ADRs, win every conflict. Round-3 findings must be processed before runtime implementation; a contract question stops implementation and enters the docs-first process.

## 1. Outcome and boundary

Stage 2A makes the smallest useful Python path complete:

```python
send_email = Task[SendEmail, SendEmailResult](
    name="emails.send",
    queue="emails",
    input_model=SendEmail,
    output_model=SendEmailResult,
)

registry = TaskRegistry([send_email])
taskq = TaskQ.from_dsn(settings.taskq_dsn, registry=registry)

result = await taskq.enqueue(
    send_email,
    SendEmail(to="person@example.com"),
    idempotency_key=request_id,
    session=session,
)
```

The domain write and job insert commit or roll back together. `result` is always a typed `EnqueueResult`; Python `None` is never a success outcome.

Stage 2A does **not** run handlers. Worker supervision, heartbeat scheduling, NOTIFY/polling, the worker CLI, FastAPI, HTTP transport, and outlabs-auth are S2-04+ or Stage 3. The registry may bind handler callables now as inert metadata so S2-04 does not require a second registration system.

The SQL kernel remains authoritative for validation at the public boundary, idempotency, admission, stamped policy, fencing, budget accounting, and state transitions. Python may reject locally invalid typed payloads and registry configuration, but it must not predict a SQL outcome or turn a SQL rejection into success.

## 2. Package layout and import boundary

```text
src/taskq/
  __init__.py          stable public exports only
  protocol.py          closed 0.1 models, TQ registry, command metadata
  errors.py            typed public exceptions and SQLSTATE normalization
  registry.py          Task[In, Out], TaskRegistry, optional handler binding
  transport.py         transport-neutral TaskqTransport protocol
  client.py            TaskQ typed facade and task-to-command compilation
  sql/
    __init__.py        migrations and verification (existing)
    transport.py       SQLAlchemy async PostgreSQL transport
```

`import taskq`, `taskq.protocol`, `taskq.registry`, `taskq.transport`, and `taskq.client` must succeed with the core extra only. They never import FastAPI or outlabs-auth. `taskq.sql.transport` may import SQLAlchemy and asyncpg because both are core dependencies. Importing a module must not create an engine, connection, task, listener, or other background resource.

`taskq.protocol` is the single Python source for command names, 0.1 outcome sets, retryability, and TQ-code metadata. SQL/HTTP parity vectors in Stage 3 must derive from it rather than copying tables. It is generated or audited against the human-maintained Tier-0 documents; it cannot expand their contract.

## 3. S2-01 — typed tasks, registry, outcomes, and errors

### 3.1 `Task[In, Out]`

`In` and `Out` are Pydantic `BaseModel` subclasses. A task is immutable metadata:

```python
@dataclass(frozen=True, slots=True)
class Task(Generic[InT, OutT]):
    name: str
    queue: str
    input_model: type[InT]
    output_model: type[OutT]
    aliases: tuple[str, ...] = ()
    retry: RetryValue = True
    priority: int | None = None
    lease_seconds: int | None = None
```

- `name` is the canonical durable `job_type`; it never derives from a Python function or module name.
- Canonical names and aliases are 1–120 characters and contain lowercase identifier segments joined by dots: `[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*`.
- Queue names follow migration 0001: `[a-z0-9_]{1,57}`.
- Aliases are dispatch lookup keys for already-durable rows. Typed enqueue always writes the canonical `name`.
- Renaming Python symbols changes nothing. Renaming the canonical wire name requires retaining the old name as an alias for the compatibility window.
- Input serialization is `input_model.model_validate(value).model_dump(mode="json")`; the result must be a JSON object. No serializer may silently drop validation failures.
- Retry metadata compiles to the existing enqueue parameters and is stamped by SQL. Registry edits never change an already-enqueued job.
- Handler binding is optional. S2-01 validates an async handler's declared input/output annotations when supplied, but does not execute it.

### 3.2 Registry invariants

`TaskRegistry` owns canonical and alias indexes and has deterministic iteration order. Registration is atomic: on failure it changes neither index.

- A canonical name may occur once.
- No canonical name or alias may shadow any existing canonical name or alias.
- Multiple aliases on one task must be distinct and must not repeat its canonical name.
- `registry.resolve(name)` accepts canonical names and aliases for future dispatch.
- `registry.canonical(name)` returns the task's canonical name.
- `registry.require(task_or_name)` raises `UnknownTaskError`; it never falls through to raw enqueue silently.
- A raw string enqueue path is separately explicit and requires `validate_job_types=False`; typed task enqueue is the default.
- Blueprint/namespace composition, import discovery, and database startup reconciliation remain deferred; Stage 2A provides the collision-safe primitives they will use.

### 3.3 Closed 0.1 enqueue models

The 0.1 `EnqueueStatus` contains exactly `created` and `existed`. Deferred replace outcomes are not accepted until their capability and contract version are active.

```python
class EnqueueResult(BaseModel):
    status: EnqueueStatus
    job_id: UUID
    created: bool
    queue: str
    job_type: str
    idempotency_key: str | None = None
    scheduled_at: datetime | None = None
```

The model is frozen and validates `created == (status is created)`. Single enqueue derives the status only from the SQL `(job_id, created)` row, then enriches the response with its canonical request fields. Bulk enqueue maps each SQL row `(input_index, job_id, outcome)` and proves contiguous input order. Missing rows, null job ids, unknown outcomes, duplicate indices, or inconsistent flags are `TQ500`-class invariant failures, never partial success.

### 3.4 Errors

`TqCode` is the closed Protocol-v1 registry: `TQ001`, `TQ409`, `TQ422`, `TQ426`, `TQ429`, `TQ500`, `TQ501`, and `TQ503`. `TaskqError` carries `code`, `retryable`, safe `details`, and an exception cause. Stable subclasses provide ergonomic catches for not-found, conflict, validation, version, backpressure, internal, capability, and unavailable categories.

The SQL transport reads SQLSTATE from the driver exception chain; it never matches exception text. Registered TQ states map 1:1. Any other database/driver failure exposed as a taskq command becomes `TaskqInternalError(code=TQ500)` unless it is a known availability failure mapped to `TQ503`; the original remains chained for protected logging. Attempt ids, connection strings, raw SQL, and driver diagnostics do not appear in public `str`, `repr`, or details.

Typed command outcomes such as `lost`, `already_settled`, `settle_conflict`, `paused`, `empty`, and `unavailable` remain result values, not exceptions.

## 4. S2-02 — async SQL transport

`TaskqTransport` is a runtime-checkable async protocol over typed command/request/result models. It contains the complete 0.1 command surface required by the later worker and operator layers:

| Capability | Methods |
|---|---|
| Producer | `enqueue`, `enqueue_many` |
| Runner | `claim`, `heartbeat`, `complete`, `fail`, `snooze`, `release`, `cancel_running`, `worker_heartbeat` |
| Observer | `get_authorization_projection`, `get_job`, `get_queue_stats`, `get_contract_meta`, `metrics` |
| Operator | `ensure_queue`, `pause_queue`, `resume_queue`, `set_concurrency_limit`, `request_worker_shutdown`, `purge_queued`, `run_now`, `reprioritize`, `cancel`, `redrive`, `redrive_failed`, `expire_job`, `expire_worker_leases` |
| Housekeeper | `tick`, `janitor` |

`SqlTaskqTransport` implements those methods with SQLAlchemy asyncio and calls only fixed, schema-qualified `taskq.*` functions from migration 0001. It does not issue table DML, interpolate identifiers, accept a configurable schema, or reproduce a server decision. SQL statements use bound parameters and explicit result-column adapters.

Connection behavior:

- Constructing from an `AsyncEngine` does not take ownership unless explicitly requested.
- Constructing from a DSN creates an owned engine lazily; `aclose()` disposes only owned engines and is idempotent.
- Each command without a supplied transaction runs in one short `engine.begin()` block.
- Cancellation rolls back the transport-owned transaction and re-raises cancellation.
- A returned result is fully materialized before the connection scope exits.
- The transport never retries a command implicitly. Retry safety belongs to the caller/worker and the Protocol-v1 matrix.
- Capability-role tests execute every method with the least-privileged role and prove forbidden cross-capability calls fail.

`ClaimedJob.attempt_id` is a fence: it is available as an attribute for later settle calls but excluded from ordinary serialization and representation. No model, exception, snapshot, or log assertion may leak it. Composite and array decoding must be covered against PostgreSQL 16 and 18 rather than relying on driver-specific tuple accidents.

## 5. S2-03 — transactional enqueue

`TaskQ` is the typed application facade. Its enqueue overload accepts `Task[In, Out]` plus `In` (or input accepted by that model), compiles task metadata and explicit overrides to an `EnqueueCommand`, and delegates exactly once.

```python
async def enqueue(
    self,
    task: Task[InT, OutT],
    payload: InT | Mapping[str, object],
    *,
    idempotency_key: str | None = None,
    scheduled_at: datetime | None = None,
    session: AsyncSession | AsyncConnection | None = None,
    **documented_overrides: object,
) -> EnqueueResult: ...
```

Transactional rules are load-bearing:

1. With `session=` or `connection=`, taskq executes on that exact SQLAlchemy object. It does not call `begin`, `commit`, `rollback`, or `close`, and it does not obtain a second connection.
2. SQLAlchemy autobegin is allowed; the caller still owns the resulting transaction.
3. Caller commit makes the domain write, job, event, and NOTIFY visible together. Caller rollback makes none visible.
4. A nested transaction/savepoint rollback removes only work in that savepoint and taskq does not disturb the outer transaction.
5. A taskq error leaves transaction recovery to the caller; taskq does not hide SQLAlchemy's failed-transaction state.
6. Without a supplied session/connection, the owned transport transaction commits before returning. Failure or cancellation rolls it back.
7. A non-SQL transport rejects `session=` with `TaskqConfigError`; it never ignores the argument.
8. `TaskQ` construction and enqueue create no worker, listener, housekeeper, or background task.

Bulk transactional enqueue follows the same ownership rules and returns one typed result per input in input order.

## 6. Acceptance matrix

| Gate | S2-01 | S2-02 | S2-03 | Evidence required |
|---|:---:|:---:|:---:|---|
| Tier-0/ADR review and Round-3 findings processed | ✓ | ✓ | ✓ | Board records disposition; open Contract question stops code |
| Stable generic task and Pydantic input/output typing | ✓ |  | ✓ | Unit tests for validation, serialization, immutable metadata |
| Canonical names, aliases, and collision-atomic registry | ✓ |  | ✓ | Table-driven valid/invalid names; every collision direction; rename dispatch |
| Closed 0.1 enqueue outcomes; no `None` success | ✓ | ✓ | ✓ | Created/existed and malformed-row tests; concurrent dedup parity |
| Closed TQ registry and message-free SQLSTATE mapping | ✓ | ✓ | ✓ | One vector per code plus native/availability fallbacks; secret-redaction tests |
| Complete typed 0.1 SQL command adapter |  | ✓ |  | Manifest-derived method matrix against live SQL; no table DML |
| Fence redaction | ✓ | ✓ |  | `repr`, dump, exception, and captured-log assertions contain no attempt id |
| Least-privilege role behavior |  | ✓ | ✓ | Producer/runner/observer/operator/housekeeper positive and negative probes |
| Caller-owned commit/rollback/savepoint semantics |  |  | ✓ | Domain row + enqueue visibility tests on the exact pooled connection |
| Transport-owned atomic commit/rollback/cancel |  | ✓ | ✓ | Success, SQL error, and task cancellation integration tests |
| No background work in insert-only mode | ✓ | ✓ | ✓ | Task enumeration/resource-count assertions before/after construction/enqueue |
| Core import isolation | ✓ | ✓ | ✓ | Python 3.12/3.13 core-only and HTTP-only clean-environment jobs |
| Packaging | ✓ | ✓ | ✓ | Wheel and sdist install/import; migration resources retained |
| PostgreSQL compatibility |  | ✓ | ✓ | Full applicable suite green on PostgreSQL 16 and 18 |
| Quality and board discipline | ✓ | ✓ | ✓ | Ruff check/format, full suite, TASKS update, one task per commit |

## 7. Commit and review sequence

1. Process the Round-3 response. Any contract question is recorded in `TASKS.md` and stops implementation.
2. **S2-01:** protocol models/errors plus `Task`, `TaskRegistry`, public exports, and unit/property tests.
3. **S2-02:** `TaskqTransport`, `SqlTaskqTransport`, full live-SQL adapter matrix, privilege and redaction tests.
4. **S2-03:** `TaskQ`, caller-owned and transport-owned transactional enqueue, bulk path, packaging/import gates.
5. Re-run the full Stage-1+2A suite on PostgreSQL 18 and the applicable suite on PostgreSQL 16 before declaring Stage 2A complete.

Each implementation task is a separate green commit with its `TASKS.md` result. No new SQL function is part of Stage 2A.
