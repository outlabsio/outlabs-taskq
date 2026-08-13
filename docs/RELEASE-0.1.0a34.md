# outlabs-taskq 0.1.0a34 release notes

**Base release:** 0.1.0a33
**SQL contract:** 0.6.6 (unchanged)
**Protocol document:** 1.0.17 (unchanged)
**Packaged migrations:** 0001–0042 (unchanged)

a34 is a package-only consumer-safety release. It carries no schema, contract,
protocol, or migration change.

## Fix: redrive retry-budget terminality was easy to miscalculate

`JobContext` exposes both `attempt_number` (the lifetime claim ordinal, which
an operator redrive intentionally preserves) and `failure_count` (the consumed
retry budget, which redrive resets to zero). A consumer that derived terminal
domain effects from `attempt_number >= max_attempts` would terminalize the
first attempt after a redrive even though TaskQ correctly schedules another
retry.

`JobContext.failure_would_exhaust_retry_budget` now provides the safe
calculation, matching TaskQ's own settlement rule
(`failure_count + 1 >= max_attempts`). The Stage 2B worker runtime
specification documents the distinct redrive semantics of both fields. A
non-retryable failure remains terminal regardless of this helper; release and
snooze still do not consume the failure budget. Unit and real-PostgreSQL
regressions cover ordinary and redriven contexts, including the full
three-failures, redrive, reclaim, retry-scheduled lifecycle.

Consumers deciding terminal provider or domain effects inside handlers should
migrate any lifetime-attempt comparison to the new helper when they pin a34.

## Fix: oversized workflow parameters normalize before SQL

`SqlTaskqTransport.create_workflow` now preflights the serialized params
document against the 64 KiB fence using the same wire-model measure the HTTP
facade enforces. An oversized document raises the public validation error with
`{field, actual_bytes, max_bytes}` details and never echoes payload content,
so HTTP consumers can return a typed 422 instead of an opaque 500.

The SQL `octet_length` fence inside `taskq.create_workflow` stays
authoritative; live SQLSTATE `TQ422` rejections from both create-workflow
overloads continue to normalize to `TaskqValidationError`, now pinned by
regression at the raw-SQL, SQL-transport, and HTTP-facade layers.

Regression evidence: a 3,984-member explicit-ID repair cohort (~155 KiB
params) admitted on 12 Aug 2026 was correctly rejected by PostgreSQL but
surfaced as consumer HTTP 500 with no byte diagnostics.

## Consumer and operational implications

Claiming, heartbeats, effects, settlement, scheduling, priority, flow control,
HTTP authorization, SQL functions, and migration state are unchanged from a33.
Databases already at SQL contract 0.6.6 remain current.

Consumers that plan large explicit-ID workflows should keep shard-splitting
parameter documents; the new preflight changes the failure mode from an
unhandled driver error to a typed validation error, not the boundary itself.

## Rollout

1. Pin API, scheduler, workers, migration tools, operator tools, and load-lab
   tools to the exact 0.1.0a34 artifact while queues remain paused.
2. Do not alter migration ledger rows; databases already at SQL contract 0.6.6
   remain current.
3. Build consumers from clean merged source and attest the source SHA,
   immutable image or bundle digest, platform, and installed TaskQ version.
4. Re-run the consumer's native concurrency, priority, mixed-worker,
   settlement-backpressure, failure-recovery, domain-parity, authorization, and
   restart gates before production promotion.
5. Promote only the highest clean staging tier and retain the previous a33
   immutable consumer artifacts plus the rehearsed rollback command.

Production mutations still require the exact installation ID,
`--allow-production`, and `--yes`. Runtime credentials remain separate from
owner/migration credentials, and package upgrades do not authorize opening
queues, changing concurrency, or enabling provider spend.
