# Round 7 production evidence addendum

Date: 2026-07-20

This additive record does not modify the immutable round-7 request. It closes the two evidence
questions that remained explicitly open in that request. Review the original host evidence commit
`5a8cb7825e2e8d18e44f528985d4f1915c16369f` together with host delta commit
`7c60229` on `codex/s4-03-cycle1`.

## Controlled-failure event oracle

A read-only query in the production PostgreSQL container selected only bounded state, counters,
attempt outcomes/timestamps, and event type/actor/timestamp for job
`019f7f21-59e3-7683-8a77-bc875a5c49bf`. It selected no payload, headers, result, error, event
message/data, attempt id, current attempt id, or fence.

Observed job counters were `status=succeeded`, `attempt_count=2`, `failure_count=0`,
`release_count=1`, and `expiry_streak=0`. Attempt outcomes were
`released/worker_shutdown` followed by `succeeded/success`. The ordered event chain was
`enqueued -> claimed -> released -> claimed -> succeeded`; the two claims have different worker
process actors. This is independent of the facade projection and establishes same-id conservation,
budget-free release, process transition, and eventual success without copying work or editing a row.

## Legacy proof terminal state

The legacy proof row `66dcee45-f3bf-4998-a8aa-3160ae8ee07b` was observed read-only at attempt 4 of
5, pending and unleased, with its final ordinary retry scheduled for
`2026-07-20T12:20:08.858370Z`. No retry was accelerated and no row was edited. At
`2026-07-20T12:20:50.049188Z`, a second read-only query observed the same row naturally terminal at
`status=failed`, `attempt_count=5`, `max_attempts=5`, unleased, and updated at
`2026-07-20T12:20:10.105189Z`.

The host evidence document records both observations. Production remains healthy in taskq mode;
this addendum authorizes no legacy retirement, branch reconciliation, or post-Stage-4 change.
