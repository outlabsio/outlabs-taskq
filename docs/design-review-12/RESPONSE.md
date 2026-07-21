# Round 12 — QDarte contact-verify consolidation review response

## Verdict: BLOCKED

The selected destination is sound: the direct catalog cannot safely host the
immutable package catalog, the one-publisher/drain rule is the right migration
shape, and the proposal correctly refuses to use P5's pure-lane recovery proof
as side-effecting-lane evidence. No Tier-0 contract question was found.

Implementation remains closed until the three preconditions below are resolved
docs-first. They are narrow host-integration corrections; none requires a
package SQL, migration, protocol, or authorization-contract change.

## What I independently verified

- `qdarteAPI` migration `20260709_0061_add_taskq_schema.py` creates a
  host-owned `taskq` schema whose direct catalog differs materially from the
  package catalog. The local `qdarteapi_dev` catalog is empty, and every live
  direct function is executable by `PUBLIC`. A package migration into that
  database would collide with existing tables/functions and would not preserve
  the package capability boundary.
- The direct producer plans live QDarte rows and enqueues `comms` /
  `contact_verify_scope`; the direct worker claims, heartbeats, settles, and
  posts each entity result through QDarte HTTP routes. The generic
  `qdarte_ops` ledger remains a separate system.
- The worker performs network verification and the result path writes the
  place, optional contact method, and monthly usage counter. The existing
  replay key is exactly `job_id:attempt_id:entity_key`, so a reclaim with a new
  attempt id can bypass it. The proposal correctly treats a stable
  job-plus-entity key as mandatory before migration.
- The package already has the needed server-side primitives: a runner may
  fence-check/extend a current attempt through `heartbeat`, and an observer may
  read the authoritative job projection through `get_job`. The worker need not
  receive direct database access.
- Targeted API source tests passed through the no-live-DB gate (34 passed) and
  the package's latest fresh-database gate was 470 passed / 1 opt-in skip.

## Preconditions

### R12-01 — BLOCKER: separate-database result authorization is unspecified

**Evidence.** The incumbent result route loads the running job through the
direct `TaskqClient` before it accepts a contact result
(`qdarteAPI/app/domains/workers/api/routes.py:2685-2713`). That client queries
`qdarteapi_dev.taskq`; it cannot validate a package job held in a separate
package database. The proposal requires that the QDarte result service validate
a fenced planned result, but does not name the authority path or operation
ordering that makes that statement true.

**Why it matters.** Simply trusting a worker-supplied queue, type, payload, or
attempt would violate the standing authoritative-row/fence rule. Reusing the
direct query would make every package result fail or tempt a database-grant
bypass.

**Smallest remedy.** Amend the proposal's target topology and C3 gate with an
explicit host-side result bridge:

1. authenticate the worker as `run` only;
2. use a server-owned, capability-sized package runtime to fence-check the
   supplied `(job_id, attempt_id, worker_id)` through the existing runner
   heartbeat path, refusing lost/settled attempts before domain work;
3. read the authoritative package job projection through the server-owned
   observer capability and validate queue, job type, planned entity, and place
   identity without trusting a request echo;
4. apply the stable job-id-plus-entity effect idempotently in the QDarte
   transaction; and
5. leave terminal settlement to the worker-owned replay path.

The amendment must specify response-loss and lease-loss behavior between each
step, deny direct worker database access, and add C3 regressions for wrong
fence, reclaimed attempt, wrong planned entity, result-write commit followed
by lost response, and same-job retry. This uses existing package primitives;
no new wire or SQL contract is needed.

### R12-02 — HIGH: incumbent catalog inventory is inaccurate

**Evidence.** The proposal says the direct migration has fifteen functions.
The migration source defines thirteen `CREATE OR REPLACE FUNCTION taskq.*`
bodies, and the live local catalog independently lists thirteen. The source
also creates `taskq_worker`, while the inspected local cluster currently has
the six package capability roles but no live `taskq_worker`; the direct path
works because `PUBLIC EXECUTE` remains granted.

**Smallest remedy.** Correct the function count to thirteen and state the
source-versus-live role discrepancy explicitly. C1's grant/role inventory must
be a measured preflight artifact for the selected environment, not a source
assumption. Preserve the current conclusion: the public grant and incompatible
catalog still require a separate package database.

### R12-03 — MEDIUM: incumbent worker transport has no tested effective-base-path contract

**Evidence.** The worker default is an admin-proxy origin ending in
`/content-api`, which the runtime documents as the supported worker route.
The direct client appends `/worker/taskq/...`; its three client tests currently
expect an origin-root path and fail because the actual request is
`/content-api/worker/taskq/...`. The current isolated-dev compose instead
points its worker traffic directly to the API origin. This is a test/route
topology disagreement, not proof that either deployed path is wrong.

**Smallest remedy.** Extend C1 with an explicit effective-base-path matrix:
direct API origin versus admin-proxy origin, exact request path after joining,
and one authenticated worker claim/result vector for each supported topology.
Repair the stale client expectation or source only after that matrix determines
the canonical behavior. The consolidation implementation must not inherit an
ambiguous direct-worker URL contract.

## Accepted conclusions and residuals

The no-dual-publish, no-active-import, no-cross-backend-fallback, direct-drain,
and post-publish rollback posture are accepted as the correct baseline. The
attempt-scoped result key is a real defect class, already caught by C3 rather
than being papered over. The pure P5 hard-kill remains explicitly insufficient
for this lane.

After R12-01 through R12-03 land docs-first, a targeted delta check can decide
whether the proposal is READY to open a separate implementation specification.
That future specification remains the only item authorized to change QDarte
source or local queue topology. No production migration, direct-queue
retirement, provider call, broad worker start, or Stage 6 work is authorized
by this response.
