# ADR-008 — FastAPI lifespan composition and process model

**Status:** Accepted 2026-07-18
**Resolves:** D-10; amends borrowed-feature 14

## Context

Setting a FastAPI `lifespan` disables legacy event handlers, and mounted sub-app lifespans don't run with the main app — a taskq helper that silently replaces the host lifespan would swallow host startup/shutdown behavior. Separately, an embedded worker starts once **per ASGI process**: under `uvicorn --workers 4`, configured concurrency and pools multiply by four, invisibly, unless the runtime says so.

## Decision

1. `TaskqRuntime` is a **composable async context manager** owning taskq's resources (pools, LISTEN, embedded worker supervisor, bounded sync-handler executor, presence, readiness). It never constructs the app or replaces a host lifespan; `compose_lifespans(host, runtime)` exists and is explicit. Composition ordering and failure cleanup are tested (harness T6).
2. **Embedded execution is opt-in:** `embedded_worker=False` whenever only a router is requested; enabling it is an explicit acknowledgement. The recommended production topology for autoscaled/multi-process deployments remains a dedicated `taskq worker` process.
3. **Budget visibility:** startup logs process-local queues, concurrency, and pool sizes, plus the deployment-wide arithmetic (`asgi_processes × concurrency`, `asgi_processes × pool_max`) when an expected process count is configured; configuration validation can estimate total connections against the database ceiling. Sync handlers run in the bounded thread executor — never on the event loop.
4. **Readiness semantics:** unready on schema incompatibility, failed listeners, or lost presence; never unready merely because a backlog exists (degraded diagnostics instead). Shutdown: stop claiming → drain grace → release unfinished claims (feature 11); uvicorn's graceful timeout must exceed `soft_stop_timeout`, warned at startup when inverted.
5. DI surface: `Depends(get_taskq_client)` over `app.state.taskq`; hosts never reach into private state; tests override the dependency.

## Consequences

- Feature 14 amended: default-off embedded worker, explicit acknowledgement, multi-process budget line.
- Facade-host `_system` claiming (spec §20.2 resolution) uses this runtime with `queues=["_system"], concurrency=1`.
- outlabsAPI dogfood (roadmap Stage 4) is the first real exercise of the embedded path.
