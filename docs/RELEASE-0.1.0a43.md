# outlabs-taskq 0.1.0a43 release notes

**Base release:** 0.1.0a42
**SQL contract:** 0.6.12 (unchanged)
**Protocol document:** 1.0.18 (unchanged)
**Packaged migrations:** 0001–0048 (unchanged)

Package-only worker fix (#61). Affected releases: 0.1.0a31 through 0.1.0a42.

## Worker: fatal settle reports stop the service

`JobRunReport.settlement_outcome` is typed `str`, so the `SettleOutcome` enum
arrives as a plain string. `_observe_job` called `.value` on it, so every fatal
report with a settlement outcome (`settle_conflict`, a capability-skew
follow-up rejection) raised `AttributeError` inside the asyncio callback. The
service was never failed, its stop flag stayed clear, and the claim loop kept
polling against a supervisor that had already closed admission.

- `_observe_job` formats `settlement_outcome` as the string it is, so the
  fatal path stops the service as intended.

## Worker: release claims that can no longer be submitted

A claim in flight when admission closed came back, `submit()` raised
`WorkerCapacityError`, and the claimed job was dropped without release. It
stayed `running` and held its concurrency slot until lease expiry (seen on
2026-11-12 with a concurrency-keyed job on a 3600 s lease).

- The claim loop releases every claimed job it can no longer submit
  (cause `worker_shutdown`, budget-free, slot freed), then fails closed as
  before.
- The release is shielded from shutdown cancellation, and a worker whose stop
  was already requested stops cleanly.

## Rollout

No database step. API and worker processes can be upgraded independently and
in either order. The fix is in worker code, so workers need a43 to get it.

Until workers run a43: after a `worker.fatal error_type=WorkerCapacityError`
that follows an `_observe_job` exception, expire the job claimed at that
moment (`taskq job expire`).

## Validation

Recorded in the release pull request: the local development gate set from
the README, plus the Diverse Data API and worker suites against the built wheel.
