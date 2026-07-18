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
| Stage | **1 — round-3 remediation BLOCKED on two Contract questions**; Stage 2A closed |
| Suite | 58/58 regular + opt-in 1M plan gate green vs PG 18.3; 54/54 vs PG 16.14 |
| Contracts | Protocol v1 + 0.1 Function Manifest (+ errata §8) |
| Next review | Round 3 verdict **BLOCKED**: 4 HIGH, 2 MEDIUM, 1 LOW, 2 Contract questions |

## Now — contract adjudication (STOP before implementation)

- [ ] **R3-CQ · Adjudicate CQ-01 and CQ-02 docs-first.** Decide explicit-`NULL` boundary semantics/registered SQLSTATEs and the stored-error ceiling's scope, byte unit, and truncation/rejection behavior. Apply accepted Tier-0 errata/version discipline before any SQL remediation.

## Next — Round-3 remediation (only after R3-CQ)

- [ ] **R3-F01 · Exact machine-readable manifest + verifier** — close R3-01 across object-set equality, function identity/attributes/ACLs, roles, tables/views/types/indexes/constraints, DML denial, seeds, and rollback-only corruption vectors.
- [ ] **R3-F02 · Migration lock failure recovery** — close R3-02 for sync/async and caller/runner-owned transactions; prove concurrent recovery after injected failure.
- [ ] **R3-F03 · Reserved-role validation** — close R3-03 by atomically rejecting unsafe pre-existing LOGIN/elevated/member roles before grants and verifying the role manifest.
- [ ] **R3-F04 · Manifest-complete T2/T8 coverage** — close R3-04 with collection completeness, all public-function behavior/error/grant vectors, shadow probes, installer concurrency/failure/CLI/sync/compatibility cases, and broader T4 operations where already contracted.
- [ ] **R3-F05 · Built-artifact CI gate** — close R3-05 by installing wheel and sdist outside the source tree, checking import isolation, entry points, packaged migration discovery, migrate, and verify.
- [ ] **R3-F06 · Benchmark reset and conservation** — close R3-06 with the normative fresh-database method and producer-stop→worker-drain B4 accounting.
- [ ] **R3-F07 · Plan-query drift detection** — close R3-07 by binding representative structural plans to actual function definitions or captured nested plans.

## Later — Stage 2 kickoff (closed until round-3 remediation passes PG16 + PG18)

- [ ] S2-01 typed `Task[In, Out]` registry + wire names/aliases (features 01/03/08-lite)
- [ ] S2-02 async SQL transport implementing the protocol commands + typed results
- [ ] S2-03 SQLAlchemy `AsyncSession` transactional enqueue (`session=`)
- [ ] S2-04 worker supervisor: heartbeat-per-job, verb-aware settle retries, R2-11 cancellation contracts, soft stop (feature 11)
- [ ] S2-05 NOTIFY listener + poll loop (feature 06); `taskq worker` CLI
- [ ] S2-06 `taskq.testing` fixtures + inline transport (feature 10)

## Contract questions (STOP-and-record before coding around)

1. **CQ-01 · Explicit `NULL` at locked numeric boundaries.** Tier 0 promises bounded claim batch (1–50), release delay (0–86400), and bulk redrive limit (1–500) with closed TQ errors, but its executable/reference bodies let SQL `NULL` bypass `IF` predicates: live probes accept a null claim batch, make redrive unbounded, and surface native `23502` from release. Decide whether explicit null is uniformly invalid (`TQ422` is the review recommendation), and record the exact contract/version treatment before changing SQL.
2. **CQ-02 · Stored-error 2KB ceiling.** Protocol H-09 freezes stored error at ≤2KB, while adopted cancel/snooze bodies store unbounded reason text; live probes stored 12,000 bytes, and existing `left(..., 2000)` paths count characters rather than bytes. Decide which job/attempt/event fields the ceiling covers, whether KB means bytes, and whether overage is rejected or byte-safely truncated before changing bodies.

## Round-3 finding dispositions

All seven findings are **accepted as source-backed**, pending the two contract decisions above. R3-01, R3-02, and both Contract questions were independently reproduced after the response landed; R3-03..07 agree with the cited ADR/harness/source gaps. R3-07 is an evidence-hardening item rather than a direct contract violation. No finding is rejected or deferred into Stage 2.

## Done

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
