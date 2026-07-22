# Internal targeted review — QDarte C7-02 one-place production cohort

## Scope and provenance

Audit the executed C7-02 cohort against the accepted Stage-5 C7 environment
plan. The owner authorized this implementation session to perform the review
because the usual separate review session is unavailable. Any response must
state clearly that it is internal and non-independent.

Review these exact repository ranges and identities:

- outlabs-taskq authority at this request commit, including the frozen C7 plan;
- QDarte API `33031263e04c777b8a4ccc4788703cf496f9d852..4f6cfd008855beaf84c5ef466430b230fcc08cdc`;
- QDarte workers `c8c03bbd369e5af09951183dcd5ab979a6c4fc55..0c795d69c3605cab5a7d133dce8159d9b11e3994`;
- QDarte runtime `9fec99c8824bc660a2835375703549d54cc59441..6fcc8ef6deed6918be13d0fd7919c0ed84b97dea`;
- cohort API execution commit
  `45c05cb8c017582736796a5f03f32f88f959f0cd`;
- cohort worker execution commit
  `ab5daf02afede19e100065cfb36721537a545c17`;
- cohort runtime execution commit
  `20f8f3ef2b6418e9a7e5234ca8295a762d34bfe1`;
- cohort API image
  `sha256:e0f60c9a2fb9fc0a4fcd1260bb4a3458dace02bf14428fdab81b44389dc0bbf9`;
- cohort worker/gateway image
  `sha256:2feadc27a2143570cd4695ac49f876bace0aaa400251a214d20bcf8c59e073cc`;
- stopped next-run worker/gateway image
  `sha256:3a2f2572286f710abd00e822b78108ce7cfb7579c1cab3f516868b7261c686b3`.

The host evidence packet is QDarte API
`docs/taskq-contact-c7-02-one-place-cohort-evidence.md` at `4f6cfd0`,
with SHA-256
`be2cf8c7ddeb9c05410b97eb98a860dd15c089fb5ee067fbb1a8b394e194bd39`.
Treat it as a claim to falsify, not an oracle.

READY may open only the already-frozen C7-03 deployment/zero-insert,
backup/restore, and rollback-evidence slice. It does not retire the direct lane,
open another lane, broaden the worker, or open Stage 6.

## Authority order

1. Transport Protocol v1 revision 1.0.8 and Function Manifest 0.1.5.
2. ADR-020, ADR-022, and ADR-023.
3. The accepted C6 compatibility/cutover specification and C7 environment
   plan.
4. Current source, migrations, operational scripts, and live read-only state.
5. The evidence packet and task board.

If source or live state contradicts higher authority, return BLOCKED. Record a
Contract question only for a real Tier-0 conflict; do not repair source during
the review.

## Required attack program

1. **One-place bound.** Re-derive the exact country/allowlisted-place request,
   provider-free planner result, selected place identity, `limit=1`, and
   pre-recorded key. Reject a second entity, hidden fallback, or unbounded
   source.
2. **Admission identity.** Derive reserve-before-plan behavior and verify
   `created` then `existed` returned the same job and receipt. Inspect the
   raw admission and job linkage; reject row copies, manual taskq DML, or
   cross-backend replay.
3. **Drain and exclusivity.** Source-audit the production direct-drain observer,
   same-process authorization, mode sampling, and exact Compose settings.
   Recompute the direct job/attempt/event full-row digests and active/lease
   counts before accepting the no-direct-insert claim.
4. **Execution history.** Inspect the one job, all three attempts, seven events,
   terminal canonical read, failure budget, and settlement. Confirm the first
   two 422s occurred inside the private gateway before verifier invocation and
   that the third attempt alone reached the external path.
5. **Stable effect.** Reconcile exactly one result application, exactly one
   selected-place contact method, the non-verified disposition, and the usage
   delta from zero to one. Reject any second place, duplicate effect, or
   provider fallback.
6. **Independent egress evidence.** Inspect the actual gateway access ledger,
   network topology, fixed destination, and count. Decide explicitly whether
   the one 200 access line plus taskq/domain/usage oracles is sufficient despite
   the missing historical structured counter. Verify the final network-disabled
   artifact proof emits the bounded exercise/destination/disposition/count
   record and performs no provider action.
7. **Privileges, tokens, and topology.** Verify the worker had only run and
   gateway credentials, no database or enqueue credential, and no external
   network; the gateway alone was dual-homed; the facade remained private; and
   all cohort tokens were removed. Recheck operator-only pause and absence of
   owner/control credentials in runtime containers.
8. **Failure honesty.** Challenge every disclosed incident: missing drain
   settings, accidental dependency recreation, stale token, missing executable,
   carrier ceiling, and logger threshold. Verify each was fail-closed at the
   claimed boundary, left the direct/database identity intact, and has the
   stated regression or artifact proof.
9. **Disk cleanup and artifacts.** Verify pruning touched only disposable cache
   and untagged layers, retained tagged current/rollback images and volumes, and
   left enough headroom. Connect cohort and stopped next-run image IDs to their
   reviewed source.
10. **Final posture.** Live-recheck API health and `draining` mode, taskq queue
    paused, facade private/healthy, worker and gateway absent, one preserved
    terminal job, and direct-ledger equality.
11. **Gates and hygiene.** Reproduce taskq 505/1 with authenticated Redis,
    workers 628, runtime 1144, API boundary 49, relevant Ruff/format/MyPy
    scopes, clean pushed worktrees, trailers, and exact evidence hash.
12. **Scope.** Confirm no direct retirement, another lane, broad worker,
    unreviewed provider action, or Stage-6 work occurred.

## Response

Write only `docs/design-review-18/RESPONSE.md`, initially uncommitted. Return
READY or BLOCKED, list every attack-program disposition, findings by severity,
Contract questions, internal/non-independent provenance, and exact scope opened.

