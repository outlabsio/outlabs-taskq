# Round 13 — durable admission primitive completion review

## Assignment and release boundary

Perform an adversarial, source-backed audit of the queue-native durable admission slice. Return
**READY** only if the SQL kernel, immutable migration, generated transports, mounted HTTP facade,
official clients, testing fake, and completion evidence jointly establish a reusable two-phase
admission primitive rather than a host-specific mapping workaround. Otherwise return **BLOCKED**
with the smallest explicit preconditions.

READY authorizes only an isolated QDarte package repin and its C6-03 created/existed replay proof.
It does **not** authorize a production migration, host deployment, existing-queue mutation,
direct-queue retirement, provider call, worker expansion, side-effecting lane, or Stage 6.

## Repository identity and exact range

- Repository: `~/Documents/projects/outlabs-taskq`
- Review the complete range `8d520d2..7f6f662` (five commits).
- Docs-first contract freeze: `4d0e131`.
- SQL kernel and immutable migration 0007: `4e5d99f`.
- Typed SQL/HTTP/client/testing surface: `87e536d`.
- Regenerated completion evidence and artifact ledger: `a770a26`.
- Completed mounted-wire disposition matrix: `7f6f662`.
- Expected migration SHA-256 at the request tip:
  `99c76b0e2c787c0f72ace34b864d098cc1977a091ed635af0bda8510f3790696`.

Derive every commit, tree, parent, changed path, and hash from Git before trusting this request.
Verify the range contains no host, deployment, production, credential, provider, or QDarte source
mutation. Do not infer correctness from `TASKS.md`, test names, or the claimed counts below.

## Authority order

Read in this order:

1. `AGENTS.md`, then `docs/README.md`;
2. Transport Protocol v1 document revision 1.0.8 and Function Manifest / SQL contract 0.1.5;
3. ADR-005, ADR-006, ADR-010, ADR-011, ADR-020, and ADR-023;
4. `TASKS.md`, the Build Plan, Test & Benchmark Harness, Durable Admission Reservation
   Specification, and the QDarte C6 Contract-question resolution;
5. migration 0007, machine manifest, verifier, transports, facade/runtime, clients, fake, tests,
   and artifact smoke code; and
6. actual PostgreSQL 16.14 and 18.x execution.

Tier 0 and accepted ADRs win every conflict. Record a **Contract question** and stop rather than
inventing a SQL/wire behavior. Never name third-party queue projects in the repository or response.

## Audit A — governance, scope, catalog, and immutability

Independently derive the 0.1.5 SQL catalog, relations, composites, grants, errors, replay rules, and
capability metadata from the Function Manifest before inspecting the machine manifest or tests.
Verify the public command ledger and internal helper catalog without trusting claimed totals.

Confirm ADR-023, Protocol 1.0.8 amendment 15, Manifest 0.1.5, and migration identity 0007 landed in
one docs-only commit before SQL or Python. Prove 0007 is append-only in the migration ledger and
that fresh installation and the full 0001→0007 upgrade converge on identical catalog, checksums,
metadata, and grants. Reject an edited earlier migration, undeclared function, public base-table
grant, manual capability switch, or implementation-specific wire alias.

Audit the bridge boundary: a route-free runtime may accept 0.1.5 metadata while exposing no
admission command; a feature runtime must require exact 0.1.5 plus exactly the declared admission
capability before mounting the routes. Try to make a 0.1.2–0.1.4 database or capability-missing
0.1.5 database expose the family.

## Audit B — linearization, replay, and durable state

Build independent SQL oracles over the private admission and job rows. Do not use a returned model
as the only oracle. Attack at least these histories:

1. first reserve, same-handle replay, competing-handle pending, and different-intent conflict;
2. reservation expiry/takeover driven only by database time, including a stale handle afterward;
3. two concurrent finishes, plus finish versus cancel, with exactly one legal linearization and at
   most one linked job;
4. commit followed by lost response and byte-identical finish replay, yielding the stored job and
   receipt rather than facade reconstruction;
5. same handle with changed canonical job or receipt yielding only `finish_mismatch`;
6. backpressure/internal failure rolling back job, finish hash, and receipt while retaining the
   reservation; and
7. cancellation, admitted-cancel replay, receipt retention, and bounded janitor cleanup without
   mutation of an admitted or terminal job.

Confirm the database stores a canonical SHA-256 finish identity, the receipt remains bounded and
immutable, and one admission can link to exactly one job. Attempt orphaned jobs, duplicate jobs,
receipt rewrite, cross-queue/key handle use, client-clock lease arithmetic, manual dependency or
workflow injection, and key/hash/handle disclosure through an error.

## Audit C — privilege, authorization, and hiding

Prove the ledger and job linkage remain private: producer receives only the three hardened
functions plus existing enqueue authority; runner/observer/housekeeper cannot reserve or finish;
producer cannot read either base table; operator authority does not leak into application pools;
and PUBLIC has no execute path.

On the mounted facade, verify authentication and path-queue authorization happen before request-ID
validation, body decoding, admission lookup, or SQL. Exercise missing/bad credentials, wrong-queue
denial, hiding equality, upstream auth 429/503, malformed/oversized bodies, unknown queue/admission,
and every conflict reason. Error details must be built only from the closed protocol registry:
never forward driver detail, SQL text, constraints, queue keys, intent hashes, handles, payloads,
or receipts.

## Audit D — wire, clients, and parity

Derive the Protocol-1.0.8 route/outcome/status matrix independently. Check the strict request and
outcome-discriminated response shapes, request-only/write-only handle posture, exact H-09 bounds,
no request echoes, and absence of competing queue/job/dependency/workflow/actor authority.

Run the same histories through direct SQL and generated client → mounted ASGI → SQL, then compare
typed outcomes against raw owner-only rows. Include reserved, pending, admitted, created, existed,
all four cancel outcomes, every safe conflict reason, and unknown state. Confirm the official sync
and async clients mint one non-nil handle per logical reserve outside all retries, replay the exact
body with fresh request IDs, never finish a pending competitor, and cannot call an unmounted
feature through a generic escape hatch.

Audit the high-level `TaskQ` transaction path and the `FakeTaskQClient`. The fake must remain a
runtime-checkable producer substitute, but its behavior is not SQL proof. Try to make either path
accept a wider shape or silently weaken a conflict.

## Audit E — resources, plans, packaging, and supported runtimes

Run the identical full suite on PostgreSQL 18.x and exact-minor 16.14 with CI-shaped Redis. Run the
admission kernel/surface set under warnings-as-errors and inspect connection/task/resource closure,
especially discarded ASGI responses and failed startup. Reproduce the opt-in million-row plan gate
on both majors and confirm admission changes do not regress existing bounded queue plans. State
plainly that this gate is regression evidence, not an invented admission-throughput claim.

Build wheel and sdist and test core, HTTP, and OutLabs extras outside the checkout on Python 3.12
and 3.13. The installed artifacts must contain migrations 0001–0007, expose the public admission
types from core without importing FastAPI or OutLabsAuth, preserve optional-extra guard messages,
and execute a fake admission reserve/finish/admitted replay. Inspect the artifact for test/bench or
source-checkout leakage.

The implementation claims 502 passed / 1 opt-in skip on each PostgreSQL major, 22 admission tests
under warnings-as-errors on each, 2/2 million-row plan checks on each, 308 DB-free tests on Python
3.12, and a 12/12 artifact matrix. Reproduce or falsify those claims; do not repeat them as proof.

## Required response

Create only `docs/design-review-13/RESPONSE.md`, modify nothing else, and leave it uncommitted.
Include:

1. **READY** or **BLOCKED**;
2. independently derived range, migration checksum, catalog, capability, and route identities;
3. findings numbered `R13-01...` with severity, authority, evidence/counterexample, impact,
   smallest remediation, regression oracle, and owning slice;
4. a separate Contract-questions section, even if none;
5. explicit dispositions for every Audit-B history, privilege wall, auth-ordering attack, SQL/HTTP
   parity case, runtime mount gate, and artifact boundary;
6. exact commands, versions, counts, and honest environmental limits; and
7. confirmation that READY opens only the isolated QDarte repin/C6-03 proof, not production,
   retirement, existing-queue mutation, deployment, provider work, or Stage 6.
