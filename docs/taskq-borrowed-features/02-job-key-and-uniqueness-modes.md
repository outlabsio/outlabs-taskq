# 02 — Job Key and Uniqueness Modes

> **Priority:** MUST for `reject` (0.1); `replace` / `preserve_run_at` / `by_args` are **0.2+ capabilities** (ADR-009/D-12 — each lands only with complete mutation/dependency transition rules; this doc remains their design)
> **Provenance:** the Node/Postgres SQL-first worker `job_key` / `job_key_mode`; the Go/Postgres job queue declarative unique-options + a unique-skipped-as-duplicate flag
> **Depends on:** [01 Typed Enqueue Results](./01-typed-enqueue-results.md), Unified Spec `jobs_idem_uq`
> **Related but distinct:** `concurrency_key` (runtime admission) — never conflate

---

## 1. Intent

Give producers a **small, named vocabulary** for “what should happen if this logical job already exists,” instead of inventing ad-hoc SELECT-then-INSERT or silent skips.

Identity for enqueue uniqueness remains `(queue, idempotency_key)` on the **active** status set (`blocked|queued|running`), as in the Unified Spec. This feature adds **modes** that control conflict behavior and optional **payload-derived keys**.

---

## 2. Vocabulary

### 2.1 Fields

| Field | Role |
|---|---|
| `idempotency_key` | Canonical uniqueness string (≤255). SQL index target. |
| `job_key` | DX alias for `idempotency_key` in Python/HTTP (same column). |
| `unique_mode` | Enum controlling conflict behavior (below). |
| `concurrency_key` | **Not** a uniqueness mode. Caps parallel runners for a resource. |

### 2.2 Modes (closed set)

| Mode | When active key exists | Payload | `scheduled_at` | Result status |
|---|---|---|---|---|
| `reject` | Do not insert; return existing | unchanged | unchanged | `existed` |
| `replace` | If not `running`: overwrite payload, priority, schedule, policy stamps, progress=NULL (or keep progress only if explicitly opted — default clear). If `running`: do not steal | new | new (caller’s) | `replaced` or `skipped_locked` |
| `preserve_run_at` | Like `replace`, but keep existing `scheduled_at` when existing job has never started (`failure_count=0` and never left `queued`/`blocked` — see §4) | new | **keep existing** | `replaced` or `skipped_locked` |

Default mode: **`reject`** (matches today’s `ON CONFLICT DO NOTHING` semantics, but with typed `existed`).

### 2.3 Forbidden mode

Do **not** ship the Node/Postgres SQL-first worker’s `unsafe_dedupe` (skip even when locked/failed). If a producer needs “ignore everything,” they can check statuses themselves. Silent skip of failed/locked work is banned.

---

## 3. Payload-derived uniqueness (`by_args`)

Optional helper for peer-style declarative uniqueness (the Go/Postgres job queue's pattern). Purely a **client-side key derivation** unless/until a SQL expression index is added.

### 3.1 Python declaration

    class ReconcileAccount(BaseModel):
        account_id: int = Field(json_schema_extra={"taskq_unique": True})
        trace_id: str  # not unique-participating

        @classmethod
        def unique_opts(cls) -> declarative unique-options:
            return declarative unique-options(
                by_args=True,
                by_period=timedelta(hours=24),  # optional window bucket
                mode="reject",
            )

### 3.2 Key derivation algorithm (normative)

1. Select fields marked `taskq_unique: True` (or explicit `unique_fields=[...]`).
2. Canonical JSON: UTF-8, sorted keys, no insignificant whitespace, numbers as JSON numbers.
3. `digest = sha256(canonical).hexdigest()[:32]`
4. If `by_period` set: `bucket = floor(utcnow / period)` using **DB `now()`** when enqueue goes through SQL with `p_unique_period_seconds`; client may precompute for display only.
5. `idempotency_key = f"{job_type}:args:{digest}"` or `f"{job_type}:args:{digest}:{bucket}"`

SQL still only sees the final `idempotency_key` string + `unique_mode`.

---

## 4. SQL behavior detail

### 4.1 `reject` (default)

    INSERT ... ON CONFLICT DO NOTHING
    -- if no row inserted: SELECT existing active id → status existed

### 4.2 `replace`

Within one transaction:

1. `SELECT ... WHERE queue=$q AND idempotency_key=$k AND status IN active FOR UPDATE`
2. If none → insert → `created`
3. If `status = 'running'` → `skipped_locked` (do **not** clear key; do **not** insert second active job)
4. Else update allowed columns:
   - `payload`, `priority`, `scheduled_at`, retry policy columns, `concurrency_key`, `affinity_key`
   - `progress = NULL` (default; avoids mixing cursors across logical jobs)
   - `job_type` **immutable** if set (changing type under same key is `TQ` caller error)
5. Emit event `job_replaced`
6. Return `replaced`

### 4.3 `preserve_run_at`

Same as `replace`, except:

- If existing row is `queued` or `blocked` and has never been claimed (`failure_count = 0` and no attempts ledger rows, or `started_at IS NULL` historically — use: **no rows in `job_attempts` for this job_id**), keep `scheduled_at`.
- If the job has been attempted before, prefer caller’s new `scheduled_at` (document: preserve only for never-started debounce/throttle windows).

### 4.4 Running-job policy (normative choice)

**Chosen:** `skipped_locked` — never steal a running job’s identity.

Rationale: stealing under CAS fences creates two writers; the Node/Postgres SQL-first worker’s “clear key + insert new” creates duplicate logical work. taskq prefers an honest 409-shaped result.

---

## 5. Python / HTTP API

    class UniqueMode(StrEnum):
        REJECT = "reject"
        REPLACE = "replace"
        PRESERVE_RUN_AT = "preserve_run_at"

    class declarative unique-options(BaseModel):
        mode: UniqueMode = UniqueMode.REJECT
        by_args: bool = False
        by_period: timedelta | None = None
        unique_fields: list[str] | None = None

    await tq.enqueue(
        task_ref,
        payload,
        idempotency_key="courts:Boone:2026-07-18",  # or job_key=
        unique=declarative unique-options(mode=UniqueMode.REPLACE),
    )

HTTP:

    POST /taskq/enqueue
    {
      "queue": "courts",
      "job_type": "missouri_casenet",
      "payload": {...},
      "idempotency_key": "...",
      "unique_mode": "replace"
    }

---

## 6. Interaction matrix

| Feature | Interaction |
|---|---|
| Followups (`chain:{job_id}:{step}`) | Always `reject` mode; derived keys |
| Cron children | Default `reject` with schedule-specific keys |
| Redrive | Resets terminal → queued; if a *new* active job holds the same key → SQL `TQ409` (existing Unified Spec) |
| `concurrency_key` | Independent; can differ across replace updates |
| Workflows / depends_on | Replace must not orphan deps: if job has dependents, replace updates payload only; dep edges stay |

---

## 7. Acceptance tests

1. `reject`: two enqueues → `created` then `existed`; one active row.
2. `replace` on `queued`: payload and schedule update; same `job_id`; status `replaced`.
3. `replace` on `running`: status `skipped_locked`; payload unchanged; still one active row.
4. `preserve_run_at` on never-started job: payload updates, `scheduled_at` unchanged.
5. `by_args`: two payloads equal on unique fields, different `trace_id` → second `existed`.
6. High contention 100× `replace` on same key: never two active rows; never Python `None`.

---

## 8. Explicit non-goals

- `unsafe_dedupe`
- Uniqueness across queues (keys are per-queue)
- Content-addressed bodies without an explicit key/mode
- Advisory-lock uniqueness fallbacks
