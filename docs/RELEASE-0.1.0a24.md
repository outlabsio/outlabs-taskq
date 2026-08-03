# outlabs-taskq 0.1.0a24 release notes

**Base release:** 0.1.0a23

**SQL contract:** 0.3.0, unchanged

**Protocol wire major:** 1, unchanged  
**Packaged migrations:** 0001–0020, unchanged

## What changes

The `skip` catch-up policy now distinguishes an ordinary due occurrence from a
backlog. During normal continuous polling it fires the one due occurrence and
advances from its nominal instant. When two or more occurrences accumulated,
it fires none of that backlog and advances to the next future instant.

Previously `skip` suppressed every due occurrence and advanced from the poll
time. Because a scheduler normally polls just after an instant becomes due, a
recurring schedule using the documented default could never enqueue a job and
its cursor drifted with each poll. `fire_once` and `fire_all` are unchanged.

The deployment guidance now makes the process boundary explicit: the scheduler
is one clock per database/environment and never runs application handlers.
Workers may remain host-native and consolidated across queues. A distinct
container per task, queue, or schedule is neither required nor recommended.

## Compatibility and rollout

- No SQL migration or wire-contract change is required.
- Existing manifests using `fire_once` or `fire_all` are unchanged.
- A manifest using `skip` begins producing ordinary future runs after upgrade;
  review any schedule that relied on the old mute behavior and pause it before
  upgrading.
- Keep one scheduler per database/environment and retain static target
  attestation. Worker placement remains independent.

## Release gates

- evaluator tests prove initialization, ordinary `skip`, multi-run backlog
  suppression, `fire_once`, `fire_all`, and DST gap/fold behavior;
- full Python 3.12/3.13 unit and lint gates;
- PostgreSQL 16/18 SQL-contract and fresh-cluster security lanes;
- wheel/sdist installed-artifact smoke; and
- an attended OutLabs staging occurrence and rollback with production
  scheduling inactive.
