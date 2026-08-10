# outlabs-taskq 0.1.0a29 release notes

**Base release:** 0.1.0a28  
**SQL contract:** 0.6.6 (unchanged)  
**Protocol document:** 1.0.17 (unchanged)  
**Packaged migrations:** 0001–0042 (unchanged)

a29 is a package-only runtime compatibility fix. It carries no schema,
contract, protocol, or migration change.

The release lock and optional-integration gate resolve Outlabs Auth 0.1.0a31,
the current compatible auth artifact.

## Fix: worker-presence runtime rejected current contracts

a28's general, admission, workflow, workflow-read, and schedule compatibility
sets accepted SQL contracts through 0.6.6. Its worker-presence compatibility
set accidentally stopped at 0.3.1. A host that correctly enabled
`worker_presence_enabled` therefore failed startup with `TQ426: unsupported
version` immediately after migrating its database to the package's own 0.6.6
contract.

a29 extends the closed worker-presence compatibility set through every released
contract from 0.4.0 to 0.6.6. The underlying worker-presence capability and SQL
surface did not change across those additive contracts. A regression test now
starts the runtime with worker presence enabled against the package's current
`CONTRACT_VERSION`, preventing the release metadata and runtime gate from
drifting apart again.

## Rollout

1. Pin every API, scheduler, worker, migration, and operator process to the
   exact 0.1.0a29 artifact.
2. Do not rerun or alter migration ledger rows solely for this fix; databases
   already at contract 0.6.6 remain current.
3. Start with queues paused, verify the API runtime reaches healthy, then start
   one scheduler and one worker lane before opening canary work.

Production mutations still require the exact installation ID,
`--allow-production`, and `--yes`. Runtime credentials remain separate from
owner/migration credentials.
