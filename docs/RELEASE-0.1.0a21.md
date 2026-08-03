# outlabs-taskq 0.1.0a21 release notes

**Status:** released 2026-08-03; current OutlabsAuth compatibility release

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
- [x] Full PostgreSQL 16 and 18 suites pass from the release candidate.
- [x] Wheel and sdist pass installed-artifact smoke tests outside the checkout.
- [x] Locked dependency audit reports no known vulnerabilities.
- [x] GitHub CI passes and immutable prerelease assets are published.

## Published evidence

- Release: [v0.1.0a21](https://github.com/outlabsio/outlabs-taskq/releases/tag/v0.1.0a21)
- Main CI: [run 30805158877](https://github.com/outlabsio/outlabs-taskq/actions/runs/30805158877)
- Wheel SHA-256: `0793bff12c4973865f58db6ddeb31e790e2a35a8712de27ba69cf404a9adc81a`
- Source SHA-256: `e32eabeea73212c2eb4524201f16fa6c9a44c2dfc8eb4b6a7a22881c58445b19`

## Upgrade notes

Install 0.1.0a21 when a host needs Auth a28 or a later compatible 0.1
prerelease. TaskQ migrations do not need to be rerun for this dependency-only
release. Hosts must still apply the migrations required by their selected
OutLabs Auth version before starting the application.
