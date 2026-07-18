# taskq — Stage 2C Claim Loop and Worker CLI Specification

> **Status:** S2-05 implementation specification — frozen 2026-07-18
> **Authority:** Tier 3. The Transport Protocol v1, Function Manifest 0.1.2, and ADRs win every conflict.
> **Depends on:** the completed Stage 2A transport/facade and Stage 2B `WorkerSupervisor`, including the round-4 remediation.

## 1. Purpose and boundary

S2-05 turns the already-claimed-job supervisor into a complete DB-direct worker service. It adds queue subscription, an authoritative polling/claim loop, optional PostgreSQL notification nudges, advisory worker presence, shutdown coordination, configuration, and the `taskq worker` process lifecycle.

The invariant for this stage is:

> Polling discovers work. Notifications only reduce latency. Capacity is reserved by claiming no more than the supervisor can accept.

S2-05 does not add or change SQL. It uses only the existing `TaskqTransport.claim(...)` and `worker_heartbeat(...)` contract calls. It does not add HTTP, FastAPI lifespan composition, embedded-worker integration, authorization, consumer test helpers, schedules, or housekeeper behavior. Those remain later stages.

No queue notification payload, worker-presence row, or in-memory state participates in correctness, fencing, retry budget, or reclaim. PostgreSQL remains the only durable authority.

## 2. Public module and API

Core-only imports remain valid without FastAPI or `outlabs-auth`.

```python
from taskq.worker import (
    NotificationSource,
    WorkerService,
    WorkerServiceOptions,
    WorkerServiceSnapshot,
    WorkerServiceState,
)
from taskq.settings import WorkerSettings
```

`WorkerSupervisor`, `WorkerOptions`, and all Stage-2B types retain their existing meanings. S2-05 composes them; it does not duplicate handler, heartbeat, settlement, or soft-stop logic.

```python
class WorkerService:
    def __init__(
        self,
        transport: TaskqTransport,
        registry: TaskRegistry,
        worker_id: str,
        *,
        options: WorkerServiceOptions,
        supervisor_options: WorkerOptions | None = None,
        notifications: NotificationSource | None = None,
        clock: WorkerClock | None = None,
    ): ...

    async def start(self) -> None: ...
    async def run(self, *, stop_signal: asyncio.Event | None = None) -> None: ...
    async def stop(self, *, cancel: bool = False) -> None: ...
    async def aclose(self) -> None: ...

    @property
    def ready(self) -> bool: ...
    @property
    def stopped(self) -> bool: ...
    @property
    def requires_process_exit(self) -> bool: ...
    def snapshot(self) -> WorkerServiceSnapshot: ...
```

Construction validates pure values and creates no tasks, threads, engines, connections, or signal handlers. `run()` starts a constructed service and then waits for shutdown; on an already-started service it only waits. `start()` is single-use and returns after the first presence result and the listener's first connect attempt. `stop()` on a constructed service closes it without opening resources. Concurrent stop callers share one operation; `stop(cancel=True)` escalates it. `aclose()` is idempotent.

`TaskQ.worker(...)` may be a convenience factory over this constructor, but it must not infer a DSN or create a listener. A caller that requests `listen=True` supplies a `NotificationSource`; omission is a configuration error. The service owns and closes a supplied source after start, but never closes its caller-owned transport. The CLI supplies and owns both PostgreSQL resources. This keeps the transport abstraction usable in tests and future non-SQL workers.

## 3. Frozen option models

`WorkerServiceOptions` is a frozen Pydantic model with `extra="forbid"`:

```python
queues: tuple[str, ...]                 # required, 1..100 distinct entries
batch: int = 1                         # 1..50 and <= supervisor concurrency
poll_interval: float = 5.0             # 0.1..3600 seconds
listen: bool = True
presence_interval: float = 60.0        # 5..3600 seconds
listener_backoff_base: float = 0.25    # >0
listener_backoff_cap: float = 30.0     # >= base, <=3600
```

Queue order is preserved for round-robin fairness. Every queue must match the contract grammar `[a-z0-9_]{1,57}`; duplicates are rejected rather than silently removed. `batch` is additionally bounded by `WorkerOptions.concurrency` at service construction.

The existing `WorkerOptions` remains the sole owner of concurrency, sync thread count, handler cancellation grace, settlement retry, no-handler delay, and soft-stop deadline. S2-05 must not create shadow copies of those settings.

## 4. Service state and readiness

`WorkerServiceState` is closed:

```text
constructed → starting → running ↔ degraded → stopping → stopped
                         ↘ failed → stopping
```

- `ready` is true only in `running`.
- `degraded` means polling and job supervision continue, but a requested notification listener is disconnected or presence has failed since its last success.
- A backlog, paused queue, or empty queue never makes the service unready.
- A listener reconnect or successful presence write restores `running` only when every requested auxiliary component is healthy.
- An unknown configured queue, an impossible claim result, a fatal supervisor report, or an internal lifecycle invariant moves to `failed` and initiates soft stop.
- `stopped` becomes true only after the claim loop is closed, any in-flight claim admission is resolved, the supervisor reaches its permitted terminal state, all owned tasks are joined, the listener is closed, and presence updates have stopped.

Contract/schema incompatibility that appears through a typed transport failure is fatal and unready. S2-05 does not weaken role separation by calling observer-only metadata functions through the runner credential. Deployment preflight continues to use `taskq verify` with an appropriate credential.

## 5. Notification source: latency only

`NotificationSource` is a runtime-checkable async protocol owned by core:

```python
class NotificationSource(Protocol):
    async def connect(self, channels: Sequence[str], nudge: Callable[[], None]) -> None: ...
    async def wait_disconnected(self) -> None: ...
    async def aclose(self) -> None: ...
```

The DB-direct implementation has these rules:

1. It owns exactly one dedicated, non-pooled async PostgreSQL connection for all subscribed queues. It never borrows a SQL transport pool connection.
   `connect()` returns only after all registrations succeed; `wait_disconnected()` blocks until that session is lost or the source is closed, giving the service one explicit reconnect boundary.
2. Channels are exactly `taskq_{queue}` and are constructed only after queue-grammar validation. Registration uses the driver's listener API rather than interpolating unvalidated SQL.
3. Payload bytes are ignored completely. Current contract SQL emits an empty payload; no payload can select a job, queue, claim key, or credential.
4. Notifications coalesce through a monotonically increasing in-process generation. A waiter snapshots the generation and wakes when it changes. Clearing an event can never erase a notification that races the clear/wait boundary.
5. Startup establishes every channel, then issues an immediate catch-up nudge. Reconnect repeats the full subscription set and issues another catch-up nudge. Thus work committed before the listener became active is found by the next claim sweep.
6. Connection loss never stops polling. It marks readiness degraded, logs once per outage, and reconnects with monotonic bounded exponential backoff. A successful reconnect logs recovery and restores readiness if presence is healthy.
7. `listen=False` creates no listener task or connection and is a healthy poll-only mode. It is the required mode behind transaction-level connection poolers that do not preserve session listeners.
8. Notification storms are bounded: they advance one generation and cause claim sweeps, not one task per notification and not an unbounded local queue.

Polling remains active in every listener state. No test may prove correctness using notification delivery alone.

## 6. Authoritative poll and fair claim loop

The service owns one claim-loop task and one `WorkerSupervisor`. While accepting:

1. Wait until at least one supervisor slot is available.
2. Select the next configured queue using a persistent round-robin cursor.
3. Compute `claim_batch = min(batch, supervisor.available_slots)` immediately before the call.
4. Call `transport.claim(queue, worker_id, batch=claim_batch)` with no targeted job id and no client-side timestamp.
5. Interpret the closed result exactly:
   - `claimed`: synchronously submit every returned claim to the supervisor;
   - `empty`: advance normally;
   - `paused`: record the state transition without hot-looping;
   - `unknown_queue`: fatal configuration failure and soft stop;
   - `unavailable`: impossible for an untargeted worker claim, therefore fatal.
6. Rotate to the next queue after every claim call, including a successful one. A hot first queue cannot permanently starve later queues.
7. Continue claiming without waiting while capacity remains and a sweep returns work. After a full queue sweep with no claim, wait for the earliest of notification generation change, the next monotonic poll deadline, capacity change, presence-requested shutdown, or local stop.

While capacity is available, the maximum interval between claim sweeps is `poll_interval` plus scheduler epsilon even when listening. Future-dated work, reaped work, a missed commit notification, and listener outages therefore remain discoverable.

There is no local prefetch buffer. A claimed job is either under `WorkerSupervisor` immediately or recovering through lease expiry after a transport-level unknown result.

### 6.1 Claim-to-submit admission critical section

From entry into `transport.claim` until every returned job is synchronously accepted by `WorkerSupervisor`, admission is one shielded critical section:

- stop requests set the stop flag immediately and prevent the next claim, but wait for this section;
- if stop arrives after the database returns claims, those claims are submitted first, then the supervisor shutdown signal applies to them;
- the service is the supervisor's exclusive submitter while running, so the previously measured capacity cannot be consumed by an unrelated caller;
- a returned batch larger than requested or a submission rejection is an internal fatal invariant. Already-returned claims are never settled by an improvised verb; supervised claims follow normal shutdown and any unsupervised claim recovers only through its lease.

Cancellation never abandons an in-flight claim coroutine merely to make shutdown faster. If the connection fails after a claim committed but before its response is known, the service logs an unknown claim result and relies on lease recovery; it must not guess fences or replay a non-idempotent claim.

## 7. Advisory presence and remote shutdown

Presence uses only the existing runner command:

```python
shutdown = await transport.worker_heartbeat(
    worker_id,
    queues,
    hostname=hostname,
    pid=pid,
    version=package_version,
    meta=safe_meta,
)
```

- The first presence call completes before the first claim. `shutdown_requested=True` starts soft stop without claiming.
- Subsequent calls run every `presence_interval` on a monotonic schedule, independent of per-job heartbeats.
- `safe_meta` may contain only bounded operational scalars such as configured concurrency, batch, listen-effective state, and process mode. It must never contain DSNs, credentials, payloads, headers, progress, results, errors, or attempt ids.
- A transient failure marks the service degraded and retries with the listener backoff policy while claiming continues. Presence is advisory and never a reclaim input.
- A successful response clears presence degradation. A true shutdown flag is sticky for the process and initiates soft stop.
- Contract 0.1 has no worker-bye mutation. Clean shutdown stops presence updates; the row becomes offline through the existing `last_seen_at` age projection. S2-05 must not add raw table DML or invent a function to clear it.

The default CLI worker id is unique for one process lifetime: `worker:{hostname}:{pid}:{boot_nonce}`. An explicit configured id is accepted up to the existing 200-character contract bound. Reuse is an operator choice and never grants settlement authority without the attempt fence; an id carrying a prior shutdown stamp remains drained under contract 0.1 and must not be reused for a replacement process.

## 8. Shutdown and process ownership

Shutdown triggers are local `stop()`, an optional caller event, remote presence shutdown, fatal service/supervisor state, SIGTERM/SIGINT in the CLI, or cancellation of `run()`.

Ordering is fixed:

1. set the service stop flag and wake every waiter;
2. prevent new claim calls and await any claim-to-submit critical section;
3. call the existing supervisor soft stop and let Stage-2B own handler cancellation, heartbeats, settlement, and executor drain;
4. stop and join the presence and reconnect loops;
5. close the dedicated listener;
6. close only resources the service/CLI owns;
7. set service `stopped`.

The first CLI termination signal requests normal soft stop. The second calls `stop(cancel=True)`. Signal handlers are installed by the CLI only, never by `WorkerService` or on import.

If the supervisor exposes `requires_process_exit` for an unkillable sync handler, the library remains honest: it does not mark itself stopped and never releases that job. The CLI logs a critical fence-free event, flushes logging, closes listener/presence resources, and terminates the process through an injectable process-exit boundary. Subprocess tests must prove the live thread cannot keep the CLI process alive and lease expiry remains the only reclaim path.

External cancellation of `run()` follows the same shielded shutdown sequence and re-raises `CancelledError` at the caller boundary after every safe cleanup that can finish. It does not turn shutdown into terminal job cancellation.

## 9. `pydantic-settings` configuration

`pydantic-settings` becomes a core dependency because the core `taskq` entry point owns the worker command. `WorkerSettings(BaseSettings)` is frozen, forbids unknown constructor fields, reads environment variables with prefix `TASKQ_`, and does not auto-load a `.env` file. Hosts may explicitly pass an env-file source themselves; the library never searches the filesystem.

Required CLI settings and canonical environment names:

| Field | Environment | CLI |
|---|---|---|
| DSN (`SecretStr`) | `TASKQ_DSN` | `--dsn` |
| registry import | `TASKQ_REGISTRY` | `--registry module:attribute` |
| queues (JSON array in env) | `TASKQ_QUEUES` | repeatable `--queue` |
| declared environment | `TASKQ_ENVIRONMENT` | `--environment` |
| worker id | `TASKQ_WORKER_ID` | `--worker-id` |
| concurrency / sync workers | `TASKQ_CONCURRENCY`, `TASKQ_SYNC_WORKERS` | matching flags |
| batch / poll interval | `TASKQ_BATCH`, `TASKQ_POLL_INTERVAL` | matching flags |
| listen | `TASKQ_LISTEN` | `--listen` / `--no-listen` |
| presence interval | `TASKQ_PRESENCE_INTERVAL` | matching flag |
| soft-stop timeout | `TASKQ_SOFT_STOP_TIMEOUT` | matching flag |
| expected environment | `TASKQ_EXPECTED_ENV` | `--expected-environment` |
| production acknowledgement | `TASKQ_ALLOW_PRODUCTION` | `--allow-production` |
| SQL pool size | `TASKQ_POOL_SIZE` | `--pool-size` |

Explicit CLI values override environment values; absent CLI values do not overwrite settings defaults or environment values. DSNs remain secret in validation errors, reprs, logs, snapshots, and exception chains.

The CLI requires a declared environment. If `expected_environment` is set it must equal the declared value. `environment="production"` is rejected unless `allow_production=True`. This is a deployment interlock over declared configuration, not a claim that a DSN can reveal its environment; startup logs the declared value without logging the DSN.

`pool_size` is 1..1000 and defaults to `concurrency + 2`, capped at 1000. The CLI SQL engine fixes `max_overflow=0`, so this is a bound rather than a suggestion. The listener connection is additional and reported separately. Startup logs process-local connection arithmetic (`pool_size + one listener when effective`), queues, concurrency, sync workers, and batch. No Stage-3 multi-process arithmetic is inferred here.

## 10. `taskq worker` CLI contract

The canonical command is:

```text
taskq worker --registry myapp.tasks:registry --queue work
```

The command performs these steps in order:

1. parse CLI overrides into `WorkerSettings` and enforce environment/production interlocks;
2. resolve exactly one `module:attribute`; the attribute must be a `TaskRegistry` or a zero-argument factory returning one;
3. validate subscriptions and require at least one registered handler for every subscribed queue;
4. construct one owned `SqlTaskqTransport`, its bounded pool, and—when enabled—one owned dedicated notification source from the same redacted DSN;
5. construct and run `WorkerService`;
6. install loop signal handlers only for the duration of the command;
7. close listener, service, and owned transport in reverse order on every exit path.

Import occurs only after settings and production interlocks pass. Registry loading never scans packages, files, or entry points. Import/config errors exit 2; clean local/remote shutdown exits 0; fatal runtime or required process exit exits non-zero. Tracebacks and logs remain DSN- and fence-free.

Existing `taskq migrate` and `taskq verify` behavior is unchanged. S2-05 does not add `worker run`, implicit migration, automatic queue creation, daemonization, PID files, or process supervision.

## 11. Observability contract

The service emits structured logs through the `taskq.worker` logger. Stable event names include:

```text
worker.starting, worker.ready, worker.degraded, worker.stopping, worker.stopped,
worker.fatal, listener.connected, listener.disconnected, listener.reconnected,
poll.sweep, claim.result, job.submitted, presence.failed,
presence.recovered, presence.shutdown_requested
```

Logs may include worker id, queue, job id, canonical job type, result state, counts, elapsed monotonic duration, configured intervals, and retry number. They never include DSN/userinfo, notification payload, job payload/headers/progress/result/error, attempt id, raw SQL parameters, or exception reprs that can carry those values.

`WorkerServiceSnapshot` is frozen and fence-free. It includes state/readiness, queues, listener requested/connected, presence healthy, available/active slot counts, cumulative claim sweeps, claimed/submitted counts, notification nudges/coalesces, reconnects, presence failures, start monotonic time, and last-success monotonic ages. It is process-local diagnostics, not a durable metric authority.

No high-cardinality metric label uses worker id or job id. Database-wide queue metrics continue to come from the existing SQL observer surface. S2-05 benchmark evidence records notification wake latency and shutdown drain duration but never promotes local measurements into a marketing claim.

## 12. Deterministic fault and race harness

S2-05 extends private package test machinery without creating the S2-06 consumer API:

- `ManualClock` drives poll, presence, and reconnect deadlines.
- `ScriptedTransport` gains ordered claim and worker-presence scripts plus a complete semantic call ledger.
- `ScriptedNotificationSource` exposes connect, subscribed, nudge, disconnect, reconnect, and close barriers.
- `ClaimBarrierTransport` separates call entry, commit/response, and return so stop/admission orders are forced.
- A process-exit strategy is injected in unit tests; CLI sync-thread exit uses a real subprocess.
- Resource ledgers enumerate claim, listener, reconnect, presence, supervisor, signal-waiter, executor, and settlement tasks/connections before and after shutdown.

No correctness test uses real sleeps. Every race below forces both winner orders repeatedly:

1. notification callback vs generation snapshot/wait;
2. listener disconnect vs committed enqueue, then reconnect catch-up;
3. poll deadline vs notification nudge (one or two sweeps, never zero or unbounded);
4. capacity freed vs stop request;
5. claim response vs stop request—the returned batch is supervised before drain;
6. presence shutdown response vs new claim boundary;
7. fatal job auto-stop vs a claim call about to begin;
8. second signal vs graceful drain;
9. listener reconnect vs final close—no resurrection after stop;
10. caller cancellation vs service cleanup and cancellation re-raise.

## 13. Acceptance matrix

### S2-05A — notification and poll kernel

- Poll-only mode claims ready and newly due jobs within `poll_interval + epsilon` on PostgreSQL 16 and 18.
- Missed, duplicated, empty, malformed, and burst notifications affect latency only; payloads are ignored.
- Dedicated connection loss leaves polling live, marks degraded, reconnects with bounded backoff, re-subscribes every channel, and performs catch-up.
- Queue/channel validation is exact and no unvalidated identifier reaches the driver.
- Repeated nudges coalesce without lost-wakeup or unbounded-task behavior.

### S2-05B — capacity, presence, and shutdown

- Claim batch never exceeds current supervisor capacity or 50; there is no prefetch queue and no capacity overshoot.
- Multiple queues rotate fairly under one permanently hot queue.
- Every closed claim state follows §6; unknown queue and impossible unavailable states are fatal.
- Stop-vs-claim races never leave a known returned claim outside supervision.
- Presence runs before first claim, is advisory, becomes degraded/recovered honestly, and remote shutdown prevents subsequent claims.
- Signal/local/remote/fatal/caller-cancel paths converge on one idempotent ordering and preserve every Stage-2B settlement rule.
- A live abandoned sync thread produces process-exit evidence and is never released.

### S2-05C — settings, CLI, observability, and packaging

- Settings precedence, JSON queue parsing, bounds, unknown constructor fields, environment mismatch, production refusal/acknowledgement, and secret redaction are exhaustive.
- Registry import succeeds for an instance/factory and rejects missing, wrong-type, async, and raising targets without opening a database connection.
- SIGTERM/SIGINT subprocess tests prove first-soft/second-hard behavior, clean exit, fatal exit, and required process exit.
- Structured logs and snapshots are stable and fence/secret/payload-free under success and every fault path.
- Core source plus wheel/sdist imports and invokes `taskq worker --help` on Python 3.12/3.13 without FastAPI or `outlabs-auth`.
- Core, HTTP-extra, and outlabs-extra artifact matrices continue to pass after adding `pydantic-settings`.

### S2-05-AUDIT — permanent completion evidence

- The ten race families in §12 run repeatedly without correctness sleeps.
- Real SQL exercises poll-only, notification wake, listener kill/reconnect, multi-queue fairness, presence shutdown, claim-stop admission, and budget/event conservation through the runner capability role.
- Resource audits return all asyncio tasks, listener connections, SQL checked-out connections, signal handlers, and executor threads to baseline.
- The identical full suite passes PostgreSQL 16.14 and 18.3; Python 3.12/3.13 and built artifacts collect the new surface permanently in CI.
- Benchmark B8 records notification-vs-poll wake distributions and B13 records graceful drain/release/expiry conservation with honest environment fingerprints. Toy/shared-runner results are report-only.
- Source and packaging searches prove no S2-05 dependency on HTTP, FastAPI, `outlabs-auth`, Stage-3 runtime code, or direct table DML.

## 14. Explicit non-goals and definition of complete

S2-05 explicitly excludes HTTP long-poll, routers, authorization, FastAPI lifespan helpers, embedded workers, housekeeper/tick ownership, consumer `taskq.testing`, automatic migrations, queue provisioning, payload-carrying notifications, local job buffers, cross-process coordination, and Stage-3 code.

S2-05 is complete only when S2-05A through S2-05C land as separate green commits, S2-05-AUDIT makes every acceptance row permanent on both PostgreSQL versions and artifact lanes, `TASKS.md` records each result in its commit, the final worktree is clean, and no excluded surface exists.
