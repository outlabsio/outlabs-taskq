# Internal targeted review — QDarte C7 environment and preflight plan

## Assignment and provenance

Audit the frozen C7-00 plan at
`docs/Task Queue Stage 5 QDarte Contact Verify C7 Environment Plan.md`.
Return **READY** only if it names the correct lasting environment, preserves
the accepted C6 one-publisher/admission/effect boundaries, and makes every
source, privilege, connection, restore, cohort, counter, and rollback
uncertainty a falsifiable C7-01 precondition. Otherwise return **BLOCKED** with
the narrowest docs-first corrections.

The owner has authorized the implementation session to perform this review
because the usual separate review session is unavailable. The response must
state plainly that it is internal and non-independent.

READY may open only C7-01 preflight. It authorizes no package cohort, provider
call, production job, direct retirement, non-contact lane, C7-02+, or Stage 6.

## Exact review identity

- taskq plan/hygiene tip:
  `7f9977b`
- accepted C6 audit response SHA-256:
  `0ad659ae143fd1fdff29a7e3718bda747a084cd10bf7fef0942cde792e5488fd`
- accepted QDarte API C6 tip:
  `7a744582b0d824a559aa29dfaf03ef1081058064`
- accepted QDarte workers C6 tip:
  `21bd880d5f2688f04cf323326512e6b630073d70`
- immutable taskq release `v0.1.0a6`, wheel SHA-256
  `a731a6dcf4cd80b94742fca1d2203e09fab2b96c4e002273d90ded29e50d5419`

Read `AGENTS.md`, `docs/README.md`, the Tier-0 contracts, ADR-020/022/023,
the C6 Compatibility and Cutover Specification, the C6 Compatibility Ledger,
the Round-15 request/response, and `TASKS.md` before judging the plan.

## Evidence sources

Inspect without mutating external state:

- current remote `main` and `staging` graphs for QDarte API, workers, and
  runtime;
- the accepted isolated C6 branches;
- QDarte runtime environment map, local-production compose, backup/restore
  runbook, and production backup helpers;
- QDarte contact harness, settings, database engine, mode controller,
  admission adapter, private reporter bridge, and closed worker source;
- the copied 2026-07-20 backup manifest/archive only as historical evidence;
  and
- taskq runtime connection-budget implementation.

Do not contact a provider, deploy, create a role/database/token, run a worker,
change a branch/ref, alter production configuration, or write any database.

## Required attack program

1. **Environment choice.** Independently derive whether Mini87 local
   production—not cloud intake or MacBook dev—is the environment that owns the
   full QDarte API, worker, domain effects, and durable root. Reject an
   environment-name assertion unsupported by source.
2. **Topology and one publisher.** Trace every network/database authority.
   Challenge the separate database, separate package process, loopback-only
   facade, HTTP-only closed worker, stable-effect reporter, and normal-app
   non-mount. Find any path that gives two producers, a worker DB password,
   package credentials to the normal app, or a public package surface.
3. **Source convergence.** Recompute the three remote graphs and confirm the
   isolated C6 tips cannot safely replace a divergent deployed line. Decide
   whether the zero-unclassified-path ledger and live build-identity
   precondition are strong enough. Reject any implicit merge policy.
4. **Privilege wall.** Derive why taskq and QDarte-domain sessions need
   separate logins. Challenge operator separation, runtime memberships,
   service-principal scopes, credential storage, and the proposed production
   construction acknowledgement. No superuser or broad-schema shortcut may be
   deferred as an acceptable residual.
5. **Connection arithmetic.** Recompute taskq pool/listener capacity from
   source and inspect the QDarte global engine's default pool. Confirm the plan
   requires a capped 2/zero-overflow domain pool, counts the worker as HTTP-only,
   and uses a measured normal-production high-water rather than an idle
   snapshot. Falsify `H + 3 <= M - 20` if any connection is omitted.
6. **Backup and restore.** Verify the historical copied archive's identity and
   its missing-globals limitation. Attack the proposed two-database plus
   globals backup, disposable restore, recurring-job expansion, and proof that
   live databases remain unchanged. A dump without an executed restore fails.
7. **Direct/effect conservation.** Re-derive the exact tables and canonical
   full-row digest shape. Confirm active work blocks drain and any later direct
   insert stops rather than replays. Reject high-water-only or count-only
   evidence.
8. **Cohort and external counter.** Challenge whether one exact `place` scope
   truly bounds planning to one entity and whether a dedicated fail-closed
   worker proxy is independent of package/domain/usage ledgers without logging
   sensitive data. An internal counter represented as independent fails.
9. **Sequence and rollback.** Walk C7-01 and C7-02 ordering. Look for a window
   with dual publication, an unpaused consumer before evidence, package publish
   during preflight, automatic backend switching, row copy, or post-effect
   direct replay.
10. **Scope.** Confirm the plan and task range are docs-only and leave C7-02,
    retirement, other lanes, and Stage 6 closed.

## Required response

Write only `docs/design-review-16/RESPONSE.md` and leave it uncommitted
initially. Include:

- READY or BLOCKED;
- exact source/ref identities and independently derived topology;
- findings for all ten attack areas;
- explicit internal/non-independent provenance;
- Contract questions, if any; and
- the exact scope opened.
