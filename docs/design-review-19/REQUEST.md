# Internal completion review — QDarte C7 production contact lane

## Scope and provenance

Audit the complete C7-01 through C7-03 production-evidence sequence against the
accepted Stage-5 C7 environment plan. The owner authorized this implementation
session to perform the review because the usual separate review session is
unavailable. Any response must state clearly that it is internal and
non-independent and must regenerate evidence rather than accepting this packet
as corroboration.

Review these exact identities:

- outlabs-taskq authority at this request commit, including the C7 plan and
  board;
- QDarte API C7 range
  `33031263e04c777b8a4ccc4788703cf496f9d852..78d5ce5b8d731fda71d590fbde03d4b4a434bf78`;
- QDarte workers C7 range
  `c8c03bbd369e5af09951183dcd5ab979a6c4fc55..0c795d69c3605cab5a7d133dce8159d9b11e3994`;
- QDarte runtime C7 range
  `9fec99c8824bc660a2835375703549d54cc59441..17e78a4e077bc9c238dbcca8f97a9d386a4331f5`;
- deployed API build
  `45c05cb8c017582736796a5f03f32f88f959f0cd` and image
  `sha256:e0f60c9a2fb9fc0a4fcd1260bb4a3458dace02bf14428fdab81b44389dc0bbf9`;
- final worker/gateway artifact image
  `sha256:3a2f2572286f710abd00e822b78108ce7cfb7579c1cab3f516868b7261c686b3`;
- production database instance
  `45677dd9-2717-4d80-bdf7-a09a94a95221`;
- C7-03 recurring backup timestamp `20260722-203544`.

The C7-03 packet is QDarte API
`docs/taskq-contact-c7-03-continuity-evidence.md` at `78d5ce5`, SHA-256
`a85a015a988e9845c3d33d5addc6664f214c1439954554c9d3c3e44f87ed35f5`.
The earlier C7-01 and C7-02 packets and Rounds 17–18 are inputs, not oracles.

READY may open only a separately written and accepted direct-retirement
specification. It does not itself retire the direct producer/consumer, unpause
or enqueue package work, migrate another lane, broaden a worker, or open Stage
6.

## Authority order

1. Transport Protocol v1 revision 1.0.8 and Function Manifest 0.1.5.
2. ADR-020, ADR-022, and ADR-023.
3. The accepted C6 compatibility/cutover specification and C7 environment
   plan.
4. Current source, immutable release artifacts, migrations, operational
   scripts, and live read-only state.
5. Evidence packets, prior internal responses, and the task board.

If source or live state contradicts higher authority, return BLOCKED. Record a
Contract question only for a real Tier-0 conflict. Modify no source or host
state during the review.

## Required attack program

1. **C7 sequence and source identity.** Derive the C7-01/02/03 order, exact
   repository tips, deployed build/images, and evidence hashes from Git and
   artifacts. Reject an unclassified forward port, dirty tree, or execution
   against an unreviewed image.
2. **Privileges, topology, and budget.** Recheck the dedicated non-superuser
   API, worker, facade, domain, operator, owner, and backup responsibilities;
   private facade; internal-only worker; dual-homed gateway; absence of runtime
   owner/control secrets; and accepted connection arithmetic. Reject any
   privilege collapse or new public package route.
3. **C7-02 retained truth.** Re-derive the exact one-place keyed admission,
   one external access, one stable application/method/usage unit, and the raw
   `succeeded / 3 attempts / 2 failures / 0 releases` history. Reject any
   summary that rewrites the two private-gateway failures as first-attempt
   success.
4. **Two normal cycles.** Verify both C7-03 API replacements are distinct,
   healthy, on the accepted immutable image/build/database, and executed with
   explicit `--no-deps`. Confirm mode `package`, queue paused, and worker plus
   gateway absent after each cycle.
5. **Zero-insert and durable conservation.** Independently recompute the full
   direct contact job/attempt/event counts and SHA-256 hashes, direct terminal
   statuses, running-attempt count, package job/attempt/event/admission hashes,
   and stable application/contact-method/usage hashes. Compare them to both
   ends of the C7-03 window; reject any row insertion, update, delete,
   cross-backend replay, or unexplained effect.
6. **Recurring backup.** Inspect the installed host wrapper and timestamp
   `20260722-203544`. Verify the API set atomically contains API, contact taskq,
   globals, manifest, and checksums; the matching Intake set contains Intake,
   globals, manifest, and checksums; local/external copies match; and the
   configured object-store uploads completed. Confirm retention touched only
   the expired named set.
7. **Restore and globals.** Recheck checksum validation, the supported
   three-database disposable restore counts/contracts, cleanup, and the two
   network-isolated PostgreSQL 18 globals-plus-dump restores. Verify the 12
   named roles, `taskq_owner` ownership, and absence of leftover restore
   databases, containers, or volumes. Do not accept archive readability as a
   restore proof.
8. **Structured egress residue.** Verify the mode-0600 persisted artifact log
   hashes to
   `f061b8d506007636a3f5683f79698f7ce5465e2a934f7faf80fd1de0a8779109`,
   contains exactly one bounded counter line, and came from the final image
   under `--network none` with an injected no-I/O verifier. Confirm it is an
   artifact proof, not misrepresented as a second production provider call.
9. **Rollback and final posture.** Derive the mode-only, zero-DML
   `package -> draining` rollback and explicit `--no-deps` replacement. Recheck
   API/container health, the supported Caddy host route, production identity,
   queue pause, private facade, absent worker/gateway, retained package/effect
   history, and no automatic package-to-direct replay. Treat the already
   documented direct OrbStack `8011` host-reset behavior according to the
   existing production runbook, not as a newly invented success claim.
10. **Failure honesty.** Challenge the failed syntax-only artifact command, the
    first restore invocation's wrong Docker PATH, and the initial isolated
    restore attempt. Confirm each failed before the claimed boundary, cleanup
    was exact, and the successful replacement proof is stronger rather than a
    hidden workaround.
11. **Gates and hygiene.** Reproduce or validate taskq 505/1 with authenticated
    Redis, workers 628, runtime 1,144, expanded API contact boundary 60,
    relevant Ruff/format/MyPy scopes, trailers, clean worktrees, and pushed
    branches. Verify no Tier-0, migration, SQL, or prior Tier-4 file changed.
12. **Scope.** Confirm C7-03 performed no new enqueue/provider/domain action,
    direct retirement, other lane, broad-worker action, or Stage-6 work.

## Response

Write only `docs/design-review-19/RESPONSE.md`, initially uncommitted. Return
READY or BLOCKED; list every attack-program disposition, findings by severity,
Contract questions, internal/non-independent provenance, and exact scope
opened.
