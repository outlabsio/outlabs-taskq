# Round 13 — durable admission primitive completion review — Response

> **Reviewed:** range `8d520d2..7f6f662` (five commits) + gate `64a1241`; contract package
> ADR-023 / Protocol 1.0.8 amendment 15 / Manifest–SQL 0.1.5 / immutable migration 0007.
> **Method:** three independent audit passes (governance/catalog; SQL kernel/linearization;
> wire/clients/facade/parity) plus reviewer-inline contract reads and a fully reproduced evidence
> program on both PostgreSQL majors and the built artifacts. Left uncommitted per the charter.

## 1. Verdict

**BLOCKED — by four small preconditions plus one one-line confirmation; the primitive itself is
sound.** The admission kernel is implemented tightly against Tier-0: a genuinely reusable
two-phase producer primitive, not a host workaround. The linearization, replay, privilege,
hiding, and packaging properties all verified with strong covering tests, and every headline
count reproduced. What blocks is exactness, not architecture: two of the five frozen conflict
reasons have zero test coverage at any layer, a latent cross-writer hash wrinkle needs a
docs-first rule, the 19-commit range (this slice included) has never been pushed or executed by
CI, and one OpenAPI response schema over-advertises fields its command can never return. A
targeted delta over the remediation range converts to READY.

## 2. Independently derived identities and reproduced evidence

- **Range:** five commits, all trailered; docs-only freeze `4d0e131` (ADR-023 + Protocol 1.0.8
  §2.6/amendment 15 + Manifest 0.1.5 §14 + the 249-line Admission Specification + registrations)
  strictly precedes SQL (`4e5d99f`) and Python (`87e536d`). No host/deployment/production/
  credential/provider/QDarte-source mutation anywhere; migrations 0001–0006 untouched (and never
  modified in all history); no Tier-4 edit. Migration checksum recomputed:
  `99c76b0e2c787c0f72ace34b864d098cc1977a091ed635af0bda8510f3790696` — matches the request.
- **Catalog (derived from prose before the machine manifest):** exactly three new producer-only
  hardened functions (`reserve_admission`, `finish_admission`, `cancel_admission`), the private
  `taskq.admissions` table with no application-role grants, nullable-unique `jobs.admission_id`,
  three composites, **no new TQ codes** — five new closed TQ409 reasons. Machine manifest
  matches: 43 → 46 function rows, PUBLIC_ERRORS 33 → 36, META_SEEDS equality
  `{"active":["admission_reservations","read_model_list_ready"]}`, 0007's entry gate requiring
  exactly the 0.1.4/ready-only prior state, and the capability flip as the final statement.
- **Reviewer-reproduced evidence (2026-07-22):** full suite **502 passed / 1 opt-in skip** on
  fresh PostgreSQL 18.3 **and** exact-minor 16.14 (fresh + full 0001→0007 chains, CI-shaped
  Redis); admission set under `-W error`: 28 matched tests green on each major (a superset of the
  claimed 22-test set, which was verified as the named union across six files); million-row plan
  gate **2/2 on both majors**; Ruff and format clean (74 files); DB-free **308 passed** on
  Python 3.12; wheel/sdist built (version 0.1.0a5) with **all 7 migrations** packaged; core-only
  3.13 install exports the admission types with FastAPI absent; and a reviewer-authored fake
  drill from the installed wheel: `reserved` → `created` → replay `existed` (same job id) →
  post-admission reserve `admitted` (matching job id). The unknown-queue fake call correctly
  raised TQ001 — the fake enforces queue-existence-before-key-state like the SQL.

## 3. What was verified sound (dispositions)

**Audit-B histories — all seven PASS**, each with mechanism and covering test, including *real*
two-connection races proven via row-lock waits: (a) reserve/replay/pending/intent-mismatch —
`FOR UPDATE` + `ON CONFLICT DO NOTHING` retry loop, competing callers never see the owner's
handle; (b) expiry/takeover on database time only (no timestamp parameters exist anywhere; test
expiry via the owner-only scratch rewind helper), stale finish rejected; (c) concurrent
finish/finish and finish-beats-cancel — row lock + structural `UNIQUE (admission_id)` backstop,
exactly one job (the cancel-wins mirror is R13-01's untested branch); (d) committed
response-loss replay returns the **stored** job/receipt — proven at SQL and through a
drop-first-committed-response ASGI transport with byte-identical bodies and fresh request IDs;
(e) canonical SHA-256 computed **in SQL only** over jsonb-canonical `{job, receipt}` — a
key-reordered replay still returns `existed`; changed content only `finish_mismatch`; (f)
backpressure/rollback leaves the reservation and creates nothing — proven with a max-depth-1
queue; (g) four cancel outcomes with admitted-cancel replay returning the stored receipt, and a
bounded (LIMIT 500, index-backed) janitor that deletes only receipt-expired job-absent admitted
rows and stale unadmitted rows.

**Privilege wall — PASS (equality, not membership):** table private to owner with a
no-capability-role `has_table_privilege` sweep; the three functions SECURITY DEFINER,
pinned-path, PUBLIC-revoked, granted to `taskq_producer` only, with an all-functions × all-roles
× PUBLIC exactness test; runner/observer/housekeeper denied; producer cannot read the table;
janitor stays housekeeper+operator as declared everywhere consistently.

**Auth ordering and hiding — PASS:** authenticate → path-queue `enqueue` authorization →
header/request-ID validation → body decode → SQL, with wire tests proving zero admission rows on
bad credentials and on wrong-queue denial (authorizer saw exactly one check; body never decoded
or echoed). Errors are structurally unforwardable: SQLSTATE-only normalization plus a
reconstruction layer that accepts only the five frozen reasons and converts anything else to an
opaque internal error.

**Wire/clients/parity — PASS:** routes/statuses/outcomes match Protocol §2.6 exactly (including
`pending` 202 enforced by the client's status-per-outcome check); strict `extra="forbid"` models
with the 12-field job command rejecting every competing authority (`idempotency_key`,
`depends_on`, `workflow_id`, parent); H-09 bounds end-to-end; the handle is request-writeOnly,
owner-returned per Tier-0, absent from `pending` and from every error and repr; official clients
mint one non-nil handle per logical operation **outside** the retry loop and replay identical
bodies with fresh request IDs (sync vector + response-loss proof); typed SQL↔ASGI↔raw-row parity
with superuser-fixture verification; the fake is narrower-never-wider (same strict models, same
five reasons, same check order, conformance-tested as a ProducerTransport).

**Runtime/bridge — PASS:** closed set `{0.1.2, 0.1.3, 0.1.4, 0.1.5}` with the preserved
pre-bridge rejection; `admission_enabled=False` default; startup requires exact 0.1.5 **and**
the `admission_reservations` capability (typed version/capability errors otherwise); disabled
facades never register the routes; 0006-state proven to lack the functions entirely.

**Governance — PASS:** docs-first held; the pre-range 13-commit QDarte C6 docs arc is
docs/board-only with a coherent CQ-01→CQ-03 chain (CQ-03 adjudicated by ADR-023); the
design-review-12 record trail is hash-pinned and internally consistent (the recorded response
SHA recomputed and matched).

## 4. Finding registry

**R13-01 · MEDIUM · Two of the five frozen conflict reasons are implemented but tested nowhere.**
`finish` → TQ409 `{"reason":"reservation_expired"}` (0007:394-397) and
`{"reason":"reservation_cancelled"}` (0007:390-393) have zero coverage at SQL, wire, or fake
layers — every existing stale-finish vector re-reserves or takes over first and lands on
`reservation_conflict` instead. The cancel-commits-then-finish race mirror is the same dark
branch. This is against the specification's own §9 "state-table vectors including every
outcome/error". Remediation: SQL vectors for both reasons (finish with the original handle on an
expired-but-unreacquired row; on a cancelled-but-unreacquired row; plus the cancel-wins race
direction) and one wire case each. Oracle: the vectors. Owner: this slice.

**R13-02 · MEDIUM · Cross-writer null-vs-omitted styles change the finish hash — spurious
`finish_mismatch` between surfaces.** The typed clients serialize with `exclude_none`, while raw
SQL accepts explicit `"field": null` — and the null-carrying key participates in the
jsonb-canonical hash, so a finish made through a typed client and replayed by a raw SQL writer
with explicit nulls (or vice versa) mismatches despite identical semantics. Each writer is
self-consistent; the trap is mixing surfaces across a replay. Remediation (docs-first, no
contract change — 0007 stays immutable): one paragraph in the Admission Specification §4.2
declaring the identity jsonb-literal, requiring a writer to keep one null-style across replays,
and noting the official clients omit; plus one cross-writer vector pinning the behavior. A
future contract revision may consider null-stripping normalization; not this slice. Owner: spec
amendment + vector, this slice.

**R13-03 · MEDIUM · The 19-commit range is unpushed; CI has never executed any of it — and the
artifact smoke was broken for two commits inside the range.** `origin/main` is 19 behind; every
green CI run predates the admission work (same class as the round-9 R9-03 finding). Additionally
`4e5d99f` updated the smoke's migration list but left the function-count assertion stale (43 vs
the actual 46), fixed only at `a770a26` — the smoke script fails if executed at either
intermediate commit (bisect-hostile; transient; noted, not separately actionable). Remediation:
push the full range; CI green at tip (the CI matrix runs both PG majors, artifacts on 3.12/3.13,
and the format/lint lanes). Owner: this slice.

**R13-04 · LOW · Cancel's OpenAPI response schema over-advertises reserve fields.** The cancel
command's HTTP `data_model` reuses `AdmissionReserveWireData`, so generated OpenAPI advertises
`handle`/`retry_after_seconds`/`reservation_expires_at` — fields the runtime can never emit for
cancel (its result model structurally forbids them). Doc-surface only; no leak path.
Remediation: a dedicated cancel wire-data model (or schema override) + the catalog oracle
asserting it. Owner: this slice (fold into the delta).

**R13-05 · LOW · Evidence-vector bundle (non-blocking, named owners):** (a) the unmounted
admission family is proven absent from OpenAPI but no live POST pins the 404/TQ001 wire
response; (b) finish-TQ429 rollback is proven via the SQL transport but not through the mounted
ASGI path; (c) the reserve recycle branch (admitted + receipt-expired + job-absent → reacquire)
is untested; (d) the janitor's reserved/cancelled prune classes and its LIMIT bound lack direct
tests; (e) no async-client reserve-retry mint-once vector (sync-only; the code path is shared).
Owner: next library test slice.

**R13-06 · LOW · Defense-in-depth:** the facade `_safe_details` blocklist does not name
`handle`/`receipt`/`intent_hash` — non-leak currently rests entirely on the (sound) closed-
registry reconstruction and SQLSTATE-only normalization. Add the three names to the blocklist.
Owner: next library slice.

**R13-07 · LOW · Records and cosmetics:** (a) **Round 12 has no entry in the external reviewer's
standing decision log, and its response and remediation landed in a single commit** (`b854f46`),
unlike the register-then-remediate pattern of every logged round — the hash-pinned trail is
otherwise consistent; the record needs the operator's one-line confirmation of who performed
Round 12, for the acceptance chain's completeness. (b) `TASKS.md` still flags S5-QD-C6-CQ-01 as
"(open)" despite its recorded resolution. (c) The testing fake's Python-canonical hash is
byte-divergent from the SQL jsonb-canonical hash — harmless today (hashes never cross the
boundary; both are key-order-insensitive) but worth one comment declaring fake hashes
non-comparable to SQL hashes. (d) The machine manifest's REPLAY_RULES gives the admission
functions only the generic replay label; admission-specific semantics are prose/test-enforced
(informational).

Premise corrections recorded for honesty: the handle is deliberately owner-returned in
`reserved` per Tier-0 §2.6 (write-only applies to request schemas; `pending` and errors never
carry it) — this reviewer's charter phrasing was stricter than the contract; the generic client
`command()` escape hatch does reach the admission family but harmlessly (strict pre-loop
validation keeps caller handles stable, and unmounted servers 404); the family is gated by
registration-bool + startup proof rather than `HttpSurface.GATED` — equivalent in effect,
different in mechanism from the read models.

## 5. Contract questions

**None.** The five findings are test coverage, documentation, process hygiene, and one OpenAPI
cosmetic. The kernel's semantics conform to ADR-023, Protocol 1.0.8, and Manifest 0.1.5 in every
audited respect; no SQL or wire behavior needs invention or change (R13-02's remediation is a
specification clarification of an already-true property).

## 6. Commands and limits

Reviewer-executed: full suites + admission `-W error` sets + million-row plan gates on
PostgreSQL 18.3 and containerized exact 16.14; DB-free lane; wheel/sdist build; core-only 3.13
venv install with admission-type/FastAPI-absence checks and a scripted fake
reserve/finish/replay drill; migration checksum recomputation; range/trailer/path derivation;
three parallel source audits with every load-bearing claim cited to file:line. Limits: the
12/12 artifact matrix was sampled at representative corners (wheel × core × 3.13 here; prior
slices covered the other axes) with the full matrix CI-asserted — which is precisely why R13-03
(push + CI) is a precondition; no host, QDarte, or production system was touched.

## 7. Preconditions to READY

1. R13-01: the missing conflict-reason and race-mirror vectors.
2. R13-02: the §4.2 hash-identity clarification + cross-writer vector.
3. R13-03: push the range; CI green at tip.
4. R13-04: the dedicated cancel wire-data model (or schema fix) + oracle row.
5. R13-07(a): the operator's one-line confirmation, for the record, of who performed Round 12.

A targeted delta review over the remediation range converts this verdict to READY.

## 8. What READY will and will not open

READY authorizes **only** the isolated QDarte package repin and its C6-03 created/existed replay
proof in the disposable local environment. It does not authorize a production migration
(0007 retains its ADR-020 bridge/rollback-floor sequence per the specification §8), a host
deployment, existing-queue mutation, direct-queue retirement, any provider call or side-effecting
lane (the hard-kill gate stands), worker expansion, UI work, or Stage 6.
