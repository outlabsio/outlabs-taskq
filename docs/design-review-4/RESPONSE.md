# outlabs-taskq external design review — round 4 response

> **Tier 4 — immutable review provenance.** Response to [REQUEST.md](./REQUEST.md), delivered 2026-07-18. Reviewed range as pinned (`abadfc4..b609b62` for the audited kernel; three post-request S3-PREP commits move HEAD to `7b96979` and the suite from 279 to 285 tests — verified to add no S2-05/Stage-3 scope; counts below are against HEAD). Method: full-source audit of the review set, expectations derived from the Tier-0 contracts and the Stage-2B specification, plus **executed adversarial probes** — nine supervisor probes, a three-mutation oracle-independence challenge on throwaway databases, a pre-0.1.2 decode probe, the full suite, plan gate, no-DSN and Python-3.13 lanes, `-W error` worker runs, and a local six-way wheel/sdist × extras artifact matrix. No repository file was modified except the addition of this response. Evidence classes: **VERIFIED** (executed or byte-confirmed), **PLAUSIBLE** (static/configured-only), **SPECULATIVE** (unused).

## 1. Verdict

**BLOCKED — S2-05 may not open yet.**

The safety core the previous rounds fought for **held under every probe**: no double settlement, no settlement after ownership loss at the terminal check, no release/snooze/cancel of a live synchronous thread, fence-free reports and contexts, budget-free release/cancel, verb-fixed-once with wrong-command outcomes fenced to `TQ500`, atomic admission with no overshoot, honest non-`stopped` drain, executor shutdown strictly after drain, and clean resource ledgers under `-W error`. The contract-0.1.2 upgrade path is exemplary and passes an executed oracle-independence challenge.

But two **HIGH, executed-counterexample** divergences from the accepted Stage-2B specification stand in the worker kernel, plus one HIGH oracle blind spot that let the first slip through — and all three sit precisely on the surface S2-05's claim loop consumes (`submit()`, settlement liveness, process-exit signalling). Building S2-05 on them would bake a wrong terminal verb and a silent lease-lapse window into the first real consumer. The fixes are narrow, worker.py-local, and fully specified below; after they land with their regression tests, S2-05 opens without a further full round.

## 2. Findings (severity-ordered)

### R4-01 — HIGH — The heartbeat does not survive handler completion; nothing extends the lease through settlement retries — VERIFIED

- **Evidence:** `src/taskq/worker.py:447` — the heartbeat loop's only continuation condition is `while control.handler is not None and not control.handler.done()`. The post-settlement cancel at `worker.py:352–353` implies the intended lifetime, but the loop self-terminates at its first wake after the handler future completes. Executed probe: lease 15 (interval 5), instant handler, `complete` scripted to fail retryably twice with `settle_backoff_base=10` — a 20-second settlement window spanning four intervals produced **zero heartbeat calls**.
- **Violated authority:** Stage 2B spec §7 rule 6 ("The heartbeat continues through handler normalization and settlement retries. It stops only after a terminal settle outcome or ownership loss"); §5's `settling → ownership_lost` transition, which is unreachable today (sub-issue: `ownership_lost` is checked exactly once pre-settle, `worker.py:320`, so a loss during settlement cannot locally suppress remaining retries — convergence rests entirely on the DB fence returning `lost`).
- **Impact:** settlement retries occur exactly when the transport is degraded; with no lease extension the claim can lapse mid-retry (worst case `settle_max_attempts` up to 100 × 5s cap against a 15s lease), converting completed work into a re-run elsewhere. The database fence keeps state safe; the spec's liveness guarantee is absent. Also subsumes the smaller observation that the invalid-payload and no-handler settle paths run with no heartbeat at all (bounded under defaults, unbounded under raised settle options).
- **Remediation (smallest):** drive the loop from an explicit per-run settlement-terminal/ownership-lost signal instead of `handler.done()`; on `ok=false` or third consecutive failure while `settling`, suppress further retries and report `ownership_lost` (the §5 arc).
- **Regression:** manual clock; instant handler; retryable settle failures with backoff greater than the interval; assert heartbeats interleave settle attempts and stop only after the terminal outcome. Second case: heartbeat `ok=false` during settling → no further settle retry, `ownership_lost`.

### R4-02 — HIGH — External cancellation of a `submit()` job task settles a terminal fenced `cancel_running` and swallows the cancellation — VERIFIED

- **Evidence:** `src/taskq/worker.py:309–312` converts any `asyncio.CancelledError` at `await control.handler` into `TaskCancelled(reason or SHUTDOWN)`; with no token signal the reason is `None` at `worker.py:343`, neither override at `344–349` fires, and `_normalize_handler_result` (`worker.py:503–504`) maps the error to `Cancel(reason="shutdown")` → `cancel_running` (`worker.py:580–587`). `run_job` and `stop` are shield-protected (`229–233`, `387`); the task returned by `submit()` (`211–214`) is not. Executed probe: cancelling the submitted task mid-handler yields transport ledger `['cancel_running']` and a `SETTLED` report with `task.cancelled() == False`.
- **Violated authority:** Stage 2B spec §6 "external cancellation of supervisor operation" row (convert to soft stop; settlement follows **shutdown** rules, i.e. `release`; then re-raise at the caller boundary); §3's normalization table, which scopes `CancelledError` handling to cancellation "caused by the supervisor".
- **Impact:** ordinary embedder patterns — `gather` sibling cancellation, `TaskGroup` teardown, timeout wrappers — **terminally cancel queued jobs** that the contract says must be released for retry, and violate asyncio's own cancellation contract (the task completes "successfully" after `.cancel()`). S2-05's claim loop is exactly such an embedder.
- **Remediation (smallest):** in the `CancelledError` branch, when the supervisor did not initiate it (no token reason, no `shutdown_deadline`, no ownership loss), signal SHUTDOWN, perform the `release(cause="worker_shutdown")` settlement inside a shielded critical section, and re-raise at the boundary.
- **Regression:** cancel a submitted task mid-handler and pre-start; assert `release` (never `cancel_running`), `CancelledError` propagates, `task.cancelled()` is true, and the settlement critical section completes.

### R4-03 — HIGH (oracle) — The suite is structurally blind to R4-01 — VERIFIED

No settlement-retry scenario spans a heartbeat interval: `tests/test_s2_worker_settlement.py:207–227` uses backoffs (0.25/0.5) far below the interval (5), and its final `clock.sleeping == 0` assertion passes identically against the broken implementation — the test named "stops_heartbeat" stops a heartbeat that was never alive. The S2-04C acceptance row "keeps heartbeats live until settlement certainty" is therefore claimed but untested. Remediation is R4-01's regression pair; this finding exists so the oracle gap itself is recorded as a defect, per the harness doc's "untested behavior, not merely untested lines" rule.

### R4-04 — MEDIUM — `abandoned_sync` is unreachable; `requires_process_exit` is false on the lease-loss leg; the report flag is dead — VERIFIED (independently found by two audit legs)

- **Evidence:** `JobRunState.ABANDONED_SYNC` (`worker.py:85`) is constructed nowhere; the lease-lost-while-sync-live window reports nothing until the thread returns, then renders `OWNERSHIP_LOST` (`worker.py:320–337`); `WorkerSupervisor.requires_process_exit` (`worker.py:187–191`) requires `_deadline_reached`, so with a zombie thread on a lost lease and no stop in flight it reads **False** (executed probe); `JobRunReport.requires_process_exit` (`worker.py:108`) is never set `True` anywhere.
- **Violated authority:** spec §5 (`abandoned_sync`: "ownership is unsafe while an unkillable sync thread remains; **process exit is required**"), §6 `ok=false` sync row, and §6's honesty paragraph ("S2-04 never lies about that").
- **Impact:** no unsafe settlement occurs — suppression and never-release both held under probe — but S2-05/operators cannot distinguish "busy" from "zombie thread on a lost lease; only process exit recovers", which is the precise signal R2-11 exists to guarantee and the signal S2-05's CLI consumes.
- **Remediation:** make the property true when any sync control has `ownership_lost` set with a not-done handler, independent of the deadline; surface `ABANDONED_SYNC` (live view and/or terminal report with `requires_process_exit=True`) or amend the spec docs-first to remove the state — implementation-conform is recommended since the spec is the accepted authority. Regression: assert the flag true while the thread lives after `ok=false`, and the report shape once it returns.

### R4-05 — MEDIUM — Dispatch arity disagrees with registration arity — VERIFIED

`worker.py:428–432` dispatches on the count of **all** parameters; `registry.py:86–93` validates **positional** parameters only. Executed probe: `def handler(payload, *, flag=True)` registers cleanly, then every invocation raises `TypeError` → normalized `Retry` → silent budget-death churn for a handler that never ran. Violates spec §2.1 + S2-04A ("invalid signatures are atomic registry failures" — this one is registry-valid yet dispatch-invalid). Remediation: dispatch on the registry's positional count captured at registration; regression covers keyword-only/`**kwargs` shapes, sync and async.

### R4-06 — MEDIUM (oracle) — External-cancellation rows and named races absent from the suite — VERIFIED

No test cancels a `run_job`/`submit`/`stop` task (every §6 external-cancellation row, including R4-02, is invisible). Also uncovered: the request's named stop-escalation-vs-admission window (the code handles it via `worker.py:291–298`, but nothing pins the await-free same-tick property); fatal auto-stop draining a second live job; post-deadline heartbeat continuation for a live sync thread; `requires_process_exit` after lease loss; ownership loss landing during settlement; two `wait_for_capacity` waiters against one freed slot. Remediation: add these as choreographed race/lifecycle tests alongside the R4-01/02 regressions.

### R4-07 — MEDIUM (harness) — The scripted ledger is too coarse to falsify replay argument-identity — VERIFIED

`tests/worker_support.py:203–210` records only ids for snooze/release/cancel_running and drops result/followups/error/progress/`retry_after_seconds`/`cause` for complete/fail — spec §8's "always with identical semantic arguments" is unfalsifiable at the unit level, and unit tests cannot distinguish `release(cause="worker_shutdown")` from `cause="no_handler"`. Remediation: record full kwargs (`RecordedCall.arguments` already exists with `repr=False`) and assert cross-retry equality in the replay tests.

### R4-08 — LOW — Raw `TaskqValidationError`/`TaskqCapabilityError` leak from the no-handler release and follow-up-escape paths — VERIFIED

`_settle_with_retry` re-raises those two (`worker.py:638–639`); `_settle_intent` catches them (`615–624`) but `_release_no_handler` (`518–532`) and the escape's terminal-fail retry (`667–694`) do not — an out-of-contract server response there becomes an unhandled task exception instead of a `RUNTIME_FAILED` fatal report (executed probe). Remediation: wrap both call sites with the existing `_runtime_failure` conversion + two scripted regressions.

### R4-09 — LOW — Pre-0.1.2 catalogs fail loudly at claim decode only by accident of implementation — VERIFIED live, unpinned

ADR-013's "new clients require contract 0.1.2 before claiming" holds today via `strict=True` in `sql/transport.py:63–66` plus the required bounded field (`protocol.py:618`) — proved by an executed probe against a 0.1.1-only throwaway catalog (typed `TaskqInternalError`) — but no test pins it; removing either incidental guard regresses silently. Remediation: one T8 vector (0001+0002-only catalog → `pytest.raises` on `transport.claim`).

### R4-10 — LOW — Manifest §9.5 boundary vectors missing at the SQL layer — VERIFIED

`claim_jobs` bounds (`0003:39–45`) have no executed min/max/out-of-range vectors for `p_lease_seconds` (14/86401) or `p_batch` (0/51); the Python registry tests never reach the SQL boundary, so replacing the SQL bound check passes the suite. Remediation: four one-line TQ422 vectors.

### R4-11 — LOW — A cancelled `stop()` awaiter leaves `_stop_task` detached — PLAUSIBLE

`stop()` awaits `asyncio.shield(self._stop_task)` (`worker.py:387`); a caller cancelled at that await abandons a running stop task that is joined again only if `stop`/`aclose` is re-entered. Remediation: document the re-await obligation (calling `aclose()` in `finally` suffices, since it shares the task) and add a task-ledger regression for the cancelled-stop-caller path.

### R4-12 — LOW (CI) — The million-row plan gate is collected by no CI job — VERIFIED

No workflow job sets `TASKQ_PLAN_CHECKS=1`, so the plan gate runs only as manual local evidence (it skips inside `sql-contract`, producing the 1-skipped shape). Remediation: add it to the nightly/scheduled lane or an explicit opt-in job so plan-shape drift is caught without local diligence.

## 3. Contract questions

**None.** Every finding above is implementation-versus-accepted-authority; the Tier-0 manifest (§0, §10, errata), Protocol v1 (H-02, amendment 7), ADR-013, and the Stage-2B specification were checked for mutual consistency on every audited behavior and none conflict. R4-04 offers a docs-first alternative, but the recommended disposition is implementation-conform.

## 4. Contract-0.1.2 matrix — VERIFIED end to end

| Axis | Evidence |
|---|---|
| Ordered composite | 15 attributes ending `lease_seconds integer`, independently derived from the manifest and byte-confirmed in four places: `0001_initial.sql:218–233` + `0003:7` (append), `sql/manifest.py:127–143`, the test literal (`test_contract_0_1_2.py:15–31`), and a live `pg_attribute` probe on a fresh throwaway install |
| Migration discipline | 0003 = ALTER TYPE append + `claim_jobs` replace (same signature; body diff vs 0002 shows only `v_lease` added to the projection) + hardening re-assertion + meta stamp; `lease_expires_at` still `now() + make_interval(secs => v_lease)`, unreinterpreted; 0001/0002/0003 byte-immutable since their landing commits (single-commit history + empty diffs; sha256s recorded) |
| Fresh + upgrade | 0001→"0.1", +0002→"0.1.1", full→"0.1.2" with ledger `[0001,0002,0003]` and 40 functions (executed); concurrent installers serialize on the advisory lock; failed-migration lock recovery proven |
| Verifier vs parity independence | **Executed three-mutation challenge:** semantic lie (constant 86400 projection) passes `verify()`, fails the parity vectors; identity lie (PUBLIC grant + volatility flip) passes behavior, fails exactly `function_catalog`/`function_privileges`/`no_public_execute`; composite drop fails both from their separate expectation sources. The oracles are complementary, not circular |
| Effective-duration precedence | claim-call override > enqueue/task stamp > queue default — traced in SQL (`0001:854`, `0003:117`) and executed live: (111,–,–)→111; (111,222,–)→222; (111,222,333)→333 with the job row retaining 222; identical `v_lease` feeds the lease update, attempt row, and projection |
| Python decoding | strict ordered decode (`sql/transport.py:63–66`, `protocol.py:618`); live positive vector green; live negative probe against a 0.1.1-only catalog fails loudly with a typed error (see R4-09 for the missing pin) |
| Heartbeat clock discipline | exactly one cadence site: `worker.py:444` `min(claim.lease_seconds / 3, 30.0)` on the loop-monotonic `WorkerClock`; `lease_expires_at` never read in `src/taskq/worker.py` (grep-clean); tests poison expiry a day into the past and still observe first beat at duration/3 |
| PG16/18 | PG 18.3 fully executed locally (suite, plan gate, probes); PG 16.14 configured in CI (`ci.yml:189–217`, full-suite matrix) and claimed by the Stage-2B audit — PLAUSIBLE from this seat |

## 5. Worker acceptance matrix

| Area | Result |
|---|---|
| Execution boundary & normalization | Closed frozen result union with exact spec bounds (`execution.py:23–50`); payload validated pre-handler; invalid payload → typed `NonRetryable`; normalization precedence (operator override, shutdown release, retry-policy mapping) correct in code and tests — **except R4-05** (arity disagreement) |
| Cancellation token & context | Monotonic thread-safe token (weaker never masks stronger — `execution.py:59–93`); `TaskCancelled` carries only the safe reason; fence absent from `JobContext` entirely (not merely redacted); headers immutable; **VERIFIED** |
| R2-11 sync-thread rule | Never released/snoozed/cancelled while a thread may run: `_enforce_shutdown_deadline` and `_lose_ownership` cancel async futures only (`worker.py:369–373`, `478–483`); settlement blocked on the live future; slot held; drain honest; executor shutdown after drain (`419–421`); post-deadline conjunction (heartbeat continues, no verb, not `stopped`, `requires_process_exit` true, no transfer) **holds for the shutdown leg — VERIFIED**; the lease-loss leg lacks the honesty signal (**R4-04**) |
| Heartbeat & checkpoints | Cadence/backoff/3-strike/ok=false/cancel-grace-singleton all correct while the handler runs; generation-fenced acks proven through an in-flight beat; **but the loop dies at handler completion (R4-01)** |
| Settlement replay | Verb fixed once; wrong-command outcomes → `TQ500`; `already_settled` success; `lost` never converted; `settle_conflict` distinct and fatal; exhaustion → `settlement_unknown` fatal; ADR-007 escape correct incl. skew soft-stop; committed-loss wrapper proves one durable application + one handler run (live SQL) — **but external cancellation forges a terminal verb (R4-02)** and the ledger can't falsify argument identity (R4-07) |
| Bounded lifecycle | Atomic admission, no overshoot, duplicate rejection, capacity honesty, intake-close-before-drain, shared stop, immediate escalation, fatal-stop no-self-deadlock, no hidden work at construction/close — **VERIFIED** across probes and tests |
| Race evidence | Five race families force both winner orders without correctness-sensitive sleeps; sync-return-vs-lease-loss never settles after loss; settlement critical section survives the shutdown deadline — with the R4-06 coverage gaps noted |
| Resource cleanup | 58/58 worker+SQL tests under `-W error`, zero warnings; explicit unobserved-exception capture via loop handler; executor threads and task ledger at baseline; SQL pool `checkedout()==0` — **PASS** |

## 6. Boundary and exit-gate assessment

- **S2-05/Stage-3 absence — CONFIRMED:** no polling/claim loop, no LISTEN/NOTIFY runtime, no `taskq worker` CLI subcommand, `src/taskq/http/` empty, no outlabs-auth import anywhere in `src/`.
- **Import/artifact isolation — locally reproduced:** wheel+sdist × core/HTTP/OutLabs = 6/6 green outside the checkout (module absence asserted, provenance checked, both console scripts, packaged migrations 0001–0003, 40 functions, core-mode fresh-DB migrate+verify); 3.12 and 3.13 unit lanes reproduced.
- **CI collection — read command-by-command:** PG16+PG18 full-suite matrix, unit lanes, races, migrations, built-artifacts all collect; `UV_FROZEN` with a current lock; marker registered; one gap recorded as R4-12 (plan gate in no job); worker races run redundantly in three jobs (harmless).
- **Live SQL conservation — executed:** status/budget/event-sequence reconciliation green for all six vectors through the **runner capability role** (`server_settings.role`, probe-confirmed `current_user=taskq_runner`, denial matrix intact; superuser fixture used only for reads/truncation).
- **Locally reproduced:** suite 285/1-skip, plan gate 2/2, no-DSN 156/130, unit lanes, `-W error` runs, ruff, lock check, artifact matrix, all probes. **Configured-only:** the PG 16.14 lane and hosted workflow execution.

## 7. Residual risks and S2-05 preconditions

**Preconditions (block S2-05 until landed, in one remediation slice with the usual per-commit board discipline):**
1. R4-01 + R4-03: settlement-liveness heartbeat rework with both regression cases (including the `settling → ownership_lost` suppression arc).
2. R4-02 + the external-cancellation rows of R4-06: submit-task cancellation converts to shielded release + re-raise, with mid-handler and pre-start regressions.
3. R4-04: process-exit honesty on the lease-loss leg (S2-05's CLI consumes this signal).
4. R4-05: dispatch arity from registry metadata.

**Should land with the same slice (small):** R4-07, R4-08; **may follow:** R4-09..R4-12.

**Residual risks (evidence-backed, acceptable):** the PG16 lane remains CI-attested rather than reviewer-executed; attempts-table terminal rows are asserted via projection only and the live committed-loss vector covers `complete` (others replay against the scripted transport) — both worth one future live vector each; a dropped `submit()` task is unobserved until drain (inherent to returning tasks; mitigated by drain and the ledger tests).

After preconditions land and the suite is green on both PG lanes, **S2-05 may open without a further full review round**; the next natural external boundary remains post-2B-remediation spot-check folded into the round-5 (Stage 3) review.
