# outlabs-taskq 0.1.0a22 release-candidate notes

**Status:** implementation candidate; not yet published

**Base release:** 0.1.0a21

**SQL contract:** 0.3.0

**Protocol wire major:** 1, unchanged
**Migrations:** 0019–0020 (additive identity checkpoint, then activation)

> **Post-release safety note:** a22/a23 `catchup: skip` advances due schedules
> without enqueueing, including during ordinary continuous polling. Recurring
> application manifests must explicitly use `fire_once` or `fire_all`. Do not
> attempt a Python-only correction: `fire_schedule` enforces the released
> policy in SQL, so corrected semantics require a new migration and stored-row
> compatibility audit.

## What changes

- `taskq scheduler`, bounded `--once`, and read-only `scheduler doctor` run
  independently of FastAPI.
- Every schedule mutation/runtime call and direct worker claim requires a
  keyed, transaction-local target attestation sourced from static settings.
- Target binding and clone rotation are explicit, CAS-checked, and audited.
- Versioned YAML manifests provide deterministic source-owned plan/apply and
  explicit retirement without implicit pruning.
- Schedule decisions and selected occurrences are durable, including lateness
  and overlap skips. Three deterministic evaluator errors auto-pause a row.
- The optional OutLabsAuth dependency keeps 0.1.0a21's bounded compatible
  range, `>=0.1.0a27,<0.2.0`.

## Required rollout order

1. Deploy the package while the database remains on contract 0.2.6.
2. Run `taskq migrate`; expect 0019 to commit and 0020 to refuse `unbound`.
3. Run `taskq target show --json` and record the installation UUID outside the
   target database configuration.
4. Bind the intended environment with an owner credential and CAS inputs.
5. Resume `taskq migrate`, then run `taskq verify` and `taskq scheduler doctor`.
6. Configure scheduler/worker expectations statically. Production additionally
   requires the installation pin and explicit production opt-in.
7. Apply a reviewed manifest, start one scheduler, and disable the legacy
   in-API schedule loop only after advancement evidence is visible.

Restored or cloned databases never inherit operational authority silently.
Retain identity for same-environment disaster recovery; explicitly rotate a
clone before binding it to another environment.

## Release gates

- [x] current `origin/main` integrated; a20/a21 notes and security history retained
- [x] independent adversarial implementation review: PASS WITH CONDITIONS, no P0/code P1
- [x] PostgreSQL 16 and 18 fresh stop/bind/resume and exact verify
- [x] Redis-backed full suite on PostgreSQL 16 and 18: 712 passed, 1 gated plan skip each
- [x] wrong-target, forged-GUC, overlap/lateness, auto-pause, replay, and pre-commit kill evidence
- [x] `fire_all` + `forbid` intra-batch semantics documented and tested
- [x] wheel/sdist installed-artifact smoke
- [x] QDarte paused-manifest plan plus staging handoff candidate

This release supports one scheduler process per database/environment. A durable
advancement heartbeat/alert and the active/active hard-kill matrix remain gates
before broad adoption or a second scheduler replica.

The reproducible local record is in
[`evidence/scheduler-0.3.0-2026-08-03.md`](evidence/scheduler-0.3.0-2026-08-03.md).
