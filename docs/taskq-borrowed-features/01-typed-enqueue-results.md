# 01 — Typed Enqueue Results

> **Priority:** MUST
> **Staging (ADR-009):** 0.1 ships `created` / `existed` (+ typed caller-error rejects); `replaced` / `skipped_locked` arrive with the replace modes (feature 02, 0.2+). The closed set below is the destination vocabulary.
> **Provenance:** Negative lesson from the Node/Postgres queue library (`send()` → `null`) and the Node/Postgres SQL-first worker (contention race → `null`); positive alignment with Unified Spec “truthful `created` reporting”.
> **Depends on:** Unified Spec §5.2 `taskq.enqueue`
> **Enables:** [02 Job Key Modes](./02-job-key-and-uniqueness-modes.md)

---

## 1. Intent

Every enqueue path — single, bulk, cron fire, settle followup, redrive child — returns a **structured outcome**. Callers never infer success from a missing id. Silent drop is a design bug.

---

## 2. Normative outcome type

### 2.1 Status enum (closed set)

| Status | Meaning | `job_id` | Typical cause |
|---|---|---|---|
| `created` | New row inserted | new id | Fresh enqueue |
| `existed` | Active row already held the idempotency/job key; insert skipped | existing id | Default reject mode conflict |
| `replaced` | Existing unlocked active row was overwritten | existing id (same key) | `replace` / `preserve_run_at` mode |
| `skipped_locked` | Active row exists and is `running` (locked); caller asked to replace but could not | existing id | Replace attempted while claimed |
| `conflict` | Could not complete the requested mode; caller must decide | may be null only if no row readable | Rare: depth gate, unknown queue, hard CHECK failure mapped to exception instead when caller-error |

**Rule:** `conflict` is for *queue-semantic* outcomes that are not caller-errors. Caller errors (unknown queue, bad payload shape, depth exceeded) remain exceptions / SQLERRM codes (`TQ001`, `TQ429`, …) — they do **not** become `status=conflict` with a null id pretending to be success.

### 2.2 Result shape (SQL composite + Pydantic)

    EnqueueResult:
      status: 'created' | 'existed' | 'replaced' | 'skipped_locked' | 'conflict'
      job_id: uuid | null          # null ONLY for conflict when no job exists to point at
      created: bool                # true iff status == 'created' (compat alias)
      queue: text
      job_type: text
      idempotency_key: text | null
      scheduled_at: timestamptz | null

Bulk enqueue returns `list[EnqueueResult]` in input order (or a parallel `created` / `existing` split **plus** per-item statuses — per-item list is normative).

---

## 3. SQL contract

### 3.1 Function return

Extend / redefine `taskq.enqueue(...)` (and bulk variant) to return the composite above, not `(uuid, boolean)` alone.

Minimum compatibility shim during migration:

    -- deprecated shim
    SELECT (result).job_id, (result).created FROM taskq.enqueue(...) AS result;

New clients MUST read `status`.

### 3.2 Truthfulness rules

1. `ON CONFLICT DO NOTHING` under default reject mode → `existed` with the **existing** job id (lookup after conflict; never invent a fake id).
2. Replace modes that update a row → `replaced` with that row’s id.
3. Replace modes that find a `running` row and refuse to mutate → `skipped_locked` with that row’s id (do **not** clear the key and insert a second active job unless mode explicitly documents otherwise — see feature 02).
4. A successful insert after a conflict-retry loop (key freed mid-flight) → `created`. Never return “null, false” for that case.
5. Settle-path followups (`p_internal = true`) use the same result type; callers in `complete_job` may ignore per-item results after asserting no unexpected `conflict`.

### 3.3 Forbidden

- Returning SQL `NULL` as the sole success indicator
- Swallowing uniqueness conflicts without a status
- Mapping `skipped_locked` to `existed` (different ops meaning)

---

## 4. Python client contract

    class EnqueueStatus(StrEnum):
        CREATED = "created"
        EXISTED = "existed"
        REPLACED = "replaced"
        SKIPPED_LOCKED = "skipped_locked"
        CONFLICT = "conflict"

    class EnqueueResult(BaseModel):
        status: EnqueueStatus
        job_id: UUID | None
        created: bool
        queue: str
        job_type: str
        idempotency_key: str | None = None
        scheduled_at: datetime | None = None

        @property
        def ok(self) -> bool:
            """True when work is represented in the queue (created/existed/replaced)."""
            return self.status in {
                EnqueueStatus.CREATED,
                EnqueueStatus.EXISTED,
                EnqueueStatus.REPLACED,
            }

    async def enqueue(...) -> EnqueueResult: ...
    async def enqueue_many(...) -> list[EnqueueResult]: ...

HTTP facade maps:

| Status | HTTP |
|---|---|
| `created` | 201 |
| `existed` / `replaced` | 200 |
| `skipped_locked` | 409 with body `{status, job_id, ...}` |
| `conflict` | 409 (or 422 if validation-like) |
| caller errors | 4xx from exception mapping |

---

## 5. Edge cases

| Case | Expected |
|---|---|
| Concurrent double-enqueue same key, reject mode | Exactly one `created`, others `existed` |
| Key present only in archive / terminal row | New `created` allowed (active-set uniqueness only) |
| Bulk of 100 with 3 duplicates | 97 `created`, 3 `existed` (or replaced per mode) — no null slots |
| Network retry of identical enqueue | Second call returns `existed` (or `replaced` if mode overwrote) — safe |

---

## 6. Acceptance tests

1. Concurrent 50× same idempotency key → exactly one `created`, 49 `existed`, zero null ids among those.
2. Client never receives Python `None` from `enqueue()` — always `EnqueueResult`.
3. Facade: ignoring body and checking only “2xx” still cannot mistake `skipped_locked` for insert success if clients check `status` (document as required).
4. Followup enqueue inside complete: results recorded in events or discarded only after status ∈ {created, existed}.

---

## 7. Explicit non-goals

- Making duplicates raise by default (status is enough; optional `strict=True` may raise on `existed` later)
- Multi-backend enqueue semantics
- Returning full job row on every enqueue (id + status is enough; optional `include_job=True` later)
