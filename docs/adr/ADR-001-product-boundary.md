# ADR-001 — Product boundary: durable task queue, not a message bus

**Status:** Accepted 2026-07-18
**Resolves:** design-review 01 (north star), 05 (positioning)

## Context

The doc family occasionally reaches for "message queue" language, and the peer field contains adjacent products (the Postgres message-queue extension's mailbox semantics, event streams, durable-execution frameworks) whose promises differ from a task queue's. An ambiguous boundary invites semantics that conflict (fan-out retention vs settlement, offsets vs budgets).

## Decision

outlabs-taskq is a **Postgres-native durable task queue for Python services**. The product promise:

> If taskq accepts a task, PostgreSQL durably owns it. A worker may execute it more than once after failure, but only the current fenced attempt may change its state, and accepted work is never silently discarded.

At-least-once execution; exactly-once **state transitions**. Explicitly not: pub/sub, an event log/stream, a log/stream-platform replacement, a broker abstraction (no Redis/RabbitMQ/SQS backends), or a durable-execution runtime (durable-execution platforms/a Postgres-backed durable-workflow platform category). Applications needing mailbox semantics run the Postgres message-queue extension beside taskq; fan-out/event retention uses an outbox or event tool beside it. Exactly-once *side effects* are never promised — handlers get idempotency tokens, not magic.

## Consequences

- Unified Spec §1 non-goals stand and gain the "beside, not inside" guidance.
- Public positioning follows design-review 05's honest comparison; no "best queue for everything" claims.
- Any future proposal adding consumer offsets, retained fan-out, or broker portability is out of scope by decision, not by taste.
