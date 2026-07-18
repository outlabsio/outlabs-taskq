# 09 — Retry Value Surface

> **Priority:** SHOULD
> **Provenance:** the mature Python/Postgres task library `RetryValue = bool | int | BaseRetryStrategy` + `RetryDecision`
> **Depends on:** [05 Queue Profiles](./05-queue-profiles.md); Unified Spec retry stamped at enqueue

---

## 1. Intent

Make common retry configuration **one token** at task definition time, while still allowing full strategies. Whatever is chosen is compiled into **row-stamped policy** at enqueue — never looked up from a live registry during settle (Unified Spec F7 kill).

---

## 2. Type

    RetryValue = bool | int | RetryStrategy

| Value | Meaning |
|---|---|
| `False` | No retries → `max_attempts = 1` (only the first try) |
| `True` | Use queue profile defaults for max/backoff |
| `int` N | `max_attempts = N` (N includes the first try); backoff from queue profile |
| `RetryStrategy(...)` | Explicit max + backoff + exception filters |

    class RetryStrategy(BaseModel):
        max_attempts: int | None = None          # None → queue default
        mode: Literal["exponential", "fixed"] = "exponential"
        base_seconds: float = 30
        cap_seconds: float = 3600
        # jitter is fixed ±15% in 0.1 (no stamped ratio field — R2-19)
        retry_exceptions: tuple[type[BaseException], ...] | None = None
        # if set: only these types are retryable; others → NonRetryable

### 2.1 Decorator usage

    @bp.task(queue="courts", retry=5)
    async def scrape(...): ...

    @bp.task(retry=RetryStrategy(max_attempts=8, base_seconds=60, cap_seconds=7200))
    async def enrich(...): ...

---

## 3. Compile-at-enqueue algorithm

1. Resolve `RetryValue` → concrete policy fields.
2. Merge with queue profile (`coalesce` strategy fields).
3. Merge with per-enqueue overrides.
4. Stamp onto job row: `max_attempts`, `retry_mode`, `retry_base_seconds`, `retry_cap_seconds`, `retry_jitter_ratio`.
5. Exception filters **cannot** live in SQL — they are worker-runtime only:
   - On unhandled exception: if `retry_exceptions` set and type not matched → treat as `NonRetryable`
   - Else → `Retry` / fail_job retryable

---

## 4. Runtime RetryDecision (optional advanced)

For handlers that need dynamic retry remapping (the mature Python/Postgres task library RetryDecision):

    class RetryDecision(BaseModel):
        after: timedelta | datetime | None = None
        queue: str | None = None          # optional move (use sparingly)
        priority: int | None = None
        lock_or_concurrency_key: str | None = None

Prefer returning `Retry(after=...)` from feature 03. Full RetryDecision remaps are **phase-2**; not required for first cut.

---

## 5. Acceptance tests

1. `retry=False` → max_attempts stamped 1; first failure terminals.
2. `retry=5` → max_attempts 5; backoff from queue profile.
3. `retry=True` → stamps equal queue defaults.
4. Strategy with `retry_exceptions=(TimeoutError,)` → `ValueError` becomes non-retryable.
5. After enqueue, deleting the Python task registration still allows fail/retry using **row stamps** only.

---

## 6. Explicit non-goals

- broker-framework-style global autodiscovery retry middleware
- Changing stamped policy mid-flight via registry edit
- Infinite retries without an explicit huge `max_attempts` / ops decision
