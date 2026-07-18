# 11 — Soft Stop and Shutdown

> **Priority:** SHOULD
> **Provenance:** the Go/Postgres job queue soft-stop timeout + Stopped; the mature Python/Postgres task library graceful abort; Unified Spec §14 worker-loop guarantees
> **Depends on:** [03 Handler Settle Results](./03-handler-settle-results.md), [06 Notify Nudge](./06-notify-nudge-and-poll.md)

---

## 1. Intent

Define one shutdown contract so Diverse and QDarte workers behave identically under SIGTERM, deploy drains, and `request_worker_shutdown`.

---

## 2. Phases (normative)

| Phase | Behavior |
|---|---|
| **0 Running** | Claiming + heartbeating |
| **1 Soft stop** | Stop claiming new jobs; in-flight continue; cooperative cancel signals may be set |
| **2 Grace deadline** | After `soft_stop_timeout`, hard-cancel handler tasks (`asyncio.CancelledError` / thread interrupt policy) |
| **3 Settle** | For each in-flight job: `release_job(p_cause='worker_shutdown', p_progress=...)` unless already settled |
| **4 Stopped** | Heartbeats done; LISTEN closed; `stopped` event set; process may exit |

If `soft_stop_timeout is None`: wait indefinitely in phase 1 until in-flight complete (the Go/Postgres job queue default).

---

## 3. Triggers

- SIGTERM / SIGINT (CLI workers)
- `taskq.request_worker_shutdown` presence row / control flag (Unified Spec)
- HTTP facade draining its housekeeper on process exit (no worker claim drain needed)

---

## 4. API

    class WorkerOptions(BaseModel):
        soft_stop_timeout: timedelta | None = None  # None = wait forever
        cancel_grace_seconds: float = 30.0          # cooperative then hard cancel

    await tq.run(stop_signal=signal_event)
    await tq.stopped.wait()

    # Programmatic
    await tq.stop()              # soft stop
    await tq.stop(cancel=True)   # skip wait; enter phase 2 immediately

### 4.1 In-flight job policy

| Situation | Action |
|---|---|
| Handler returns normally during soft stop | `Complete` / etc. as usual |
| Handler checkpoints then returns `Snooze(0)` | Allowed; budget-safe park |
| Grace exceeded | Cancel task → `release_job(worker_shutdown)` with last progress |
| Settle returns `already_settled` | Success |
| Settle returns `lost` | Loud error; process still exits after logging attempt id |

---

## 5. Interaction with operator cancel

Shutdown is not cancel: outcome `worker_shutdown` via **release**, not `cancelled`. Jobs re-enter `queued` after short delay (Unified Spec release delay) so another worker can take them.

---

## 6. Acceptance tests

1. Soft stop with timeout None: in-flight finishes Complete; no release.
2. Soft stop with 0.1s timeout + slow handler: release with `worker_shutdown`; failure_count unchanged.
3. Double SIGTERM: second is hard cancel path; no crash.
4. `stopped` awaits only after all settles attempted.

---

## 7. Explicit non-goals

- Killing sibling processes via `ps` inspection
- Checkpoint durability beyond existing `progress` column
- Coordinated cluster quorum shutdown
