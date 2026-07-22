# Round 20 targeted delta response

## Verdict

**READY.** R20-01, R20-02, and R20-03 are closed docs-first. C8-R1 may open
only after the next naturally scheduled 03:15 backup and the remaining §4
eligibility evidence pass.

This response is owner-authorized, internal, and **not independent**. The same
session wrote and reviewed the remediation because the separate reviewer
remains unavailable. I rechecked the immutable response hash, Git range,
source behavior, read-only production aggregate, and repository gates.

READY does not authorize package enablement, an entity cohort, C8-R2 producer
removal, C8-R3 consumer removal, data/schema deletion, another lane, or Stage 6.

## Evidence

The Round-20 response remains byte-identical at SHA-256
`df8b7e3b52432720072e3f5f14903eb31b8a0d8d8794f340e3cd20820a621574`.
The remediation commit is exactly
`39100fad4f8e43fff49c829636190794dab22332`, touches only the Tier-3
specification, Tier-2 board/plan, and tier map, and carries the required
trailer. It changes no source, configuration, service, IAM, database, queue,
worker, deployment, Tier-0, ADR, SQL, migration, or prior Tier-4 file.

The production aggregate was regenerated read-only from the six retained
`qdarte_ops.worker_jobs` contact payloads by counting only the entities arrays:
`[1,25,86,100,176,293]`, total 681, average 113.50, five completed and one
cancelled. No entity value, place, phone, credential, or provider result was
returned. This is consistent with the Round-19 six-job baseline and exposes the
real gap between the admin's current `limit: 500`, C7's proven one-place
cohort, and the historical maximum 293.

Taskq source identity is unchanged. The authenticated-Redis full gate remains
505 passed with one opt-in skip; Ruff and format are clean.

## Findings closed

### R20-01 — PASS

The amended sequence begins from the accepted real posture: draining API,
paused queue, absent worker, and absent gateway. Submission is a distinct
server-side gate defaulting false. Candidate API and disabled UI deploy first;
then facade/domain/auth/IAM/private origin and depth/concurrency settings are
verified; gateway starts before exactly one closed worker; package startup
earns a fresh direct drain; the operator unpauses only after health; submission
and the admin control enable last.

The failure order is the correct inverse: submission and UI off, queue paused,
worker stopped, gateway stopped, API back to draining. Package history stays
intact and neither direction uses direct fallback, row copy, cross-backend
retry, or compensating enqueue. Each step names its owner.

### R20-02 — PASS

The design no longer treats caller migration as implicit 500-entity
authorization. Production requires an explicit `limit` and rejects absent or
over-current-cap input before drain authorization, reservation, planning, or
provider work. The completed plan is bounded again before finish. Queue
`max_depth`, the fixed concurrency key limit, and closed-worker count are each
one.

The accepted one-place C7 result is retained without a new call. Later caps
require separate C8-E25, C8-E100, and C8-E300 evidence. The 300 stage combines
an exact no-network/sink production-clone path with a natural 101–300 production
cohort. Synthetic filler and re-verification are forbidden. If a natural stage
does not exist, the prior cap remains and any narrower replacement contract is
explicitly owner-accepted before producer removal. Full measured-envelope
parity cannot be claimed without E300.

### R20-03 — PASS

The product posture is explicit: an exact-ID authoritative status read plus a
client-side persisted hint replaces scope rediscovery, while cancellation
remains one-off operator-only. The API principal stays enqueue+read only. UI
vectors must prove that reload/hint loss shows no inferred package job, never
uses the direct list/cancel route for a package ID, and never re-submits merely
to discover an old ID. No shadow mapping or runtime operator is proposed.

## Contract questions

None.

## Preconditions still outside this delta

Before C8-R1 changes a caller, IAM, configuration, service, or production
state, the eligibility packet must record:

1. the next naturally scheduled 03:15 backup passing with all required
   database/globals/copy/object-store/checksum evidence;
2. current live source/image/config identities and health;
3. fresh exact direct/package/domain baselines with zero active direct work;
4. package metadata/readiness and one authorized existing-job read;
5. current caller/deployment/access-log inventory; and
6. immutable rollback images/settings.

An on-demand backup does not satisfy item 1. A failed scheduled run blocks C8-R1
until explained and followed by a scheduled success.

## Scope opened

READY opens C8-R1 caller/status-floor implementation only after all six
eligibility items pass. C8-R1 must still stop for its own acceptance before
any direct producer becomes unreachable. C8-R2, C8-R3, history/schema removal,
another lane, and Stage 6 remain closed.
