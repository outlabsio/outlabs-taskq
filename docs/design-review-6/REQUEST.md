# External Design Review Round 6 — Stage-4 First-Host Dogfood Gate

## Assignment

Perform an adversarial, source-backed review of the frozen Stage-4 outlabsAPI dogfood plan. Decide
whether host implementation may open at S4-01. This is a specification review, not an implementation
review: no Stage-4 host code should exist in the reviewed range.

Repository paths:

- library: `/Users/macbookm3/Documents/projects/outlabs-taskq`
- first host: `/Users/macbookm3/Documents/projects/outlabsAPI`

Pinned baselines:

- `outlabs-taskq`: review through the S4-00 commit that contains this request; accepted Stage-3 code
  ends at `b6e29ca`, and `8a13262` records its independent acceptance;
- `outlabsAPI`: `a0019cd`; and
- OutLabs package reality: host pin `0.1.0a20`, taskq adapter pin/API audit `0.1.0a24`.

Write the response as new files under `docs/design-review-6/` and modify nothing else. The request is
immutable once handed off. Do not implement a fix, update the board, or edit either host repository.

## Authority read order

Read in this order before judging the Stage-4 text:

1. `AGENTS.md`
2. `docs/README.md`
3. `docs/Task Queue Transport Protocol v1.md`
4. `docs/Task Queue 0.1 Function Manifest.md`
5. ADR-002, ADR-005, ADR-006, ADR-007, ADR-008, ADR-010, ADR-011, ADR-013, and ADR-017
6. `docs/Task Queue Stage 3 FastAPI and Authorization Specification.md`
7. `docs/Task Queue Authorization & Queue Permissions.md`
8. `docs/taskq-borrowed-features/14-embedded-worker-and-fastapi-lifespan.md`
9. `docs/Task Queue Library Extraction Design Brief.md` §2.3
10. `docs/Task Queue Stage 4 outlabsAPI Dogfood Specification.md`
11. the live taskq and outlabsAPI source named below

Tier 0 and ADRs win every conflict. If the plan needs a new taskq wire field, SQL function, outcome,
or permission grammar, file a Contract question and block implementation; do not accept a host-side
workaround.

## Independently establish source reality

Do not trust the specification's inventory. Inspect and report independently:

### outlabsAPI

- dependency pins and lock state;
- Docker process count and shutdown command;
- current lifespan/startup/shutdown ownership;
- authentication construction, session dependency, API-key authorization, migrations, and schema;
- queued tools route, current response, publish/fallback behavior, registry, tool result type, and
  worker execution semantics;
- CORS, health, environment settings, deployment documentation, database DSN normalization, and
  managed-database assumptions;
- host tests and which infrastructure cases are skipped; and
- exact absence of taskq source/dependency/configuration.

### outlabs-taskq

- artifact/version/release reality;
- `TaskqRuntime.from_dsn`, pool/listener arithmetic, one-process acknowledgement, readiness, and
  stop/process-exit behavior;
- lifespan composition ordering;
- mounted facade route ownership and operator omission;
- `OutlabsQueueAuthorizer` against exact a24, session lifecycle, permission candidates, and actor
  mapping;
- provisioning report/apply/reconcile and standard-role contents;
- canonical queue-profile fields and bounds;
- job-detail redaction/result query behavior; and
- worker handler normalization, retry, idempotency, fencing, and response-loss guarantees.

Run the library's current full PostgreSQL suite and the host's DB-free suite if the local services are
available. An a24 overlay may support source-feasibility evidence, but it is not a substitute for the
locked resolver/schema proof the plan assigns to S4-01.

## Required adversarial audit

### A. Dependency and upgrade gate

1. Is exact a24 the narrowest honest choice, or does the host require the static adapter?
2. Does the plan distinguish source compatibility from resolver, migration, lock, and real-schema
   compatibility?
3. Is the immutable taskq artifact requirement executable given the repository's current release
   state, without smuggling a local path into production?
4. Are version, hash, preview branch, migration credential, runtime credential, and rollback duties
   separately owned?

### B. Producer and idempotency

1. Prove there is no path that publishes to both taskq and the legacy system.
2. Probe ambiguous enqueue response, keyed replay, concurrent keyed requests, unkeyed honesty,
   queue-depth refusal, invalid tool/params, and allowlist changes.
3. Determine whether the proposed exact 202 body and status URL are producible from current taskq
   APIs without request echoes masquerading as durable state.
4. Check that callers cannot bypass the registered-tool allowlist through a direct generic enqueue
   grant.

### C. Result read and authorization

1. Trace caller credential → host producer authorization → trusted DB enqueue separately from caller
   credential → mounted job read.
2. Verify `taskq_tools:read`/global read, denial/absence hiding, result/error opt-ins, actor mapping,
   and session finalization against real a24 APIs.
3. Verify operator routes and credentials remain absent from the ordinary app pool.
4. Attempt cross-queue reads, direct enqueue, missing result, terminal error, malformed id, invalid
   request id, and credential-change races.

### D. Handler and side-effect safety

1. Inspect both selected tools and challenge the claim that they are safe embedded read-only work.
2. Check payload/result bounds, secret leakage, parameter validation, exception classification,
   terminal `success=false`, cancellation, retry budget, and event-loop responsiveness.
3. Determine whether any tool performs an external mutation, long CPU/browser/render work, or returns
   credentials/network details that would make the lane unsafe.
4. Verify the failure probe is unreachable from public tool discovery/routes and cannot survive its
   enable flag accidentally.

### E. Runtime, managed database, and lifecycle

1. Recompute process-local and deployment-wide connection arithmetic, including host SQL/Auth pools
   and reserve—not only taskq pools.
2. Challenge the one-Uvicorn-process invariant and platform graceful-shutdown timing.
3. Verify poll-only correctness with all listeners disabled and queue notification configuration
   aligned.
4. Audit managed-database role creation, session/transaction pooler behavior, SSL/DSN normalization,
   autosuspend-vs-housekeeper cost, preview isolation, and migration/runtime credential separation.
5. Execute or model startup failure at every boundary and prove resource/lifespan unwind order.

### F. Deployment, failure, and rollback

1. Falsify the two-deploy-cycle evidence: old/new overlap, keyed conservation, soft drain, readiness,
   and no duplicate tool invocation.
2. Falsify the controlled process-termination drill: same job id, new fence, lease expiry/reap,
   bounded budget, eventual success, and no manual DML.
3. Walk rollback with queued, running, retry-scheduled, succeeded, and failed jobs. Nothing may be
   copied to the legacy system or silently abandoned.
4. Verify re-enable convergence and that legacy retirement is strictly post-acceptance.

### G. Acceptance-oracle quality and scope

For every S4-01/S4-02/S4-03/S4-AUDIT row, identify the independent oracle, mutation or counterexample
that can make it fail, required environment, and durable evidence artifact. Reject acceptance rows
that can pass by asserting the implementation against itself.

Diff the taskq S4-00 range and prove it contains only the acceptance record, Tier-3 specification,
review request/registration, and Tier-2 status. There must be no SQL/migration/Tier-0/Tier-1 decision
change, Tier-4 edit, host edit, dependency edit, or Stage-4 implementation.

## Required response structure

1. **Verdict:** `READY` or `BLOCKED` for S4-01.
2. **Reproduced evidence:** commits, tests, package/API versions, and source inventory.
3. **Finding registry:** stable ids `R6-01...`, severity, authority, exact source evidence, failure
   mode, smallest remediation, and required regression/oracle.
4. **Contract questions:** separate from implementation/spec defects; state `none` explicitly.
5. **Acceptance audit matrix:** every frozen row mapped to an independent oracle and owner.
6. **Scope/hygiene result:** exact file range and prohibited-surface check.
7. **Preconditions to open S4-01:** shortest ordered list; say `none` if clean.
8. **Deferred follow-ups:** only genuinely non-blocking work with a named future owner.

Do not name third-party queue projects in the response. Refer to the existing system generically as
the legacy broker/path.
