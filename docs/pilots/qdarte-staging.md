# QDarte staging scheduler pilot handoff

**Pilot:** replace the reversible 900-second
`com.qdarte.intake-submission-review-pull` launchd timer with one TaskQ schedule.
**Starting state:** candidate manifest is paused; no staging or production
mutation has been performed by this package branch.

## Why this pilot

The pull is bounded, idempotent at its intake/local-receipt boundary, already
operated on a 900-second timer, observable in the review ledger, and reversible
by reloading the existing launchd definition. It exercises scheduler restart,
lateness, overlap prevention, and an external dependency without importing or
publishing media.

## Consumer work required before apply

1. Add a registered `platform.intake_submission_review_pull` handler on queue
   `qdarte_platform_control`. It should reuse the existing bounded
   `run_intake_submission_review_pull(limit, bff_base_url)` behavior and obtain
   the BFF URL from QDarte's mode-600 staging configuration, never task payload.
2. Give its worker only the staging API/database/BFF configuration and make it
   perform the existing QDarte environment/database-identity checks before
   claiming.
3. Upgrade qdarteAPI/worker locks to TaskQ 0.1.0a22, disable the FastAPI
   schedule loop, and keep the housekeeper pool only where non-schedule
   compatibility work still requires it.
4. Provision a separate scheduler login with the package housekeeper grants;
   do not give the scheduler an API, operator, migration, backup, Docker, media,
   or production credential.

## Staging gate

Using owner and runtime credentials from the staging secret manager:

```bash
taskq migrate
taskq target show --json
# Review the fingerprint, then bind with explicit CAS values.
taskq target bind staging --actor qdarte-release \
  --expected-installation-id "$TASKQ_INSTALLATION_ID" \
  --expected-binding-version 0
taskq migrate
taskq verify

TASKQ_EXPECTED_ENV=staging taskq scheduler doctor --json
taskq schedule plan examples/qdarte-staging-intake-review.yaml --json
taskq schedule apply examples/qdarte-staging-intake-review.yaml \
  --actor qdarte-release --expected-environment staging
```

The first apply must leave the schedule paused. Confirm the queue and handler,
then review a manifest change to `state: active`; do not make an ad-hoc SQL
state edit. Start exactly one supervised scheduler with static
`TASKQ_EXPECTED_ENV=staging` and a pinned installation UUID.

Before changing the manifest to active, install an external lag check that
polls `taskq.get_scheduler_health()` and alerts if `last_decision_at` stops
advancing or schedule lag reaches 900 seconds. Process liveness alone is not a
sufficient pilot health signal.

## Evidence and rollback

- Doctor is ready and identifies the intended staging installation.
- One occurrence creates one job carrying package-owned schedule headers.
- A second scheduler invocation cannot duplicate that occurrence.
- The review ledger records the bounded pull and no media import/publication.
- Scheduler lag remains below 900 seconds and auto-paused count stays zero.
- A scheduler configured for production or the wrong installation refuses.

Rollback: pause the manifest entry, stop the standalone scheduler, confirm no
running pilot job, and reload the existing launchd timer. Do not run both clocks
after rollback confirmation. Retain decisions/occurrences for the incident or
pilot record.
