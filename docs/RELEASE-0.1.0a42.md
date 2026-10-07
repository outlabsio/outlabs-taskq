# outlabs-taskq 0.1.0a42 release notes

**Base release:** 0.1.0a41
**SQL contract:** 0.6.12 (unchanged)
**Protocol document:** 1.0.18 (unchanged)
**Packaged migrations:** 0001–0048 (unchanged)

## Worker claim loop: wake on work, not on a timer

`WorkerService` slept `poll_interval` (default 5 s) after any claim round that
filled every slot and after every empty answer. The Stage 2C claim-loop
specification already required continuing without a wait while work flows and
listed capacity change as a wake source; the loop did not honor it. Over HTTP,
where the facade already holds an empty claim open and answers it on the
queue's commit notification, the sleep was pure latency.

Measured on a production HTTP consumer (a40): jobs completed in about 0.14 s
and each worker then idled about 5.3 s before its next claim with 500 jobs
ready. Every worker did at most `concurrency` jobs per poll round.

This release:

- re-claims immediately after a productive sweep. With every slot busy the
  loop head waits on the supervisor's capacity event, so the next claim leaves
  the moment a job settles. This applies to every transport, SQL and HTTP;
- treats a server-held empty claim as the wait. A single-queue worker whose
  transport implements the new `taskq.transport.ClaimWaitTransport` capability
  (`AsyncTaskqHttpClient.claim_wait_seconds`) re-issues the claim at once when
  the empty answer was held for at least `min(claim_wait_seconds,
  poll_interval)`. An idle worker therefore always has exactly one held claim
  outstanding, and a commit notification delivers work without a client sleep;
- keeps every bounded wait: claim errors back off with
  `claim_backoff_base`/`claim_backoff_cap`, paused queues probe at
  `paused_poll_interval`, throttled claims honor the server retry hint, and an
  empty answer that returns sooner than the held threshold (an intermediary or
  server ignoring the wait) falls back to `poll_interval`, so the idle claim
  rate never exceeds one claim per poll interval;
- leaves multi-queue services on the fair sweep plus bounded poll interval, as
  Stage 3 permits server long poll only for single-queue workers.

## Load

An idle single-queue HTTP worker now sends one claim per `claim_wait_seconds`
instead of one per `claim_wait_seconds + poll_interval` (25 s instead of 30 s at
the CLI defaults). The facade's per-request authoritative rechecks are
unchanged; the worker simply spends the whole idle period inside a held claim.
Consumers that want the previous idle request rate can raise
`claim_wait_seconds` by the old poll interval (maximum 30 s).

## Rollout

No SQL, protocol, migration, HTTP route, authorization, or scheduler change.
API runtimes on a40/a41 remain compatible; only workers need a42 to gain the
behavior. Instant wake still depends on the API runtime's long-poll listener
(`TaskqRuntimeOptions.long_poll_listener_enabled`); without it a held claim is
answered by the facade's bounded recheck.

## Validation

- New `tests/test_s2_worker_service_wake.py`: busy queues drain back-to-back
  without advancing the clock (SQL- and HTTP-shaped transports), an empty
  long-poll queue makes exactly one claim per window, a notification answers
  the held claim and re-arms at once, and unheld empty answers, paused queues,
  claim-error backoff, throttling, and multi-queue services keep their waits.
- The four wake-on-work tests fail on the a41 loop; the guard tests pass on
  both.
- Full suite including the PostgreSQL contract and HTTP runtime layers.
