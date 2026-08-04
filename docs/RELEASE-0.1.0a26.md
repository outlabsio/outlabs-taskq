# outlabs-taskq 0.1.0a26 release notes

**Base release:** 0.1.0a25
**SQL contract:** 0.3.0 and 0.3.1
**Protocol document:** 1.0.16
**Packaged migrations:** 0001–0021, unchanged

## Production integration closeout

This release keeps the a25 command and database contracts while closing the
gaps found during the first OutlabsAPI and Créditos integrations.

- `--dsn-env NAME` makes an environment-backed SQL target explicit without
  putting a credential-bearing DSN in process arguments or requiring a context
  file on every supervised local worker host.
- `worker run`, `scheduler run`, and `scheduler once` close their short-lived
  target-attestation transport before starting the runtime-owned transport, so
  each long-running process owns only the pool/client it actually uses.
- Consumer-shaped tests cover consolidated worker and bounded scheduler
  invocations using the stable `taskq.cli/v1` output contract.
- Documentation records that 0.1.0a24 was never published and that a25/a26 can
  be deployed on contract 0.3.0 before migration 0021 activates 0.3.1.
- Tag publication builds and verifies distributions, publishes through trusted
  PyPI, and attaches the exact wheel and sdist to a GitHub prerelease.

## Rollout

1. Stop every worker and scheduler sharing the target TaskQ database.
2. Pin every process and deployment command to the exact 0.1.0a26 artifact.
3. Replace old alpha CLI invocations with `worker run`, `scheduler run|once`,
   and `db plan|migrate|verify`.
4. Start against the existing contract 0.3.0 database and run target, doctor,
   and compatibility checks.
5. Only after no pre-a25 runtimes remain, review `db plan` and apply migration
   0021 to activate contract 0.3.1.
6. Start one worker and one scheduler clock, then observe one complete job
   before restoring normal supervision.

Production mutations still require the exact installation ID,
`--allow-production`, and `--yes`. Runtime credentials remain separate from
owner/migration credentials.
