# 07 — Dead Letter Lineage and Redrive

> **Priority:** SHOULD — same-queue failed state + redrive are **0.1**; the optional `dead_letter_queue` redirect mode (§2.2) is **0.3 and only if a real need appears** (ADR-009/D-12)
> **Provenance:** the Node/Postgres queue library DLQ source fields + `redrive()`; Unified Spec `failed` as dead-letter set + `taskq.redrive_job`
> **Depends on:** Unified Spec §3 failed status, §11.5 runbook, [05 Queue Profiles](./05-queue-profiles.md)

---

## 1. Intent

Failed/poison jobs are inspectable and redrivable without hand `UPDATE`. When a queue opts into a separate dead-letter queue, **lineage fields** preserve where the work came from so ops can redrive intelligently.

Default taskq posture remains: **`failed` on the original queue IS the DLQ** (Unified Spec). Separate DLQ queues are optional sugar.

---

## 2. Modes

### 2.1 Default (same-queue DLQ)

- Terminal `failed` rows stay on `queue`
- View `taskq.dead_jobs` lists them
- `taskq.redrive_job(id, actor)` → back to `queued`, resets failure counters per Unified Spec

### 2.2 Optional redirect (`queues.dead_letter_queue`)

On terminal failure (retry exhausted, non-retryable, poison):

1. Insert (or move) a row onto `dead_letter_queue` with lineage columns set
2. Original row becomes terminal `failed` **or** is archived per janitor — choose **copy+fail** to keep audit simple:
   - Original: `failed` with outcome
   - DLQ child: new job `queued` or `failed` awaiting ops? **Normative:** DLQ row created as `failed` with `outcome='dead_lettered'` and lineage pointing at source — ops redrive the DLQ row (or source). Simpler alternative: only lineage columns on the original failed row, no redirect.

**Chosen for taskq simplicity:** prefer **lineage columns on the failed row itself**; optional `dead_letter_queue` only if product needs a separate ops inbox. If redirect enabled:

    -- on terminal fail
    enqueue into dead_letter_queue with:
      job_type = 'dead_letter.' || source.job_type
      payload = source.payload
      lineage fields set
      status = 'failed'   -- waiting for redrive, not auto-run

Workers must **not** auto-claim `dead_letter.*` unless an ops worker is subscribed.

---

## 3. Lineage columns (normative)

Add to `taskq.jobs` (nullable):

| Column | Type | Meaning |
|---|---|---|
| `source_queue` | text | Original queue |
| `source_job_id` | uuid | Original job id |
| `source_job_type` | text | Original type |
| `source_created_at` | timestamptz | Original created_at |
| `source_failure_count` | int | failure_count at dead-letter time |
| `source_outcome` | text | outcome at dead-letter time |

For same-queue DLQ mode, these may remain NULL (the row is its own source). For redirect mode, populate on the DLQ row.

Also emit event `dead_lettered` with the same fields.

---

## 4. Redrive API

### 4.1 SQL

    taskq.redrive_job(
      p_job_id uuid,
      p_actor text,
      p_reset_progress boolean DEFAULT false
    ) → EnqueueResult-like / settle-like composite

Behavior (Unified Spec + lineage):

1. Target must be `failed` (or DLQ failed row).
2. If an **active** job already holds the same `(queue, idempotency_key)` → error `TQ409`.
3. Reset: `status=queued`, `failure_count=0`, `expiry_streak=0`, `outcome=NULL`, `finished_at=NULL`, `scheduled_at=now()`, clear `current_attempt_id`.
4. Optionally clear `progress` when `p_reset_progress`.
5. Event `redriven` with actor.
6. If redriving a DLQ redirect row: redrive **onto `source_queue`** with original `job_type` (not `dead_letter.*`), preserving payload; lineage retained for audit.

### 4.2 Python / CLI / HTTP

    await tq.redrive(job_id, actor="operator:andi", reset_progress=False)
    # CLI: taskq redrive <job_id> --actor operator:andi
    # HTTP: POST /taskq/jobs/{id}/redrive

Bulk:

    await tq.redrive_many(job_ids, actor=..., limit=100)

---

## 5. Ops views

    taskq.dead_jobs          -- already in Unified Spec
    taskq.dead_jobs_lineage  -- dead_jobs + lineage columns for redirect mode

Stats: count failed by `outcome` (`poison`, `retry_exhausted`, `non_retryable`) — never by free-text `error`.

---

## 6. Acceptance tests

1. Exhaust retries → row `failed`, appears in `dead_jobs`, redrive → `queued` with budgets reset.
2. Redrive while duplicate active key exists → `TQ409`.
3. Poison quarantine terminal → redrivable.
4. Redirect mode (if enabled): lineage fields populated; redrive lands on source queue/type.
5. Janitor does not DROP failed rows before `failed_retention_hours`.

---

## 7. Explicit non-goals

- Automatic infinite redrive
- Separate broker-style DLQ topic infrastructure
- Redrive of `cancelled` (use explicit re-enqueue with new key)
- Silent deletion of failed rows
