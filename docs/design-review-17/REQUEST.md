# Internal targeted review — QDarte C7-01 production preflight

## Scope and provenance

Audit the executed C7-01 disabled production preflight against the accepted
Stage-5 C7 environment plan. The owner authorized this implementation session
to perform the review because the usual separate review session is
unavailable. Any response must state clearly that it is internal and
non-independent.

Review these exact repository ranges and live identities:

- outlabs-taskq authority at the request commit, including the frozen C7 plan;
- QDarte API `5e25ab695b6c8f7c5bf92c649c0f78413553e467..33031263e04c777b8a4ccc4788703cf496f9d852`;
- QDarte workers `f7427cb7ffd759eb7d2a0ec7d00a1dd830b23497..c8c03bbd369e5af09951183dcd5ab979a6c4fc55`;
- QDarte runtime `a6117c6e22a855ce1d1f57ed059be0eeda7b15fa..9fec99c8824bc660a2835375703549d54cc59441`;
- deployed API code/image commit `65fbd225cb849619653b0d3f56e1d5a5b0e252b1`;
- deployed worker source `c8c03bbd369e5af09951183dcd5ab979a6c4fc55`;
- deployed runtime controller code `36bfe69be22f52ca61ac7b8d99731696c29e1008`;
- image ID
  `sha256:bb734fa568d44657b4edf7dd249f0a4c7e0019c44c0bc819bbdcfac8dbd89f88`;
- transfer artifact SHA-256
  `9fa8b04434e2d92ddabe685f99cfdb6df377eaa09a35a2c50c1d1cfb83cccfb5`.

The host evidence packet is QDarte API
`docs/taskq-contact-c7-01-production-preflight-evidence.md` at `3303126`.
Treat it as a claim to falsify, not an oracle.

READY may open only the already-frozen C7-02 one-country/one-place cohort. It
does not itself enqueue a package job, start the closed worker/egress service,
authorize a provider call, retire the direct lane, expand to another lane, or
open Stage 6.

## Authority order

1. Transport Protocol v1 revision 1.0.8 and Function Manifest 0.1.5.
2. ADR-020, ADR-022, and ADR-023.
3. The accepted C6 compatibility/cutover specification and C7 environment
   plan, including C7-CQ-02.
4. Current source, migrations, operational scripts, and live read-only state.
5. The evidence packet and task board.

If source or live state contradicts higher authority, return BLOCKED. Record a
Contract question only for a real Tier-0 conflict; do not repair source during
the review.

## Required attack program

1. **Source convergence and artifact identity.** Re-derive each branch from
   its stated `origin/main`, verify the ranges are pushed and clean, inspect
   the forward-ported C6 boundary rather than trusting commit messages, and
   connect the deployed image and immutable taskq a6 pin to the reviewed
   source. Reject unclassified paths or credential-bearing image history.
2. **Incumbent privilege conversion.** Derive role attributes, memberships,
   database ACLs, schema/table/sequence/function grants, and owner default
   privileges from the manifest. Challenge the API and ordinary-worker split,
   owner-free startup, host-only backup control, secret-free desired state,
   mode-0600 worker injection, and package blindness. Extra privilege is a
   failure.
3. **Exact contact identities.** Verify the domain identity receives only the
   named contact/auth tables and no future defaults. Verify facade/operator
   capability equality, cross-database denial, no operator escalation, no raw
   taskq read, no schema/admin powers, and queue-scoped enqueue/run denial in
   the opposite direction.
4. **Auth lifecycle and continuity.** Source-audit the facade's explicit
   OutLabsAuth initialize/readiness/shutdown ordering. Verify the real human
   login, authenticated read, service-token path, rolled-back domain write,
   and ordinary-worker zero-job claim under restricted identities.
5. **Database chain and disabled state.** Verify host Alembic head
   `20260721_0076`, immutable taskq migrations 0001–0007, contract 0.1.5,
   exact active capabilities, repeated `verify()`, operator-only queue
   provisioning, queue paused, and equality to zero for jobs, attempts,
   events, and admissions.
6. **Topology and secrets.** Derive the Compose graph. The facade must be
   unpublished; the main API must not mount its routes; contact mode must be
   `legacy`; the closed worker and egress gateway must be absent; runtime
   containers must lack owner/migration/backup/run/egress credentials and
   control mounts. The accepted exact private origin and network-only worker
   design must remain source-complete for C7-02 without being started now.
7. **Connection arithmetic.** Recompute `M=100`, the 180-sample `H=16`, the
   facade increment 1+2, and `H+3 <= M-20`. Check current sessions only as a
   corroborating sample, never as a replacement for the observation window.
8. **Durability.** Independently inspect backup `20260722-185358`, checksums,
   external copies, and restore output. It must cover API, package, Intake,
   and globals atomically. Verify the installed recurring wrapper persists the
   contact-package flags and that the restore drill dropped only its named
   temporary databases.
9. **Conservation.** Recompute the six direct/effect full-row digests and
   direct status/lease counts. Verify no package row or provider/effect action
   occurred, and no manual queue DML, row copy, fallback, or dual publication
   entered the implementation.
10. **Readiness and failures.** Challenge wrong contract/capability, package DB
    loss, domain DB loss, and privilege loss. Verify health cannot remain 200
    when a dependency is unusable. Inspect the honest broad-API red baseline
    and decide whether any failure belongs to C7 rather than accepting its
    classification by assertion.
11. **Gates and resource honesty.** Reproduce taskq 505/1, runtime 1144,
    workers 627, focused API 34, Ruff/format/MyPy scopes, container/session
    ledgers, and image disk posture. Do not turn the known Mini87 disk pressure
    or host-forward residual into an unrecorded workaround.
12. **Scope.** Confirm the review and preflight performed no package enqueue,
    provider call, result application, direct retirement, non-contact work, or
    Stage-6 action.

## Response

Write only `docs/design-review-17/RESPONSE.md`, initially uncommitted. Return
READY or BLOCKED, list every attack-program disposition, findings by severity,
Contract questions, the internal/non-independent provenance, and the exact
scope opened.
