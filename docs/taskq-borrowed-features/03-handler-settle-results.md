# 03 — Handler Settle Results

> **Priority:** MUST
> **Provenance:** the Go/Postgres job queue a snooze job-return value / a cancel job-return value / work return semantics; mapped onto Unified Spec settle functions + typed settle results
> **Depends on:** Unified Spec §5 complete/fail/release/snooze/cancel; §14 worker-loop guarantees
> **Does not change:** CAS fencing, attempt ledger, poison quarantine

---

## 1. Intent

Handlers express intent by **returning a typed result** (or raising a small set of control exceptions). The worker runtime maps that intent onto SQL settle functions. Handlers never call `complete_job` / `fail_job` directly.

This is the Go/Postgres job queue’s DX with taskq’s correctness: control flow is data; settle races stay typed (`ok | already_settled | lost | …`).

---

## 2. Result types (closed set)

    class Complete(BaseModel):
        result: dict[str, Any] = Field(default_factory=dict)
        followups: list[Enqueue] = Field(default_factory=list)
        # followups executed inside complete_job transaction, lossless-atomic per
        # ADR-007 (validate → TQ422 reject → terminal-fail escape; never truncated).
        # Staging (ADR-009): field reserved in 0.1 — the 0.1 worker/contract reject
        # non-empty followups clearly; the capability activates with 0.2.

    class Snooze(BaseModel):
        delay: timedelta           # 0 => immediately claimable (scheduled_at = now())
        progress: dict | None = None
        reason: str | None = None  # free text → error/events, not outcome taxonomy abuse

    class Cancel(BaseModel):
        reason: str
        # terminal cancelled; does not burn failure_count

    class Retry(BaseModel):
        after: timedelta | datetime | None = None  # hint → retry_after_seconds
        error: str | None = None
        progress: dict | None = None
        # burns failure_count via fail_job(retryable=True)

    class NonRetryable(BaseModel):
        error: str
        progress: dict | None = None
        # fail_job(retryable=False)

Also accepted for ergonomics:

| Handler action | Normalized to |
|---|---|
| `return None` / `return {}` / `return {"ok": true}` | `Complete(result=...)` |
| `raise Retry(...)` or `raise RetryError` | `Retry` |
| `raise NonRetryable(...)` | `NonRetryable` |
| `raise CancelledError` / cooperative abort after cancel request | worker issues cancel/release per §11 — not handler `Cancel` unless handler returns it |
| unhandled exception | `Retry(error=repr(exc))` with retryable=True |

**Prefer return values over exceptions** for Snooze/Cancel/Complete. Exceptions remain for unexpected failures and optional Retry/NonRetryable sugar.

---

## 3. Mapping to SQL (normative)

| Handler result | SQL call | Budget (`failure_count`) | Attempt outcome |
|---|---|---|---|
| `Complete` | `taskq.complete_job(..., p_followups=...)` | unchanged | `success` |
| `Snooze` | `taskq.snooze_job(..., p_delay_seconds=..., p_progress=...)` | **0 — never consumed** | `snoozed` |
| `Cancel` | **`taskq.cancel_running_job(job_id, attempt_id, worker_id, reason)`** — the fenced worker-side cancel (ADR-007): matching running attempt only, replay → `already_settled`, stale fence → `lost`. Never the operator `cancel_job` | **0** | `canceled` |
| `Retry` | `taskq.fail_job(..., retryable=true, retry_after_seconds=...)` | +1 (subject to policy) | `retry_scheduled` or `retry_exhausted`→`failed` |
| `NonRetryable` | `taskq.fail_job(..., retryable=false)` | terminal fail path | `non_retryable` |

### 3.1 Cancel from handler vs operator cancel

- **Handler `Cancel`:** worker holds the attempt fence and settles to `cancelled` via `cancel_running_job` (job will not run again). The operator `cancel_job` is never called by the worker result mapper (ADR-007 resolved this doc's earlier hedge).
- **Operator cancel while running:** sets `cancel_requested_at`; heartbeat surfaces it; handler should return promptly; worker hard-cancels after grace (feature 11). Pending cancel is honored even if handler returns `Snooze`/`Retry` (Unified Spec §5.7).

### 3.2 `Snooze(0)`

Means: release back to `queued` with `scheduled_at = now()` without burning budget. Used for:

- Soft interrupt / yield
- “Come back immediately but let other jobs interleave”
- Shutdown-friendly park when work is safely checkpointed

---

## 4. Worker runtime obligations

1. Translate result → settle call with `(job_id, attempt_id, worker_id)`.
2. On settle transport errors: retry settle; `already_settled` = success; `lost` = ERROR (never re-report domain results elsewhere).
3. Cap followups (Unified Spec: 20/settle).
4. Normalize `Retry.after` client-side to integer seconds before SQL.
5. Never let handler code open a second settle path.

---

## 5. JobContext helpers

    class JobContext(Protocol):
        job_id: UUID
        attempt_id: UUID
        worker_id: str
        queue: str
        job_type: str
        payload: dict
        progress: dict | None
        cancel_requested: bool

        async def checkpoint(self, progress: dict) -> None: ...
        def raise_if_cancelled(self) -> None: ...
        def should_cancel(self) -> bool: ...

`checkpoint` batches onto heartbeat (Unified Spec §5.4) — not a settle.

---

## 6. Example

    @tq.task(queue="courts", job_type="missouri_casenet")
    async def scrape(ctx: JobContext, payload: dict) -> Complete | Snooze | Cancel:
        ctx.raise_if_cancelled()
        if quota_exceeded():
            return Snooze(delay=timedelta(hours=1), reason="provider_quota")
        if payload.get("case_dismissed"):
            return Cancel(reason="case_dismissed")
        data = await do_scrape(payload, progress=ctx.progress)
        await store_results(data)  # domain, idempotent
        return Complete(
            result={"n": len(data)},
            followups=[Enqueue("enrich_county", {"county": payload["county"]}, step="enrich")],
        )

---

## 7. Acceptance tests

1. `Snooze(3600)` → job `queued`, `scheduled_at ≈ now()+1h`, `failure_count` unchanged, outcome/event `snoozed`.
2. `Snooze(0)` → immediately claimable; budget unchanged.
3. `Cancel` → `cancelled`, not `failed`; not in failure stats.
4. `Retry(after=30)` → `failure_count+1`, future `scheduled_at`, or terminal if budget exhausted.
5. Duplicate settle after network retry → `already_settled`, handler side effects not double-applied by runtime (domain still must be idempotent).
6. Operator cancel + handler returns `Snooze` → ends `cancelled`, not snoozed.

---

## 8. Explicit non-goals

- Go-style “return error to snooze” as the primary API
- Business side effects inside `complete_job`
- Allowing handlers to pass raw SQL settle dictionaries
- A `partial` status (progress checkpoint only)
