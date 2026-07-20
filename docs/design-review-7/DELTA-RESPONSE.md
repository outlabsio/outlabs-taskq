# Round 7 targeted delta acceptance — Response

> **Scope:** only the two round-7 preconditions, per the delta request. Nothing accepted earlier
> is reopened; nothing further is authorized.
> **Deltas verified:** taskq `5fef55c..96194a8` (+ the gate commit `82d4ee7` carrying this
> request) on `main`; host `7c60229..9348f85` on `codex/s4-03-cycle1`; production remains
> application revision `3f50b7d`.

## Verdict

**ACCEPTED — Stage 4 complete.**

## Check results

1. **R7-02 — PASS.** The packet (`docs/taskq-s4-audit.md` at `9348f85`) now states plainly that
   cycle 1 produced the complete production canonical path for the first tool while cycle 2's
   initial post-fix closure ran only in the isolated production-shape environment, and that the
   round-7 remediation "repeated that closure in production rather than treating the local record
   as production history." The recorded live pair is exact: one ephemeral key scoped
   `tools:run` + `taskq_tools:read`; the same `Idempotency-Key`
   (`s4-r7-aerolineas-13564c34-7f23-4274-9eb7-29e82904a141`) submitted twice; HTTP 202 `created`
   then HTTP 202 `existed`; identical job id `019f7f95-3c93-71ce-9c8a-7c610212dead`; authorized
   canonical GET returning protocol 1, a minted request id, HTTP 200, terminal `succeeded` with
   attempt count 1 and failure count 0. The separate read-only production-table oracle shows one
   `succeeded/success` attempt, counters `attempt_count=1, failure_count=0, release_count=0,
   expiry_streak=0`, and event chain `enqueued -> claimed -> succeeded`, selecting no payload,
   result, error, event message/data, attempt id, or fence. The temporary key is reported revoked
   and its principal archived through the public services.
2. **R7-04 (folded) — PASS.** Recomputed: `7 + 15 + 15 + 15 = 52 <= 100 - 20 = 80`. The packet
   now states "The usable budget is ceiling minus reserve, `100 - 20 = 80`, so honest headroom is
   28 connections; the reserved 20 is not counted as headroom."
3. **R7-01 — PASS.** Stage-4 §6 (at `bf2744e`) now describes the rolling-replacement drill as the
   graceful worker contract — budget-free `worker_shutdown` release with the same job id claimed
   by a different worker process — and states that a graceful replacement cannot honestly prove
   lease-expiry recovery because the worker settles the held job before the lease expires. The
   REQUIRED production hard-kill drill is owned by the future side-effecting-lane expansion
   slice, gated before any side-effecting lane migrates, with the exact required oracle: first
   attempt `expired/lease_expired`, a `lease_expired` event, same-id reclaim by a different
   worker attempt, terminal convergence, correct budget arithmetic, and zero manual DML — and the
   Stage-4 graceful-release evidence explicitly "does not satisfy or waive that future gate."
   §7.2 step 5 is re-pointed consistently.
4. **Docs-only deltas — PASS.** The taskq delta commits touch only `TASKS.md`, `docs/README.md`,
   the Stage-4 specification, and the design-review-7 records; the host delta touches only
   `docs/taskq-s4-audit.md` (+20/−6). No taskq SQL, migration, Tier-0, ADR, prior Tier-4, host
   source, host migration, legacy retirement, or branch reconciliation change exists in either
   range. All five commits carry the required trailer.
5. **Response identity and gates — PASS.** The registered round-7 response is byte-identical
   (SHA-256 `d110e13a7edd3300bfe9f911a22edd58cd2867aa2abbf74cc4e5267e19370bdd`, recomputed by
   this reviewer against both the committed blob and the working file). Reproduced by this
   reviewer on 2026-07-20: taskq **450 passed / 1 opt-in skip** with a CI-shaped Redis service
   plus Ruff clean; host **72 passed / 5 existing infrastructure skips** plus Ruff clean and
   MyPy clean across 64 source files.

## Effect

Stage 4 — the outlabsAPI first-host dogfood — is **complete and independently accepted**: the
durable typed queue serves the two allowlisted read-only tools in production behind the frozen
authorization, budget, rollback, and honesty contracts, with the legacy path retained as the
mutually exclusive fallback. This acceptance opens only the later work of **separately
specifying** legacy-path retirement and branch reconciliation; it does not execute or authorize
either change, and the hard-kill lease-expiry drill remains a REQUIRED gate owned by the future
side-effecting-lane expansion slice. The round-7 deferred follow-ups (R7-03, R7-05, R7-06,
R7-07, restore/PITR) retain their named owners.
