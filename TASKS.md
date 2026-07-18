# outlabs-taskq — Execution Tracker

> **Tier 2 (live).** Task-level truth for the implementation: what is in flight, what is next, what is done. The [Build Plan](docs/Task%20Queue%20Build%20Plan.md) owns stage strategy and exit gates; this file owns the granular work. **Update this file in the same commit as the work it describes** — a task not updated here didn't happen.

## Cold start (any agent, from zero)

1. Read `AGENTS.md` (hard rules) → `docs/README.md` (tier map — Tier-0 contracts beat everything) → this file.
2. Environment: Python 3.12+, `uv`, and a local PostgreSQL. A dev Postgres 18 usually runs via docker (`docker ps` → container from localDevServices, `postgres/postgres@localhost:5432`). Create/reuse the scratch DB:
   `psql postgresql://postgres:postgres@localhost:5432/postgres -c "CREATE DATABASE taskq_stage1_test"` (ignore exists-error).
   Caveat: migration 0001 creates six cluster-wide `taskq_*` roles on that server — expected on a dev cluster; never point tests at a shared/production server.
3. Run everything:
   ```bash
   uv sync --extra dev
   uv run pytest tests/ -q                                   # T1 only (no DSN)
   TASKQ_TEST_DSN="postgresql://postgres:postgres@localhost:5432/taskq_stage1_test" \
     uv run pytest tests/ -q                                 # T1 + T2 (must be 42/42 before you start)
   uv run ruff check .
   ```
4. Pick the topmost unchecked task in **Now**, or the next in **Next**. Work it to its acceptance criteria.
5. Definition of done, every task: suite green (no skips you introduced), `ruff check` clean, docs amended if the task's row says so, this file updated (move the task, one-line result note), one commit ending with the repo's `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` trailer convention.

**Standing rules (non-negotiable):** Tier-0 contracts and ADRs win every conflict — if implementation reveals a contract bug, STOP, record it under **Contract questions** below, and fix docs-first (errata/ADR), never code-around. Never name third-party queue projects. Never edit Tier-4 historical docs. New SQL functions require a Function Manifest entry first.

## Status snapshot

| | |
|---|---|
| Stage | **S3-PREP boundary hardening active** — inbound extras policy is green; tagged result unions are next; round 4 remains pending and S2-05 closed |
| Suite | 281/281 regular on PG18.3; prior PG16.14/plan/artifact evidence remains CI-gated |
| Contracts | Protocol v1 + Function Manifest 0.1.2 (+ ADR-012/013) |
| Next review | Hand off `docs/design-review-4/REQUEST.md`; adjudicate the response before opening S2-05 |

## Now — S3-PREP Python-surface only

- [ ] **S3-PREP-02** — tagged/discriminated enqueue and settlement result unions with byte-identical wire serialization
- [ ] **S3-PREP-03** — module-level bulk-item/claim-batch `TypeAdapter`s and B2/B3 before/after evidence

## Next — gated after Stage 2B review

- [ ] S2-05 NOTIFY listener + poll loop (feature 06); `taskq worker` CLI
- [ ] S2-06 `taskq.testing` fixtures + inline transport (feature 10)

## Later

- [ ] **S3-PREP-04 guidance (deferred to S2-05)** — worker CLI configuration must use `pydantic-settings`, not hand-rolled environment parsing; do not implement configuration before S2-05 opens.

*(subsequent stages remain sequenced by the Build Plan)*

## Contract questions (STOP-and-record before coding around)

*(none open — ADR-013 resolves S2-CQ-01 as contract 0.1.2: append the effective lease duration to `claimed_job`; workers schedule from it monotonically and never subtract local wall time from the absolute expiry. ADR-012 resolves round-3 CQ-01/CQ-02.)*

## Round-3 finding dispositions

All seven findings are **accepted as source-backed**; ADR-012 resolved the two Contract questions. R3-01, R3-02, and both Contract questions were independently reproduced after the response landed; R3-03..07 agree with the cited ADR/harness/source gaps. R3-07 is an evidence-hardening item rather than a direct contract violation. No finding is rejected or deferred into Stage 2.

## Done

- [x] **S3-PREP-01 · Direction-aware extras policy** — documented the ADR-005 boundary rule in `taskq.protocol`: inbound enqueue command/bulk-item models now forbid unknown fields so typos fail locally, while outbound projections/results explicitly ignore additive fields for forward-compatible decoding; typo and unknown-result vectors bring PG18 to 281/281 without changing wire or SQL contracts.
- [x] **S2-04-R4 · Round-4 review packet** — registered an immutable, contract-first adversarial request covering the contract-0.1.2 additive upgrade and verifier, every S2-04 execution/heartbeat/replay/lifecycle acceptance row, mandatory R2-11 live-sync counterexamples, repeated races, real-SQL conservation, resource cleanup, artifact/import isolation, CI collection, and strict absence of S2-05/Stage-3 scope; the reviewer may add only `docs/design-review-4/RESPONSE.md` and must decide whether S2-05 may open.
- [x] **S2-04-AUDIT · Stage 2B permanent completion evidence** — five repeated, barrier-choreographed race families cover both winner orders without correctness sleeps; live SQL vectors prove complete/retry/snooze/cancel/shutdown/no-handler budget and exact event conservation plus committed-response replay; task, exception, executor-thread, and SQL-pool ledgers return to baseline; source CI imports the worker on Python 3.12/3.13 and every fresh wheel/sdist core/HTTP/OutLabs install smokes it outside the checkout. The exact full suite is 279/279 plus the million-row plan gate on PostgreSQL 18.3 and 16.14, with 149/149 in the clean Python 3.13 worker/unit lane.
- [x] **S2-04D · Bounded concurrency and soft stop** — added synchronous slot reservation, duplicate-attempt rejection, capacity waiting, lazy bounded sync execution with active heartbeat while thread-queued, atomic intake close, cooperative/infinite drain, monotonic deadline, shared escalation, async shutdown release, honest live-sync process-exit signaling, fatal auto-stop, and complete task/executor joining; 8 deterministic vectors bring PG18 to 264/264.
- [x] **S2-04C · Verb-aware settlement replay and fault injection** — settlement now retries only the original verb with bounded exponential backoff, validates command-specific outcomes, converges after a committed-but-lost response, keeps heartbeats live until certainty, classifies exhausted certainty as fatal, and applies the frozen invalid-follow-up terminal escape; 13 deterministic vectors bring PG18 to 256/256 and prove one semantic settlement plus one handler invocation under response loss.
- [x] **S2-04B · Monotonic heartbeat and fenced per-job supervision** — added the core worker options/clock/state/report API, exact `lease_seconds/3` cadence, one heartbeat coroutine per active handler, generation-safe checkpoint flush, two-failure recovery/third-failure ownership loss, typed loss, operator grace cancellation, non-retryable runtime failure, no-handler release, async/sync dispatch, and joined lifecycle; 10 deterministic vectors bring PG18 to 243/243 without reading absolute expiry for scheduling.
- [x] **S2-04A · Execution primitives and deterministic harness** — added frozen closed handler intents, thread-safe escalating cancellation, fence-free `JobContext` with generation-safe 2KB checkpoints, exact sync/async one-/two-argument handler registration, public core exports, and private manual-clock/scripted-response-loss utilities; 12 boundary/concurrency vectors bring the PG18 suite to 233/233 with no optional imports or construction-time work.
- [x] **S2-04-SPEC · Worker-runtime contracts frozen** — the new Tier-3 specification fixes the S2-04-only module/API boundary, closed result normalization, cancellation precedence, monotonic lease-derived heartbeat state machine, verb-aware replay, R2-11 sync honesty, bounded supervisor/soft stop, deterministic harness, and A/B/C/D/audit acceptance matrix; S2-05 and Stage 3 remain excluded.
- [x] **S2-CI-01 · Contract 0.1.2 implemented and proven** — immutable migration `0003` appends `claimed_job.lease_seconds`, returns the exact effective duration, advances meta without changing the 40-function surface, and is decoded by the Python transport; `verify()` plus an independent ordered catalog assertion, default/stamped/override vectors, fresh install, and the full `0001 → 0002 → 0003` upgrade chain pass on PG18.3 and PG16.14 (221/221 plus the million-row plan gate on both).
- [x] **S2-CQ-01 · Effective claimed lease adjudicated docs-first** — accepted ADR-013 and amended Protocol v1, the Function Manifest, and Unified Spec §14 before SQL: contract 0.1.2 appends the exact effective `lease_seconds`, retains `lease_expires_at`, and bans client-wall-clock duration derivation; implementation was separately gated as S2-CI-01.
- [x] **S2-AUDIT-03 · Function-specific outcome enforcement** — every scalar and composite transport result is checked against its command's own protocol-owned outcome set; rollback-only wrong-command outcomes become `TQ500` even when the value is valid for a different command (217/217 on PG18 and PG16, plus the plan gate on both).
- [x] **S2-AUDIT-02 · Permanent acceptance evidence** — transport-level 20-way dedup proves one `created`/19 `existed`, captured logs remain fence-free, SQL construction/commands leave no background tasks or checked-out connections, transaction vectors conserve domain/job/event rows, and CI now runs the full suite on PG16/PG18 plus explicit core/HTTP/outlabs isolation on Python 3.12/3.13 and on every Python-3.12 wheel/sdist; all local mirrors pass (216/216 + plan gate on both PG versions, 73 Python-3.13 unit tests).
- [x] **S2-AUDIT-01 · Protocol single-source correction** — `taskq.protocol` now owns the closed 30-command names, SQL identities, capability roles, outcomes, TQ errors/retryability, and replay rules; typed settle/job/operator enums reject invented values, while independent parity proves exact agreement with the Tier-0-derived machine manifest (214/214 on PG18).
- [x] **S2-03 · Typed facade and transactional enqueue** — `TaskQ` compiles registered canonical tasks and retry stamps exactly once, keeps raw enqueue explicitly opt-in, and executes single/bulk enqueue on the caller's exact `AsyncSession`/`AsyncConnection` without owning its lifecycle; commit, rollback, autobegin, savepoint, cancellation/error ownership, non-SQL rejection, and no-background-work contracts pass on PG16/PG18, while clean wheel/sdist core installs import the complete Stage 2A surface (212/212 each).
- [x] **S2-02 · Complete async SQL transport** — runtime-checkable `TaskqTransport` and lazy `SqlTaskqTransport` cover all 30 manifest-public functions with fixed bound calls, typed/fence-safe adapters, no table DML or implicit retries, owned/borrowed engine semantics, SQLSTATE-only failures, malformed-bulk invariants, and transaction rollback/cancellation; every method passes through its least-capability role with cross-role denials on PG16 and PG18 (201/201 each).
- [x] **S2-01 · Typed task registry and protocol values** — immutable generic Pydantic task metadata validates canonical names, queues, aliases, stamped retry policy, handler annotations, and JSON payloads; collision-atomic deterministic registration preserves rename dispatch; the closed enqueue/TQ models, fence-redacted claim projection, SQLSTATE-only typed errors, safe public exports, and 62 unit/property vectors bring the PG18 suite to 188/188.
- [x] **R3-F08 · Cross-version exact-catalog normalization** — the exact constraint axis now excludes PostgreSQL 18's version-specific `NOT NULL` projection while table shapes continue to close nullability; the identical 126-test suite and opt-in million-row plan gate pass on PostgreSQL 16.14 and 18.3.
- [x] **R3-F07 · Plan-query drift detection** — every representative million-row structural query is now bound to normalized fragments from the actual owning function definition; a rollback-only full-scan mutation proves the regular guard fails on function drift and recovers after rollback (126/126 plus the opt-in gate on PG18).
- [x] **R3-F06 · Benchmark reset and conservation** — every B1–B4 scenario now creates/migrates/fingerprints/drops its own fresh database; B4 stops and joins producers before a bounded worker drain, then records and asserts accepted = terminal + active with zero active/running jobs or attempts (all four toy smokes green, no databases leaked).
- [x] **R3-F05 · Built-artifact CI gate** — CI builds wheel + sdist, installs each core and HTTP extra into clean environments outside the checkout, proves optional-import isolation and installed-package provenance, exercises both entry points, asserts the packaged 0001+0002/40-function manifest, and performs a fresh database CLI migrate + exact verify; the identical four-environment smoke is green locally.
- [x] **R3-F04 · Manifest-complete T2/T8 coverage** — closed ledgers cover all 30 public functions, registered errors, replay declarations, and exact grants; direct vectors fill bulk/runner/observer/operator/housekeeper gaps, assert safe views and shadow resistance, add concurrent install + CLI gates, reuse failure/sync/upgrade/corruption T8 evidence, and extend T4 with heartbeat and worker-cancel replay transitions (125/125 on PG18).
- [x] **R3-F03 · Reserved-role validation** — migration preflight now rejects colliding reserved names with LOGIN, SUPERUSER, CREATEROLE, CREATEDB, REPLICATION, BYPASSRLS, or inherited membership before target-database DDL; seven fresh-database probes prove atomic refusal and lock cleanup, while the exact verifier enforces the installed role manifest (113/113 on PG18).
- [x] **R3-F02 · Migration lock failure recovery** — caller-owned migrations now use a transaction advisory lock while runner-owned multi-transaction applies retain an explicitly released session lock; async/sync-adapter × caller/runner failure probes leave zero locks and prove immediate second-connection recovery (106/106 on PG18).
- [x] **R3-F01 · Exact machine-readable manifest + verifier** — the independent 0.1.1 catalog projection closes the 40-function surface and exact role/relation/type/index/constraint/view/ACL/seed axes; read-only verification rejects 36 rollback-only corruptions, including all five R3-01 counterexamples, then proves restoration green (102/102 on PG18).
- [x] **R3-CI · Implement contract 0.1.1** — immutable migration `0002_contract_0_1_1` adds the owner-only byte-safe truncation helper, applies ADR-012 null boundaries and diagnostic caps, advances the contract version, and passes fresh-chain plus `0001` upgrade vectors (64/64 on PG18).
- [x] **R3-CQ · Contract questions adjudicated docs-first** — accepted [ADR-012](docs/adr/ADR-012-null-boundaries-byte-safe-diagnostics.md) makes explicit null invalid (`TQ422`), caps stored diagnostics by UTF-8 bytes with settlement-safe truncation, adds the owner-only helper to the Function Manifest before SQL, and advances the immutable migration chain to contract 0.1.1/`0002`.
- [x] **R3-01 · External response processed** — the immutable [round-3 response](docs/design-review-3/RESPONSE.md) was independently adjudicated: verdict BLOCKED; all 7 findings accepted; CQ-01/CQ-02 recorded above; S2-01 remains closed.
- [x] **S2-00 · Stage-2A implementation specification** — the new Tier-3 spec fixes the typed task/registry boundary, closed 0.1 outcomes and TQ errors, complete async SQL transport scope, caller-vs-transport transaction ownership, fence/import safety, and the S2-01..03 acceptance matrix; it remains subordinate to the blocked round-3 remediation.
- [x] **Design phase** — spec v1.6, ADR-001..011, two review rounds folded, Protocol v1 + Function Manifest canonical, docs constitution (`6cf6793`..`e1237c5`)
- [x] **S1 opening slice** — migration `0001_initial.sql` (6 roles, 39 hardened functions, self-checking), ADR-004 runner (`migrate`/`migrate_sync`/`verify` + CLI), T1 (26) + T2 (15) suites, 42/42 green vs PG 18.3, wheel packaging fixed, single-writer ledger + typed-cancel reconciliations in manifest errata §8 (`3e7d55d`)
- [x] **S1-01 · T3 choreographed races** — six advisory-barrier/hold-open race cases run deterministically for 20 rounds each: same-key convergence, double-claim exclusion, post-reap fence loss, cross-verb settle conflict, ten-way cap admission, and the single permitted pause slip.
- [x] **S1-02 · T3-R randomized stress** — seed-replayable, env-scalable producer/worker/operator load mixes all 0.1 settle verbs, then drains and asserts durable duplicate-claim, attempt-token, conservation, terminal-state, and no-wedge invariants (30s default run green with seed `424242`).
- [x] **S1-03 · T4 stateful model** — Hypothesis drives enqueue/claim/complete/fail/release/snooze/cancel/lease-rewind+tick/redrive through capability roles; every step reconciles budget, fence, attempt-ledger, terminal-shape, dedup, and conservation invariants (20×40 default green with seed `24680`).
- [x] **S1-04 · verify corruption matrix** — T2 now corrupts and restores each hardening axis; `verify()` precisely names missing pinned paths, PUBLIC EXECUTE, wrong ownership, ledger checksum drift, and a missing capability role, then proves the restored catalog green.
- [x] **S1-05 · PG16 lane** — the identical 54-test suite passes on PostgreSQL 16.14 and 18.3, including the uuid7 fallback, races, stress, model, and verifier corruption matrix; no PG16 manifest caveat was required.
- [x] **S1-06 · 1M-row plan checks** — opt-in `tests/test_plans.py` seeds mixed states, stabilizes stats/visibility, runs `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`, and structurally asserts claim/dedup/reap/stats index families, bounded hot-path rows, and no full `jobs` scan (two consecutive PG18 runs green).
- [x] **S1-07 · B1–B4 benchmark smoke** — packaged `taskq-bench` runs single enqueue, 1000-row bulk, empty/deep claim→settle, and mixed producer/worker load for ≥3 repetitions; toy tests and the CLI print/write JSON with method, machine/PG/settings, WAL/storage/tuple/lock/connection, latency/throughput, event-loop, and structural EXPLAIN evidence. No baseline was created.
- [x] **S1-08 · CI wiring** — GitHub Actions now gates Ruff check/format, Python 3.12/3.13 core+HTTP import isolation and T1, PostgreSQL 16/18 SQL contracts, PG18 races/T4, migrations, and B1–B4 smoke; README records the required branch-protection checks.
- [x] **S1-09 · Stage-1 exit review packet** — the Build Plan records every exit gate green and the immutable Tier-4 [round-3 request](docs/design-review-3/REQUEST.md) gives Andi a contract-first audit program for migration 0001, runner/verifier, SQL suites, plans, benchmarks, packaging, and CI.
