# 05 — Queue Profiles

> **Priority:** SHOULD
> **Provenance:** the Node/Postgres queue library “queue as config unit”; aligns with Unified Spec `taskq.queues`
> **Depends on:** Unified Spec queue table + policy stamped at enqueue

---

## 1. Intent

**Rich defaults live on the queue row. Worker processes expose almost no knobs.**

Producers may override per job; omitted fields inherit from `taskq.queues`. This is how taskq stays configurable without a 40-flag worker CLI.

---

## 2. Queue profile fields (normative)

Stored on `taskq.queues` (extend Unified Spec columns as needed):

| Column | Purpose | Default suggestion |
|---|---|---|
| `name` | Queue id | — |
| `paused_at` | Pause claims | null |
| `max_attempts` | Default retry budget | 5 |
| `lease_seconds` | Default lease | 900 |
| `retry_mode` | `exponential` \| `fixed` | exponential |
| `retry_base_seconds` | Backoff base | 30 |
| `retry_cap_seconds` | Backoff cap | 3600 |
| (retry jitter is FIXED ±15% in 0.1 — the SQL stamps no jitter column; a real stamped ratio is a possible 0.2+ addition, never a phantom field — R2-19) | | |
| `default_priority` | 0–1000, lower wins | 100 |
| `heartbeat_seconds` | Hint for workers | min(lease/3, 30) |
| `failed_retention_hours` | Hot failed retention | 336 (14d) |
| `terminal_retention_hours` | succeeded/cancelled | 48 |
| `depth_limit` | Optional enqueue gate | null |
| `dead_letter_queue` | Optional redirect name (feature 07 — **0.3 conditional**, absent from the 0.1 model/migration) | null (= stay on same queue as `failed`) |

Workers do **not** re-read these mid-flight for retries — values are **stamped onto the job at enqueue** (Unified Spec anti-KeyError rule). Changing a queue profile affects **future** enqueues only.

---

## 3. Inheritance algorithm

At enqueue:

1. Load queue profile (must exist — unknown queue → `TQ001`).
2. For each policy field: `coalesce(p_override, queue.default)`.
3. Stamp onto `taskq.jobs` row.
4. Return typed `EnqueueResult` (feature 01).

CLI / Python:

    await tq.ensure_queue(
        "courts",
        max_attempts=5,
        lease_seconds=900,
        retry_base_seconds=30,
        dead_letter_queue=None,
    )

---

## 4. Worker knobs (closed set — keep tiny)

Only these belong on the worker process:

| Knob | Meaning | Default |
|---|---|---|
| `queues` | Subscription list | required |
| `concurrency` | In-process parallel jobs | 1 |
| `batch` | Claim batch size | 1 |
| `poll_interval` | Fetch polling seconds | 5 |
| `listen` | Enable NOTIFY nudge | True |
| `soft_stop_timeout` | Drain grace | None (wait) |
| `worker_id` | Stable identity | required |

Everything else → queue profile or job overrides.

---

## 5. Acceptance tests

1. Enqueue with no policy overrides → job row matches queue defaults.
2. Per-job `max_attempts=2` overrides queue default 5; stamped value is 2.
3. Alter queue default after enqueue → in-flight job keeps old stamp; new enqueue gets new default.
4. Worker CLI help lists ≤10 runtime flags.

---

## 6. Explicit non-goals

- the Node/Postgres queue library multi-policy matrix (`stately`, `short`, `exclusive`, …)
- Live mutation of retry policy for running jobs
- Per-worker retry registries
