# Round 14 — QDarte C6-03B durable admission repin — Response

## Verdict

**READY — the isolated QDarte admission repin and C6-03B evidence satisfy the
targeted gate.** The already-frozen local C6-04 rollback exercises may begin.

This is an owner-authorized **internal** review performed by the same Codex
implementation session because the usual separate review session was
unavailable. It is not represented as independent external assurance. The
review re-derived the requested artifact, source, privilege, raw-state, and
test evidence rather than merely accepting the implementation report.

READY does not authorize a worker or provider run, production migration,
existing-queue mutation, direct-queue retirement, non-contact work, C7, or
Stage 6.

## Identities inspected

- taskq annotated tag `v0.1.0a6`, peeling to
  `c2f6827fc4a8563cc5a3910b1a1319b53cdfd9c8`;
- immutable taskq wheel
  `outlabs_taskq-0.1.0a6-py3-none-any.whl`;
- QDarte API range `c0940fb..96fe5f0` on
  `codex/taskq-pilot-p1`;
- QDarte workers range `abeaac1..21bd880` on
  `codex/taskq-pilot-p1`; and
- disposable local PostgreSQL database `qdarte_contact_verify_dev` on
  PostgreSQL 18.4.

All three repositories were clean at their inspected tips. The two isolated
QDarte branches and taskq `main` were pushed before review.

## Artifact and migration findings

The release asset was downloaded again during review. Its SHA-256 is exactly:

`a731a6dc69e7346e2069ea9ac71257bf832be6e73bd4a2d01d709fd82d0d5419`

The wheel metadata declares `0.1.0a6` and contains exactly the immutable
0001–0007 SQL files expected for this release. Both QDarte lockfiles point at
the same release URL and the same SHA-256; neither contains a path, branch,
range, or alternate package source.

The disposable database reports contract `0.1.5` and exact active
capabilities `admission_reservations` and `read_model_list_ready`. Its migration
ledger contains 0001–0007. Stored 0007 checksum:

`99c76b0e2c787c0f72ace34b864d098cc1977a091ed635af0bda8510f3790696`

No production database or incumbent QDarte package database was involved in
this review or the C6-03B drill.

## Privilege boundary

The long-lived facade identity is `qdarte_contact_facade_dev`. PostgreSQL
reports `rolsuper=false`, `rolcreaterole=false`, and `rolcreatedb=false`. Its
memberships are exactly producer, runner, observer, and housekeeper. The
separate `qdarte_contact_operator_dev` login alone holds operator.

Executed negatives under the facade identity:

- `SET ROLE taskq_operator` — permission denied;
- direct `taskq.admissions` read — permission denied; and
- `taskq.ensure_queue(...)` — permission denied.

The facade therefore cannot administer the queue or bypass the hardened
function surface.

## Algorithm derivation

The QDarte adapter derives the canonical key and versioned SHA-256 intent
before planning, then mints one UUID handle for that logical adapter call. It
re-observes the process-owned direct-drain proof before constructing the
admission client and calls `reserve_admission` before invoking the candidate
planner.

The branches are closed and source-backed:

- `AdmissionAdmittedResult` returns the stored job ID and validates the
  immutable one-field receipt without planning;
- `AdmissionPendingResult` returns only a sanitized unavailable error and does
  not plan or finish;
- only `AdmissionReservedResult` invokes the planner and may call
  `finish_admission`; and
- any typed taskq, HTTP, mode, or planning failure remains a sanitized refusal
  with no direct-producer fallback.

No QDarte admission mapping table, key-to-job cache, payload snapshot, or
lookup-then-enqueue bridge was added. The package facade remains a separately
constructed local harness rather than a taskq facade mounted by normal QDarte
application startup.

The successful request's independently recomputed intent hash is:

`978d42ccdd4ba23c6a9236530af105a03d8be4d481e97bc6c46fc9104571ee23`

It binds the canonicalization marker, `country:AR`, limit one,
unverified-only selection, request labels, browser options, and official
client omission of `None`. The supplied-or-derived key behavior and exact
receipt validation remain pinned by unit vectors.

## Raw-state and replay findings

The authoritative uppercase admission is:

- key `contact_verify_scope:country:AR`;
- state `admitted`;
- job `019f89f4-b4d0-760c-a513-1c76ed6fbf9a`; and
- receipt `{"planned_entities": 1}`.

The linked job is `queued`, type `qdarte.contact_verify.scope`, with zero
attempts, failures, and releases. No worker could have executed it, and no
result application exists for that job.

The implementation evidence's replay probe replaced the planner with a
function that raises if called. The real adapter returned `existed` with the
same job ID and receipt, recorded zero planner calls, and required package,
direct, and effect snapshots to remain equal before reporting success. Review
also verified the corresponding source branch and its explicit regression.

The retained lowercase diagnostic is separately identifiable:
`contact_verify_scope:country:ar`, state `reserved`, no linked job, no receipt,
and a distinct intent hash. It is not conflated with the successful proof and
was not manually rewritten or cleaned up.

## Regression gates

- QDarte API focused C6/config/route/facade set: **62 passed**;
- API Ruff: clean;
- API changed-file format: clean;
- API targeted MyPy: clean;
- QDarte worker taskq/contact/config set: **73 passed**;
- worker Ruff: clean; and
- installed worker environment imports taskq version `0.1.0a6`.

The taskq release-tip CI and the later documentation-tip CI were already green
across both PostgreSQL majors, artifact/import isolation, races, migrations,
audit, units, benchmark smoke, and lint.

## Findings and Contract questions

No implementation blocker and no Contract question was found.

The sole assurance qualification is reviewer independence: this response is
an explicitly disclosed internal review authorized by the owner, not a
separate external-review session. That affects provenance, not the technical
disposition of the executed gate.

## Scope disposition

READY opens only C6-04's three local, zero-row-copy rollback postures under the
already-frozen specification. C6-AUDIT remains a later gate, and C7,
production, retirement, non-contact work, and Stage 6 remain closed.
