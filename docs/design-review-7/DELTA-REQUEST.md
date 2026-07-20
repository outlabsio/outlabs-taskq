# Round 7 targeted delta acceptance request

## Assignment

Perform only the targeted delta check authorized by round-7 response §7. Decide whether the two
preconditions are `ACCEPTED` or remain `BLOCKED`. Do not reopen findings already accepted in
substance and do not authorize legacy retirement, branch reconciliation, or a side-effecting lane.

Pinned deltas:

- taskq: `5fef55c..96194a8` on `main`;
- host: `7c60229..9348f85` on `codex/s4-03-cycle1`;
- production remains application revision `3f50b7d`; and
- immutable round-7 response SHA-256 remains
  `d110e13a7edd3300bfe9f911a22edd58cd2867aa2abbf74cc4e5267e19370bdd`.

Write `docs/design-review-7/DELTA-RESPONSE.md` and modify nothing else.

## Required checks

1. **R7-02:** verify the host packet now distinguishes the original local Aerolineas closure from
   the remediation's production run. The recorded live pair must show the same idempotency key,
   HTTP 202 `created` then 202 `existed`, identical job id, authorized canonical HTTP 200
   `succeeded`, and a separate read-only production-table oracle with one successful attempt, zero
   failures/releases/expiry streak, and `enqueued -> claimed -> succeeded`. Confirm the temporary
   key was reported revoked and its principal archived.
2. **R7-04 folded into R7-02:** recompute `52 <= 100 - 20 = 80`; the packet must call 28—not
   48—the available headroom and must not count the reserve as headroom.
3. **R7-01:** verify Stage-4 §6 now says a graceful rolling replacement produces budget-free
   `worker_shutdown` release and same-id reclaim. It must state that lease-expiry/reap requires a
   process unable to settle past platform grace and name the future side-effecting-lane expansion
   slice as owner of a REQUIRED hard-kill production drill before any side-effecting lane migrates.
   Its oracle must require `expired/lease_expired`, a `lease_expired` event, same-id reclaim by a
   different attempt, terminal convergence, correct budget arithmetic, and zero manual DML.
4. Confirm the deltas are documentation/board evidence only: no taskq SQL, migration, Tier-0, ADR,
   prior Tier-4, host source, host migration, legacy retirement, or branch reconciliation change.
5. Confirm the response is byte-identical. Test evidence for the unchanged source is taskq
   450 passed/1 opt-in skip with CI-shaped Redis plus Ruff clean, and host 72 passed/5 existing
   infrastructure skips plus Ruff and 64-file MyPy clean.

If all five checks pass, state `ACCEPTED — Stage 4 complete`. Acceptance opens only the later work
of separately specifying legacy retirement and branch reconciliation; it does not execute or
authorize either change.
