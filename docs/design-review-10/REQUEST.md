# External targeted review — first-host read-model adoption

## Assignment

Audit the proposed first-host adoption plan in
`docs/Task Queue Stage 5 Read Model Host Adoption Specification.md`. Return
**READY** only if the two-artifact release sequence, production migration
boundary, rollback floor, authorization, and evidence matrix are safe and
falsifiable. Otherwise return **BLOCKED** with the narrowest preconditions.

This is a docs-only review. It authorizes no package publication, host commit,
dependency pin, deployment, database/IAM mutation, queue-profile change,
producer/consumer action, retirement action, UI work, or Stage-5 pilot.

## Authority and required derivation

Read `AGENTS.md`, `docs/README.md`, Protocol v1 revision 1.0.7, Function
Manifest 0.1.4, ADR-019/020/021, the Stage-4 host specification, the read-model
specification, and the host's authoritative `main` source. Derive the current
host pin and taskq runtime behavior from source rather than trusting the plan.

In particular, independently establish whether `0.1.0a2` can boot against
0.1.4, whether the proposed `a3` bridge has no H-08/H-11 route surface, and
whether the full artifact could safely precede the migration. Treat any error
as a deployment-blocking finding, not a reason to broaden runtime privileges or
hide a route behind undocumented behavior.

## Required attack program

1. Recompute the two-artifact ordering and ADR-020 rollback floor. Try to
   falsify both pre- and post-migration rollback claims, the owner/admin versus
   runtime credential separation, and immutable 0004–0006 ledger/metadata
   verification.
2. Trace the host's mounted facade, OpenAPI merge, pools, worker, and
   authorization adapter. Verify that adoption adds no host-owned read route,
   direct SQL projection, operator transport, global list, or producer action.
3. Test the proposed `tools` permission/queue boundary against the Protocol:
   profile and `ready` GET success; wrong-queue hiding; unknown authorized queue
   TQ001; malformed cursor/request-ID ordering; and `running`/`finished` typed
   TQ501 with safe details. Confirm profile PUT stays unavailable in the host.
4. Assess every acceptance oracle. Reject a plan that requires production job
   injection for pagination, claims restore/PITR without executing it, treats a
   host counter as an independent external counter, or lets read-model work
   disturb the L1 legacy observation.
5. Confirm the plan leaves all non-goals closed: further capability activation,
   production action, UI, retirement, side-effecting lanes, and Stage 5.

Write only `docs/design-review-10/RESPONSE.md`, leave it uncommitted, and modify
nothing else. Include independent identities, findings, Contract questions,
acceptance/rollback disposition, and an explicit statement of what READY does
and does not open.
