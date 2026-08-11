# outlabs-taskq 0.1.0a33 release notes

**Base release:** 0.1.0a32
**SQL contract:** 0.6.6 (unchanged)
**Protocol document:** 1.0.17 (unchanged)
**Packaged migrations:** 0001–0042 (unchanged)

a33 is a package-only installed-harness compatibility release. It carries no
schema, contract, protocol, or migration change.

## Fix: installed benchmark harnesses required Git at runtime

The packaged benchmark and load-lab provenance helper previously executed
`git rev-parse HEAD` without handling an unavailable executable. Exact slim
runtime images intentionally omit Git, so an otherwise valid installed a32
artifact could complete load scenarios and then crash while recording their
provenance.

The helper now treats both a missing Git executable and a non-checkout runtime
as explicit `unknown` source provenance. Installed package and image identity
remain independently attested by the consumer release manifest; no synthetic
source SHA is invented.

Regression coverage exercises both failure modes and proves the installed
harness remains usable with no Git on `PATH` and outside a source checkout.

## Consumer and operational implications

This change affects only benchmark/load-harness metadata collection. Claiming,
heartbeats, effects, settlement, scheduling, priority, flow control, HTTP
authorization, SQL functions, and migration state are unchanged from a32.

Consumers that run the packaged load lab from minimal images should pin a33.
They must continue to attest the immutable image digest, installed package
version, source release SHA, SQL contract, target binding, and authorization
profile separately. An `unknown` harness Git field is expected in an installed
artifact that has no repository metadata and is not a substitute for release
provenance.

## Rollout

1. Pin API, scheduler, workers, migration tools, operator tools, and load-lab
   tools to the exact 0.1.0a33 artifact while queues remain paused.
2. Do not alter migration ledger rows; databases already at SQL contract 0.6.6
   remain current.
3. Build consumers from clean merged source and attest the source SHA,
   immutable image or bundle digest, platform, and installed TaskQ version.
4. Run the complete packaged TaskQ load-lab matrix from the exact a33 runtime
   image with Git absent. Require every scenario to pass and record explicit
   installed-artifact provenance.
5. Re-run the consumer's native concurrency, priority, mixed-worker,
   settlement-backpressure, failure-recovery, domain-parity, authorization, and
   restart gates before production promotion.
6. Promote only the highest clean staging tier and retain the previous a32
   immutable consumer artifacts plus the rehearsed rollback command.

Production mutations still require the exact installation ID,
`--allow-production`, and `--yes`. Runtime credentials remain separate from
owner/migration credentials, and package upgrades do not authorize opening
queues, changing concurrency, or enabling provider spend.
