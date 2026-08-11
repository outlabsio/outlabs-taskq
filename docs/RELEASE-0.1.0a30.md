# outlabs-taskq 0.1.0a30 release notes

**Base release:** 0.1.0a29
**SQL contract:** 0.6.6 (unchanged)
**Protocol document:** 1.0.17 (unchanged)
**Packaged migrations:** 0001–0042 (unchanged)

a30 is a package-only worker-runtime fix. It carries no schema, contract,
protocol, or migration change.

## Fix: paused queues could overload HTTP control planes

`ClaimState.PAUSED` is a normal operator-controlled queue state. The worker
service accepted the typed result but did not put that queue into a bounded
probe interval. Consumers configured for low-latency polling could therefore
continue claiming a deliberately paused queue every fraction of a second,
eventually hit an API rate limit, and misclassify the resulting transport error
as an environment outage.

The worker now places a paused queue into a default five-second probe interval,
honors a longer server retry hint, applies upward-only jitter, and ignores
notification nudges until the probe is due. Other subscribed queues remain
eligible and continue independently. `paused_poll_interval` is configurable on
`WorkerServiceOptions` for deployments that need a different recovery bound.

A deterministic regression test proves that repeated nudges cannot hot-loop a
paused queue, the service remains ready, and claiming resumes when the bounded
probe expires.

## Rollout

1. Pin every API, scheduler, worker, migration, and operator process to the
   exact 0.1.0a30 artifact.
2. Do not rerun or alter migration ledger rows solely for this fix; databases
   already at contract 0.6.6 remain current.
3. Start workers while queues remain paused and verify there are no
   `claim.unavailable` or environment-recovery loops.
4. Open one queue and one bounded canary before releasing other lanes.

Production mutations still require the exact installation ID,
`--allow-production`, and `--yes`. Runtime credentials remain separate from
owner/migration credentials.
