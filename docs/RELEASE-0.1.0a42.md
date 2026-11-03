# outlabs-taskq 0.1.0a42 release notes

**Base release:** 0.1.0a41
**SQL contract:** 0.6.12 (unchanged)
**Protocol document:** 1.0.18 (unchanged)
**Packaged migrations:** 0001–0048 (unchanged)

Package-only reliability release for API stalls. On 2026-11-02 a ~90 s API
stall made a40 workers drop in-flight jobs silently (one Remine job, then 48
of 80 Zillow jobs). The rows stayed `running`, held their concurrency slots
until lease expiry, and used retry budget at reap. This release closes the
worker-side cause and adds a server-side safety net for late claims.

## Worker: lease-time heartbeat ownership (#56)

- A job is dropped only when no heartbeat has been confirmed for
  `lease − min(60, lease/4)` since the last confirmed heartbeat send (the
  claim request send for the first). The old rule dropped after three failed
  heartbeats however little time had passed, so fast errors (connection
  refused, proxy 502/503/504) dropped a 3600 s lease in under a second.
- Every heartbeat call is bounded by the remaining budget. A crashed or
  cancelled heartbeat task drops ownership instead of running on unrenewed.
- Owned settles keep retrying `TQ503`, server `TQ500` and transport errors past
  `settle_max_attempts` while heartbeats are healthy, bounded by one lease
  budget and paced at the heartbeat interval. A draining worker keeps the cap.
- A `TQ500` the HTTP client raises itself on a malformed response is now a
  `TaskqProtocolError` (subclass of `TaskqInternalError`; same code and
  `retryable` flag and details; `repr` and logged type names show the new
  class). It keeps the settle attempt cap, because resending cannot repair
  it: for example a bare proxy 400, 404 or 413, a request-id mismatch, or an
  envelope that fails validation. A bare 5xx, 408, 425 or 429 without the TaskQ
  protocol header stays a plain `TaskqInternalError` and keeps the
  lease-bounded retry.
- After a stop, a post-cap settle backoff wakes once so the cap applies at
  once; later `TQ429` backoffs during the drain sleep normally.
- New logs: `job.ownership_lost` (WARNING), `job.heartbeat_failing` /
  `job.heartbeat_recovered`, `job.settle_failing` / `job.settle_recovered` /
  `job.settle_exhausted`, each with `job_id` and `attempt_id`.
- `WorkerSupervisor.submit()` and `run_job()` accept an optional keyword
  `claimed_at`.

## HTTP facade: release undeliverable claims (#57)

After a non-empty claim, the facade releases the claimed attempts with
`release_job(cause='released')` when the claim can no longer be delivered:
more than `max(28 s, wait_seconds + 3 s)` since the handler started (measured
before `prepare_queue`), or the client has disconnected. The release is fenced,
does not consume retry budget, and frees `max_running` and per-key slots. The
facade logs `claim.undeliverable` and returns `TQ503 claim_undeliverable`;
existing workers already back off on `TQ503`. A failed or non-ok release is
logged and lease expiry still recovers the job.

## Rollout

No database step. API and worker processes can be upgraded independently and
in either order:

- Upgrading the API gives every connected worker version the claim safety net.
- Upgrading workers gives lease-time heartbeat ownership and settle retries.

HTTP workers should run leases of at least about 75 s (about 2.5 × the 30 s
request timeout): on leases up to about 72 s, a successful heartbeat slower than
about 0.42 × lease now drops the job.

## Known gaps

- A claim committed server-side whose response is lost after the facade's
  check (for example while the host finalizes its own unit of work) is still
  orphaned until lease expiry. The full fix is an idempotent claim token.
  Lowering `claim_wait_seconds` (for example 10 s with the 30 s client
  timeout) widens the margin meanwhile.
- The 28 s delivery window counts from handler start, so time spent before the
  handler (authentication, a full DB pool) is not counted. It assumes the
  default 30 s client read timeout; clients with a longer timeout get late
  claims requeued instead of delivered.
- A permanent server-sent `TQ500` on settle, or a SQL-transport `TQ500`, is
  still retried for up to one lease budget while heartbeats are healthy.
- In-flight settle calls are not bounded by the lease budget. Sync and
  cancellation-ignoring handlers keep running after ownership is dropped.
- A release does not restore a half-open breaker probe or refund GCRA claim
  tokens.

## Validation

Recorded in the release pull request: the full local development gate set
from the README, run on PostgreSQL 16, 17 and 18 with Redis 8 and Python 3.12
and 3.13, plus Diverse Data API and worker suites against the built wheel.
