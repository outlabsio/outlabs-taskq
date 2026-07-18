# 14 — Embedded Worker and FastAPI Lifespan

> **Priority:** SHOULD
> **Provenance:** the Rails in-process Postgres queue async mode (worker inside the web process); the Go/Postgres job queue insert-only symmetry (capability by construction); Unified Spec §11.4 housekeeper topology; FastAPI lifespan idiom
> **Depends on:** [04 Insert-Only Client](./04-insert-only-client.md), [11 Soft Stop and Shutdown](./11-soft-stop-and-shutdown.md), [05 Queue Profiles](./05-queue-profiles.md)
> **First host:** outlabsAPI (single container, Coolify + Neon, no worker fleet)

---

## 1. Intent

The smallest real deployment of taskq is **one FastAPI process**: API + housekeeper + a small embedded worker, one Postgres. No worker fleet, no broker, no second container. That is exactly the outlabsAPI shape today (its RabbitMQ consumers run in-process already — this feature replaces them with durable taskq lanes) and the dev-environment shape for every host.

Three runtime roles, all composable in one process or split across many:

| Role | What runs | Who uses it |
|---|---|---|
| **Facade host** | HTTP router + housekeeper coroutine | diverse-data-api, qdarteAPI today (Unified Spec §11.4) |
| **Embedded worker** | claim/settle loop inside the API process | outlabsAPI, dev environments, tiny hosts |
| **External worker** | separate process, HTTP or DB-direct | Diverse/QDarte fleets |

Embedded is a **deployment choice, not an API**: the same `@tq.task` handlers move unchanged to an external worker when the host outgrows one process.

---

## 2. Lifespan integration (normative)

### 2.1 One-liner

    from fastapi import FastAPI
    from taskq.http import taskq_lifespan

    app = FastAPI(lifespan=taskq_lifespan(
        tq,
        housekeeper=True,                          # tick loop in this process
        worker=WorkerOptions(                      # omit → facade-host only
            queues=["tools", "notifications"],
            concurrency=2,
            listen=True,
        ),
    ))

### 2.2 Composable form (host already has a lifespan)

    from taskq.http import TaskqRuntime

    runtime = TaskqRuntime(tq, housekeeper=True, worker=WorkerOptions(...))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.start()
        try:
            async with host_lifespan(app):
                yield
        finally:
            await runtime.stop()      # runs feature-11 soft stop

`TaskqRuntime.start()` is idempotent; `stop()` awaits phase 4 (`stopped`) of feature 11. Uvicorn/ASGI graceful-shutdown timeout MUST exceed `soft_stop_timeout` — document this beside the option, and log a warning at startup when both are configured and inverted.

### 2.3 Dependency injection

    from taskq.http import get_taskq

    @app.post("/tools/{name}/runs/queued", status_code=202)
    async def queue_tool_run(
        name: str,
        params: dict,
        tq: TaskQ = Depends(get_taskq),
        session: AsyncSession = Depends(get_session),
    ):
        result = await tq.enqueue(
            queue="tools",
            job_type=f"tools.{name}",
            payload=params,
            idempotency_key=request_id_from(params),
            session=session,          # joins the host transaction (below)
        )
        return {"job_id": result.job_id, "status": result.status}

### 2.4 Transactional enqueue (the load-bearing DX rule)

When `session=` is passed, the enqueue **joins the host's open transaction**: the job becomes visible if and only if the caller's domain writes commit. NOTIFY is emitted in-transaction (Unified Spec / feature 06), so a rollback produces no phantom wakeups. This is taskq's outbox equivalent — document it as *the* recommended pattern for "write row + enqueue follow-up work" endpoints, and test it in the harness (enqueue + rollback → no job).

Without `session=`, the client uses its own short transaction (auto-commit enqueue).

---

## 3. Embedded worker rules (the honest part)

Running claims inside an API process trades isolation for simplicity. The rules that keep it honest:

0. **Opt-in, never implied (ADR-008).** `embedded_worker=False` whenever only a router is requested; enabling it is an explicit acknowledgement. Startup logs process-local queues/concurrency/pools AND the deployment-wide arithmetic — under `uvicorn --workers N`, every budget multiplies by N (`N × concurrency` handlers, `N × pool_max` connections); when an expected process count is configured, validation estimates total connections against the database ceiling. Dedicated `taskq worker` processes remain the recommendation for autoscaled/multi-process deployments.
1. **Async or declared-blocking.** Handlers must be non-blocking async; a sync/CPU-bound handler must declare `@tq.task(..., blocking=True)` and is offloaded via `anyio.to_thread`. An undeclared blocking handler starving the event loop is the #1 failure mode — call it out in docs and default `concurrency=1`.
2. **Never embed heavy lanes.** Rendering, browser scraping, LLM batch work do not run embedded. Queue profiles for such lanes should set a marker (`embedded: discouraged` note field) — advisory, not enforced.
3. **Pool split.** The embedded worker uses its own small connection pool (default 2) plus the dedicated LISTEN connection — it must not exhaust the API's request pool. Both pools may point at the same DSN; sizes are independent settings.
4. **Ordinary worker semantics.** The embedded worker registers presence (`worker_id = "api:{hostname}:{pid}"`), heartbeats, and releases on shutdown exactly like an external worker. No special settle paths; lease expiry covers a SIGKILLed container.
5. **Deploy behavior.** SIGTERM → ASGI shutdown → feature-11 soft stop → in-flight async jobs released with `worker_shutdown`, budget untouched; `blocking=True` thread handlers follow the R2-11 sync contract (cooperative token; no release while the thread lives — hold the lease or exit and let expiry reclaim). On platforms with rolling deploys (Coolify), old and new processes may briefly claim concurrently — safe by construction (SKIP LOCKED + fencing).
6. **Graduation path.** When embedded stops being enough: run `taskq worker run --queues tools` pointing at the same DB with the same handler package, set `worker=None` in the API. Nothing else changes.

---

## 4. First-host adoption sketch (outlabsAPI)

Current: `POST /tools/{tool}/runs/queued` → FastAPI `BackgroundTasks` → RabbitMQ publish → in-process aio-pika consumer; **no durable run row, no status endpoint, no retry budget.**

Target: same route calls `tq.enqueue("tools", ...)` (202 + `job_id`); embedded worker executes tool handlers; run status is the taskq read model (`GET /taskq/jobs/{id}`); retries/backoff/dead-letter come from the queue profile. Contact-form, newsletter, analytics, and WhatsApp lanes migrate the same way, one queue each. RabbitMQ (aio-pika + pika + the standalone `workers/` processes) retires when the last lane moves — completing the standing "Postgres-native, no brokers" stack decision. outlabs-workers (Telegram/UAYA notifications) follows the same pattern against the same schema.

---

## 5. Acceptance tests

1. Lifespan context: `TestClient` startup/shutdown starts and cleanly stops housekeeper + embedded worker (no orphan tasks, LISTEN conn closed).
2. End-to-end in one process: enqueue via route → embedded worker claims, runs handler, settles — against real SQL contract.
3. Transactional enqueue: enqueue with `session=` then rollback → no job row, no NOTIFY received by a listening worker.
4. `blocking=True` handler: event loop stays responsive (concurrent request latency probe passes while handler sleeps in thread).
5. SIGTERM with in-flight job → released `worker_shutdown`, `failure_count` unchanged, process exits before uvicorn grace deadline.
6. Pool split: saturating the worker pool does not block API requests (and vice versa).

---

## 6. Explicit non-goals

- Multiprocessing/fork inside the API container
- Embedded execution for CPU/browser lanes at any fleet host
- Auto-scaling or supervisor logic (Coolify/systemd own process lifecycle)
- A second execution semantics — embedded is a normal worker in the same process
