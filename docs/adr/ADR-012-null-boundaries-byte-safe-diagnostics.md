# ADR-012 — Explicit-null boundaries and byte-safe stored diagnostics

**Status:** Accepted 2026-07-18
**Amends:** Transport Protocol v1 H-09; 0.1 Function Manifest (contract patch 0.1.1)
**Resolves:** Round-3 Contract questions CQ-01 and CQ-02

## Context

Round-3 implementation review found two contradictions inside the locked 0.1 contract.

First, public numeric arguments have non-null bounded domains and registered `TQ422` validation promises, while several adopted SQL predicates use ordinary three-valued comparisons. An explicitly supplied SQL `NULL` therefore bypasses the check: a null claim batch is accepted, a null bulk-redrive limit becomes unbounded, and a null release delay reaches native PostgreSQL `23502` instead of the closed TQ registry. Omission and explicit null need distinct semantics.

Second, Protocol H-09 freezes stored errors at 2KB, while adopted function bodies store some caller/handler reason text without a bound and other bodies use character-counted `left(..., 2000)`. UTF-8 characters can occupy multiple bytes, so neither pattern enforces the published byte ceiling. Rejecting oversized diagnostics during settlement would create a worse correctness failure: accepted work could remain running because its error message was too large.

## Decision

1. **Omission uses a SQL default; explicit `NULL` never does.** Every public function argument whose documented domain is non-null validates `IS NULL` before state change. A null required/bounded value raises `TQ422`, just like an out-of-range value. Optional fields whose documented domain includes null remain optional. This applies uniformly to direct SQL and every later transport.
2. **Persisted diagnostic limits are UTF-8 byte limits.** `taskq.jobs.error`, `taskq.job_attempts.error`, and `taskq.jobs.cancel_reason` store at most **2,048 bytes**. `taskq.job_events.message` stores at most **500 bytes**. Null remains null.
3. **Diagnostics truncate; they do not reject settlement.** Oversized error/reason/message text is truncated to the longest valid UTF-8 prefix within its field limit. Payload, headers, progress, result, and request/body ceilings retain H-09's `TQ422` rejection semantics. Diagnostic truncation is the deliberate exception because observability text must not block a durable state transition.
4. **One owner-only helper enforces the rule.** Contract 0.1.1 adds `taskq.truncate_utf8(text, int)`, listed first in the Function Manifest. It is `SECURITY DEFINER`, pinned, owned by `taskq_owner`, PUBLIC-revoked, and granted to no application capability role. Every contract function that persists the fields above routes through it; `emit_event` centralizes the event-message cap.
5. **This is SQL contract patch 0.1.1; Protocol major remains v1.** Migration `0001_initial.sql` is immutable. Ordered migration `0002_contract_0_1_1.sql` replaces affected function bodies, installs the helper, and advances `taskq.meta.contract_version` to `0.1.1`. It does not change public function identities or result shapes.
6. **The gates are executable.** Boundary vectors cover omitted/defaulted, explicit-null, below/minimum, maximum, and above-maximum inputs for every public bounded parameter. Diagnostic vectors cover ASCII and multibyte text at, below, and above each byte cap and prove that settle/operator transitions still complete.

## Consequences

- Direct SQL callers receive the same closed error family as later HTTP clients; null can no longer turn a bounded operation into an unbounded one.
- Stored diagnostics are safe for projections and storage regardless of Unicode width, without sacrificing settlement correctness.
- The manifest gains one internal helper and migration 0002; exact-catalog verification must expect both.
- Clients negotiating SQL contract `0.1` must not claim compatibility with `0.1.1` until their supported range includes this patch. Protocol-v1 wire shapes and outcome vocabularies do not change.
