# taskq — Test & Benchmark Harness

> **Status:** Normative design — 2026-07-18; **ADR fold-in applied same day** (design-review 04 merged: T8 migration suite, capability-role fixtures, owner-function time travel, calibrated performance envelope, WAL/EXPLAIN evidence, B12–B13)
> **Companions:** Unified Design Spec §16.3 (validation gates — the WHAT to prove), §17 (25-scenario failure audit = the acceptance checklist), §18/§20 (open constants the harness must inform); [`taskq-borrowed-features/10-test-helpers.md`](./taskq-borrowed-features/10-test-helpers.md) (helpers for *consumers*; this doc is the package's OWN harness)
> **Why this doc:** the spec mandates gates but never designs the machinery — no CI matrix, no deterministic race technique, no benchmark suite, no regression tracking. Development quality lives or dies here, so the harness is specified like a feature, not left to convention.

---

## 0. Verdict

Three artifacts, one repo:

1. **`tests/`** — pytest suites T1–T8 (below), from pure-unit to kill-9 chaos and migration drills, runnable on a laptop with one env var.
2. **`bench/`** — a named-scenario benchmark suite (B1–B14) with a calibrated, committed performance envelope and a compare gate. Benchmarks are *first-class deliverables*: they discharge the spec's explicitly benchmark-gated decisions (unindexed lease §18.10, claim-cost model §5.3) and put numbers behind "the most robust Postgres queue we can build."
3. **CI matrix** — GitHub Actions jobs that make the import-isolation rule, the PG16/17/18 spread, and the race gates un-skippable before merge.

Floors inherited from the spec, restated as hard harness assertions: **0 duplicate claims under any profile** (qdarte stress floor: ≥80 claim+settle/s sustained), all §16.3 gates green before any production lane moves.

---

## 1. Test layer map

| Layer | Suite | Needs Postgres | What it proves |
|---|---|---|---|
| Unit | T1 | no | pydantic contracts, retry compilation, key derivation, worker-loop logic against the fake client |
| SQL contract | T2 | yes | every `taskq.*` function's documented behavior, one test per contract row (§3.2 transitions, §3.3 budget table) |
| Race / concurrency | T3 | yes | the §16.3.1 gate: dedup convergence, double-claim impossibility, fence `lost`, cap no-overshoot, diamond no-deadlock |
| Property (stateful) | T4 | yes | §16.3.2 budget semantics + state-machine invariants under random interleaving |
| Crash / chaos | T5 | yes + subprocesses | §16.3.3: kill -9 mid-handler, lost settle responses, followup exactly-once, tick savepoint isolation |
| Facade + auth | T6 | yes | mounted-subapp envelope ownership plus the canonical path-scoped authorization vectors in Authorization doc §8, HTTP↔settle-result mapping, lifespan/embedded runtime (feature 14 §5) |
| Soak | T7 | yes, long-lived | §16.3.4 24h bloat profiles; nightly 1h mini-soak; release-gate full run |
| Migration / compatibility | T8 | yes | ADR-004: clean install at N; double-invocation/lock contention; N→N+1 upgrade with queued/running/failed/archived jobs present; old client vs new schema inside the compatibility window; new client vs old schema fails fast with a stable error; interrupted migration resumes or reports a deterministic operator action; `verify` detects corrupted signatures/ownership/grants/checksums/missing indexes; mid-flight worker survives a pre-migration (brief §8.2 upgrade test) |

Consumer-facing helpers (feature 10: fake client, `work`, `require_enqueued`, inline/drain modes) are **built on T-layer plumbing and tested by it** — the package dogfoods its own testing story.

### 1.1 Infrastructure rules

1. **One env var:** `TASKQ_TEST_DSN`. Absent → PG-marked suites skip (unit always runs). CI supplies service containers; local dev uses `docker compose -f bench/compose.yaml up -d` (pinned PG18 with the spec's recommended settings) or any scratch DB.
2. **Schema lifecycle:** installer runs once per session into schema `taskq` (asserting idempotent double-install — feature 13); per-test isolation via truncation fixture, not re-install. A `--fresh-schema` flag forces full reinstall for installer-focused tests.
3. **Roles matter in tests (ADR-010):** suites connect as the exact capability role each call requires (`taskq_producer`/`taskq_runner`/`taskq_observer`/`taskq_operator`) — so grant regressions fail loudly. T2 additionally runs the **privilege/shadow suite** as an untrusted role: direct `UPDATE taskq.jobs` raises, ungranted functions raise, PUBLIC execute is revoked on every function, and shadow-object attacks (attacker-created same-name objects in writable schemas) cannot capture a pinned-`search_path` definer function.
4. **Sanctioned time travel:** DB `now()` is the only clock, so tests never mock time — the harness installs **test-only owner functions** (`taskq_test.rewind_lease(job_id, by)` etc.) into the **ephemeral test database only**; they are not part of the package migrations and can never exist in production. No fixture issues raw DML, so the no-direct-DML invariant holds even inside the harness.
5. **Determinism first:** every T3 race is a *choreographed* interleaving (§2), plus a smaller randomized stress layer on top. A race test that only sometimes exercises the race is a lottery ticket, not a gate.

---

## 2. Deterministic concurrency technique (normative)

Two (or N) real asyncpg connections, stepped explicitly:

- **Barrier = advisory lock:** session A takes `pg_advisory_lock(k)`; session B's function call blocks inside a strategic wait; the test releases to sequence the exact interleaving under test.
- **Hold-open = uncommitted claim:** start `claim_jobs` in an open transaction to hold row locks while the second session runs the competing path (SKIP LOCKED behavior, dedup convergence loop, replace-vs-running).
- **Lost-response injection:** a `FaultyClient` wrapper around the real SQL/HTTP client with programmable fault points — `drop_response_after_send("complete_job", times=1)` — proving settle retries resolve to `already_settled`, never discarded work (the DCP 7.2 class, killed forever by a test).
- **Crash = real crash:** T5 spawns worker subprocesses and `SIGKILL`s them mid-handler (handler signals "started" via NOTIFY, harness kills, rewinds lease via time travel, asserts reap → backoff → budget +1 → `expiry_streak` +1 → poison at 3). No mocked "pretend crash" is accepted for these gates.

Randomized layer: T3-R runs K producers × M workers × mixed ops for N seconds (small in PR CI, big nightly) asserting the global invariants only (0 duplicate claims, conservation, no wedged rows after drain + final tick).

---

## 3. Property-based suite (T4)

`hypothesis` stateful testing against real Postgres:

- **Model:** the §3 state machine reduced to per-job (status, failure_count, expiry_streak, attempt ledger size).
- **Ops drawn:** enqueue (± idempotency key, ± deps), claim, complete, fail(retryable|not), release, snooze, lease-rewind+tick, cancel, redrive — each generated op dispatches through its exact capability role (producer/runner/observer/operator/housekeeper — ADR-011), so the property suite doubles as a continuous grant check.
- **Invariants after every step:** ≤1 running attempt per job (and `uq_job_attempts_running` never violated); `failure_count` only moves per the §3.3 table (releases/snoozes never consume budget); terminal ⇔ `finished_at` set; conservation (every enqueued job is exactly one of active/terminal — never vanished); dedup: ≤1 active row per (queue, key); typed results only (no unexpected exceptions).
- Shrinking gives minimal reproductions of any state-machine bug — the cheapest adversarial reviewer the project can hire.

---

## 4. CI matrix (GitHub Actions, uv-managed)

| Job | Matrix | Runs |
|---|---|---|
| `lint` | — | ruff + format check |
| `import-isolation` | py3.12 / py3.13 | `uv pip install .` (NO extras) → `python -c "import taskq, taskq.client, taskq.worker"`; then `.[http]` without outlabs; asserts the extraction brief's forbidden-import rule mechanically |
| `unit` | py3.12 / py3.13 | T1, no services |
| `sql-contract` | **PG 16 / 17 / 18** service containers | T2 + installer/verify + capability-detection paths (uuid7 fallback on 16/17) |
| `races` | PG18 | T3 choreographed + T3-R (30s) + T4 (bounded examples) |
| `crash` | PG18 | T5 subprocess suite |
| `facade` | PG18, `[http]` and `[http,outlabs]` variants | T6 (outlabs variant seeds a throwaway SimpleRBAC to run the §8 auth matrix) |
| `migrations` | PG18 (min-version lane on release) | T8 |
| `bench-smoke` | PG18 | every B-scenario at toy scale — proves the harness itself, records nothing |

Nightly: T3-R long (10 min), T4 large example budget, T7 mini-soak (1h, heartbeat-heavy profile), full bench run with envelope compare (report-only). Release gate (manual): 24h T7 soak per §16.3.4, full T8 upgrade-path matrix, and the pinned-runner envelope hard gate. Branch protection requires everything through `migrations`.

---

## 5. Benchmark suite (B1–B14)

`bench/` ships a small runner (`uv run taskq-bench run B3 --dsn ... --scale small|full`) — scenarios are code, results are JSON.

| ID | Scenario | Primary metrics | Informs |
|---|---|---|---|
| B1 | Single enqueue throughput/latency | enq/s, p50/p99 | client overhead |
| B2 | Bulk enqueue (1000/call) | rows/s | ingestion lanes |
| B3 | Claim→settle round trip, empty vs 1M-row backlog | claim p99, e2e p99 | claim-index shape; Solid-Queue ~110µs anchor (§5.3) |
| B4 | Mixed sustained load (N workers, M producers, realistic type mix) | jobs/s sustained, e2e p95 | the ≥80/s qdarte floor — must beat it |
| B5 | **Heartbeat HOT ratio** (long-lease jobs, heartbeat-heavy) | `n_tup_hot_upd/n_tup_upd` (target ≥0.95), index bytes flat | **discharges §18.10** (deliberately-unindexed lease) |
| B6 | Retry storm (mass failure + backoff) | requeue/s, claim p99 during storm, index growth | backoff engine, §13 index churn model |
| B7 | Saturated concurrency cap | claim p99 with cap hot, overshoot count (**must be 0**) | try-lock admission cost (§5.3 honesty note) |
| B8 | NOTIFY wake latency vs poll-only | enqueue→claim-start p50/p99 both modes | feature 06 latency claim |
| B9 | Stats under depth (1M backlog) | `queue_stats` / `metrics()` latency | §12.1 cost note; dashboard safety |
| B10 | Archive sweep + partition drop under live load | sweep rows/s, claim p99 impact | §13 archival non-interference |
| B11 | Embedded-mode overhead (feature 14) | API request p99 with/without embedded worker under load | pool-split sizing defaults |
| B12 | Migration + `verify` on a populated schema (T8 companion) | lock duration, service disruption during migrate | ADR-004 upgrade windows |
| B13 | Graceful fleet shutdown | drain duration; released vs expired claims (expired must be 0 within grace) | feature 11 defaults |
| B14 | Generated client → ASGI → SQL command path | client/e2e p50/p99, facade overhead, structural SQL plans | Stage-3 transport overhead and parity |

**Method rules:** each result JSON records scenario, scale, git sha, PG version + settings fingerprint, machine fingerprint, workload manifest + seed, warmup/duration/repetitions; ≥3 runs, report median (throughput) and worst (p99); DB dropped/recreated between scenarios. Beyond latency/throughput, capture **WAL bytes per accepted/settled job**, table/index bytes, live/dead tuples, vacuum activity, lock waits, connections, and client event-loop delay. Representative queries run `EXPLAIN (ANALYZE, BUFFERS, WAL)` and assert **structural** properties — expected index family, no unbounded full scan, bounded rows — never exact planner cost strings.

**Regression gate (calibrated envelope, not prose thresholds):** the pinned dedicated runner is calibrated first and the accepted bounds are committed as `bench/envelopes/performance-envelope.toml`; `taskq-bench compare --against envelope` blocks a release on breach without an approved explanation. CI shared runners and laptops run report-only — development signals, never release evidence, and never committed as baselines. Correctness floors stay absolute regardless of hardware: 0 duplicate claims, 0 cap overshoot, and the no-op claim/settle path sustaining at least the greater of the historical 80 jobs/s floor or 2× the highest measured production peak.

**Open-constant duty:** B5→§18.10, B7→§20.1 poison threshold context, B3/B9→§20.6 archive/stats access patterns, B8→§20.7 long-poll sizing, B11→feature 14 defaults. Each spec open question that is empirical gets its number from here, and the spec edit cites the bench run.

---

## 6. What the harness explicitly is not

- Not a distributed multi-node rig — single host, one Postgres; the design targets that scale (§18.7 reopen thresholds stand).
- Not marketing benchmarks — numbers are for regression detection and decision discharge; publishing comes later, if ever, with full methodology.
- Not a mock-Postgres correctness story — no gate may pass on the fake client alone; SQLite/in-memory backends will never exist (Postgres-only is a feature).
- Not a substitute for the §17 audit — the 25-scenario table remains the acceptance checklist; T-suites implement it and cite scenario numbers in test names.
