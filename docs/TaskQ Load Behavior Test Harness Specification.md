# TaskQ Load Behavior Test Harness Specification

**Status:** docs-first slice S0 of the flow-control effort (branch `claude/taskq-flow-control`).
**Owner intent:** before any flow-control feature lands, TaskQ gains a harness that can reproduce, measure, and permanently regression-gate queue behavior under load — herds, retry storms, schedule stampedes, backpressure and contention spirals. The harness is a standing product surface: every future flow-control feature ships with its scenario, and maintenance runs the same scenarios forever.
**Relationship to `src/taskq/bench.py`:** extension, not replacement. The B-series scenarios, scale registry, and report discipline are unchanged. This spec adds an L-series (load behavior) on the same chassis.

## 1. Posture

1. **Report-only until calibrated.** Exactly like the B-series: toy runs prove the harness, never a baseline. Numeric bounds are enforced only where an accepted envelope exists for that scenario, scale, and runner class. Envelope acceptance is an explicit, recorded act (SS7).
2. **Invariants are always enforced, at every scale, in every tier.** Each scenario declares correctness invariants (no lost job, no duplicate execution, no unexplained worker fatal, conservation of counts). These make even the toy-scale smoke lane meaningful on day one, before any envelope exists.
3. **Deterministic by construction.** Every scenario takes an explicit RNG seed recorded in the report artifact; scenario logic reads database time, never wall-clock assumptions. Same seed + same code + same runner class → comparable reports.
4. **Failure modes are proved red before they are fixed.** A scenario that encodes a known defect (e.g. the 2026-08-05 admission-contention worker exit) must first demonstrate the defect on unmodified code, with that red run recorded as evidence, then flip green when the fix lands. No scenario is accepted green-only.

## 2. Chassis: reused and added

**Reused from `bench.py`:** fresh-database-per-scenario lifecycle via `TASKQ_BENCH_ADMIN_DSN` (create → migrate 0001–current → verify → run → drop unless kept), PostgreSQL settings fingerprint, WAL-bytes delta, `EXPLAIN` capture, event-loop delay sampling, ≥3 repetitions, machine/Python/git fingerprint, JSON report artifact, the `toy | small | full` scale registry.

**Added (new subpackage `src/taskq/loadlab/`, layout indicative not normative):**

1. **Fleet driver.** Run N `WorkerService` instances concurrently, each with its own transport, pool, and (when the scenario demands) a real LISTEN connection. Per-worker instrumentation comes free from `WorkerServiceSnapshot` (claim sweeps, claimed jobs, nudges, coalesces, reconnects). An optional multi-process mode exists for scenarios where a single event loop would serialize timing artifacts; v1 scenarios must state which mode they require.
2. **Producer driver.** Arrival-pattern generators over SQL or HTTP transport: constant rate, burst (K at once), ramp, co-timed cohort, Poisson. Concurrency-parameterized so admission-vs-drain contention shapes (4-way admission against 25-way settle) are first-class.
3. **Fault injection (v1 scope).** A wrapping `RunnerTransport` that injects typed failures on schedule (retryable claim/settle errors, timeout-shaped errors, one-shot non-retryable classification); worker kill/restart (service stop or task cancellation mid-flight); request-pool shrink to force pool contention. Server-side stalls, network shaping, and kernel-level faults are out of scope (SS8).
4. **Seed profiles.** Deterministic backlog shapes seeded through the public contract only (`enqueue`/`enqueue_many`/schedule functions), never raw INSERT, so rows carry real stamped policies: uniform FIFO; priority-mixed; key-skewed (Zipf over `concurrency_key`); retry-heavy (cohorts driven through real fail cycles so `failure_count`/`scheduled_at` are authentic); future-scheduled mix; multi-queue spread. Where full-scale aging is impractical, a documented test-only helper may compress time by bounded `UPDATE` of `scheduled_at` alone, flagged in the report.
5. **Time-series capture.** A per-second sampler persisting queue stats (ready/running/scheduled/oldest-age via the snapshot or live view), per-worker snapshot deltas, and `job_events` counts into the report artifact, so dispersion and recovery claims are made against a recorded series, not a final aggregate.
6. **Envelope layer.** Per-scenario typed metrics (SS3) with the invariant/envelope split from SS1. Envelope files are machine-readable, versioned under `docs/evidence/`, and looked up by (scenario, scale, runner class).

## 3. Metrics vocabulary (normative names)

| Metric | Definition |
|---|---|
| `claim_calls_per_delivered_job` | total claim calls / jobs delivered, fleet-wide |
| `empty_claim_ratio` | empty claim results / total claim calls |
| `wake_dispersion_ms` | p50/p95 spread of worker claim times following one wake stimulus |
| `retry_arrival_dispersion_s` | window containing 95% of first retries of a co-failed cohort |
| `admission_latency_ms` | producer enqueue latency p50/p95/p99 under load |
| `admission_reject_rate` | typed backpressure rejections / admission attempts |
| `depth_overshoot_rows` | max observed ready+queued above configured `max_depth` |
| `drain_rate_jobs_s` | settled jobs per second, steady-state |
| `time_to_recover_s` | fault cleared → drain rate back within 10% of pre-fault |
| `worker_fatal_count` | worker services entering FAILED (invariant: scenario-declared, usually 0) |
| `duplicate_execution_count` | attempts running concurrently for one job (invariant: 0) |
| `lost_job_count` | seeded − (terminal + in-flight + ready) (invariant: 0) |

## 4. Scenario catalog (L-series)

Each scenario names: load shape, faults, invariants, envelope metrics, and the failure mode / proposal it gates (F/P numbers refer to the vault research report of 2026-08-05).

| ID | Name | Shape | Gates |
|---|---|---|---|
| L1 | Notify herd | N idle listening workers; single enqueues then trickle at rate r; measure `claim_calls_per_delivered_job`, `wake_dispersion_ms`, `empty_claim_ratio` | F1/F2; P1, P3, P4, P14 |
| L2 | Retry storm | Cohort of K jobs co-fail (handler `Retry` and injected failure); measure `retry_arrival_dispersion_s` and the claim/settle spike at retry due-time | F3/F4; P1 client backoff, P9 jitter modes |
| L3 | Schedule co-due | M schedules due the same instant (+ `fire_all` catchup variants); measure firing spread, occurrence tail latency, notify burst | F5; P13 |
| L4 | Admission-vs-drain contention (**premier-refresh regression, 2026-08-05**) | Depth cap D; A-way HTTP admission against C-way settle through a deliberately small request pool; phase-overlapped | F6/F13; P1b, P7, P8 — invariants: no unexplained worker fatal, typed backpressure only, idempotent replay converges, bounded `depth_overshoot_rows`, admission resumes without operator action when drain resumes |
| L5 | Claim-error contention | Transport-injected retryable claim-error bursts plus occasional misclassified non-retryable; assert survival posture and backoff cadence | F13; P1b |
| L6 | Counter contention | High-TPS status transitions on one queue with per-queue counters enabled vs disabled; measure trigger overhead delta and count correctness vs ground truth | Wave 2a gate; P6 |
| L7 | Resume/redrive stampede | Paused-full queue resumed; bulk redrive with and without smear/ramp; measure first-60s claim/settle/notify spike | F12/F4; P12 |
| L8 | Breaker lifecycle | Induced downstream failure → trip → half-open probes → close; measure time-to-stop-claiming, probe single-flight, recovery ramp | Wave 3 gate; P10 |
| L9 | Starvation observatory | Priority flood + key-skew; record starvation age distribution (observational, no envelope in v1) | F8; P15/P16 prep |
| L10 | Rate/cap/key conformance | Queue GCRA rate limit + `flow_limits` key-space + `max_running` cap under sustained load; measure steady-state rate convergence, no free-key starvation, cap holds under a burst | Wave 2 gate; P5/P6/P7 |

L1–L5 are the v1 build set (S1). L6 lands with Wave 2a, L7 with P12, L8 with Wave 3, L10 with the 0.5 flow-control plane (rate/cap/key-fairness conformance). L4 and L5 must reproduce their defects red on current code before P1b lands (SS1.4). Amendment (2026-08-07): the `full` scale tier was made runnable end-to-end for L3/L7/L8/L10 by chunking seed enqueues to the plane's own API bounds (≤1000-spec `enqueue_many`, ≤100 schedule-claim, ≤50 job-claim); the `small` tier — the one that first caught the half-open single-flight violation (breaker T1) — should be run periodically, not only under the toy CI smoke.

## 5. Temporary database and runner contract

- Same lifecycle as bench: `TASKQ_BENCH_ADMIN_DSN` provisions a throwaway database per scenario repetition; `--keep` retains it for inspection. Nothing in the harness ever points at a consumer database; the admin DSN refuses non-throwaway host allowlists via the existing bench guard conventions.
- Local runner recipe: a compose file providing PostgreSQL 16 and 18 side by side, mirroring the CI services, documented in the harness README section. Scenarios must pass on both majors, as with the SQL-contract lanes.
- HTTP-mode scenarios mount the real FastAPI facade in-process (B11/B14 precedent) with `request_pool_max` scenario-configurable — small pools are a load feature, not a bug, for contention scenarios.

## 6. CI integration

- **`load-smoke` (per-PR):** L1–L5 at toy scale, invariants only, both PostgreSQL majors, target < 3 minutes total. Joins the required-gate list alongside `bench-smoke`.
- **`load-full` (scheduled/dispatchable):** small scale nightly, full scale on dispatch — the `million-row-plans` pattern. Uploads report artifacts; enforces envelopes only where accepted for the CI runner class.
- A scenario whose envelope regresses on the calibrated runner fails `load-full` loudly; `load-smoke` never enforces numerics.

## 7. Envelope acceptance process

1. Run the scenario on the calibrated runner (3+ repetitions) → candidate numbers in the report artifact.
2. Review and accept explicitly; record the accepted envelope (metric bounds + runner class + code SHA + date) in `docs/evidence/load-envelopes-<date>.md` plus the machine-readable file the harness consumes.
3. Envelopes are amended by the same process, never silently. A feature that legitimately shifts an envelope ships the amendment in the same PR.

## 8. Out of scope (v1)

Multi-machine distributed load generation; kernel/network fault shaping (tc, iptables); host-metric collection beyond the existing fingerprint; soak runs beyond one hour (the chassis permits them; nothing gates on them); production traffic replay; any consumer-database access.

## 9. Acceptance for this docs-first slice

This spec is accepted when the scenario catalog demonstrably covers the failure catalog F1–F13 (mapping above), the invariant/envelope split and red-before-green rule are fixed, chassis reuse boundaries are named, and the CI tiers are defined — at which point implementation slice S1 (chassis + L1–L5 at toy scale + `load-smoke`) opens. Amendments to this spec follow the repository's normal docs-first review discipline.
