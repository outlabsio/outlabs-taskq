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
| Stage | **1 — round-3 remediation in progress**; ADR-012 resolved Contract questions; Stage 2A closed |
| Suite | 126/126 regular + opt-in 1M plan gate green vs PG 18.3; final PG16 remediation rerun in progress |
| Contracts | Protocol v1 + Function Manifest 0.1.1 (+ ADR-012) |
| Next review | Round 3 verdict **BLOCKED**: 4 HIGH, 2 MEDIUM, 1 LOW, 2 Contract questions |

## Now — Round-3 remediation

- [ ] **R3-F08 · Cross-version exact-catalog normalization** — make the exact constraint manifest compare the same contract surface on PG16 and PG18, then rerun the complete remediation suite on both versions.

## Next — Round-3 remediation

*(none — R3-F08 is the final remediation audit item)*

## Later — Stage 2 kickoff (closed until round-3 remediation passes PG16 + PG18)

- [ ] S2-01 typed `Task[In, Out]` registry + wire names/aliases (features 01/03/08-lite)
- [ ] S2-02 async SQL transport implementing the protocol commands + typed results
- [ ] S2-03 SQLAlchemy `AsyncSession` transactional enqueue (`session=`)
- [ ] S2-04 worker supervisor: heartbeat-per-job, verb-aware settle retries, R2-11 cancellation contracts, soft stop (feature 11)
- [ ] S2-05 NOTIFY listener + poll loop (feature 06); `taskq worker` CLI
- [ ] S2-06 `taskq.testing` fixtures + inline transport (feature 10)

## Contract questions (STOP-and-record before coding around)

*(none open — ADR-012 resolves round-3 CQ-01/CQ-02 as contract 0.1.1: explicit null → `TQ422`; stored diagnostics truncate to UTF-8 byte caps without blocking settlement)*

## Round-3 finding dispositions

All seven findings are **accepted as source-backed**; ADR-012 resolved the two Contract questions. R3-01, R3-02, and both Contract questions were independently reproduced after the response landed; R3-03..07 agree with the cited ADR/harness/source gaps. R3-07 is an evidence-hardening item rather than a direct contract violation. No finding is rejected or deferred into Stage 2.

## Done

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
