# taskq — Transport Protocol v1 (canonical)

> **Status:** CANONICAL — accepted 2026-07-18, satisfying ADR-005's Stage-0 exit requirement. This document + its adopted base define protocol v1 for the 0.1 contract; every route sketch elsewhere in the doc family is illustrative and yields to this.
> **Adopted base:** [`design-review-2/03-protocol-draft.md`](./design-review-2/03-protocol-draft.md) §2–§6 (wire shapes, command × outcome × HTTP tables, TQ registry, retry/idempotency matrix, version negotiation) are adopted **verbatim** as protocol v1 content, as amended by §2 below. The draft's §1 decisions 1–10 are all **accepted**.
> **Companions:** the exact SQL signatures/composites live in [`Task Queue 0.1 Function Manifest.md`](./Task%20Queue%200.1%20Function%20Manifest.md); authorization semantics in the Authorization doc (ADR-006/011).

## 1. Hole closures (the draft's §7, decided)

| Hole | Decision |
|---|---|
| H-01 typed claim state | **Closed — contract change:** `taskq.claim_jobs` returns the composite `taskq.claim_batch (state text, jobs taskq.claimed_job[])` with `state ∈ claimed | empty | paused | unknown_queue | unavailable` (targeted claim). One atomic call; the facade maps state 1:1 to the draft's outcomes and never infers from an empty set. Batch ≤50 keeps the array cheap. |
| H-02 frozen composites | **Closed by the 0.1 Function Manifest** — named composite types with additive-only evolution (new fields append; removal/rename = contract major). |
| H-03 cross-verb replay | **Closed — the draft's refinement is accepted:** a replay of the SAME verb for an already-settled attempt returns `already_settled`; a DIFFERENT verb for that attempt returns typed `settle_conflict` (HTTP 409, prior verb + terminal status in data, never the fence). This amends the v1.1 any-verb acknowledgement: acknowledging `complete` after `fail` hid client bugs. Applied in the manifest bodies (the attempt-ledger check compares the settled verb). |
| H-04 worker label binding | Accepted as drafted: per-worker credential → server binds label to subject; shared fleet token → label validated, stored, explicitly advisory; principal actor is always the token subject. |
| H-05 bulk convergence | Closed by spec v1.6 §5.2 (one-result-per-input, later-snapshot resolution, `TQ500` atomic rollback) + manifest body. |
| H-06 error envelope + native normalization | Accepted: §4 registry is closed; the manifest enumerates every public raise; facade normalizes any unregistered SQLSTATE to `TQ500` and logs the original privately. |
| H-07 job-detail projection | **Closed — minimal safe projection frozen for 0.1:** `id, queue, job_type, status, outcome, priority, attempt_count, failure_count, max_attempts, created_at, scheduled_at, started_at, finished_at, updated_at` always; `error` (≤2KB), `result` (≤8KB), `progress` (≤2KB) only via explicit `include=` flags gated by queue `read`; `payload` (≤64KB) via `include=payload`, same gate; **never** headers, fences, or worker internals. Redaction hook point reserved per field. |
| H-08 list cursors/indexes | Deferred as drafted: 0.1 read surface is get-by-id + per-queue stats; the general list route stays operator-minimal until the keyset + EXPLAIN evidence (B9) lands. |
| H-09 size ceilings | **Closed — published limits (also in `/meta.limits`):** payload ≤64KB, progress ≤2KB, result ≤8KB, stored error ≤2KB, bulk ≤1000 items and ≤4MB body, claim batch ≤50, `wait_seconds` ≤30, job-type filter ≤20 entries, headers ≤8KB. Oversize → `TQ422`. |
| H-10 long-poll lifecycle | Accepted as drafted: disconnect cancels the waiter, never a committed claim; shutdown drains hub subscribers before LISTEN/pool close (tested in T6). |
| H-11 profile If-Match | Deferred (queues are bootstrap/migration-owned in 0.1). |
| H-12 0.2/0.3 commands | Deferred; inactive fields rejected with `TQ501`, never ignored. |
| H-13 single generation source | **Closed:** one Python protocol manifest (models + command table) generates the OpenAPI schema, sync + async HTTP clients, and the SQL/HTTP parity test vectors. Hand-copied route tables are banned — this document and the manifest are the only human-maintained sources. |

## 2. Amendments to the adopted base

1. Wherever the base says claim outcomes are inferred, H-01's typed `claim_batch` is authoritative.
2. The settlement tables gain the `settle_conflict` outcome per H-03 (409, `retryable=false`).
3. The base's §3.6 list-jobs row is downgraded to "operator-minimal, pre-H-08" for 0.1.
4. `GET /taskq/v1/meta` additionally reports the H-09 limits verbatim.

## 3. Stage-0 exit status (ADR-005 checklist)

- Draft §1 decisions: accepted (10/10). Holes: H-01..H-07, H-09, H-10, H-13 closed above; H-08/H-11/H-12 explicitly deferred with capability gates.
- Exact SQL signatures/composites/grants/SQLSTATEs per command: the 0.1 Function Manifest (same pass).
- Parity suite, OpenAPI fence-redaction, and compatibility-window tests: harness T6/T8 obligations, pre-wired in the manifest's per-function test ids.
- Legacy Diverse/QDarte paths remain host adapters with a removal milestone; they define no second protocol.
