# outlabs-taskq 0.1.0a20 release notes

**Status:** release candidate

**Base release:** 0.1.0a19 (`b4ccb13`)

**SQL contract:** 0.2.6, unchanged

**Protocol document:** 1.0.15, unchanged

**Packaged migrations:** 0001–0018, unchanged

## Why this release exists

0.1.0a20 is a dependency-only compatibility release for the audited
`outlabs-auth==0.1.0a27` package. TaskQ deliberately pins its optional
`outlabs` adapter dependency exactly, so downstream services cannot resolve
Auth a27 until this immutable TaskQ release exists.

There are no TaskQ runtime, SQL, protocol, migration, or permission-contract
changes in this release.

## Release checklist

- [x] Package, source, tests, smoke script, README, and lockfile identify `0.1.0a20`.
- [x] The optional `outlabs` extra pins exactly `outlabs-auth==0.1.0a27`.
- [x] Migration files, SQL contract, protocol document, and runtime code are unchanged from 0.1.0a19.
- [ ] Full PostgreSQL 16 and 18 suites pass from the release candidate.
- [ ] Wheel and sdist pass installed-artifact smoke tests outside the checkout.
- [x] Locked dependency audit reports no known vulnerabilities.
- [ ] GitHub CI passes and the immutable prerelease assets are published.

## Upgrade notes

Install 0.1.0a20 when a host needs OutLabs Auth a27. TaskQ migrations do not
need to be rerun for this dependency-only release. Hosts still must apply the
OutLabs Auth migrations through `20260802_0025` before starting the upgraded
application.
