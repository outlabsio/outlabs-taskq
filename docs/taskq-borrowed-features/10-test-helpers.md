# 10 — Test Helpers

> **Priority:** SHOULD
> **Provenance:** the Go/Postgres job queue the Go peer's insert-assertion helper / an in-test worker call; the mature Python/Postgres task library an in-memory connector + a connector-swap hook; its inline/manual testing modes inline/drain modes (Peer Research §7.2 S15)
> **Package module:** `taskq.testing` / `taskqtest`
> **The package's own harness** (CI matrix, race/property/crash suites, benchmarks) is a separate design: [`../Task Queue Test & Benchmark Harness.md`](../Task%20Queue%20Test%20%26%20Benchmark%20Harness.md). These helpers are for CONSUMERS' test suites.

---

## 1. Intent

Adoption dies when tests need a full fleet. Ship helpers that make the happy paths one-liners while still allowing real Postgres contract tests in CI.

---

## 2. Helpers (normative)

### 2.1 `require_enqueued`

    from taskq.testing import require_enqueued

    result = await enqueue(...)
    job = await require_enqueued(
        conn,
        job_type="courts.missouri_casenet",
        where={"payload.county": "Boone"},  # optional simple matchers
        unique_skipped=False,               # assert EnqueueResult.status == created
    )
    assert job.idempotency_key.endswith("Boone")

Fails the test (pytest `assert` / `pytest.fail`) if not found within the current transaction/connection view.

### 2.2 `work`

Run a handler against a job **inside a transaction** (or against a fake attempt):

    from taskq.testing import work

    settle = await work(
        conn,
        handler=scrape_missouri,
        payload={"county": "Boone"},
        progress=None,
    )
    assert isinstance(settle, Complete)

Rules:

- Uses a synthetic / inserted job + attempt fence when `conn` is real Postgres.
- May disable uniqueness conflicts for the test insert (the Go/Postgres job queue pattern) via `unique_mode` override.
- Does not require NOTIFY/LISTEN.

### 2.3 `replace_client` / fake contract

    with tq.replace_client(FakeTaskQClient()) as fake:
        await my_api_handler(...)
        fake.assert_enqueued("courts.missouri_casenet", count=1)

`FakeTaskQClient` implements enqueue/claim/settle in memory with **typed results**, not a full SQL emulator. Document clearly: unit-test double, not protocol proof.

### 2.4 Postgres contract marks

    @pytest.mark.taskq_sql
    async def test_claim_fencing(pg): ...

CI runs these against the installer schema.

### 2.5 Inline and drain modes (the Elixir/Postgres job framework borrow)

    from taskq.testing import inline_mode, drain

    # Inline: enqueue executes the handler immediately, records the settle
    async with inline_mode(tq) as recorder:
        await my_api_handler(...)                 # enqueues internally
        assert recorder.settled("courts.missouri_casenet")[0].is_complete

    # Drain: synchronously claim+run queued jobs until the queue is empty
    await tq.enqueue(...)
    report = await drain(tq, queue="courts", max_jobs=100)
    assert report.completed == 1 and report.failed == 0

Rules:

- Both are `taskq.testing`-only — **never a production execution mode** (feature 14's embedded worker is the production in-process story; do not conflate).
- Inline mode still produces typed `EnqueueResult`s and typed settles; followups are recorded, and executed only if `follow=True`.
- `drain` runs through the real claim/settle SQL when given a real connection (integration tests assert end state instead of sleeping), or through the fake client in unit tests.
- `drain` refuses `max_jobs=None` — unbounded drains hide runaway followup loops; the default cap fails the test loudly.

---

## 3. What tests belong where

| Layer | Use |
|---|---|
| Unit (fake client) | Domain handlers, API mount auth injection |
| Helper `work` | Handler result mapping |
| `@taskq_sql` | Uniqueness modes, settle races, notify optional |

---

## 4. Acceptance tests (for the helpers themselves)

1. `require_enqueued` fails when missing; returns job when present.
2. `work` maps `Snooze` without needing a live worker loop.
3. Fake client records typed enqueue statuses.
4. Core `import taskq` works without pytest (helpers import-optional or in `taskq.testing`).

---

## 5. Explicit non-goals

- Full in-memory reimplementation of all PL/pgSQL edge cases
- Browser-based dashboard test harness
- Snapshotting entire `job_events` timelines by default
