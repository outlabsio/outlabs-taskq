# 06 — NOTIFY Nudge and Poll

> **Priority:** SHOULD
> **Provenance:** the Node/Postgres SQL-first worker insert nudge; the Node/Postgres queue library “NOTIFY is a hint”; the mature Python/Postgres task library listen + poll intervals; Unified Spec §14 LISTEN
> **Depends on:** Worker runtime, insert-only vs worker modes

---

## 1. Intent

**Polling is correctness. NOTIFY is latency.**

Missed notifications must never lose jobs. Future-dated / throttled / backoff jobs must not rely on notify.

---

## 2. Normative rules

1. Every worker (DB-direct) polls at `poll_interval` (default 5s) regardless of LISTEN state.
2. When `listen=True`, open **one dedicated non-pooled connection** and `LISTEN` on queue channels.
3. On notification, set an in-process event / nudge counter so the claim loop wakes early.
4. Notification payload is a **hint** (`{"count": N}` or empty) — never job bodies, never credentials.
5. Emit NOTIFY from SQL **only when** at least one inserted/updated row is immediately runnable (`status` claimable and `scheduled_at <= now()`).
6. Behind transaction poolers that break LISTEN: degrade silently to poll (`listen` effective false); log once.
7. HTTP workers do not LISTEN; facade may long-poll using NOTIFY internally (Unified Spec §14).

---

## 3. Channels

| Channel | Purpose |
|---|---|
| `taskq_<queue>` | Per-queue insert/replace wake |
| `taskq_migrate` | Breaking schema (feature 12) |
| `taskq_abort` (optional) | Cancel/abort nudge; else abort polling interval |

Queue names sanitized to `[a-z0-9_]+` for channel safety; reject otherwise at ensure_queue.

---

## 4. Worker loop sketch (normative behavior)

    loop:
      claimed = claim(batch)
      if claimed:
        dispatch(claimed)
        continue  # keep claiming while capacity
      wait_any(notify_event, sleep(poll_interval), stop_event)
      clear notify_event

Abort/cancel polling (the mature Python/Postgres task library pattern): while any job running, also poll cancel flags every `abort_poll_interval` (default 5s) even if notify missed.

---

## 5. SQL emit points

- `taskq.enqueue` / bulk / replace that leaves a ready row
- `taskq.redrive_job` when resulting row is ready
- `taskq.resume_queue` (optional wake)
- NOT on: future `scheduled_at`, snooze into future, pause

Use `pg_notify` inside the same transaction as the write.

---

## 6. Acceptance tests

1. Kill LISTEN connection mid-run → jobs still claimed within ≤ `poll_interval` + epsilon.
2. Enqueue with `scheduled_at = now()+1h` → no notify required; becomes claimable via poll after time.
3. Burst enqueue 100 ready jobs with listen on → workers drain without waiting full poll each time.
4. Transaction-pooling mode with listen disabled → only poll; no crash.

---

## 7. Explicit non-goals

- Exactly-once notify delivery
- Putting payload in NOTIFY
- Replacing the claim index with notify-driven job ids
- the Node/Postgres SQL-first worker localQueue prefetch buffers
