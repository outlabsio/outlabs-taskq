# Round 13 — targeted admission remediation delta

## Assignment and fixed scope

Perform a targeted source-backed check of the Round-13 remediation. Return **READY** only if
R13-01, R13-02, R13-03, R13-04, and the R13-07(a) provenance confirmation are closed exactly as
required by the immutable response. Otherwise return **BLOCKED** with only the remaining minimum
preconditions. Do not reopen findings that the response accepted without new counterevidence.

READY authorizes only the isolated QDarte package repin and its C6-03 created/existed replay proof.
It authorizes no production migration, deployment, existing-queue mutation, direct-queue
retirement, provider call, side-effecting lane, worker expansion, UI work, or Stage 6.

## Identities to derive, not trust

- Repository: `~/Documents/projects/outlabs-taskq`
- Original reviewed implementation tip: `7f6f662`.
- Round-13 request gate: `64a1241`.
- Review the remediation range `64a1241..7fb6568` before this delta-request commit:
  - `064dd95` — immutable response registration, R13-02 docs-first clarification, and Round-12
    provenance record;
  - `e0da3d7` — R13-01 conflict/race vectors and R13-02 cross-writer vector;
  - `7fb6568` — R13-04 cancel response projection and OpenAPI/catalog oracles.
- Immutable response SHA-256:
  `7ce717e60e39e0dace15d635c8c5959876cad9433015c051fab002d9cd43ffd7`.
- Immutable migration 0007 must remain SHA-256:
  `99c76b0e2c787c0f72ace34b864d098cc1977a091ed635af0bda8510f3790696`.

Derive commits, parents, paths, hashes, remote refs, and CI identity directly. Confirm all commits
carry the required trailer and same-commit board updates. The response and every prior Tier-4 file
must be byte-identical or untouched.

## Check 1 — R13-01 conflict branches and race mirror

Execute and inspect the new direct-SQL and mounted-wire vectors. Require:

- an original handle finishing an expired, unreacquired reservation returns only TQ409
  `reservation_expired` and creates no job;
- an original handle finishing a cancelled, unreacquired reservation returns only TQ409
  `reservation_cancelled` and creates no job;
- the cancel-first two-connection race visibly blocks the concurrent finish on the row lock,
  commits cancellation, then produces `reservation_cancelled` with zero jobs; and
- the fake pins cancelled-finish behavior without being treated as SQL proof.

Reject a test that takes over/re-reserves first, accepts either conflict reason, uses sleeps as the
race oracle, or asserts only a returned model without raw job/admission state.

## Check 2 — R13-02 literal-JSONB identity

Confirm the Tier-3 specification changed before the vector and says exactly: finish identity is the
literal JSONB `{job, receipt}` content; omitted and explicit-null optional fields differ; one writer
must keep one style across replays; official typed clients omit `None`; migration 0007 remains
immutable; future normalization needs the ordinary docs-first process.

Run the cross-writer vector: typed client commits an omitted-null job, raw SQL replays the same
semantic command with an explicit-null key, and the database returns only TQ409
`finish_mismatch`. Confirm this is a deliberately documented identity boundary, not a Python-side
hash or a code-around. Recompute the migration checksum.

## Check 3 — R13-04 cancel OpenAPI projection

Independently derive cancel data from Protocol §2.6: empty for `cancelled`, `already_cancelled`, and
`expired`; optional `job_id`, `receipt`, and `receipt_expires_at` for `already_admitted`. Confirm the
dedicated wire model advertises exactly those three optional fields and no handle, reservation
expiry, or retry delay. Check both the generated catalog oracle and actual mounted OpenAPI, and
confirm runtime response bytes/outcomes did not change.

## Check 4 — R13-03 publication and CI

Confirm `origin/main` contains the complete admission range and remediation without force or
history rewrite. Identify the GitHub Actions run at the exact delta-request tip and require every
job green, including PostgreSQL 16/18 SQL-contract lanes, Python 3.12/3.13 artifact and import
isolation lanes, lint/format, races, migrations, and relevant smoke gates. Scheduled-only
million-row evidence may rely on the reviewer-reproduced dual-major 2/2 record; do not claim a new
scheduled run if none occurred.

Local final-tip evidence is expected to be 505 passed / 1 opt-in skip on PostgreSQL 18.3 and exact
16.14, 309 DB-free passes on Python 3.12, clean Ruff/format, and a successful wheel/sdist build.
Reproduce or falsify it rather than copying these numbers.

## Check 5 — R13-07(a) provenance and residual ownership

Confirm the live board records the owner's statement: Round 12 was performed by a separate
parallel external-review session, not the implementation agent, and response plus remediation were
intentionally recorded together in `b854f46`. Do not edit the immutable Round-12 record.

Confirm R13-05/06/07(c,d) remain explicitly owned as nonblocking follow-ups and are not
misrepresented as completed or as prerequisites to this delta. The stale C6-CQ-01 heading may be
corrected from open to resolved because its existing resolution text is unchanged.

## Required response

Create only `docs/design-review-13/DELTA-RESPONSE.md`, modify nothing else, and leave it
uncommitted. Include:

1. **READY** or **BLOCKED**;
2. independently derived remediation and remote/CI identities;
3. a PASS/FAIL disposition for each of the five checks;
4. any remaining finding with exact evidence and smallest remedy;
5. a separate Contract-questions section, even if none;
6. exact commands, versions, counts, and honest environmental limits; and
7. the exact narrow scope that READY opens.
