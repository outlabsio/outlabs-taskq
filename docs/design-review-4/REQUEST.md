# outlabs-taskq external design review — round 4 request

> **Tier 4 — immutable review provenance.** Prepared 2026-07-18 after the Stage-2B exit gate. This file records the request exactly; the reviewer may add only `RESPONSE.md` beside it. Accepted findings must subsequently follow the repository's ADR, contract, and task-board process.

Andi, please perform an independent, adversarial review of the completed Stage-2B worker execution kernel and the SQL contract-0.1.2 upgrade that makes its heartbeat guarantee implementable. The question is not whether the worker looks plausible: determine whether S2-04A through S2-04D faithfully implement the locked contracts and accepted decisions under cancellation, ownership loss, response loss, concurrency, and shutdown.

Do not modify any existing repository file during this review. Write the self-contained response as `docs/design-review-4/RESPONSE.md` and modify nothing else. Do not infer intent from implementation when a higher-authority document answers the question. Do not name external queue projects in the response.

## Authority and read order

Read in this order before evaluating code:

1. `AGENTS.md` — hard implementation rules.
2. `docs/README.md` — tier map and conflict rules.
3. `docs/Task Queue Transport Protocol v1.md` and `docs/Task Queue 0.1 Function Manifest.md` — locked Tier-0 contracts.
4. `docs/adr/README.md` and ADR-001..013 — accepted decisions; ADR-013 owns the contract-0.1.2 lease projection.
5. `docs/Task Queue Stage 2B Worker Runtime Specification.md` — S2-04 execution, cancellation, heartbeat, settlement, lifecycle, and acceptance contracts.
6. `docs/Task Queue Test & Benchmark Harness.md` — normative evidence design.
7. `TASKS.md` and `docs/Task Queue Build Plan.md` — claimed completion and exit gates.
8. Only then inspect the implementation and tests listed below.

Tier 0 wins every conflict, followed by accepted ADRs. If the implementation exposes a genuine contract defect or contradiction, label it a **Contract question**; do not recommend an implementation workaround or silently reinterpret the contract.

Round 2 finding R2-11 is a mandatory audit target, not historical background to trust: a live synchronous thread can never be treated as hard-cancelled. Independently prove that the implementation never releases, snoozes, cancels, or otherwise transfers ownership while such a thread may still produce side effects.

## Review set

Audit these artifacts as one system:

- `docs/adr/ADR-013-effective-lease-claim-projection.md`
- `src/taskq/sql/migrations/0001_initial.sql`
- `src/taskq/sql/migrations/0002_contract_0_1_1.sql`
- `src/taskq/sql/migrations/0003_contract_0_1_2.sql`
- `src/taskq/sql/__init__.py`
- `src/taskq/sql/manifest.py`
- `src/taskq/protocol.py`
- `src/taskq/errors.py`
- `src/taskq/execution.py`
- `src/taskq/registry.py`
- `src/taskq/transport.py`
- `src/taskq/sql/transport.py`
- `src/taskq/worker.py`
- `tests/conftest.py`
- `tests/worker_support.py`
- `tests/test_contract_0_1_2.py`
- `tests/test_installer_matrix.py`
- `tests/test_manifest_parity.py`
- `tests/test_verify_manifest.py`
- `tests/test_s2_worker_execution.py`
- `tests/test_s2_worker_supervision.py`
- `tests/test_s2_worker_settlement.py`
- `tests/test_s2_worker_lifecycle.py`
- `tests/test_s2_worker_races.py`
- `tests/test_s2_worker_sql.py`
- `scripts/artifact_smoke.py`
- `.github/workflows/ci.yml`
- `pyproject.toml` and `uv.lock`

The implementation claims an append-only contract-0.1.2 claim projection, closed handler-result types, one heartbeat per active handler, generation-safe checkpoints, verb-aware settlement replay, bounded total and synchronous concurrency, split async/sync cancellation, idempotent soft stop, and permanent PG16/PG18 plus artifact evidence. Derive the expected behavior from the authority chain rather than trusting those claims or the test names.

Explicitly confirm that no S2-05 polling, LISTEN/NOTIFY runtime, worker CLI, HTTP adapter, or authorization integration was implemented early.

## Required audit program

### 1. Contract-0.1.2 upgrade and catalog parity

Independently derive the ordered `taskq.claimed_job` catalog from the Function Manifest. Confirm migration 0003 appends `lease_seconds integer` at the end, retains and does not reinterpret `lease_expires_at`, changes no unrelated public identity, stamps the exact effective lease chosen by `claim_jobs`, and advances metadata consistently.

Audit fresh installation and the complete `0001 → 0002 → 0003` upgrade path, including discovery, checksums, concurrent installers, exact verification, Python decoding, queue-default/task-stamped/call-override precedence, and PostgreSQL 16/18 compatibility. Challenge both `verify()` and the independent parity matrix: construct a falsifiable catalog mutation that would pass if either oracle merely trusted the other.

Search the worker and tests for all lease scheduling. Confirm heartbeat cadence is derived monotonically from the returned duration and never from `lease_expires_at - local_now()` or any equivalent client wall-clock calculation.

### 2. Execution boundary and result normalization

Build a matrix covering every allowed synchronous and asynchronous one-/two-argument handler form, input/output validation, normal return normalization, every closed handler intent, exception/retry policy mapping, invalid signatures, invalid payloads, missing handlers, checkpoint bounds, and fence redaction.

Determine whether registration is atomic and whether handler metadata can drift after registration. Look for a path that invokes a handler twice, validates after an irreversible side effect, leaks the attempt fence through repr/model/log/error text, or invents a result outside the frozen union.

### 3. R2-11 cancellation and ownership audit

Independently derive and test the complete precedence/state matrix, including at least:

- lease loss versus handler return for async and synchronous handlers;
- operator-cancel observation versus normal completion;
- repeated cancel observations and grace-timer uniqueness;
- graceful shutdown completion before the deadline;
- async hard cancellation at the deadline and release only after the async task has ended;
- synchronous return before and after the deadline;
- a synchronous handler that ignores its token indefinitely;
- external cancellation of `run_job` or `stop` during handler execution and during settlement;
- operator, shutdown, and lease-loss reason escalation in every ordering.

For a live synchronous handler after the deadline, prove all of the following simultaneously: heartbeat continues, no settlement verb is sent, `stopped` is not reported, `requires_process_exit` is true, and no capacity/cleanup path can make the attempt appear safely transferred. Check the same rule after lease loss. A cancellable `Future` is not evidence that its underlying thread stopped.

Confirm settlement critical sections are preserved across caller cancellation and soft-stop escalation. Flag any path where suppressing an outer `CancelledError` can hang forever without exposing the documented process-exit state, or where a process-exit indication can clear before ownership is actually safe.

### 4. Heartbeat, checkpoint, and supervision state machine

Verify the exact `min(lease_seconds / 3, 30s)` monotonic cadence, including boundary durations, delayed loop scheduling, settlement retries, operator grace, synchronous executor queuing, and soft-stop drain. Prove exactly one heartbeat coroutine exists per active attempt and every heartbeat/grace/handler task is joined.

Challenge the one/two/three transient-failure transitions, non-retryable errors, typed `ok=false`, cancellation observation, and ownership-loss dominance. Confirm no settlement follows loss even if a handler returns concurrently.

For checkpoints, force updates before snapshot, during an in-flight heartbeat, after response but before acknowledgement, and during settlement. Confirm an acknowledgement can clear only the generation it carried and cannot erase newer progress.

### 5. Settlement replay and fatal-stop semantics

Construct a command matrix for complete, retryable fail, terminal fail, snooze, release, and running cancellation. For every command verify:

- the chosen verb and semantic arguments are fixed once;
- only that verb is replayed with the same attempt and worker identity;
- only retryable protocol/transport failures back off;
- backoff is bounded and uses the worker clock;
- heartbeating continues until settlement certainty;
- committed-but-lost responses converge through the command's allowed replay outcome without rerunning the handler;
- `lost`, `settle_conflict`, wrong-command outcomes, non-retryable errors, and retry exhaustion remain distinct;
- fatal outcomes atomically close intake and drain other work without interrupting a permitted settlement.

Audit the ADR-007 invalid-follow-up escape for deterministic validation rejection and inactive-capability skew. Confirm it cannot report parent success with missing children, switch verbs after ownership loss, or consume failure budget more than once.

### 6. Bounded admission and lifecycle

Review synchronous slot reservation, duplicate-attempt rejection, `available_slots`, `wait_for_capacity`, `start`, `submit`, `run_job`, `stop`, escalation, and `aclose` as a concurrent state machine. Force submissions against capacity release and stop phase 1. No job may start after intake closes, no total-concurrency overshoot is permitted, and no claimed sync job may wait for a thread without an active heartbeat.

Prove concurrent `stop()` callers share one operation, `stop(cancel=True)` escalates immediately, infinite grace drains honestly, deadlines are monotonic, fatal auto-stop cannot deadlock on its own active task, and executor shutdown happens only after all synchronous calls finish. Check construction and close for hidden tasks, eager threads, or resource ownership surprises.

### 7. Test validity, live SQL, packaging, and CI

Assess whether the manual clock and scripted transport are independent enough to catch implementation defects rather than reproduce them. For every choreographed race, confirm barriers force both winner orders without correctness-sensitive sleeps. Look for races absent from the oracle, especially task completion between admission/control registration and stop escalation.

For live SQL vectors, independently reconcile job rows, attempt rows, failure budget, event sequence, and replay outcomes for complete/retry/snooze/cancel/shutdown/no-handler. Confirm the committed-response-loss wrapper performs one durable semantic application and one handler invocation. Verify tests actually execute through the runner capability role and cannot pass through superuser leakage.

Audit task, unobserved-exception, executor-thread, SQL-pool, and connection cleanup. Inspect source and built-artifact isolation for Python 3.12/3.13 and core/HTTP/OutLabs extras. Read the CI commands themselves and determine whether PostgreSQL 16/18, worker races, the full suite, migrations, and every wheel/sdist smoke are collected without accidental skips. Distinguish locally reproduced evidence from workflow configuration not observed on the hosting service.

## Evidence available, not assumptions

- PostgreSQL 18.3: 279 regular tests green; one opt-in million-row test skipped in the regular run; the two-test plan gate is separately green.
- PostgreSQL 16.14: the same 279 regular tests and the two-test plan gate were reported green in the Stage-2B completion audit.
- No-DSN collection: 151 passed and 129 SQL-dependent/opt-in tests skipped.
- Clean Python 3.13 worker/unit lane: 149 regular tests green, with two SQL-dependent skips.
- Fresh wheel and sdist installations were reported green in core, HTTP, and OutLabs modes outside the checkout; permanent CI performs the artifact matrix on Python 3.12.
- Ruff check and format checks are green.
- Stage-2B/contract commits, newest first: `b609b62`, `b6004df`, `c1ddca7`, `d35b3b7`, `7af8238`, `18a7ea5`, `51fb670`, `abadfc4`; root status correction: `b1ddf55`.

Re-run tests and devise counterexamples where useful. The standard scratch DSN is `postgresql://postgres:postgres@localhost:5432/taskq_stage1_test`; migrations create cluster-wide `taskq_*` roles, so use only an isolated development PostgreSQL cluster.

## Required response shape

Return one self-contained `docs/design-review-4/RESPONSE.md` with:

1. **Verdict:** `PASS`, `PASS WITH FINDINGS`, or `BLOCKED`, plus whether S2-05 may open.
2. **Findings:** ordered by severity (`BLOCKER`, `HIGH`, `MEDIUM`, `LOW`), identified `R4-01`, `R4-02`, and so on. Each finding must include exact file/line evidence, the violated authority or invariant, impact, a falsifiable counterexample, and the smallest contract-correct remediation and regression test.
3. **Contract questions:** separated from implementation defects. State the conflicting Tier-0/ADR passages precisely enough to locate them; do not propose coding around them.
4. **Contract-0.1.2 matrix:** ordered composite/catalog attributes, migration/fresh/upgrade evidence, verifier/parity evidence, effective-duration precedence, Python decoding, and PG16/18 result.
5. **Worker acceptance matrix:** execution/result normalization, R2-11 async/sync cancellation, heartbeat/checkpoints, every settlement verb/replay outcome, bounded lifecycle, fatal stop, race evidence, and resource cleanup — each with implementation and test evidence or an explicit gap.
6. **Boundary and exit-gate assessment:** absence of S2-05/Stage-3 scope, import/artifact isolation, CI collection, live SQL conservation, and locally reproduced versus configured-only evidence.
7. **Residual risks and S2-05 preconditions:** only risks supported by repository evidence.

If there are no findings in a category, say so explicitly. Treat test code, packaging scripts, and CI configuration as production-critical evidence, and prefer a minimal executable counterexample over a general concern.
