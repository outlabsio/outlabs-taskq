# outlabs-taskq 0.1.0a19 release notes

**Status:** approved for publication

**Base release:** 0.1.0a18 (`96699f2`)

**SQL contract:** 0.2.6, unchanged

**Protocol document:** 1.0.15, unchanged
**Packaged migrations:** 0001–0018, unchanged

## Why this release exists

0.1.0a18 changed only a comment in the already-released `0001_initial.sql`. That changed the raw-file
SHA-256 even though the executable SQL statements stayed equivalent. Databases originally installed
with 0.1.0a17 therefore retained a valid historical ledger checksum that the a18 verifier rejected.

0.1.0a19 keeps exact checksum verification and adds one closed compatibility entry for migration
`0001_initial`:

- 0.1.0a17 released checksum:
  `6d5b8196c091bbf08a2ea5ddec99eb5d386a018c462761caee15dad54f0571e3`
- 0.1.0a18/0.1.0a19 packaged checksum:
  `6b4a2c2514ebf481d21093f75e31b3678e0ec63dba455f91812fb5703c461c5c`

No other alternate migration checksum is accepted. Unknown values still report an immutable-history
violation. Operators must not rewrite existing migration ledger rows.

## Included changes

- Accept the exact released a17 checksum for `0001_initial` during read-only verification.
- Keep arbitrary checksum tampering fail-closed and regression-tested.
- Make reserved-role preflight tests self-contained on a genuinely blank PostgreSQL cluster.
- Add an independent blank-cluster security matrix for PostgreSQL 16 and 18.
- Prefer environment-provided CLI credentials and warn, without echoing secrets, for credential-bearing
  command-line arguments.
- Pin GitHub Actions to reviewed commit SHAs and audit the complete locked third-party dependency graph.
- Correct the 2026-08-02 security audit and the public ADR index.

## Release checklist

- [x] Package, source, tests, smoke script, README, and lockfile identify `0.1.0a19`.
- [x] Migration files and SQL contract remain unchanged from 0.1.0a18.
- [x] a17 ledger compatibility and unknown-checksum rejection are covered by live tests.
- [x] Reserved-role tests pass alone on blank PostgreSQL 16 and 18 clusters.
- [x] CLI credential paths and secret-free warnings are unit-tested.
- [x] CI action pins resolve to the reviewed upstream commits.
- [x] Locked dependency audit reports no known vulnerabilities.
- [x] Full PostgreSQL 16 and 18 suites pass from the release candidate.
- [x] Wheel and sdist build and pass installed-artifact smoke tests outside the checkout.
- [x] Maintainer review and release authorization are complete.

## Validation record

- PostgreSQL 16: 693 passed, 1 skipped (the opt-in million-row plan test).
- PostgreSQL 18: 693 passed, 1 skipped (the opt-in million-row plan test).
- Blank-cluster reserved-role preflight: 7 passed independently on each PostgreSQL major.
- Installed artifacts: wheel and sdist passed core, HTTP, OutLabs, and all-extras smoke tests on
  Python 3.12 and 3.13. Core smoke includes the historical a17 ledger checksum upgrade path.
- `pip-audit` 2.10.1: no known vulnerabilities in the complete locked third-party graph.
- Ruff lint and formatting: clean.

## Upgrade notes

Install 0.1.0a19, run `taskq migrate` with `TASKQ_DSN` supplied by the deployment environment, and
then run `taskq verify`. No application migration or ledger edit is required. The GitHub release
records the immutable wheel and source-distribution checksums used by downstream consumers.
