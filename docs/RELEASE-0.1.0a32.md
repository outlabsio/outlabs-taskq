# outlabs-taskq 0.1.0a32 release notes

**Base release:** 0.1.0a31
**SQL contract:** 0.6.6 (unchanged)
**Protocol document:** 1.0.17 (unchanged)
**Packaged migrations:** 0001–0042 (unchanged)

a32 is a package-only worker settlement-backpressure release. It carries no
schema, contract, protocol, or migration change.

## Fix: completion could become settlement-unknown under HTTP backpressure

The worker previously applied its ordinary five-attempt transient settlement
bound to HTTP 429 responses. A sufficiently busy shared worker credential could
therefore exhaust the short retry window while the worker still owned and
heartbeated the lease, then terminate fail-closed with `settlement_unknown`.

The HTTP decoder now preserves a bounded numeric `Retry-After` hint on typed
TaskQ errors. The worker treats `TQ429` settlement backpressure separately from
ordinary transient failures: it maintains heartbeat ownership and retries until
a configurable elapsed-time horizon (120 seconds by default, bounded to 3,600
seconds). Other transient settlement errors retain the existing attempt bound,
and exhaustion of either bound remains fail-closed as `settlement_unknown`.
Fatal diagnostics retain the job ID and safe typed error code without exposing
payloads, credentials, or arbitrary server text.

Regression coverage includes repeated 429 responses beyond the ordinary retry
count, elapsed-horizon exhaustion retaining `TQ429`, preservation of
`Retry-After`, and five simultaneous backpressured completions recovering
without a worker fatal.

## Consumer capacity contract

This release improves recovery; it does not make an undersized authorization
budget a valid fleet configuration. Consumers must budget claim, heartbeat,
effect, and settlement requests for the entire credential-sharing fleet. Use a
reviewed fleet credential with explicit headroom or distinct least-privilege
process credentials, then verify the deployed Outlabs Auth key configuration
before opening scaled queues.

## Rollout

1. Pin API, scheduler, workers, migration tools, and operator tools to the exact
   0.1.0a32 artifact while queues remain paused.
2. Do not alter migration ledger rows; databases already at SQL contract 0.6.6
   remain current.
3. Verify each deployed worker credential's scopes and request budget through
   the consumer's source-controlled bootstrap/reconciliation path.
4. Rehearse deliberate 429 settlement backpressure and stepped multi-worker
   concurrency on staging. Require zero fatal exits, bounded retries, unique
   worker identities, and exact TaskQ/domain-ledger parity.
5. Promote only the highest clean staging tier. Keep production fail-closed and
   retain the previous immutable package and consumer artifacts for rollback.

Production mutations still require the exact installation ID,
`--allow-production`, and `--yes`. Runtime credentials remain separate from
owner/migration credentials.
