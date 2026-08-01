# ADR-013 — Effective lease duration in the claim projection

**Status:** Accepted 2026-07-18
**Resolves:** S2-CQ-01; amends Protocol v1 H-02 and SQL contract 0.1.1

## Context

The worker guarantee in Unified Spec §14 requires one heartbeat per running job at `min(lease/3, 30s)`. ADR-003 makes the database clock the sole clock for lease decisions. The 0.1.1 `taskq.claimed_job` composite returns `lease_expires_at`, but omits the effective `lease_seconds` that `claim_jobs` selected from either its explicit override or the durable job row.

A worker therefore cannot implement the required cadence honestly. Subtracting its local wall clock from the database expiry makes clock skew load-bearing. Always supplying a global lease override hides the omission by defeating the per-task/per-queue lease stamped at enqueue. Neither is acceptable.

Protocol H-02 already permits additive composite evolution: new fields append, while removal, rename, or reordering is breaking.

## Decision

1. SQL contract 0.1.2 appends `lease_seconds integer` as the final attribute of `taskq.claimed_job`. No existing attribute moves or changes; `lease_expires_at` remains for observability.
2. `taskq.claim_jobs` returns the exact effective `v_lease` used for the row update and attempt insert. The value is non-null and remains within the existing 15–86400 second contract.
3. Workers schedule heartbeats from the returned duration using a monotonic timer. They must never derive lease duration from `lease_expires_at - local_now()`; client wall time does not participate in lease math.
4. Protocol major remains v1 because the field is additive under H-02. Immutable migration `0003_contract_0_1_2` advances the SQL contract version and replaces `claim_jobs` without adding a public function.
5. `verify()`, the independent catalog-parity projection, transport decoding, fresh-chain tests, and the full `0001 → 0002 → 0003` upgrade path assert the appended attribute and effective value on PostgreSQL 16 and 18.

## Consequences

- S2-04 can implement its normative heartbeat cadence without clock-skew assumptions or lease overrides.
- Older clients continue decoding the original prefix; new clients require contract 0.1.2 before claiming.
- The Function Manifest and Protocol v1 amendments are canonical before migration 0003 is implemented.
