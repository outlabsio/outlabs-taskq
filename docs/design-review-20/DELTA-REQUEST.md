# Round 20 targeted delta request

Review only the docs remediation range:

```text
a355f47279a7a8b9a47d0c0f84b6166161e7272e..39100fad4f8e43fff49c829636190794dab22332
```

The immutable Round-20 response SHA-256 is
`df8b7e3b52432720072e3f5f14903eb31b8a0d8d8794f340e3cd20820a621574`.
Verify it remains byte-identical. Read the original request/response and the
amended Tier-3 specification. Modify only
`docs/design-review-20/DELTA-RESPONSE.md`; leave it uncommitted.

This review is owner-authorized internal/non-independent if the separate
reviewer remains unavailable. State that provenance. Regenerate the cited
source/read-only facts; do not call the response independent.

## Checks

1. **R20-01:** the specification starts from the accepted live posture
   (`draining`, queue paused, worker/gateway absent), deploys server-disabled
   API and disabled caller first, verifies facade/IAM/private topology, starts
   gateway then one closed worker, earns a fresh drain, unpauses, enables
   submission last, and defines an inverse safe unwind that disables admission
   first. No fallback or direct row is involved.
2. **R20-02:** the historical production aggregate is reproducible as six jobs
   with planned counts `[1,25,86,100,176,293]`, without reading sensitive
   values. Production requires explicit `limit`, rejects absent/over-current-
   cap input before reservation/planning, fixes queue depth/concurrency/worker
   at one, and advances only through separately accepted 25/100/300 gates.
   Synthetic filler and re-verification are forbidden. C8-R2 depends on owner
   acceptance of the actual supported cap; 300 parity requires C8-E300.
3. **R20-03:** exact-ID status plus a client-side persisted hint is an explicit
   product posture, package cancellation remains operator-only, and vectors
   cover reload/hint loss/no direct cancel/no resubmit-for-discovery. No runtime
   operator or shadow mapping is introduced.
4. **Scope/hygiene:** remediation is docs-only, trailered, board-coupled, and
   changes no response, Tier-0/ADR/SQL/migration/source/config/service/IAM/
   database/queue/worker/production state. Taskq source identity is unchanged;
   505/1 and Ruff/format remain green.

Return `READY` only if all four checks pass with no unresolved blocker/high and
zero Contract questions. READY opens only C8-R1 **after** the next naturally
scheduled 03:15 backup and the remaining §4 eligibility evidence. It does not
authorize service enablement, a production cohort, C8-R2/R3, data/schema
deletion, another lane, or Stage 6. Otherwise return `BLOCKED` with exact
preconditions.
