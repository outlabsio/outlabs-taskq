# Scheduler 0.3.0 implementation evidence — 2026-08-03

This record covers the 0.1.0a22 release-candidate branch. It is local release
evidence, not a claim that a package was published or deployed.

## Static and complete suite

```text
ruff check src tests scripts: passed
ruff format --check src tests scripts: 102 files formatted
git diff --check: passed
pytest on PostgreSQL 16.14 with Redis: 712 passed, 1 intentionally gated skip
pytest on PostgreSQL 18.4 with Redis: 712 passed, 1 intentionally gated skip
```

## PostgreSQL matrix

| Server | Contract | Bound environment | Result |
| --- | --- | --- | --- |
| PostgreSQL 16.14 | 0.3.0 | test, binding v1 | exact `taskq verify` passed |
| PostgreSQL 18.4 | 0.3.0 | test, binding v1 | exact `taskq verify` passed |

Both fresh clusters ran the complete suite and exercised the intended 0019
commit → 0020 unbound refusal → explicit bind → 0020 resume path, including
the concurrent installer and sync-psycopg migration vectors.

## Safety and scheduler behavior

Live SQL tests prove:

- a forged transaction-local GUC fails with `target_attestation_required`;
- missing static expectations and an environment mismatch fail before claim;
- manifest reapply is unchanged and cross-source ownership fails `TQ409`;
- package-owned schedule headers are injected and immutable;
- active-job overlap and maximum lateness produce durable NULL-job occurrence
  decisions;
- a bounded `fire_all` batch with `overlap: forbid` fires the first nominal
  instant and durably overlap-skips later instants in that transaction;
- the third deterministic error durably auto-pauses;
- killing a real PostgreSQL backend after `fire_schedule` executes but before
  commit rolls the whole transaction back, after which lease reclaim produces
  exactly one committed occurrence/job; and
- DST gap/fold behavior stays single-instant under `fire_once` and `fire_all`.

Existing schedule contract vectors additionally cover two-housekeeper claim,
definition-version fencing, response replay, bounded catch-up, error replay,
and janitor ownership.

## External implementation review disposition

The adversarial review returned **PASS WITH CONDITIONS**, with no P0 or
functional P1 defect. Commit `ad9c9a6` integrates current `origin/main`, retains
the published a20/a21 release history, and preserves the consumer security
audit. The localized follow-ups are also closed: the dead owner-only
`_fire_schedule_unattested` body was removed; `fire_all` plus `forbid` is
documented and tested; Redis-less runs skip the explicitly Redis-backed Auth
integration; capability-role target-identity visibility is documented; and an
already-requested stop prevents a `--once` tick.

Advancement heartbeat/alerting and the second-replica active/active kill matrix
remain explicit gates before broad or multi-replica adoption. The QDarte pilot
therefore remains single-scheduler and requires an external lag monitor before
activation.

## Built artifacts

`uv build` produced both 0.1.0a22 artifacts. Archive inspection confirmed the
sdist contains examples, QDarte pilot docs, scheduler runtime, and migrations;
the wheel contains the runtime and both migrations. A fresh core-only virtual
environment installed the wheel, imported outside the checkout, exercised the
CLI, performed fresh and upgrade stop/bind/resume flows, and passed exact
verification.

## QDarte handoff

The paused candidate manifest plans as one create:

```json
{"key":"intake-submission-review-pull","name":"qdarte.intake-submission-review-pull","action":"create"}
```

It was not applied to staging. Activation remains conditional on the consumer
handler/queue, staging-only credential, static installation pin, and rollback
checks in [`../pilots/qdarte-staging.md`](../pilots/qdarte-staging.md).
