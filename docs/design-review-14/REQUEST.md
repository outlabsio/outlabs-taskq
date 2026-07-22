# External targeted review — QDarte C6-03B durable admission repin

## Assignment

Audit the isolated QDarte exact-hash repin and C6-03B created/existed proof.
Return **READY** only if accepted immutable taskq release `0.1.0a6` fixes the
QDarte replanning replay gap through the general queue-native admission
primitive, with no host mapping, direct fallback, worker/provider execution,
or incumbent-ledger mutation. Otherwise return **BLOCKED** with the narrowest
preconditions.

READY may open only the already-frozen local C6-04 rollback exercises. It does
not authorize a worker or provider run, production migration, existing-queue
mutation, direct-queue retirement, a non-contact lane, C7, or Stage 6.

## Authority and exact ranges

Read `AGENTS.md`, `docs/README.md`, Protocol v1 revision 1.0.8, Function
Manifest / SQL contract 0.1.5, ADR-020, ADR-023, the Durable Admission
Reservation Specification, the Stage-5 QDarte Contact Verify Compatibility
and Cutover Specification, and `TASKS.md`. Tier 0 and ADRs control every
conflict.

Inspect these exact published/local identities without modifying them:

- taskq release tag `v0.1.0a6`, peeling to
  `c2f6827fc4a8563cc5a3910b1a1319b53cdfd9c8`; release-record tip `c2f40a6`;
- QDarte API branch `codex/taskq-pilot-p1`, range
  `c0940fb..96fe5f0`, at
  `/Users/macbookm3/Documents/projects/qdarte-pilot-p0.18RsaF/qdarteAPI`;
- QDarte workers branch `codex/taskq-pilot-p1`, range
  `abeaac1..21bd880`, at
  `/Users/macbookm3/Documents/projects/qdarte-pilot-p0.18RsaF/qdarte-workers`;
  and
- disposable local PostgreSQL database `qdarte_contact_verify_dev` in the
  running `qdarte-dev` stack. Database inspection is read-only; do not clean
  up, cancel, claim, settle, or prune any row.

The evidence record is
`qdarteAPI/docs/taskq-contact-c6-03b-admission-evidence.md`. Treat it as a
claim to falsify, not an oracle.

## Required attack program

1. **Artifact identity.** Download or inspect the immutable a6 release and
   independently verify the wheel hash
   `a731a6dc69e7346e2069ea9ac71257bf832be6e73bd4a2d01d709fd82d0d5419`,
   version, 0001–0007 migration payload, and annotated-tag provenance. Confirm
   both QDarte lockfiles use only that exact URL/hash.
2. **Migration and privilege boundary.** Read the disposable database's
   migration ledger, metadata, capabilities, roles, and grants. Require SQL
   0.1.5, exact active capabilities `admission_reservations` plus
   `read_model_list_ready`, immutable 0007 checksum, a dedicated non-superuser
   facade login with producer/runner/observer/housekeeper but never operator,
   and a distinct operator login. Confirm no QDarte production or incumbent
   package database was migrated.
3. **Algorithm source audit.** Trace the retained caller boundary through the
   QDarte adapter and generated HTTP client. Prove intent is hashed before
   planning, one handle is minted per logical call, `reserve_admission`
   precedes candidate planning, only `reserved` can plan and finish,
   `admitted` returns the stored receipt without planning, and `pending` fails
   closed. Reject any QDarte key-to-job table/cache, lookup-then-enqueue race,
   direct producer fallback, payload replan, request echo as authority, or
   admission-capable normal app mount.
4. **Created/existed raw oracle.** Independently inspect the uppercase
   canonical admission and linked job. Require one `admitted` row for
   `contact_verify_scope:country:AR`, one stable job ID, exact receipt
   `{"planned_entities":1}`, and a linked queued job with zero attempts,
   failures, and releases. Reproduce or source-verify that an identical replay
   returns `existed` with zero planner calls and no package/direct/effect
   mutation. Do not treat the retained lowercase no-candidate reservation as
   the successful proof; verify it has no linked job or effect and was not
   manually rewritten.
5. **Intent and caller semantics.** Recompute the versioned canonical request
   hash for the successful request and require
   `978d42ccdd4ba23c6a9236530af105a03d8be4d481e97bc6c46fc9104571ee23`.
   Confirm supplied versus derived keys, `None` omission, immutable receipt
   validation, sanitized failure behavior, and canonical caller response
   remain consistent with the frozen C6 contract.
6. **Regression and scope gates.** Re-run the focused API and worker suites,
   Ruff/format, and targeted MyPy. Confirm no worker was started, no package
   job claimed, no provider/effect invoked, no direct row copied or created,
   no production/existing queue changed, and no C6-04/C7/Stage-6 code landed.

## Required response

Write only `docs/design-review-14/RESPONSE.md`, leave it uncommitted, and
modify nothing else. Include exact source/artifact/database identities,
independently derived algorithm and raw-state findings, test results, Contract
questions, and an explicit statement of what READY does and does not open.
