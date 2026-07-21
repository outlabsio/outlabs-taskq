# External targeted review — Stage 5 QDarte local-first pilot

## Assignment

Audit the proposed local-only QDarte pilot in
`docs/Task Queue Stage 5 QDarte Pilot Specification.md`. Return **READY** only
if P0–P5 can prove the library fit without changing QDarte's existing worker
ledger, running a side effect, or widening a credential boundary. Otherwise
return **BLOCKED** with the narrowest preconditions.

This is a planning review. It authorizes no QDarte source edit, compose change,
package pin, database/IAM mutation, queue provisioning, worker start,
deployment, production action, existing-lane migration, retirement, UI work,
or Stage 6 action.

## Authority and required derivation

Read `AGENTS.md`, `docs/README.md`, Protocol v1 revision 1.0.7, Function
Manifest 0.1.4, ADR-006, ADR-011, ADR-020, the Build Plan, and the Stage 5
QDarte Pilot Specification. Treat Tier 0 and ADRs as controlling.

Independently inspect the current QDarte sources at:

- `/Users/macbookm3/Documents/projects/qdarteAPI`
- `/Users/macbookm3/Documents/projects/qdarte-workers`
- `/Users/macbookm3/Documents/projects/qdarte-runtime`
- `/Users/macbookm3/Documents/projects/qdarte`

The local clones are not assumed current: their checked branches may be behind
their remotes and the UI clone has an unrelated untracked `.wrangler/`
directory. Derive the intended implementation baseline and all queue/handler
claims from the authoritative source state rather than trusting the proposal's
inventory. Do not modify any repository while reviewing.

In particular, establish whether the proposed adapter can invoke the named
empty-input calculation without API, database, network, browser, filesystem,
provider, media, communication, or legacy-queue side effect. A claim of purity
must be source-backed, not inferred from a test name.

## Required attack program

1. **Legacy boundary.** Reconstruct QDarte's existing `qdarte_ops` queue,
   worker-control routes, registry, worker claim loop, and all consumers. Try
   to falsify the claim that a separate `taskq.qdarte_pilot` queue and job type
   cannot be claimed, inserted, or settled by the legacy fleet. Require a
   before/after raw-ledger oracle that detects inserts, updates, and high-water
   drift, not merely row count.
2. **Artifact and migration bridge.** Verify that immutable `v0.1.0a3` is the
   correct exact artifact for a local `0001`–`0005` install, accepts only the
   declared SQL-contract set, exposes no read-model surface, and does not need
   migration `0006`. Challenge the proposed owner/admin-only migration path,
   `verify()` checks, zero manual metadata DML rule, and the claim that no
   rollout or production rollback floor is implicated.
3. **Credentials and topology.** Recompute the isolated compose connection
   budget from actual API, worker, pool, listener, and reserve settings. Trace
   every proposed identity. Confirm the long-lived API login is not a
   superuser/operator, the HTTP worker receives no database credential, the
   owner/admin and one-off operator credentials stay out of runtime pools, and
   the queue-scoped service token has only its declared `run` permission. Try
   to bypass the boundary using `SET ROLE`, direct tables, wildcard scopes,
   wrong queue, wrong token, or a public generic enqueue route.
4. **Pilot behavior.** Attack the deterministic shadow, keyed canary,
   response-loss settlement replay, and hard-kill vectors. The oracle must
   prove one handler invocation after a committed response loss; after a true
   termination beyond grace, a second worker must reclaim the **same** job id
   and the attempts/events/budget ledger must conserve. Reject sleeps or log
   assertions in place of barriers, durable state, or resource ledgers.
5. **Isolation and rollback.** Confirm the existing isolated smoke remains
   valid, source environment/production mounts stay masked, disabling the
   pilot requires no taskq or legacy-table DML, and all existing QDarte health
   checks remain green. Reject dual publication, a legacy shadow job, a copied
   taskq HTTP or SQL surface, any external side effect, chaining/followups, and
   read-model/UI activation.
6. **Stage boundaries.** Confirm this plan does not waive the future
   side-effecting-lane hard-kill gate, disturb the ongoing L1 observation, or
   authorize production, an existing-lane migration, retirement, or Stage 6.

## Required response

Write only `docs/design-review-11/RESPONSE.md`, leave it uncommitted, and
modify nothing else. Include the exact source identities inspected, the
reviewer's independently derived queue/worker/handler inventory, findings and
Contract questions, each acceptance-oracle disposition, and an explicit
statement of what **READY** does and does not open. A READY result may
authorize only P0–P5 in an isolated disposable local environment.
