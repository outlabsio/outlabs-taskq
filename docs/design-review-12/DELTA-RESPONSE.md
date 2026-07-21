# Round 12 — QDarte contact-verify consolidation delta response

## Verdict: READY for implementation specification only

The three Round-12 preconditions are closed by the docs-only remediation at
`b854f46`. The original response remains byte-identical to its recorded
historical file (SHA-256 `34b160da143751fa7f800680e78f551b26111f39f1946b204f9258dd02a12f32`).
No Tier-0 contract question was found and no package SQL, migration, protocol,
or authorization contract changed.

This verdict authorizes one subsequent task only: a separately boarded
direct-contact implementation specification for isolated local development.
It does not authorize QDarte source, database, IAM, worker, route, deployment,
provider-call, direct-queue, retirement, cloud, or production change.

## Delta checks

### R12-01 — accepted

Section 5.1 now names the complete server-owned result bridge and its order:
run-only authentication; runner heartbeat with the supplied job, attempt, and
worker identities; observer read of the authoritative package projection;
stable job-plus-entity domain application in the QDarte transaction; then
worker-owned terminal settlement/replay. It explicitly rejects direct worker
database access and request echoes as authority.

The lease- and response-loss cases are also no longer implicit. A lost,
settled, absent, or projection-mismatched attempt performs no QDarte write; a
committed domain transaction whose response is lost re-enters through the
stable effect key; and a reclaimed old attempt cannot write after its heartbeat
is rejected. C3 now requires the wrong-fence, reclaimed-attempt,
wrong-planned-entity, same-job-retry, and commit-then-lost-response vectors.

### R12-02 — accepted

The incumbent inventory now correctly records thirteen direct functions.
It distinguishes the source migration's `taskq_worker` role from the measured
local cluster, where that role is absent and `PUBLIC EXECUTE` makes the direct
path work. C1 requires the later implementation to record that source/live
role-and-grant posture rather than assuming either side is the deployment
truth.

### R12-03 — accepted

The specification now freezes both supported URL compositions: a direct API
origin joins to `/worker/taskq/...`, while the admin-proxy base ending in
`/content-api` joins to `/content-api/worker/taskq/...`. C1 requires an
authenticated claim and result vector for each topology before source or test
changes decide which setting is canonical. This matches the current worker
default's proxy prefix and prevents a test-only root-path assumption from
becoming an implicit wire change.

## Carried gates

The direct queue remains authoritative. The future work remains one publisher
only, drains the direct lane before package admission, never imports active
rows or cross-backend retries, and must meet C1–C7 before any external contact
verification. The pure P5 hard-kill proof remains insufficient for this lane.
