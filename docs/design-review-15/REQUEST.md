# Internal targeted review — QDarte C6 local cutover completion

## Assignment and provenance

Audit the complete local C6-01..04 contact-verify compatibility/cutover range.
Return **READY** only if the current source and raw ledgers prove a single
publisher, fresh direct-drain interlock, durable package admission without
replanning, and all three zero-row-copy rollback postures. Otherwise return
**BLOCKED** with the narrowest preconditions.

The owner has authorized the implementation session to perform this review
because the usual separate review session is unavailable. The response must
say plainly that it is an internal, non-independent review.

READY may open only C7-00 environment planning. It does not authorize a
production mutation, package cohort, worker/provider run, deployment,
direct-queue retirement, non-contact lane, C7-01+, or Stage 6.

## Authority and exact evidence

Read `AGENTS.md`, `docs/README.md`, Protocol v1 revision 1.0.8, Function
Manifest / SQL contract 0.1.5, ADR-020, ADR-022, ADR-023, the Stage-5 QDarte
Contact Verify Compatibility and Cutover Specification, the Compatibility
Ledger, and `TASKS.md`.

Inspect:

- taskq release `v0.1.0a6` and current `main`;
- QDarte API branch `codex/taskq-pilot-p1` through `7a74458`, including C6
  commits `1379f3f`, `145ca1a`, `c0940fb`, `84e23ea`, `96fe5f0`, and
  `7a74458`;
- QDarte workers branch `codex/taskq-pilot-p1` through `21bd880`;
- `qdarteAPI/docs/taskq-contact-c6-03b-admission-evidence.md` and
  `qdarteAPI/docs/taskq-contact-c6-04-local-rollback-evidence.md`; and
- disposable local databases `qdarte_contact_verify_dev` and `qdarteapi_dev`
  read-only. Do not resume, claim, redrive, prune, delete, or manually rewrite
  any package/direct row.

Treat every evidence document as a claim to falsify.

## Required attack program

1. **Closed mode dispatch.** Derive `legacy | draining | package` from source.
   Prove the incumbent taskq selector cannot influence it; invalid/mixed mode
   fails startup; draining constructs neither producer; package can become
   effective only after its same-process proof; and a package failure never
   invokes the direct producer.
2. **Direct-drain interlock.** Trace both observations, bounds, database
   identity, process-local opaque attestation, five-minute ceiling, and
   pre-admission re-observation. Try to find serialization, route, mutable
   flag, manual setter, package-table read, payload read, or a way for a direct
   insert/active lease to retain authorization.
3. **Caller and admission semantics.** Verify the retained URL has one
   backend-neutral response and one producer per request. Re-derive canonical
   key/intent behavior, reserve-before-plan, admitted no-plan replay, pending
   refusal, immutable receipt, and exact-hash a6 pin. Reject a host mapping,
   cache, lookup/enqueue race, active-row import, row copy, copied package
   route, or cross-backend fallback.
4. **Rollback postures.** Check all three C6-04 moments against raw ledgers:
   legacy/no package entry; paused/cancelled zero-attempt jobs before work with
   idempotent typed controls and retained admission history; and one succeeded
   post-effect job resolved through canonical safe read plus exactly one stable
   domain effect. Reject manual DML, deletion, direct recreation, provider
   rerun, fence/raw-provider exposure, or automatic backend switching.
5. **Raw conservation.** Recompute package queued/running counts, attempts and
   cancel events, direct contact full-row hashes, and stable effect/contact/
   usage rows. Confirm no broad worker or package worker is running and no new
   provider unit was consumed.
6. **Regression and boundary.** Re-run focused API/worker tests, Ruff/format,
   and targeted MyPy. Confirm C7, production, retirement, non-contact work, and
   Stage 6 are source-absent.

## Required response

Write only `docs/design-review-15/RESPONSE.md`, leave it uncommitted initially,
and modify nothing else during the review. Include exact identities, derived
dispatch/interlock/admission/rollback findings, raw-state and test evidence,
Contract questions, the independence disclosure, and the exact READY scope.
