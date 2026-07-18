# taskq — Borrowed Features Specification Set

> **Status:** Normative feature specs — 2026-07-18
> **Audience:** Implementers of the `taskq` Python package
> **Rule:** These documents are self-contained. You should not need to re-open any peer project's source to build these features.
>
> Peer provenance lives only in [`../Task Queue Peer Patterns Research.md`](../Task%20Queue%20Peer%20Patterns%20Research.md).
> Protocol correctness (CAS, statuses, poison, concurrency admission) lives in [`../Task Queue — Unified Design Spec.md`](../Task%20Queue%20%E2%80%94%20Unified%20Design%20Spec.md).
> Package/auth boundaries live in [`../Task Queue Library Extraction Design Brief.md`](../Task%20Queue%20Library%20Extraction%20Design%20Brief.md).

---

## How to use this set

1. Treat each file as a **feature contract**: intent, normative behavior, SQL + Python surfaces, edge cases, acceptance tests, explicit non-goals.
2. When a borrowed feature touches the SQL protocol, the Unified Design Spec remains authoritative for state machine / fencing; these docs specify the **additive product surface**.
3. Priority tags:

| Tag | Meaning |
|---|---|
| **MUST** | Ship in first library cut; correctness/DX footguns without them |
| **SHOULD** | Ship in first or immediate second cut; high leverage, low protocol risk |
| **NICE** | After core adoption; polish / ops safety |

---

## Document index

| # | Doc | Priority | One-line |
|---|---|---|---|
| 01 | [Typed Enqueue Results](./01-typed-enqueue-results.md) | MUST | Every enqueue returns a structured status — never silent null |
| 02 | [Job Key and Uniqueness Modes](./02-job-key-and-uniqueness-modes.md) | MUST | Named modes: reject / replace / preserve_run_at (+ by_args) |
| 03 | [Handler Settle Results](./03-handler-settle-results.md) | MUST | `Complete` / `Snooze` / `Cancel` / `Retry` / `NonRetryable` |
| 04 | [Insert-Only Client](./04-insert-only-client.md) | SHOULD | Same client; omit worker config → enqueue-only |
| 05 | [Queue Profiles](./05-queue-profiles.md) | SHOULD | Retry/retention/lease defaults live on the queue row |
| 06 | [Notify Nudge and Poll](./06-notify-nudge-and-poll.md) | SHOULD | NOTIFY is a hint; polling is correctness |
| 07 | [Dead Letter Lineage and Redrive](./07-dead-letter-lineage-and-redrive.md) | SHOULD | Source fields + first-class `redrive` |
| 08 | [Blueprints and Namespaces](./08-blueprints-and-namespaces.md) | SHOULD | Modular task registration with rename aliases |
| 09 | [Retry Value Surface](./09-retry-value-surface.md) | SHOULD | `retry=False \| True \| int \| RetryStrategy` |
| 10 | [Test Helpers](./10-test-helpers.md) | SHOULD | `require_enqueued` / `work` / connector swap |
| 11 | [Soft Stop and Shutdown](./11-soft-stop-and-shutdown.md) | SHOULD | Drain → grace → cancel → release |
| 12 | [Migrate Break Channel](./12-migrate-break-channel.md) | NICE | Workers exit on breaking schema |
| 13 | [SQL Packaging Conventions](./13-sql-packaging-conventions.md) | NICE | schema.sql / queries.sql / pre-post migrations |
| 14 | [Embedded Worker and FastAPI Lifespan](./14-embedded-worker-and-fastapi-lifespan.md) | SHOULD | In-process worker (borrowed from a Rails-world peer) + lifespan/DI + transactional enqueue |

---

## Cross-cutting rules (apply to every feature)

1. **SQL is the contract.** Python maps to functions; it does not reimplement admission, uniqueness, or settle races.
2. **No silent success.** Any path that used to return `null` / `None` / ignored conflict must return a typed status.
3. **Worker knobs stay tiny.** Rich behavior belongs on queue rows, job rows, or handler return values.
4. **outlabs-auth stays optional.** None of these features import IAM.
5. **Don't invent a second queue.** Features extend `taskq.*`; they do not add a parallel table family.

---

## Suggested implementation order

    Phase A (MUST):  01 → 02(reject) → 03
    Phase B (SHOULD core DX):  04 → 05 → 09 → 13
    Phase C (SHOULD runtime/ops):  06 → 11 → 07 → 10 → 12 → 14
    Phase D (0.2+ capabilities):  02(replace/by_args) → followups (03/ADR-007) → 08

(14 lands with Phase C: it is the outlabsAPI adoption surface and the recommended
`_system`-queue claimer for facade hosts — Unified Spec §20.2.)

## Release staging (ADR-009 — authoritative)

| Release | Features |
|---|---|
| **0.1** | 01 (created/existed), 02 reject mode, 03 (Complete/Retry/Snooze + **fenced** Cancel; followups field reserved+rejected), 05, 06, 07 same-queue DLQ + redrive, 09, 10, 11, 12 (promoted), 13 (migrations-canonical form), 14 (opt-in embedded) |
| **0.2** | Followups (lossless-atomic, ADR-007), dependencies/workflows, schedules + backfill, 02 replace/preserve/by-args (one at a time), 08 reassessed against typed task objects |
| **0.3** | 07 redirect DLQ (only if a real need appears), partitioned archive/retention automation |

Where a feature header's original MUST/SHOULD/NICE tag disagrees with this table, this table (ADR-009) wins.
