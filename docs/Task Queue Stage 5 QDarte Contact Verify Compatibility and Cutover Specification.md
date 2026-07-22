# taskq — Stage 5 QDarte contact-verify compatibility and cutover specification

> **Status:** Tier-3 planning only. This document sequences the C6 local
> compatibility-and-rollback work and the later C7 production-evidence gate
> after accepted isolated-local CV-01..CV-05 evidence. It authorizes no
> production change, direct-queue retirement, package migration in a lasting
> environment, broad worker, cloud target, or non-contact lane.
>
> **Authority:** subordinate to the Transport Protocol v1, Function Manifest
> 0.1.4, ADR-006, ADR-007, ADR-010, ADR-011, ADR-020, ADR-022, the Build Plan,
> the Stage 5 QDarte Contact Verify Consolidation Specification, and its
> accepted Round-12 delta. The consolidation specification owns the C1–C7
> destination and safety rules. This document owns only the work order and
> evidence required before a future, separately approved cutover.

## 1. Objective and fixed boundaries

The direct QDarte `contact_verify_scope` lane remains authoritative. C6 must
prove that a package-backed host adapter can preserve its caller contract while
selecting exactly one backend, draining the direct lane before any package
publish, and returning to a safe configured posture without copying or
recreating work. C7 must later prove the production environment and bounded
cohort before the direct lane can even become eligible for retirement.

This plan deliberately does **not**:

- change the direct `taskq` schema, its functions, grants, routes, worker, or
  existing history;
- admit a package contact job outside an accepted local C6 exercise;
- dual publish, import active direct jobs, replay across backends, or fall back
  after an ambiguous enqueue, reporter, or settlement outcome;
- start a broad QDarte worker, use a worker database password, or add a
  generic package producer/result endpoint;
- select a production, cloud, Mac-mini, or lasting package target; or
- retire, drop, rename, or modify the direct catalog or a QDarte legacy ledger.

Every C6 implementation increment must be local and disposable until a
separate targeted review accepts its complete evidence. Every C7 increment
requires a separately approved environment-specific plan. A passed earlier
slice never implies permission for a later slice.

## 2. Preconditions and carried evidence

CV-01 through CV-05 established only the following reusable evidence:

| Evidence | What it establishes | What it does not establish |
| --- | --- | --- |
| C1 base-path inventory | The direct worker’s two authenticated claim/result path forms are source-backed. | Public producer compatibility or a package-backed host adapter. |
| C2/C3 local bridge | A package result can be checked through a server-owned heartbeat/observer bridge and stable `(job_id, entity_key)` effect ledger. | A lasting package database, production credentials, or direct-lane cutover. |
| C4/C5 local effect drills | One closed worker has a bounded canary, committed-response replay, and real lease-expiry reclaim with no duplicate domain effect. | Caller compatibility, direct drain, rollback after package publish, or production evidence. |

The direct lane is therefore the only permitted normal publisher and consumer
at the start of every C6 slice. A failed C6 test leaves it authoritative and
does not create a compensating package or direct job.

## 3. C6 local compatibility and rollback program

### C6-00 — contract and topology inventory

Before host source changes, record from one named QDarte source revision:

1. every public and internal caller of contact verification;
2. request grammar, validation, status/body shape, idempotency-key semantics,
   authorization, and operational-status behavior;
3. direct producer, direct worker, result route, queue/type identity, and
   direct raw-ledger state/high-water; and
4. the exact configured environment variables and deployment topology that
   select the existing lane.

The inventory becomes the compatibility ledger. Each field must cite source or
an executable vector. A test name or old design text is not evidence. If an
existing caller relies on behavior that the package host adapter cannot
preserve, stop for a Tier-3 amendment or a separately approved public API
revision; do not silently change the caller contract.

### C6-01 — explicit one-publisher mode design

Add a closed, startup-validated host mode with only these values:

| Mode | Producer | Consumer | Required posture |
| --- | --- | --- | --- |
| `legacy` | Direct producer only | Direct worker only | Current default and rollback baseline. |
| `draining` | No new contact enqueue | Direct worker only until terminal direct drain | No package admission. |
| `package` | Package facade only | Closed package worker only | Available only after every prior C6 gate passes. |

The mode is sampled once per request. Invalid, absent, or mixed configuration
fails closed during startup. Neither request retries nor host exceptions may
switch modes. The adapter must construct a package command only from the
authoritative host-planned scope; it must not expose a generic producer,
worker-fence operation, copied direct worker model, or worker database
credential.

#### C6-01 configuration decision

The package-lane selector is a new, contact-only
`QDARTE_CONTACT_VERIFY_MODE` setting. Its unset value is exactly `legacy`.
Only the exact lowercase values `legacy`, `draining`, and `package` are valid;
whitespace, aliases, comma-separated values, and any simultaneous package
selector fail startup. It must not read, reinterpret, or inherit any
`QDARTE_TASKQ_*` setting.

Those older settings and their `/ops/taskq`/`/ops/cutover` routes belong to
the incumbent host-owned direct catalog recorded in the C6-00 ledger. They
are neither the package database nor a package migration bridge. C6-01 first
adds the separate mode parser and local routing seam. Until C6-02 has a fresh
drain attestation and C6-03 supplies the scoped package adapter, `package`
is deliberately unavailable and fails closed before admission. `draining`
refuses new contact enqueue with a fixed host-owned `503` detail that contains
no queue identity, SQL text, credential, payload, or fallback instruction.

The existing `/ops/cutover/jobs/contact-verify-scope` response discriminator
is an explicit compatibility decision for C6-03: it cannot silently survive
as an indicator of backend choice. C6-01 must pin the legacy route behavior
and prove the new mode seam never calls either direct or package producer when
it refuses an invalid, draining, or not-yet-admissible package configuration.

Required local vectors:

- each valid mode reaches only its named producer/consumer seam;
- invalid/mixed mode refuses boot before either producer is called;
- a keyed request creates at most one job in exactly one ledger;
- ambiguous package admission has no direct fallback; and
- an adapter failure returns the frozen caller-compatible error without package
  internals, credentials, fences, or raw SQL text.

### C6-02 — direct drain and package-admission interlock

The direct producer must be disabled before a drain begins. The drain oracle
reads the direct system only and proves, for every direct
`contact_verify_scope` job, zero `queued`, `blocked`, or `running` rows, no
leased attempt, and stable jobs/attempts/events high-waters across a bounded
second observation. Existing direct work is resolved only in the direct
system; it is never copied, transformed, or re-enqueued in the package.

The `package` mode must refuse admission unless it receives a fresh successful
drain attestation for the same named local exercise. The attestation is
bounded, cannot be hand-edited, and records only counts, identifiers/high-water
values, timestamps, and a source revision—never payloads, provider results,
or credentials. A direct insertion after the first sample invalidates the
attestation and returns the host to `draining` or `legacy`; it must not permit
package admission.

### C6-03 — caller-compatible package adapter

Once C6-00/01/02 vectors are green, implement the smallest host adapter behind
the existing caller boundary. It must preserve the compatibility ledger’s
authorized request/response contract and idempotency behavior while recording
only bounded diagnostics. Its package credentials are capability-sized:

- the facade runtime has producer/runner/observer/housekeeper memberships;
- the closed worker has a short-lived queue-scoped `run` credential and no
  package database password;
- local test/harness enqueue and read credentials are distinct, short-lived,
  process-only, and cannot access a public or non-contact path; and
- owner and operator identities are used only for explicit administration.

No package route becomes part of the normal QDarte app merely because the
adapter exists. The private reporter bridge retains its CV-04 closed union and
the worker remains permanently restricted to the one contact type/queue.

### C6-04 — rollback exercises

Execute the following local exercises without manual queue-table DML:

| Moment | Required action | Oracle |
| --- | --- | --- |
| Before package publish | Return to `legacy`; assert no package contact row was created. | Direct ledger remains the sole publisher/consumer. |
| After package publish, before external work | Stop package admission and use typed package controls to drain/cancel. Do not recreate any job directly. | Package ledger explains every job; direct high-water remains unchanged. |
| After external work | Stop automatic switching. Preserve package history and resolve through the stable effect oracle. | No cross-backend replay and no duplicate domain/usage effect. |

The final case is a decision boundary, not an automatic rollback procedure.
It must surface the exact retained package job/effect state to the authorized
operator without exposing fences, raw provider text, or credentials.

### C6-AUDIT — local cutover acceptance

An independent targeted review must derive the compatibility ledger from the
then-current QDarte source, inspect the mode dispatch rather than trusting
configuration names, rerun the direct-drain/package-admission interlock, and
challenge every rollback claim with raw-ledger plus domain-effect oracles. It
must reject any dual publisher, active-job import, cross-backend fallback,
broadened worker, direct package-table access, or unbounded package route.

Acceptance opens **only** C7 planning. It does not authorize a lasting package
database, package production migration, production package publish, direct
retirement, or a non-contact lane.

## 4. C7 production-evidence sequence (planned, not authorized)

C7 begins only after C6-AUDIT acceptance and a distinct environment decision.
The following future tasks are sequenced now so that no production work is
improvised:

| Future task | Required evidence | Stop condition |
| --- | --- | --- |
| C7-00 environment plan | Named environment, topology, account/credential ownership, connection arithmetic, backup and restore target, and explicit direct-lane baseline. | Any missing least-privilege or restore authority. |
| C7-01 preflight | Immutable release/pin, migrate/verify twice, non-superuser runtime boot, owner/operator negative vectors, successful test restore, and zero direct-lane change. | A runtime login can administer, bypass RLS, or access direct package base tables. |
| C7-02 bounded cohort | One declared bounded contact cohort, a package-only keyed pair, canonical caller read, package/domain/provider independent counters, and no direct insert. | Any double publish, fallback, unbounded cohort, or oracle disagreement. |
| C7-03 two normal cycles | Two normal deployments/cycles while the package lane is observed, plus a zero-insert direct-ledger window and rollback rehearsal. | Any direct insertion or unexplained external/domain count. |
| C7-AUDIT | Independent evidence review against raw ledgers, backups/restores, caller suite, operator credentials, and external counter. | Any incomplete C7 requirement. |

Even C7-AUDIT acceptance permits only a separate direct-retirement
specification. Dropping/renaming the direct schema, deleting history, or
retiring unrelated QDarte workers remains a later, separately approved change.

## 5. Acceptance ledger and project board order

| Board item | Completion criterion | Opens |
| --- | --- | --- |
| S5-QD-C6-SPEC | This document and C6/C7 sequencing are recorded docs-first. | C6-00 inventory only. |
| S5-QD-C6-00 | Source-backed compatibility ledger and direct high-water baseline. | C6-01 implementation design. |
| S5-QD-C6-01 | Closed-mode and no-fallback local vectors. | C6-02 drain interlock. |
| S5-QD-C6-02 | Direct drain attestation and package-admission refusal vectors. | C6-03 adapter. |
| S5-QD-C6-03 | Caller-compatible package adapter plus scoped identity vectors. | C6-04 rollback exercises. |
| S5-QD-C6-04 | Three rollback postures proven locally with no row-copy fallback. | C6-AUDIT. |
| S5-QD-C6-AUDIT | Targeted acceptance of all C6 evidence. | C7-00 planning only. |
| S5-QD-C7-00..AUDIT | Each future production-evidence task above. | Separate direct-retirement specification only. |

## 6. Non-goals and standing safety rules

The local C6 program does not satisfy C7’s backup/restore, independent
production counter, two-cycle, or direct zero-insert evidence. Neither C6 nor
C7 waives the hard-kill requirement already met only in the isolated local
contact lane; any new side-effecting scope needs its own result-boundary proof.
The broader QDarte worker fleet, the `qdarte_ops` legacy ledger, direct queue
retirement, and all non-contact work remain outside this plan.
