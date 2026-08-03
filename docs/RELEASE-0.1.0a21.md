# outlabs-taskq 0.1.0a21 release notes

**Status:** release candidate

**Base release:** 0.1.0a20

**SQL contract:** 0.2.6, unchanged

**Protocol document:** 1.0.15, unchanged

**Packaged migrations:** 0001–0018, unchanged

## Why this release exists

0.1.0a21 replaces the optional OutLabs adapter's exact Auth a27 pin with the
bounded compatible range `outlabs-auth>=0.1.0a27,<0.2.0`. The exact pin made
independent, backward-compatible Auth patch releases impossible to consume and
caused downstream dependency resolution failures.

The lower bound preserves the audited adapter contract introduced in a27. The
upper bound fails closed before a future 0.2 API boundary. Consumer lockfiles
still select and hash one exact Auth artifact.

There are no TaskQ runtime, SQL, protocol, migration, queue, or delivery
behavior changes in this release.

## Release checklist

- [x] Package, source, tests, smoke script, README, and lockfile identify `0.1.0a21`.
- [x] The optional `outlabs` extra uses `outlabs-auth>=0.1.0a27,<0.2.0`.
- [x] Migration files, SQL contract, protocol document, and runtime code are unchanged from 0.1.0a20.
- [ ] Full PostgreSQL 16 and 18 suites pass from the release candidate.
- [ ] Wheel and sdist pass installed-artifact smoke tests outside the checkout.
- [ ] Locked dependency audit reports no known vulnerabilities.
- [ ] GitHub CI passes and immutable prerelease assets are published.

## Upgrade notes

Install 0.1.0a21 when a host needs Auth a28 or a later compatible 0.1
prerelease. TaskQ migrations do not need to be rerun for this dependency-only
release. Hosts must still apply the migrations required by their selected
OutLabs Auth version before starting the application.
