# outlabs-taskq 0.1.0a31 release notes

**Base release:** 0.1.0a30
**SQL contract:** 0.6.6 (unchanged)
**Protocol document:** 1.0.17 (unchanged)
**Packaged migrations:** 0001–0042 (unchanged)

a31 is a package-only worker observability and rollout-safety release. It carries
no schema, contract, protocol, or migration change.

## Fix: concurrent worker failures were opaque

When a job handler reached a fatal settlement outcome, the service previously
logged only `worker.fatal` and an exception type. Under concurrent load this
hid the job outcome and the settlement operation that failed, and concurrent
callbacks could emit duplicate fatal records after the first failure had
already selected the service's terminal error.

The worker now records a bounded, non-payload diagnostic containing the fatal
error type, TaskQ error code when available, job-run outcome, settlement
command, and settlement outcome. Only the first fatal transition logs and owns
the stored terminal error. Secrets, payloads, provider responses, and arbitrary
exception text are not included.

The release also documents the consumer rollout rule: API, scheduler, workers,
migration tools, and operator tools must all use the same exact TaskQ artifact,
and package capability checks do not replace database-contract verification.

## Rollout

1. Pin every API, scheduler, worker, migration, and operator process to the
   exact 0.1.0a31 artifact before opening queues.
2. Do not rerun or alter migration ledger rows solely for this package-only
   release; databases already at contract 0.6.6 remain current.
3. Reproduce the formerly opaque failure on isolated staging while collecting
   the new `worker.fatal` record and settlement context.
4. Fix the owning TaskQ or consumer source, rebuild immutable artifacts, then
   rerun stepped concurrency and mixed-queue gates before production promotion.
5. Keep production at its last proven concurrency until staging evidence shows
   worker health, retry correctness, domain persistence, and ledger parity.

Production mutations still require the exact installation ID,
`--allow-production`, and `--yes`. Runtime credentials remain separate from
owner/migration credentials.
