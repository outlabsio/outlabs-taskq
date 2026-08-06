# Task Queue 0.4 Queue Counters and Health Specification

**Status:** docs-first slice S3 of the flow-control effort (Wave 2a — signals). This document is the review gate: no migration is written until the owner accepts this contract. Implementation lands as migration 0022 (inactive) + 0023 (activation), following the finite-projection precedent (0011/0012, 0014/0015).
**Sources:** research report proposals P6 (trigger-maintained counters) and P19 (health verdicts); consumer demand recorded 2026-08-05 (premier-refresh incident: depth pinned behind a dead consumer, discovered by hand; D-ALERT unbuilt; render lane accumulated five days unnoticed).
**Implementation-study amendments (2026-08-06, recorded before build):** (a) the `max_depth` probe replacement (SS2.5) and the `health_lag_seconds` profile column move to Wave 2b — both require redefining the enqueue family / profile CRUD, which Wave 2b redefines anyway for typed verdicts; 0.4.0 uses a fixed 600s health lag constant and keeps the existing probe. (b) The counters trigger is installed **disabled** by 0022 and enabled by 0023 in the same transaction as an atomic re-backfill (`ALTER TABLE ... ENABLE TRIGGER` takes SHARE ROW EXCLUSIVE, so the backfill is consistent with concurrent writers by construction) — no per-row capability guard cost, ever. (c) Level counters use `greatest(0, ...)` clamps with NO check constraints: counter drift must never abort a production settle; L6 asserts exact equality instead. Cumulative totals are documented as since-activation, seeded from surviving rows (rates use deltas only, so the seed baseline is irrelevant).

**Scope boundary:** signals only. No enforcement (in-flight caps, rate limits, TTL, ramps) — that is Wave 2b, gated on the S4 owner decisions. No wire/HTTP changes beyond additive read surfaces.

## 1. What this adds and why

1. **O(1) per-queue level counters** maintained by trigger, replacing index walks for status-based levels and making the `max_depth` probe a counter comparison.
2. **Cumulative per-queue totals**, so two snapshots yield true arrival/settle/failure **rates** and a **drain ETA** — the missing derivative layer (`rates_15m` was deliberately excluded from 0.1; this is its bounded successor).
3. **Typed health verdicts** (`taskq.queue_health`) composing counters, rates, staleness, and worker presence into operator-facing conclusions — including `no_consumer`, the verdict that would have flagged both the render-lane accumulation and the premier-refresh worker exit at presence-lapse time.

## 2. Contract surface (migration 0022, SQL contract 0.4.0)

### 2.1 `taskq.queue_counters`

One row per queue, created by the `queues` insert trigger and backfilled for existing queues at migration time.

| column | type | meaning |
|---|---|---|
| `queue` | text PK, FK `queues(name)` | — |
| `blocked` | bigint NOT NULL DEFAULT 0 CHECK (>= 0) | status-level count |
| `queued` | bigint, same | status-level count (due **and** future — status-based only) |
| `running` | bigint, same | status-level count |
| `enqueued_total` | bigint, same | cumulative inserts |
| `requeued_total` | bigint, same | cumulative running→queued transitions (retry, snooze, release, reap — verb-agnostic by design; triggers cannot see verbs) |
| `succeeded_total` / `failed_total` / `cancelled_total` | bigint, same | cumulative terminal transitions |
| `updated_at` | timestamptz NOT NULL DEFAULT now() | last counter write |

**Deliberate exclusions.** `ready` vs `scheduled` (due-time split) is clock-derived, not status-derived — a trigger cannot maintain it. The tick snapshot keeps computing that split and `oldest_ready_seconds` exactly as today (index-walked, bounded). Counters carry only what row transitions can prove.

### 2.2 Maintenance trigger

`AFTER INSERT OR DELETE OR UPDATE OF status ON taskq.jobs FOR EACH ROW` → single-row upsert-free `UPDATE` of the queue's counter row (decrement old status level, increment new, bump the relevant cumulative). Delete paths (janitor retention) decrement levels only. The trigger is installed by 0022 but **fires only when the `queue_counters` capability is active** (cheap guard read, mirroring the finite-projection activation pattern) so 0022 is metadata-only-safe on live consumers.

**Contention posture.** One counter row per queue is a serialization point on the hot table. The accepted mitigation ladder, decided by L6 evidence, in order: (a) plain single row (baseline — matches the accepted `workflow_member_counts` precedent, which already fires a per-settle trigger); (b) counter row `fillfactor` tuning; (c) N-shard rows per queue summed on read (shard by `hashtext(job_id::text) % N`), shape reserved in this spec but **not** implemented unless L6 shows unacceptable overhead at `full` scale. No other design is on the table.

### 2.3 Derived rates in the snapshot

`refresh_stats_snapshot()` v2: reads levels from `queue_counters` (O(1)), keeps the due-split/oldest-age walk, and stores the previous snapshot's cumulative totals in `control_state` to emit per-queue deltas:

```
rates: { enqueued_per_s, settled_per_s, failed_per_s, window_seconds }
drain_eta_seconds: ready / settled_per_s   (null when settled_per_s = 0)
```

Rates are tick-cadence (default ~5s window), computed from monotonic cumulative counters — no new write path, no event-table aggregation.

### 2.4 `taskq.queue_health`

`taskq.queue_health(p_queue text DEFAULT NULL) RETURNS TABLE (queue text, verdict text, detail jsonb)` — observer-grantable, snapshot-backed (staleness ≤ one tick), one row per queue (or the named queue). Verdict vocabulary, first match wins:

| verdict | rule |
|---|---|
| `paused` | `queues.paused_at IS NOT NULL` |
| `no_consumer` | ready > 0 AND no online worker (presence window, 180s) declares the queue |
| `choking` | `max_depth IS NOT NULL` AND `blocked + queued >= max_depth` |
| `behind` | `drain_eta_seconds > health_lag_seconds` (new optional queue-profile column, default 600) OR `oldest_ready_seconds > 2 × health_lag_seconds` |
| `starved` | ready > 0 AND settled rate > 0 AND `oldest_ready_seconds > 2 × health_lag_seconds` while newer jobs settle (priority starvation signature) |
| `inactive` | all levels zero and no settle activity in the window |
| `ok` | otherwise |

`detail` carries the numbers behind the verdict (levels, rates, ETA, oldest age, online workers). Alert delivery stays consumer-owned; TaskQ computes verdicts only. `taskq.metrics()` gains the rate gauges and a `taskq_health{queue,verdict}` enumeration (additive). CLI: `taskq queue health [-o json]` over the same function.

### 2.5 `max_depth` probe replacement

`enqueue`/`enqueue_many`/workflow admission replace the `OFFSET`-existence probe with `queue_counters.blocked + queued >= max_depth` when the capability is active (falling back to the probe when not). Same advisory semantics, narrower race window, O(1). Behavior-compatible: TQ429 shape unchanged. (The replay-of-existing-key-at-cap finding from L4 is **not** fixed here — check ordering belongs to Wave 2b's enqueue verdict, S4 decision 3 territory.)

## 3. Activation gates (migration 0023)

0023 activates the `queue_counters` capability only after, on **both** PostgreSQL majors:

1. **L6 counter-contention scenario** (loadlab): trigger overhead delta vs baseline on B1/B4-class write paths within the accepted envelope on the calibrated runner (report-only until an envelope is accepted, per harness posture), and counter values exactly equal to ground-truth `GROUP BY` counts after a mixed concurrent workload (claims, settles, retries, cancels, janitor deletes).
2. **Million-row backfill proof**: 0022's backfill of `queue_counters` from a 1M-row `taskq.jobs` fixture completes with a bounded plan (B9 discipline: no unbounded sort, recorded EXPLAIN + duration).
3. Full dual-major suite green; `taskq verify` exact on fresh and upgraded clusters; dump/restore proof per the 0016 precedent.

## 4. Compatibility

- SQL contract 0.4.0; supported-set documents updated per ADR-020. 0022 is inactive-installable on live 0.3.x consumers; nothing changes for them until 0023.
- No claim/settle wire changes. New read surfaces are additive; old `get_queue_stats` callers see a snapshot with extra keys.
- The Python package reads verdicts/rates through existing transports; no worker changes.

## 5. Out of scope (explicit)

Enforcement of any kind (per-queue `max_running`, rate limits, TTL, slow-start ramps, smears, `notify_mode`, breakers) — Wave 2b, blocked on the S4 decision gate. Event-table aggregation. Alert delivery. Per-tenant/key counters.

## 6. Acceptance for this slice

This spec is accepted when the owner signs off on: the counter column set and its deliberate exclusions (SS2.1), the capability-guarded trigger with the single-row-first contention ladder (SS2.2), the snapshot-delta rates design (SS2.3), the verdict vocabulary and rules (SS2.4), the probe replacement (SS2.5), and the activation gates (SS3). Implementation (0022/0023 + L6 + CLI) opens only then.
