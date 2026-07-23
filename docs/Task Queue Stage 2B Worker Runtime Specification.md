# taskq — Stage 2B Worker Runtime Specification

> **Status:** Stage-2 implementation design, accepted 2026-07-18.
> **Scope:** S2-04A through S2-04D and S2-04-AUDIT.
> **Authority:** subordinate to Transport Protocol v1, the 0.1.2 Function Manifest, and ADR-001..013. If this document conflicts with them, the higher tier wins and implementation stops.

## 1. Purpose and boundary

S2-04 turns an already-claimed `ClaimedJob` into exactly one supervised handler execution. It owns handler dispatch, one heartbeat task per job, checkpoint batching, cancellation, fenced settlement retries, bounded concurrency, a bounded synchronous executor, and soft stop.

S2-04 deliberately does **not** claim work. The NOTIFY listener, polling/claim loop, worker presence loop, signal/CLI integration, and queue subscription validation are S2-05. Consumer-facing `taskq.testing` fixtures and inline/drain transports are S2-06. FastAPI, HTTP, and outlabs-auth remain Stage 3.

No S2-04 code may:

- infer lease duration from `lease_expires_at` or any wall clock;
- reproduce claim, fencing, retry-budget, or schedule decisions in Python;
- settle without the claim's exact `(job_id, attempt_id, worker_id)` fence;
- expose an attempt id through model dumps, reprs, ordinary logs, metrics, or public exceptions;
- start background work at import, registry construction, `TaskQ` construction, or supervisor construction;
- release, snooze, cancel, or otherwise settle a synchronous attempt while its handler thread may still run.

## 2. Module and public API boundary

S2-04 adds two core-only modules. Both import without optional extras.

```text
src/taskq/execution.py
    CancellationReason, CancellationToken, TaskCancelled
    Complete, Snooze, Cancel, Retry, NonRetryable, HandlerResult
    JobContext

src/taskq/worker.py
    WorkerOptions, JobRunState, JobRunOutcome, JobRunReport
    WorkerCapacityError, WorkerInvariantError
    WorkerClock, RealWorkerClock
    WorkerSupervisor

tests/worker_support.py
    ManualClock, ScriptedTransport, response-loss injection, handler barriers
```

`taskq.__init__` re-exports the user-facing execution and worker values. Test utilities are not packaged as a public API and do not pre-empt S2-06.

The supervisor consumes an existing `TaskqTransport` and `TaskRegistry`. It neither creates nor closes the transport. `TaskQ.worker(...)` and the claim loop are deferred to S2-05.

### 2.1 Handler call shapes

Registration accepts async functions and ordinary synchronous functions in either shape:

```python
def handler(payload: InputModel) -> OutputModel | HandlerResult: ...
def handler(ctx: JobContext, payload: InputModel) -> OutputModel | HandlerResult: ...
```

Async variants use the same arguments. The final positional parameter must be annotated with the task's exact input model. A two-parameter handler's first parameter must be annotated as `JobContext`. Return annotations may be the exact output model, a closed handler-result type, or a union containing only those values and `None`. Variadic positional handlers are rejected. Existing one-argument async handlers remain valid.

Synchronous handlers always run in the supervisor's bounded executor, never on the event loop. A handler's sync/async nature is immutable registry metadata.

## 3. Closed handler results

All result models are frozen Pydantic models and reject unknown fields.

```python
class Complete(BaseModel):
    result: dict[str, Any] = Field(default_factory=dict)
    followups: tuple[dict[str, Any], ...] = ()  # at most 20

class Snooze(BaseModel):
    delay_seconds: int = Field(ge=0, le=2_592_000)
    progress: dict[str, Any] | None = None
    reason: str | None = None

class Cancel(BaseModel):
    reason: str = Field(min_length=1)

class Retry(BaseModel):
    after_seconds: int | None = Field(default=None, ge=0, le=2_592_000)
    error: str | None = None
    progress: dict[str, Any] | None = None

class NonRetryable(BaseModel):
    error: str = Field(min_length=1)
    progress: dict[str, Any] | None = None
```

`HandlerResult` is the closed union of those five models. Follow-ups are reserved in 0.1.2: the worker passes non-empty values to `complete`, allowing the contract's `TQ501` skew path to fire rather than silently dropping them.

Normalization is exactly:

| Handler completion | Normalized intent |
|---|---|
| output-model instance or mapping | validate through the task's output model, JSON-dump, `Complete` |
| `None` | validate `{}` through the output model; complete only when valid |
| explicit closed result | preserve the requested intent; validate explicit complete data through the output model |
| `TaskCancelled` | cancellation precedence in §6 decides; never a failure |
| `asyncio.CancelledError` caused by the supervisor | cancellation precedence in §6 decides |
| configured `retry_exceptions` match | `Retry` |
| any other unhandled exception | `Retry` unless `task.retry is False`, then `NonRetryable` |
| invalid output/result | `NonRetryable(error="invalid_handler_result: …")` |

Diagnostic strings may enter the protected job error field but never ordinary logs with payload or fence data. SQL applies the byte-safe cap from ADR-012.

### 3.1 Settlement mapping

| Intent | Fenced transport command |
|---|---|
| `Complete` | `complete(result=…, followups=…)` |
| `Snooze` | `snooze(delay_seconds, reason, progress)` |
| `Cancel` | `cancel_running(reason)` |
| `Retry` | `fail(retryable=True, retry_after_seconds, error, progress)` |
| `NonRetryable` | `fail(retryable=False, error, progress)` |
| shutdown interruption | `release(cause="worker_shutdown", progress=latest)` |
| missing task/handler | `release(cause="no_handler", delay_seconds=60)` |

Handlers never receive the transport and cannot open a second settlement path through `JobContext`.

## 4. Execution context and cancellation token

`CancellationToken` is safe to set from the event-loop thread and read from a synchronous handler thread. It uses thread-safe state, not an event-loop-bound primitive.

```python
class CancellationReason(StrEnum):
    SHUTDOWN = "shutdown"
    OPERATOR = "operator"
    LEASE_LOST = "lease_lost"
```

Reason precedence is `LEASE_LOST > OPERATOR > SHUTDOWN`; a later stronger signal replaces a weaker one and a weaker signal never masks a stronger one.

`JobContext` exposes `job_id`, queue, canonical job type, payload model, initial/latest progress, attempt number, failure/max-attempt counts, headers, and the cancellation token. Its repr and serialization omit the attempt id. The fence remains privately accessible only to the supervisor.

Context operations:

- `should_cancel()` reads the token.
- `raise_if_cancelled()` raises `TaskCancelled` carrying only the safe reason.
- `await checkpoint(progress)` validates a JSON object ≤2,048 UTF-8 bytes, stores the newest snapshot, and yields to the event loop; it does not issue SQL.
- `checkpoint_nowait(progress)` provides the same thread-safe staging operation for synchronous handlers.
- the heartbeat loop atomically snapshots pending progress; a failed heartbeat retains it for the next attempt. A successful heartbeat marks only that snapshot flushed. A newer concurrent checkpoint is never discarded.

The claim payload is validated through the registered input model before the handler starts. Invalid payload is a non-retryable handler failure, not an untyped crash.

## 5. Per-job state machine

`JobRunState` is closed:

```text
accepted → running → settling → settled
                   ↘ cancel_pending ↗
          running/cancel_pending/settling → ownership_lost
          running/cancel_pending → abandoned_sync
          any nonterminal → runtime_failed
```

| State | Meaning |
|---|---|
| `accepted` | capacity reserved; no handler or heartbeat task yet |
| `running` | handler and exactly one heartbeat coroutine active |
| `cancel_pending` | operator or shutdown signal delivered; heartbeat continues |
| `settling` | handler stopped; heartbeat remains active while the same verb is retried |
| `settled` | typed command returned a command-valid success outcome |
| `ownership_lost` | heartbeat `ok=false`, three consecutive heartbeat failures, settle `lost`, or retry exhaustion made ownership unsafe; no alternative settle follows |
| `abandoned_sync` | ownership is unsafe while an unkillable sync thread remains; process exit is required and no settlement is permitted |
| `runtime_failed` | local invariant/config/cross-verb conflict; supervisor enters soft stop |

Every transition is recorded in a fence-free `JobRunReport`. A state may not transition twice to terminal and one job may issue at most one settlement verb (retries repeat that exact verb only).

## 6. Cancellation and precedence matrix

Lease ownership dominates every handler outcome; operator cancellation dominates shutdown and normal results.

| Event | Async handler | Sync/thread handler | Settlement |
|---|---|---|---|
| heartbeat `ok=false` | cancel immediately | set lease-lost token | suppress forever; sync becomes `abandoned_sync` until thread returns |
| third consecutive heartbeat transport failure | cancel immediately | set lease-lost token | suppress forever; stop heartbeating and let lease expiry reclaim |
| non-retryable heartbeat error | cancel immediately; supervisor soft-stops | set lease-lost token; supervisor soft-stops | suppress forever |
| heartbeat `cancel_requested=true` | signal operator token; hard-cancel after `cancel_grace_seconds` | signal token; cannot hard-cancel; keep heartbeating | when handler stops, `cancel_running`; ignore its normal result |
| soft stop begins | signal shutdown token; handler may finish normally | signal shutdown token; handler may finish normally | normal result wins before deadline |
| soft-stop deadline | hard-cancel | signal only; continue heartbeat and wait | async `release(worker_shutdown)`; never release live sync thread |
| `stop(cancel=True)` | enter deadline behavior immediately | enter deadline behavior immediately | same as deadline |
| handler returns `Cancel` | stop handler normally | stop handler normally | fenced `cancel_running` |
| external cancellation of supervisor operation | convert to soft stop, preserve settlement critical section | signal shutdown; wait | same shutdown rules, then re-raise at caller boundary |

For operator cancellation, a handler returning after the signal cannot complete successfully: `cancel_running` is the sole chosen verb. For shutdown, a handler that finishes before the deadline settles its actual result; shutdown is not job cancellation.

A synchronous handler that ignores cancellation may keep the worker from reaching `stopped`. S2-04 never lies about that. It exposes `requires_process_exit`; S2-05's CLI may exit the process and let lease expiry reclaim, but it must not release the live attempt first.

## 7. Heartbeat supervision

Each job gets exactly one heartbeat coroutine. The interval is:

```python
min(claimed_job.lease_seconds / 3, 30.0)
```

It is scheduled only with `WorkerClock.sleep`, whose production implementation uses the event loop's monotonic clock. `lease_expires_at` is never read for scheduling.

At each interval the coroutine calls `heartbeat` with the exact fence, the effective `lease_seconds`, and at most one latest checkpoint. Behavior:

1. Successful `ok=true` resets the consecutive-failure count and commits the checkpoint snapshot.
2. `cancel_requested=true` signals operator cancellation and starts one grace deadline; repeated responses do not create timers.
3. `ok=false` is immediate ownership loss.
4. Retryable transport failures back off monotonically and retain pending progress. One failure never stops the heartbeat. The third consecutive failure is ownership loss.
5. Non-retryable errors are runtime failures and ownership becomes unsafe.
6. The heartbeat continues through handler normalization and settlement retries. It stops only after a terminal settle outcome or ownership loss.

The failure threshold is the normative constant three, not a user option.

## 8. Verb-aware settlement retry policy

Only the chosen fenced verb may be retried, always with identical semantic arguments and the same attempt id. Retryable `TaskqError` values (`TQ500`/`TQ503`) and transport timeouts use bounded exponential backoff on `WorkerClock`. Non-retryable exceptions fail immediately.

`WorkerOptions` defaults to five settle attempts, 0.25-second base, and 5-second cap. Tests use a manual clock; production does not use random jitter inside a single worker because database-side job backoff is a separate concern.

Outcome rules:

- the command's original success outcomes and `already_settled` are success;
- `lost` means ownership loss and is never converted to success;
- `settle_conflict` is a cross-verb invariant failure, causes supervisor soft stop, and never triggers another verb;
- a value outside that command's protocol-owned outcome set is `TQ500` at the transport boundary;
- exhausted transient retries stop heartbeating, report `settlement_unknown`, soft-stop the supervisor, and let lease expiry provide the only recovery.

Programmable lost-response injection applies the scripted command once, drops its response, then lets the identical retry observe `already_settled`. The harness asserts the handler ran once and only one semantic command was selected.

For `complete` with non-empty follow-ups, deterministic `TQ422` or inactive-capability `TQ501` triggers the ADR-007 escape: retry-safe `fail(retryable=False, error="invalid_followup: …")`, then supervisor soft stop for capability skew. No parent success or child loss may be reported.

## 9. Bounded supervisor and soft stop

`WorkerOptions` is frozen and validates:

```python
concurrency: int = 1                 # 1..1000
sync_workers: int | None = None      # default concurrency; 1..concurrency
soft_stop_timeout: float | None = None
cancel_grace_seconds: float = 30.0
settle_max_attempts: int = 5
settle_backoff_base: float = 0.25
settle_backoff_cap: float = 5.0
no_handler_delay_seconds: int = 60
```

Construction creates no task or thread. `start()` creates no polling loop; it only opens the supervisor for submissions. `available_slots` and `wait_for_capacity()` let S2-05 avoid over-claiming. `submit(claimed_job)` reserves a slot synchronously or raises `WorkerCapacityError`; an already-running job id/attempt is rejected. `run_job` is the awaitable single-job convenience.

The supervisor owns exactly one `ThreadPoolExecutor(max_workers=sync_workers)`, created lazily on the first sync handler and shut down only after no sync call remains. At most `concurrency` total handlers and `sync_workers` synchronous handlers execute. Claimed jobs never sit queued inside the executor without an active heartbeat.

Soft-stop phases:

1. close intake atomically;
2. signal shutdown cooperatively and continue heartbeat/normal completion;
3. at the optional deadline (or immediately for `stop(cancel=True)`), hard-cancel async handlers and release them with `worker_shutdown`;
4. keep live sync handlers heartbeating and wait, or surface `requires_process_exit`; never release them;
5. attempt every permitted settlement, join heartbeat tasks, shut down an idle executor, set `stopped`.

`stop()` is idempotent and concurrent callers share one stop operation. A second `stop(cancel=True)` escalates to phase 3. No submission succeeds after phase 1.

## 10. Deterministic test machinery

S2-04 tests use no correctness-sensitive real sleeps.

- `ManualClock` exposes monotonic time, records sleepers in deadline order, and advances only when the test requests it.
- `ScriptedTransport` implements the worker-used `TaskqTransport` methods, records redacted command identities/arguments, and serves scripted results or errors.
- `drop_response_after_apply(command, times=1)` records one durable semantic application, raises a retryable transport error, then returns the scripted replay outcome.
- async and sync handler barriers expose started, checkpointed, cancellation-seen, and release gates.
- task-ledger assertions enumerate all live asyncio tasks and executor threads before and after each scenario.

These are package-owned harness utilities, not the S2-06 consumer testing API.

## 11. Acceptance matrix

### S2-04A — execution primitives

- Frozen closed result models accept every boundary and reject invented fields, negative/oversize delays, empty cancel/error values, and >20 follow-ups.
- Async/sync one- and two-argument handler registration is annotation-checked; invalid signatures are atomic registry failures.
- Input/output validation, normal-result mapping, exception/retry policy mapping, and 2KB checkpoint validation are exhaustive.
- Cancellation reason precedence and thread-safe checkpoint races are deterministic.
- Core imports remain free of FastAPI and outlabs-auth.

### S2-04B — heartbeat and per-job supervision

- Exact cadence is derived from returned `lease_seconds` with a monotonic manual clock; tests prove `lease_expires_at` is unused.
- Exactly one heartbeat task exists per active job and is joined at terminal state.
- One/two transient heartbeat failures recover; the third cancels async or suppresses sync settlement.
- `ok=false`, operator cancellation grace, checkpoint retention/flush, missing handler release, and invalid payload behavior follow §§4–7.
- Fences are absent from repr, model dump, caplog, reports, and exception text.

### S2-04C — settlement replay

- Every handler intent maps to exactly one fenced verb and its command-specific outcome set.
- Lost responses for complete/fail/snooze/release/cancel retry the same verb and converge through `already_settled`.
- `lost`, `settle_conflict`, retry exhaustion, non-retryable errors, and wrong-command outcomes have distinct terminal reports.
- No handler reruns during settlement retry; no alternative verb follows ownership loss/conflict.
- Follow-up `TQ422`/`TQ501` uses the terminal-fail escape and soft-stops for skew.

### S2-04D — bounded concurrency and soft stop

- N-way submission never exceeds configured total or sync concurrency.
- Intake closes before drain; no new job starts after stop begins.
- Infinite grace drains normal results. Deadline releases hard-cancelled async jobs budget-free.
- Live sync threads are never released; the supervisor stays non-stopped or reports process-exit required.
- Concurrent/double stop is idempotent and immediate escalation works.
- All handler, heartbeat, grace, settlement, executor, and stop tasks are joined.

### S2-04-AUDIT — permanent evidence

- Choreographed races cover handler return vs heartbeat loss, operator cancel vs complete, shutdown deadline vs settle response, checkpoint vs heartbeat snapshot, and sync return vs lease loss for repeated rounds without sleeps.
- Resource audit proves zero leaked asyncio tasks, executor threads, checked-out SQL connections, or unobserved task exceptions.
- Real SQL integration proves complete/retry/snooze/cancel/shutdown/no-handler budget and event conservation plus lost-response replay on PostgreSQL 16 and 18.
- Full regular suite and the million-row plan gate pass on both versions.
- Python 3.12/3.13 source isolation and wheel/sdist core, HTTP-extra, and outlabs-extra smokes import the worker surface without optional dependency leakage.
- CI permanently runs the worker unit/race/resource suite and the full PG16/PG18 SQL integration suite.

## 12. Definition of complete

S2-04 is complete only when S2-04A through S2-04D each land as a separate green commit with `TASKS.md`, S2-04-AUDIT makes every acceptance row permanent, the final worktree is clean, and no S2-05 or Stage-3 module exists. The natural round-4 external review boundary is the completed Stage 2B worker kernel plus the contract-0.1.2 upgrade path.

## 13. Trusted host side-effect reporter (ADR-022)

An optional reporter is a worker-runtime capability for a host-owned domain
effect that must be authorized against the current package attempt. It is not
a registry task, transport credential, or public handler field. The service
owns the current attempt record and invokes the reporter through a bounded
`JobContext.report_effect()` request; handlers never receive an attempt id or
fence. The service retains all heartbeat, cancellation, ownership-loss,
process-exit, and settlement replay ownership. A reporter failure cannot
invent a settlement verb or cause a handler rerun during settlement replay.

The reporter's host must perform authoritative stable-effect lookup before an
external action and idempotent application afterwards. This makes a reclaimed
attempt able to observe the earlier committed effect rather than repeat the
external action. Any host adoption requires its own real SQL/HTTP race,
response-loss, hard-kill, and resource evidence; the generic worker extension
alone authorizes no external-effect lane.

### 13.1 Closed provider-control member (ADR-031)

A host may extend its reporter union with the closed
`llm_provider_control` member when a handler must preserve a durable
provider-budget reservation without receiving attempt identity. This is not a
generic reporter method, provider proxy, taskq Protocol command, or queue
admission reservation. The reporter binds the current job, attempt and worker;
the handler supplies only one strictly bounded reserve or settle request.

Reserve accepts a closed lane, entity and operation, provider and model,
canonical request fingerprint, and token estimate. The host authenticates and
authorizes the authoritative task queue before body decode, validates those
fields against stored strict input, derives a stable logical identity plus a
numbered generation from the reporter-owned attempt, and stamps time in
PostgreSQL. Same-attempt reserve replay is byte-stable. A different attempt
cannot inherit a live generation and receives typed retryable
`reservation_pending` until database expiry. Settle row-locks the reservation
and atomically records its state plus one provider event using a canonical
settlement hash. Exact replays return the same receipt and changed replays fail
closed.

An expired unsettled reservation releases its budget hold but remains a typed
`expired_unsettled` unknown-cost record; it is never represented as zero
usage. The first observing attempt is recorded so its exact response replay is
stable. Only a later attempt may create the next numbered generation and spend
a new budget unit; no generation is rewritten or transferred. Every adopting
side-effecting lane must prove hard-kill reclaim through that state machine and
does not inherit evidence from a pure-lane drill.
