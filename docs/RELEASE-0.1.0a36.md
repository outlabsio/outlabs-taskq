# outlabs-taskq 0.1.0a36 release notes

**Base release:** 0.1.0a35
**SQL contract:** 0.6.6 (unchanged)
**Protocol document:** 1.0.17 (unchanged)
**Packaged migrations:** 0001–0042 (unchanged)

a36 is a package-only worker-observability release. It carries no schema,
contract, protocol, or migration change, and no behavior change beyond log
levels and one new log event.

## Change: degraded-recovery transitions log at WARNING

`WorkerService` logged the `worker.degraded` transition at WARNING but the
paired `worker.ready` recovery at INFO. Under default WARNING-level logging a
transient degrade — for example a listener connect attempt that loses a
sub-second race at startup and reconnects immediately — left a lone
`worker.degraded` line as the log's final word: a worker whose LISTEN session
had been healthy for hours read as degraded the whole time.

The `worker.ready` transition out of `DEGRADED` now logs at WARNING, so the
recovery edge is as visible as the failure it clears. The initial
`STARTING` → `RUNNING` transition on a clean start keeps logging
`worker.ready` at INFO, so quiet startups stay quiet at the default level.

## Change: listener connect failures log a throttled `listener.error`

The listener reconnect loop swallowed connect exceptions with no log line at
all: a listener that never managed to connect degraded the worker without ever
saying why. Connect and wait failures now log a `listener.error` WARNING
carrying the exception summary (`error`) and the count of repeats suppressed
since the last line (`suppressed_errors`). Because reconnect attempts start at
sub-second backoff delays, the event is throttled to one line per
`listener_backoff_cap` window (default 30 seconds): the first failure logs
immediately, repeats inside the window are counted, and the next line reports
how many were suppressed.

## Validation

Unit regressions cover the reported shape end to end: a startup listener
connect failure followed by an immediate reconnect must emit
`worker.degraded`, `listener.error`, and `worker.ready` all at WARNING; a
presence-driven degrade/recover cycle must show both edges at WARNING; a clean
start must keep `worker.ready` at INFO; and a permanently failing listener
must log exactly one `listener.error` per backoff-cap window with an accurate
suppressed count.

## Consumer and operational implications

Claiming, heartbeats, effects, settlement, scheduling, priority, flow control,
HTTP authorization, SQL functions, and migration state are unchanged from a35.
Databases already at SQL contract 0.6.6 remain current.

Log pipelines that alert on WARNING now see `worker.ready` when a degraded
worker recovers and `listener.error` while a listener cannot connect; alerts
keyed on a trailing `worker.degraded` line should pair it with the
`worker.ready` that clears it. Consumers pinning an earlier prerelease (for
example `outlabs-taskq[outlabs]==0.1.0a35`) must bump their pin and republish
to pick up the visibility fixes.

## Rollout

1. Pin API, scheduler, workers, migration tools, operator tools, and load-lab
   tools to the exact 0.1.0a36 artifact while queues remain paused.
2. Do not alter migration ledger rows; databases already at SQL contract 0.6.6
   remain current.
3. Build consumers from clean merged source and attest the source SHA,
   immutable image or bundle digest, platform, and installed TaskQ version.
4. Re-run the consumer's native concurrency, priority, mixed-worker,
   settlement-backpressure, failure-recovery, domain-parity, authorization, and
   restart gates before production promotion.
5. Promote only the highest clean staging tier and retain the previous a35
   immutable consumer artifacts plus the rehearsed rollback command.

Production mutations still require the exact installation ID,
`--allow-production`, and `--yes`. Runtime credentials remain separate from
owner/migration credentials, and package upgrades do not authorize opening
queues, changing concurrency, or enabling provider spend.
