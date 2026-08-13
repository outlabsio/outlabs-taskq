# outlabs-taskq 0.1.0a35 release notes

**Base release:** 0.1.0a34
**SQL contract:** 0.6.6 (unchanged)
**Protocol document:** 1.0.17 (unchanged)
**Packaged migrations:** 0001–0042 (unchanged)

a35 is a package-only deploy-safety release. It carries no schema, contract,
protocol, or migration change.

## Change: availability outages keep workers alive but unready

`WorkerService` previously applied one bounded-retry-then-fatal posture to
every claim-path error class: eight consecutive errors failed the service
closed. For availability-shaped errors (`TQ503`) that turned a rolling API
deployment or dependency outage into a process exit — a systemd restart loop,
or a permanently down replica under fleet supervisors that treat exits as
manual-restart events.

Claim errors whose code is `TQ503` now back off under the existing capped
jittered cadence indefinitely: the worker stays alive and unready, claims
nothing, and resumes claiming on the first successful claim after the
dependency returns. A one-time `claim.outage_persisting` escalation logs at
the point the old posture would have died, so a persisting outage stays loud.
Every other claim error class keeps the exact bounded-retry-then-fatal
behavior, and the new `unavailable_fatal_threshold` worker-service option
restores a bounded posture for availability errors where a deployment wants
one. The backoff exponent is guarded for very long streaks.

## Change: bare 502/503/504 responses classify as unavailable

An intermediary (load balancer, reverse proxy) answering during a deploy
window returns 5xx without the TaskQ protocol envelope. The HTTP client
previously classified that as generic protocol drift (`TaskqInternalError`,
`missing_or_invalid_protocol_header`); it now raises `TaskqUnavailableError`
with the upstream status and any `Retry-After` guidance, so worker backoff and
consumer handlers see the outage as what it is. Envelope-less responses on any
other status keep the protocol-drift classification.

## Validation

Loadlab L5 gains a sustained-outage phase: a twelve-error availability streak
far past the bounded threshold must leave the worker alive and unready, and
claims must resume once the fault clears. Unit regressions cover the
availability posture, the restored bounded option, the unchanged
non-availability posture, the exponent guard, and the 5xx reclassification
with `Retry-After`.

## Consumer and operational implications

Claiming, heartbeats, effects, settlement, scheduling, priority, flow control,
HTTP authorization, SQL functions, and migration state are unchanged from a34.
Databases already at SQL contract 0.6.6 remain current.

Fleet supervisors that raised `claim_fatal_threshold` to buy outage headroom
can leave it at the default after pinning a35; the threshold again means what
it says for genuinely suspicious error streaks. Rolling API deployments no
longer require draining workers first, though drain-first remains the
sanctioned procedure for replacing or resizing lanes that hold paid work.

## Rollout

1. Pin API, scheduler, workers, migration tools, operator tools, and load-lab
   tools to the exact 0.1.0a35 artifact while queues remain paused.
2. Do not alter migration ledger rows; databases already at SQL contract 0.6.6
   remain current.
3. Build consumers from clean merged source and attest the source SHA,
   immutable image or bundle digest, platform, and installed TaskQ version.
4. Re-run the consumer's native concurrency, priority, mixed-worker,
   settlement-backpressure, failure-recovery, domain-parity, authorization, and
   restart gates before production promotion.
5. Promote only the highest clean staging tier and retain the previous a34
   immutable consumer artifacts plus the rehearsed rollback command.

Production mutations still require the exact installation ID,
`--allow-production`, and `--yes`. Runtime credentials remain separate from
owner/migration credentials, and package upgrades do not authorize opening
queues, changing concurrency, or enabling provider spend.
