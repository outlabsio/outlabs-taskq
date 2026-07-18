# Round 2 decision log

Findings are ranked by priority and then by blast radius. Each recommendation is phrased as a decision the maintainer can accept or reject. An ADR amendment is requested only where new source evidence exposed a gap in an accepted decision; otherwise the finding repairs a subordinate document.

## R2-01 — The ADR-007 follow-up fold-in is not executable

**Priority:** P0 — correctness and contract

**Claim.** The normative `complete_job` body changes the parent to `succeeded` before validating follow-ups, never implements the promised 0.1 `TQ501` branch, and writes `TQ422` only into exception messages rather than SQLSTATE. This is an incomplete fold-in of accepted ADR-007, not a request to reopen atomic follow-ups.

**Evidence — VERIFIED against repository SQL and PostgreSQL 18.**

- ADR-007 requires validation before *any* parent state change and typed `TQ422` before the CAS (`docs/adr/ADR-007-atomic-followups-fenced-cancel.md`, lines 12–18).
- The body updates the parent at `docs/Task Queue — Unified Design Spec.md`, §5.5, lines 959–980; validation starts only at lines 1006–1026.
- Lines 1004–1005 promise `TQ501` for non-empty follow-ups in 0.1, but the function contains no version/capability branch that raises it.
- Lines 1008–1024 use bare `RAISE EXCEPTION 'TQ422: …'`. PostgreSQL 18 documents that a bare `RAISE EXCEPTION` has SQLSTATE `P0001`, while a custom five-character SQLSTATE must be supplied explicitly: [PostgreSQL 18 PL/pgSQL errors and messages](https://www.postgresql.org/docs/18/plpgsql-errors-and-messages.html).
- PostgreSQL errors abort the surrounding transaction, so the current ordering does not commit partial success; the defect is the promised order and stable error contract, not transaction atomicity: [PostgreSQL 18 PL/pgSQL control structures](https://www.postgresql.org/docs/18/plpgsql-control-structures.html).

**Proposed decision.** Replace §5.5's algorithm with this ordered contract:

1. Read enough state to recognize replay/loss without mutating the parent. Replays of an already-settled successful attempt return `already_settled` and never revalidate the old follow-up payload.
2. For the 0.1 contract, accept `NULL`/`[]`; any other `p_followups` value raises SQLSTATE `TQ501` before mutation. Treat it as fatal client/contract skew: terminal-fail the current parent as `unsupported_followup`, then soft-stop the incompatible worker.
3. For 0.2+, validate array shape, cap, fields, casts, queue existence, and uniqueness of derived `step`/idempotency keys. Any deterministic invalid raises `USING ERRCODE = 'TQ422'` before parent mutation.
4. Acquire the required locks using the R2-06 graph order, re-check the fence, mutate the parent, insert all children, update the attempt, unlock dependencies, and emit events in one transaction.
5. A worker receiving `TQ422` terminal-fails the still-running parent as `invalid_followup`. `fail_job` then performs the existing dependent cascade (`Unified Spec` §5.6 lines 1164–1170), and workflow rollup remains eventually consistent (§10 lines 1527–1529).

**Redrive decision.** Document that a `failed` parent cannot already own children committed by `complete_job`: a successful complete plus children is one transaction, while a rejected complete rolls all of it back. Redriving that failed parent therefore does not free-and-duplicate prior atomic chain children; it reruns the handler and may create the chain once on a later successful complete. Handler-created side effects or explicit enqueues performed *before* failure can repeat, which is the already-accepted at-least-once rule in ADR-007 line 17 and Unified Spec §18.2.

**Amend exactly:** `docs/Task Queue — Unified Design Spec.md` §5.5 and §17 follow-up row; `docs/adr/ADR-007-atomic-followups-fenced-cancel.md` consequences (clarify replay → 0.1 capability gate → 0.2 validation order and the worker response to `TQ501`); `docs/Task Queue Test & Benchmark Harness.md` T2/T3/T5; the canonical ADR-005 protocol document to be created from review doc 03.

## R2-02 — Worker-wide expiry can reap the wrong jobs

**Priority:** P0 — correctness

**Claim.** `expire_worker_leases` reports that all matching jobs were synchronously reclaimed, but it calls `reap_expired(N)`, whose global oldest-expiry selection may consume unrelated jobs and leave the requested worker's jobs running.

**Evidence — VERIFIED against the normative SQL.**

- `reap_expired` selects any expired running rows ordered by `lease_expires_at` (`Unified Spec` §5.8, lines 1229–1241).
- `expire_worker_leases` backdates every matching worker row, records their count, then calls the generic `reap_expired(greatest(v_n, 1))` (§5.9, lines 1417–1425).
- If older expired rows from another worker exist, the generic limit may be exhausted before the newly backdated target rows. The returned `v_n` is the number backdated, not the number actually reaped.

**Proposed decision.** Capture the target ids from `UPDATE … RETURNING id`, order them deterministically, call the factored `reap_job(id)` for each while still in the same transaction, and return an explicit `{matched, reaped, skipped}` result. Re-check worker id/status inside `reap_job` or pass an expected worker/fence so the sugar cannot reclaim a row that changed before it was locked. Keep budget/backoff/poison behavior centralized in `reap_job`.

**Amend exactly:** `docs/Task Queue — Unified Design Spec.md` §5.9 body and §11.1 outcome; `docs/Task Queue Test & Benchmark Harness.md` T3 with unrelated-old-expiry and racing-heartbeat cases; `docs/Task Queue Staging Cutover Runbook.md` expire-worker evidence step when added.

## R2-03 — Cancel-via-fail consumes budget and mislabels the attempt

**Priority:** P0 — state-machine correctness

**Claim.** When an operator has requested cancellation of a running job and the worker then calls `fail_job`, the job lands `cancelled` but its attempt is first marked `failed` and `failure_count` is incremented. This contradicts the accepted “cancel budget untouched” rule.

**Evidence — VERIFIED against ADR and SQL.**

- ADR-003 says only failures and lease expiries consume `failure_count`; releases and snoozes do not (`docs/adr/ADR-003-fencing-typed-outcomes.md`, lines 12–16). ADR-007 says fenced handler cancel lands cancelled with budget untouched (lines 20–24).
- `fail_job` unconditionally marks the attempt `failed` at `Unified Spec` §5.6 lines 1121–1123, then the pending-cancel terminal branch increments `failure_count` at lines 1147–1163 even though it selects `status='cancelled'`/`outcome='canceled'`.
- The prose for snooze/release explicitly says their pending-cancel path sets attempt status `cancelled` and does not touch failure budget (§5.7 lines 1186–1224), so the verbs disagree.

**Proposed decision.** After the fence/replay check and before failure accounting, branch on `cancel_requested_at`: set job and attempt to cancelled/canceled, clear the lease, keep `failure_count` unchanged, cascade dependents, emit cancelled, and return `ok/cancelled`. Only the non-cancel path may mark the attempt failed or increment failure budget. Apply the same pending-cancel table to every fenced settle and specify whether complete-before-cancel-observation wins; recommended: a valid complete may win until the worker observes cancellation, while cancel/release/snooze/fail explicitly terminalize cancelled.

**Amend exactly:** `docs/Task Queue — Unified Design Spec.md` §3.2/§3.3 and §5.6; ADR-003 consequence note; `docs/Task Queue Test & Benchmark Harness.md` T2/T4 state model.

## R2-04 — ADR-010 hardening is prose beside insecure exact DDL

**Priority:** P0 — security

**Claim.** The blanket hardening paragraph is correct, but the “normative reference implementation” still shows `SECURITY DEFINER` function declarations without an attached `SET search_path`, and helper functions without an explicit public/internal grant posture. Copying the exact bodies would recreate the Round 1 escalation surface.

**Evidence — VERIFIED against the documents and PostgreSQL 18.**

- ADR-010 requires every function to be owner-owned, pin `search_path`, revoke PUBLIC in the creation migration, and grant the smallest role (`docs/adr/ADR-010-db-roles-security-definer-maintenance.md`, lines 12–24). Unified Spec §4 repeats it at lines 541–548.
- Unified Spec §5 calls the displayed bodies normative at lines 564–568, but declarations such as `enqueue` (lines 591–612), `claim_jobs` (766–775), `heartbeat` (912–916), and `complete_job` (948–953) omit the `SET search_path` clause. `emit_event` (403–409) and `backoff_seconds` (573–585) do not state whether they are owner-only internal helpers.
- PostgreSQL grants function EXECUTE to PUBLIC by default and recommends revoking in the same transaction as creation: [PostgreSQL 18 privileges](https://www.postgresql.org/docs/18/ddl-priv.html).

**Proposed decision.** Make the migration source mechanically authoritative through a function manifest. For every signature it must record owner, language/security mode, pinned `search_path`, PUBLIC revoke, allowed roles, and statement timeout posture. Internal helpers (`uuid7`, `backoff_seconds`, `emit_event`, `reap_job`, cascade/finalizer helpers) receive no application-role EXECUTE. The generated snapshot may show the full DDL, but migrations remain canonical. `verify` must compare every signature's `proowner`, `prosecdef`, `proconfig`, ACL, and public exposure.

**Amend exactly:** `docs/Task Queue — Unified Design Spec.md` §4 and every §5/§11/§13 function declaration convention; `docs/taskq-borrowed-features/13-sql-packaging-conventions.md` §4; `docs/Task Queue Test & Benchmark Harness.md` T2/T8. ADR-010 itself need not change.

## R2-05 — Housekeeping has no least-privilege role or complete credential story

**Priority:** P0 — security and deployability

**Claim.** The facade host is required to run `tick` and the 0.1 janitor pass, but ADR-010's typical facade credential has only producer+runner+observer while transactional maintenance is operator-tier. Granting operator to the web process would erase the capability split. `_system` and external maintenance have related unresolved privilege gaps.

**Evidence — VERIFIED; this is new evidence warranting an ADR-010 amendment.**

- ADR-010 assigns transactional maintenance to `taskq_operator` and says a typical facade user holds producer+runner+observer (`docs/adr/ADR-010-db-roles-security-definer-maintenance.md`, lines 14–26).
- Unified Spec §11.4 requires the HTTP facade process to run the housekeeper (`lines 1615–1618`), and §13.5 puts the 0.1 janitor inside that tick (line 1753).
- The actual Diverse facade service calls `taskq.tick()` (`~/Documents/projects/diverse-data-api/src/diverse_data_api/domains/queue/taskq_service.py`, lines 308–311); its staging HTTP route exposes the same call under a coarse write credential (`.../taskq_api.py`, lines 323–336). This confirms the runtime flow is not hypothetical.
- Unified Spec §20.2 says an embedded `_system` worker should run the janitor job (lines 2051–2055), but a runner-only worker cannot execute operator maintenance.
- PostgreSQL 17–18 permit relation maintenance through `MAINTAIN`, but PostgreSQL 16 has no such privilege and requires ownership for `REINDEX INDEX/TABLE`. All supported versions prohibit `REINDEX CONCURRENTLY` inside a transaction block: [PostgreSQL 16 privileges](https://www.postgresql.org/docs/16/ddl-priv.html), [PostgreSQL 16 REINDEX](https://www.postgresql.org/docs/16/sql-reindex.html), [PostgreSQL 17 privileges](https://www.postgresql.org/docs/17/ddl-priv.html), [PostgreSQL 17 REINDEX](https://www.postgresql.org/docs/17/sql-reindex.html).

**Proposed decision.** Amend ADR-010 with `taskq_housekeeper NOLOGIN`: EXECUTE on `tick`; 0.1's internal daily-janitor entry; and, from 0.2, schedule-claim/fire/error coordination. It gets no pause, cancel, redrive, expire-worker, queue-profile mutation, or direct DML. Grant `tick`/`janitor` to operator as manual escape hatches too. The facade DB login holds producer+runner+observer+housekeeper, never operator. In 0.2 the designated embedded `_system` runtime must hold housekeeper; ordinary external runners must not subscribe to `_system` janitor jobs.

Make the maintenance credential version-aware. On PostgreSQL 17–18, use a separate login with `MAINTAIN` only on taskq hot tables, schema usage, autocommit, and the CLI's advisory-lock protocol. PostgreSQL 16 has no equivalently narrow grant: default to reporting the maintenance plan for a DBA/owner-managed job and refuse automated reindex without an explicitly provisioned owner-authorized credential. Never give either credential to the facade. A migration login may `SET ROLE taskq_owner`; routine PostgreSQL 17–18 maintenance must not, and any PostgreSQL 16 owner-backed exception must be short-lived, isolated, and named as broader authority.

The exhaustive accepted matrix is proposed in [04-sql-and-role-audit.md](./04-sql-and-role-audit.md).

**Amend exactly:** `docs/adr/ADR-010-db-roles-security-definer-maintenance.md` role/credential tables; Unified Spec §4, §11.4, §13.5, §20.2; Authorization doc §1–§2 (remove public HTTP tick); Extraction Brief topology/credentials; harness role fixtures.

## R2-06 — UUIDv7 is not a causality proof for lock order

**Priority:** P0 — concurrency correctness

**Claim.** “Ascending UUIDv7 means parents before children” is not guaranteed across sessions or caller-generated IDs. Validating the version nibble would not repair the proof.

**Evidence — VERIFIED against PostgreSQL 18, RFC 9562, and the spec.**

- Unified Spec §5 says ids are globally locked ascending because uuidv7 ordering coincides with parents-before-children and says client ids must be v7 (line 568); §5.5 repeats that child ids always sort after parents (lines 1048–1057).
- PostgreSQL 18 calls UUIDv7 “time-ordered” but describes millisecond/sub-millisecond time plus random bits and warns extracted timestamps depend on the generating implementation: [PostgreSQL 18 UUID functions](https://www.postgresql.org/docs/18/functions-uuid.html).
- RFC 9562 makes extra within-timestamp monotonicity an optional construction; separate producers are not one monotonic sequence: [RFC 9562 §6.2](https://datatracker.ietf.org/doc/html/rfc9562#section-6.2).
- The shown `enqueue` signature has no caller id (`Unified Spec` lines 591–611), yet §15.1 says ids are accepted as enqueue parameters (line 1830). The contract contradicts itself.
- PostgreSQL's documented defense is a consistent object order, not a time-derived assumption: [PostgreSQL 18 explicit locking](https://www.postgresql.org/docs/18/explicit-locking.html).

**Proposed decision.** Keep server-generated uuidv7 for locality/FIFO tie-breaking, but remove it from the correctness proof. Dependency edges are acyclic because a dependency is an already-existing job and edges are immutable except deletion; acquire graph locks in topological parent-before-dependent order and sort ids only among siblings at the same frontier. Multi-parent operations first normalize/distinct their parent set. Prohibit caller-supplied job ids in 0.x because no host needs them; if later added, accept any UUID without changing correctness. Add same-millisecond reverse-sort and arbitrary-UUID DAG race tests.

**Amend exactly:** Unified Spec §5 preamble, §5.2 dependency-lock explanation, §5.5 dependent locks, §15.1, and §16.3 race cases; harness T3. No ADR must reopen.

## R2-07 — Public parameters bypass admission and range invariants

**Priority:** P0 — security and contract

**Claim.** A producer can pass the internal depth-bypass flag, and runners can supply lease/retry overrides outside the queue's validated range. Direct SQL therefore has powers and failure shapes not represented by the facade models.

**Evidence — VERIFIED against normative SQL.**

- `enqueue` exposes `p_internal boolean` in the producer-callable signature (`Unified Spec` §5.2 lines 591–611) and skips max-depth admission when true (lines 628–638). ADR-010 grants producers the enqueue family.
- Queue/job lease defaults are constrained to 15–86400 seconds (§4 lines 210–220 and 279–292), but `claim_jobs` accepts an unchecked `p_lease_seconds` and uses it directly (lines 766–774, 859–873); heartbeat likewise directly extends by an unchecked override (lines 912–925).
- `fail_job` accepts unchecked negative or arbitrarily large `p_retry_after_seconds` (lines 1088–1094, 1129–1133). Several casts inside follow-up enqueue can leak native `22P02`/range errors instead of `TQ422` (lines 1037–1042).
- The max-depth probe uses `OFFSET q.max_depth` (lines 628–638), so a queue already holding exactly N active rows accepts row N+1 before rejecting. `max_depth` also has no positive-value CHECK (§4 line 224).
- Queue names allow 63 ASCII bytes (§4 line 211), while notification channels prepend `taskq_` (lines 749–751). A 63-byte queue therefore produces a 69-byte channel, beyond PostgreSQL's default 63-byte identifier limit: [PostgreSQL limits](https://www.postgresql.org/docs/18/limits.html).

**Proposed decision.** Remove `p_internal` from every producer-granted signature. In 0.2 create an owner-only internal follow-up insert path called only by `complete_job`. Validate direct-SQL arguments at each public boundary: non-empty worker/actor ids; batch 1–50; lease 15–86400; max attempts 1–100; priority 0–1000; non-negative bounded snooze/release/retry delays; JSON type/size; and follow-up casts. Define `max_depth` as NULL or positive and reject when the pre-existing active depth is already N (`OFFSET N-1`); keep it explicitly advisory under concurrency/bulk. Cap queue names at 57 ASCII bytes while per-queue channel names use `taskq_`, or adopt one fixed channel with queue in the payload. Emit registered TQ SQLSTATEs, never raw check/cast exceptions for caller mistakes. The HTTP model mirrors, but does not replace, these checks.

**Amend exactly:** Unified Spec §5.2–§5.7; ADR-010 function grants/manifest consequence; Authorization doc action map; harness T2/T4/T6.

## R2-08 — The 0.1 function surface is not an executable contract

**Priority:** P0 — Stage-0 exit contract

**Claim.** Multiple functions that ADR-009 requires in 0.1 are signatures or prose only, so the first migration cannot be derived from the normative document and transport parity cannot be tested.

**Evidence — VERIFIED against ADR-009 and the Unified Spec.**

- ADR-009's 0.1 list includes bulk enqueue, release, snooze, fenced cancel, pause/resume, redrive/operator cancel, concurrency caps, safe views, metrics, and janitor (`docs/adr/ADR-009-first-release-scope.md`, lines 10–16).
- The spec gives only prose for `enqueue_many` (§5.2 line 761), `release_job` (§5.7 line 1224), `cancel_running_job` (§4 line 550), `reap_job` (§5.8 line 1291), both cancel straggler passes (§5.9 lines 1352–1369), `redrive_failed` (line 1394), most operator functions (line 1429), worker presence (§11.2 line 1556), and `metrics()` (§12.2 line 1723). `tick` calls finalizers whose bodies are absent (lines 1577–1608).
- Schedule and archive bodies may remain deferred, but 0.1 DDL must not reference their 0.2/0.3 tables or functions.

**Proposed decision.** Before Stage 0 exits, publish a 0.1-only function manifest with exact signatures, typed results, SQLSTATEs, grants, and normative bodies for every 0.1 command. Gate later objects by migration: dependency/workflow/schedule functions in 0.2; archive tables/lookup/rotation in 0.3. Do not ship stubs that return success. The destination spec can retain later sections, but each must display its activation contract.

**Amend exactly:** Unified Spec §4–§6, §11–§13 with release badges and complete 0.1 bodies; ADR-005 protocol document; SQL packaging feature §2/§4; harness T2/T8.

## R2-09 — The 0.1 janitor carve-out is absent and internally contradictory

**Priority:** P1 — operational correctness

**Claim.** The accepted hardwired daily janitor is not present in `tick`, while the overview, installer, migration phase, runbook, and open question still require an install-time schedule and `_system` worker. Adding the janitor naively at the end of the current tick could starve reaping.

**Evidence — VERIFIED against the docs and PostgreSQL transaction behavior.**

- ADR-009 explicitly defers schedules to 0.2 and requires a due-gated, bounded daily janitor inside the 0.1 tick (`docs/adr/ADR-009-first-release-scope.md`, lines 12–16).
- The actual tick body only reaps and runs cancel/dependency/workflow finalizers (`Unified Spec` §11.4 lines 1565–1612); there is no due-state or janitor call.
- Premature schedule seeding remains in the overview line 23, installer lines 521–527, migration phase line 1868, synthesis line 2027, and `_system` resolution lines 2051–2055. The staging runbook also enables `_system`/`taskq.janitor` (`docs/Task Queue Staging Cutover Runbook.md`, lines 20–27, 50–59, 153–154, 218–225, 254–262).
- A PL/pgSQL exception block is a subtransaction, not an independent commit; a slow janitor still lengthens the tick's one transaction: [PostgreSQL 18 PL/pgSQL transaction management](https://www.postgresql.org/docs/18/plpgsql-transactions.html).

**Proposed decision.** In 0.1, run reaping first on every accepted tick. Then atomically claim a `janitor_daily` due marker in `control_state`; if due, execute independently bounded passes with per-pass row limits and a total statement/time budget smaller than the tick interval's overlap tolerance. Record per-pass counts/durations/errors and advance the due marker only after the intended policy (recommended: successful acquisition with failed passes due again on next tick). Keep `taskq janitor` as an operator escape hatch. Do not create `_system`, schedules, dependency/workflow finalizers, archive objects, or a janitor job until their staged migration. In 0.2, replace only the trigger; preserve the same janitor function and bounds.

**Amend exactly:** Unified Spec §0, §4 seeding, §11.4, §13.5, §16.1 Phase 0, §19/§20; Staging Cutover Runbook; borrowed feature README lines 65–74; ADR-009 consequence wording only if the due-marker retry policy is accepted.

## R2-10 — TQ codes and transport outcomes have no single registry

**Priority:** P1 — public contract

**Claim.** The documents use TQ001/TQ409/TQ422/TQ429/TQ500/TQ501 without one definition table, stable detail schema, or complete HTTP mapping. Claim, long-poll, replay, bulk, and mismatch outcomes remain ambiguous.

**Evidence — VERIFIED against repository documents.**

- Unified Spec §5 preamble names only some exceptions and says expected races are typed (lines 564–568). The bodies use `TQ001` for unknown queue, dependency, and job (lines 625–670, 1297–1303), `TQ429` for depth (638), `TQ500` for convergence (726–729), and `TQ409` for redrive collision (1387–1392).
- `TQ422` and `TQ501` have the defects in R2-01. No document is an error registry.
- `claim_jobs` collapses unknown queue and paused queue into an empty set (§5.3 lines 789–792). The facade only says `lost→409`, `already_settled→200` (§14 line 1819). ADR-005 explicitly requires the protocol before migration (`docs/adr/ADR-005-transport-parity.md`, lines 8–15).

**Proposed decision.** Accept [03-protocol-draft.md](./03-protocol-draft.md) as input to the canonical protocol. Each TQ SQLSTATE maps to exactly one HTTP status; resource subtype lives in structured details. Claims always return a body with `claimed|empty|paused|timeout|unavailable`, never 204. Bulk is atomic and one-result-per-input. The facade dispatches on SQLSTATE/typed composite, never parses messages.

**Amend exactly:** create ADR-005's canonical protocol document; link it from `docs/README.md`; amend Unified Spec §5 preamble/§14; Authorization doc mismatch language; harness T6.

## R2-11 — Thread-offloaded sync handlers cannot be hard-cancelled safely

**Priority:** P1 — runtime correctness

**Claim.** The runtime promises to hard-cancel handlers after grace and then release the job, while the embedded design explicitly offloads sync handlers to threads. A running Python thread/future cannot be cancelled; releasing while it continues creates two active side-effect producers.

**Evidence — VERIFIED against Python and consumer source.**

- Unified Spec §14 promises immediate task cancellation after lost lease/cancel grace and release on shutdown (lines 1811–1815). Borrowed feature 11 calls for “thread interrupt policy” then release (`docs/taskq-borrowed-features/11-soft-stop-and-shutdown.md`, lines 15–23, 50–58). Feature 14 offloads `blocking=True` via `anyio.to_thread` and promises release after timeout (lines 88–98, 110–117).
- Python's `Future.cancel()` returns false for a call already running; `running()` explicitly means it cannot be cancelled: [Python concurrent.futures](https://docs.python.org/3/library/concurrent.futures.html).
- QDarte's current sync runtime uses a cooperative `threading.Event` from heartbeat (`~/Documents/projects/qdarte-workers/src/qdarte_workers/codex_runtime.py`, lines 740–787). Diverse's current TaskQ pilot heartbeat does not propagate cancel at all (`~/Documents/projects/diverse-data-workers/src/diverse_data_workers/runtime/queue/worker.py`, lines 747–791), and its handler performs blocking batch work before settlement (lines 1265–1384).

**Proposed decision.** Define two execution contracts. Async handlers are cancellable at await points and may be released after their task has actually ended. Sync/thread handlers receive a cooperative cancellation token; after grace the runtime must keep the lease alive and wait, or terminate the entire process and let lease expiry reclaim—never release/snooze while the thread can continue. Offer subprocess isolation later for truly hard cancellation. On lost lease, suppress settlement and make the process/handler fail loud; the runtime cannot truthfully guarantee immediate side-effect cessation for arbitrary sync code. Add cancellation propagation to the Diverse pilot before acceptance.

**Amend exactly:** Unified Spec §14 worker guarantees; borrowed features 11 and 14; Extraction Brief §10 open decision/sync runtime; harness B13/T5; QDarte/Diverse adoption acceptance text (docs only here).

## R2-12 — Bulk enqueue's result algorithm is incomplete

**Priority:** P1 — contract correctness

**Claim.** A single `INSERT … ON CONFLICT DO NOTHING RETURNING` reports rows that landed but not the existing job id for every conflict, so it cannot by itself satisfy ADR-009's typed `created/existed` result per input.

**Evidence — VERIFIED against the spec; implementation behavior is reasoned.**

- Unified Spec §5.2 describes one multi-row insert with `RETURNING id, idempotency_key` and says it reports exactly what landed (line 761).
- ADR-009 requires single/bulk enqueue with typed created/existed results (`docs/adr/ADR-009-first-release-scope.md`, line 12). Borrowed feature 01 rejects silent/partial outcomes.
- The single enqueue needs a later-snapshot convergence loop to identify an existing holder after a conflict (`Unified Spec` lines 679–729); bulk needs an equivalent set-based convergence rule.

**Proposed decision.** 0.1 bulk is one transaction, one queue per call, at most 1000 specs, dependencies forbidden, and all validation/preflight happens before insert. Return exactly one result per input with stable `input_index`, `job_id`, and `outcome=created|existed`, preserving duplicates within the same request deterministically. Resolve conflict holders in later statements and retry keys whose holder settled during convergence; on exhaustion raise TQ500 and roll back the whole batch. No per-item errors or HTTP 207 in 0.1.

**Verification class:** **PLAUSIBLE** for the precise best implementation; the insufficiency of the documented result source is **VERIFIED** from the stated algorithm.

**Amend exactly:** Unified Spec §5.2; borrowed feature 01 bulk section; protocol doc bulk table; harness T2/T3/B2.

## R2-13 — Archive ordering and release staging are underspecified

**Priority:** P1 — deferred correctness

**Claim.** The destination archive promises attempt aggregation while deleting a job whose attempt rows cascade. Without an explicit pre-delete snapshot/order, history can be lost. Meanwhile 0.1 SQL references archive resolution even though archive objects are deferred to 0.3.

**Evidence — VERIFIED against the schema/staging docs; exact trigger timing risk is avoided by decision rather than assumed.**

- `job_attempts.job_id` has `ON DELETE CASCADE` (`Unified Spec` §4 lines 356–381).
- §13.2 says `DELETE … RETURNING` moves the job with attempts aggregated as JSON but supplies no body or evaluation ordering (lines 1743–1750).
- `enqueue` queries `jobs_archive` for missing dependencies (lines 661–674), while ADR-009 says dependencies activate in 0.2 and the partitioned archive/archived-dependency resolution in 0.3 (`ADR-009`, lines 14–16).

**Proposed decision.** In the 0.3 migration, select and lock bounded candidate jobs; aggregate attempts while they still exist; insert complete archive rows; then delete hot jobs in the same transaction, with row-count conservation assertions. Do not rely on cascade/`RETURNING` evaluation order. Keep archive tables/functions absent from 0.1; in 0.2 dependency lookup is hot-table-only and an archived/missing parent is a typed error until the 0.3 capability activates. Add restore/lineage tests.

**Verification class:** **PLAUSIBLE** for the latent data-loss mechanism because no archive body exists; **VERIFIED** that the necessary ordering and stage boundary are absent.

**Amend exactly:** Unified Spec §5.2, §13.2, §16 release phases; ADR-009 consequence clarification; harness T2/T8/B10.

## R2-14 — SSE needs prune-gap and initialization semantics

**Priority:** P1 — pending proposal correctness

**Claim.** `Last-Event-ID` replay is not lossless once `job_events` is pruned, and a listen/replay handoff can miss or duplicate events unless its initialization order is specified.

**Evidence — VERIFIED against the proposal and PostgreSQL 18.**

- Growth §5 promises replay from `job_events.id` and says sleeping dashboards catch up losslessly (`docs/Task Queue Growth, Topology & Live Visibility.md`, lines 86–99).
- Unified Spec §13.4 prunes verbose events at 7 days and all events at 30 days (lines 1741–1753). Numeric identity gaps alone cannot distinguish pruning from rolled-back/unused identity values.
- `emit_event` inserts ledger rows but emits no event-channel NOTIFY (`Unified Spec` lines 403–409).
- PostgreSQL requires LISTEN to be committed first, then state inspected in a new transaction, then notifications consumed; early notifications may duplicate inspected state: [PostgreSQL 18 LISTEN](https://www.postgresql.org/docs/18/sql-listen.html).

**Proposed decision.** Accept SSE only with the reset/handshake contract in [05-growth-proposals.md](./05-growth-proposals.md): persist `max_pruned_event_id`; a cursor below it gets a typed reset and snapshot refetch, while equality is safe because that event was already observed; commit LISTEN before replay; capture/replay a high-water mark; dedupe live notifications by id; bounded catch-up and slow-consumer overflow also reset. Add a payload-free or id-only `taskq_events` NOTIFY inside `emit_event`; polling the ledger remains truth.

**Amend exactly:** Growth §5 and §6; Unified Spec `emit_event`/retention; future ADR-005 SSE section; harness T6/T7/B8.

## R2-15 — Dedicated DB needs a durable-intent and restore contract

**Priority:** P1 — pending topology decision

**Claim.** The proposal correctly admits the post-commit crash gap and correctly refuses `session=`, but it treats an idempotent re-trigger path as interchangeable with a durable outbox and understates backup/restore and query losses.

**Evidence — VERIFIED for the documented gap; PLAUSIBLE for host failure consequences.**

- Growth §3 states co-resident transactional enqueue is lost, enqueue happens after commit, and a crash in the gap loses intent (`docs/Task Queue Growth, Topology & Live Visibility.md`, lines 45–64).
- It also says queue rows are “usually re-derivable” and suggests a re-trigger path or host outbox (lines 49–52, 61–64). Accepted taskq jobs are durable operational records and are not generally reconstructible from domain state.
- Feature 14 defines `session=` as joining the host transaction (lines 62–84). A session bound to another database/engine cannot provide that property.

**Proposed decision.** Accept `dedicated` only as a named 0.2 topology with a mandatory choice per producer: `best_effort_after_commit` (documented loss accepted) or `host_outbox` (durable intent). Refuse the public `session=` parameter entirely in dedicated mode, and validate the actual session bind rather than trusting a mode flag. If a future low-level API needs queue-local transaction participation, give it a distinct name and contract. Define queue RPO, independent backup/restore, reconciliation after queue-only restore, and the loss of cross-database joins/atomic domain-write+settle. Taskq-internal archive/dependency/lineage still work because they remain in the queue DB; only domain↔queue joins/FKs/transactions break.

**Amend exactly:** Growth §3/§6; future topology ADR; feature 14 transactional enqueue; Extraction Brief topology table and operational runbook.

## R2-16 — The read-model proposal needs exact query and authorization contracts

**Priority:** P1 — pending public API

**Claim.** Endpoint names and page caps are not enough to guarantee bounded reads or queue scoping. Unfiltered lists, arbitrary filter combinations, payload/error/event exposure, and cursor order are unresolved.

**Evidence — VERIFIED against current indexes/docs; recommendations are PLAUSIBLE until EXPLAIN-tested.**

- Growth §4 proposes queue/status/job-type filters, payload inclusion, global stats, and job timelines (`docs/Task Queue Growth, Topology & Live Visibility.md`, lines 68–82).
- Authorization requires global `read` for unfiltered lists and authoritative job lookup for id routes (`docs/Task Queue Authorization & Queue Permissions.md`, lines 50–62).
- The claim index is `(queue, priority, scheduled_at, id)` and deliberately lacks `job_type` (`Unified Spec` §4 lines 323–354); arbitrary status/job-type browsing is not automatically a bounded “partial index” query.

**Proposed decision.** Freeze one projection and keyset per supported query, including a deterministic composite cursor and a matching index/EXPLAIN gate. Queue-scoped credentials must supply exactly one authorized queue; global aggregation/listing requires `taskq:read`. Default detail excludes payload, result, error text, event data, headers, and attempt stats; each optional field has a permission, byte cap, and redaction hook. Never expose attempt ids/fences. Snapshot responses carry `as_of` and staleness. Storage/catalog stats are operator/admin, not ordinary queue read.

**Amend exactly:** Growth §4; ADR-005 protocol read section; Authorization §1/§2; Unified Spec §12; harness T6/B9.

## R2-17 — Auth integration is nearly right, but two credential classes and one namespace are conflated

**Priority:** P1 — authorization and adoption

**Claim.** Per-queue wildcard service tokens work exactly as documented. API-key scopes do not accept wildcard strings, and the accepted `taskq` permission namespace is not configurable. The first outlabsAPI dogfood also cannot use the verified adapter unchanged while pinned to a20.

**Evidence — VERIFIED against outlabs-auth `0.1.0a24` and host source.**

- Version is `0.1.0a24` (`~/Documents/projects/outlabsAuth/outlabs_auth/_version.py`, lines 1–6).
- Service tokens embed their permissions (`.../services/service_token.py`, lines 86–145). `check_service_permission` normalizes `*` and calls `PermissionService._permission_set_allows` (lines 211–240); that matcher accepts `*:*`, exact, and `resource:*` (permission.py lines 558–590). Therefore `taskq_email:*` works for service tokens.
- The actual dependency path recognizes `source == "service_token"` and invokes that method with embedded token metadata (`.../dependencies/__init__.py`, lines 365–389); authentication placed the validated token payload in that metadata (`.../authentication/strategy.py`, lines 391–434). This rules out a separate exact-match-only path.
- API-key policy explicitly rejects any scope containing `*` (`.../services/api_key_policy.py`, lines 1211–1217). API keys need explicit action scope lists even though a role/catalog may contain wildcard permissions.
- ADR-006 fixes `taskq:{action}` and `taskq_{queue}:{action}` (`docs/adr/ADR-006-permission-grammar-authoritative-lookup.md`, lines 10–22), but Authorization §3.1/§4 still offers `resource_prefix`, `--prefix`, and rename/multi-install behavior (lines 102–113, 139–175).
- outlabsAPI is pinned to outlabs-auth `0.1.0a20` (`~/Documents/projects/outlabsAPI/pyproject.toml`, lines 21–25), while the adapter's claims are verified only at a24. The Extraction Brief already notes this caveat (line 133).

**Proposed decision.** Keep wildcard service-token guidance and add the exact source-backed distinction: service-token embedded permissions may use `taskq_queue:*`; API-key requested scopes enumerate `enqueue|run|read|control|admin`. Remove `resource_prefix`/`--prefix` from 0.x; separate databases are the supported installation isolation. Gate outlabsAPI `[outlabs]` dogfood on upgrading to and pinning a supported a24+ compatibility range; use the static adapter before that if desired.

**Amend exactly:** Authorization §0, §3.1, §4.1–§4.4; ADR-006 credential consequence (clarification, not grammar change); Extraction Brief dogfood checklist; harness T6 auth matrix.

## R2-18 — 0.1 fits the pilots, with two explicit acceptance additions

**Priority:** P1 — release fitness

**Claim.** ADR-009's accepted 0.1 feature set is sufficient for outlabsAPI's embedded tools/notifications lanes and one non-chaining QDarte/Diverse pilot. The design should not cut another kernel feature, but the release gate must explicitly include the sync HTTP/runtime path and a minimal job-result read/handle.

**Evidence — VERIFIED against consumer source and current docs.**

- outlabsAPI is a single FastAPI process already checking PostgreSQL and RabbitMQ in lifespan (`~/Documents/projects/outlabsAPI/app/main.py`, lines 35–128) and carries both aio-pika and pika (`pyproject.toml`, lines 13–24). Feature 14's opt-in embedded worker directly fits immediate tools/notifications lanes without schedules or chains.
- QDarte's shared worker client is synchronous `httpx.Client` using a managed OutLabsAuth service token (`~/Documents/projects/qdarte-runtime/src/qdarte_runtime/core/worker_api/client.py`, lines 93–198). A non-chaining lane needs enqueue/claim/heartbeat/settle plus result/status read, all otherwise in 0.1.
- Extraction Brief line 621 already upgrades sync HTTP client/loop support to a requirement. Growth §4 says job detail belongs in 0.1 (lines 68–82), but ADR-009 says only “safe ops views + metrics” and does not explicitly name a producer result handle.

**Proposed decision.** Keep ADR-009 unchanged in feature breadth. Add release-gate wording: (1) synchronous HTTP client and sync-handler adapter, under the safe cancellation contract in R2-11, are required for the first fleet pilot; (2) `get_job`/terminal result projection and bounded wait/poll helper are required for outlabsAPI 202 responses. No schedules, follow-ups, dependencies, workflows, archive, SSE, or bundled dashboard are needed for either 0.1 pilot. Do not cut concurrency caps, janitor, migrate-break, auth, or harness: each is tied to a named first host or recovery property.

**Amend exactly:** ADR-009 consequences/release acceptance (not its feature staging); Extraction Brief Phase C/D and acceptance criteria; feature 14 acceptance; Growth §4 staging sentence; protocol read/claim table.

## R2-19 — Mechanical consistency and peer-provenance cleanup remains

**Priority:** P2 — documentation precision

**Claim.** Stale role names, dotted permissions, route paths presented as contracts, an unfrozen module path, retry jitter fields absent from SQL, and old staging text remain. Most peer claims verify, but the Postgres message-queue extension's archive claim should not imply partition retention is its default, and the second-slice caveat can now be narrowed.

**Evidence — VERIFIED by the sweep in [02-consistency-audit.md](./02-consistency-audit.md) and current upstream sources.**

- The contradiction table lists exact repository locations and fixes.
- the Elixir/Postgres job framework telemetry/testing, the Rails-native Postgres queue pause/recurring config, the Rails in-process Postgres queue in-process async workers, a lean Redis/asyncio job library hooks/UI/group admission, and a Redis/asyncio task library defer/result-retention directions are supported by current upstream docs/source. Keep them as provenance, not load-bearing specification.
- the Postgres message-queue extension upstream clearly supports visibility timeout and `archive()` to an archive table, but partitioned queues are a separate option; revise “archive-instead-of-delete with partition retention” to avoid combining features: the Postgres message-queue extension.
- the newer Python/Postgres queue library `1.2.0` uploaded 2026-07-15 is verified by the trusted-publisher PyPI record: the newer Python/Postgres queue library.
- a pure-SQL event-stream project exists and explicitly positions itself as a pure-SQL fan-out event/message queue, not a job framework: a pure-SQL event-stream project. Its snapshot batching/rotation is useful benchmark context, not new evidence to reopen ADR-001.

**Proposed decision.** Apply all “fix” rows in review doc 02 in one docs-only cleanup after accepting the higher-priority decisions. Change Peer Research §7's blanket caveat to per-claim source links and correct only the the Postgres message-queue extension partition wording. Preserve the design distinction between task execution and event streams.

**Amend exactly:** every location enumerated in `02-consistency-audit.md`; `docs/Task Queue Peer Patterns Research.md` §7; `docs/design-review/05-peer-research-addendum.md` the newer Python/Postgres queue library/a pure-SQL event-stream project entries with verified links/date.
