# 04 — Insert-Only Client

> **Priority:** SHOULD
> **Provenance:** the Go/Postgres job queue insert-only clients (omit Queues / skip Start)
> **Depends on:** Package client surface from Extraction Design Brief

---

## 1. Intent

One package, one `TaskQ` type. API processes and producers enqueue without starting a claim loop, housekeeper, or LISTEN connection. Workers opt into execution by supplying queue subscriptions and calling `run()`.

---

## 2. Normative construction

    # Insert-only — valid and complete for producers
    tq = TaskQ(dsn=settings.dsn, handlers=registry)  # handlers optional but recommended

    await tq.enqueue(...)
    await tq.enqueue_many(...)
    await tq.create_workflow(...)
    # NO claim / heartbeat / tick unless explicitly called

    # Worker — opt-in
    tq = TaskQ(
        dsn=settings.dsn,
        handlers=registry,
        queues=["courts"],                 # required to run
        worker_id="studio:courts:1",
        worker_defaults=WorkerOptions(...),
    )
    await tq.run()                         # blocks until shutdown

### 2.1 Rules

1. Omitting `queues` (or passing `queues=None`) **must not** start background tasks on construction.
2. Calling `run()` without queues raises `TaskqConfigError`.
3. Insert-only clients MAY call one-shot ops (`tick`, `janitor`) explicitly — they do not auto-schedule them.
4. HTTP facade host uses insert+SQL client internally; workers use HTTP client insert/claim — both are “insert-capable”; only processes that `run()` or host housekeeper tick.

---

## 3. Kind / job_type validation

Optional strictness (the Go/Postgres job queue a skip-unknown-job check flag inverted default):

| Setting | Default | Behavior |
|---|---|---|
| `validate_job_types=True` | True when `handlers` provided | Unknown `job_type` on enqueue → error |
| `validate_job_types=False` | — | Allow enqueue for types handled by another process |

Insert-only API servers that enqueue types worked elsewhere should set `validate_job_types=False` **or** register stub handler metadata (name + policy defaults) without callable.

---

## 4. Connection / pool expectations

| Client mode | Pool | LISTEN conn |
|---|---|---|
| Insert-only | Small (1–N) for enqueue | None |
| Worker | Pool for claim/settle + **dedicated** LISTEN conn if `listen=True` | Yes when listening |
| Facade host | App pool + housekeeper | Optional notify for long-poll |

---

## 5. Acceptance tests

1. Construct without queues → no tasks scheduled; enqueue works.
2. `run()` without queues → raises.
3. `validate_job_types=True` + unknown type → enqueue error before SQL.
4. Worker with queues claims jobs; insert-only process in parallel only enqueues.

---

## 6. Explicit non-goals

- Separate `taskq-producer` / `taskq-worker` packages
- Auto-starting housekeeper on import
- Requiring handlers for pure SQL enqueue of fully-specified rows (payload+policy stamped)
