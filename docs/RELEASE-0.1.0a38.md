# outlabs-taskq 0.1.0a38 release notes

**Base release:** 0.1.0a37  
**SQL contract:** 0.6.8  
**Protocol document:** 1.0.18  
**Packaged migrations:** 0001–0044

## Continuation flow inheritance

Policy-bearing workflow followups now inherit the parent job's `flow_key`
inside `_enqueue_followup`. This keeps every dynamically admitted provider
continuation behind the same TaskQ flow limit as its root job. Detached
followups retain their historical null flow key.

The typed `TaskQ.enqueue` facade now also exposes the already-supported
`ttl_seconds` and `flow_key` command fields, matching `enqueue_many` and the
SQL/HTTP transports.

## Verification

- The complete PostgreSQL 18 suite passed: 836 tests passed and 12 optional
  tests skipped.
- The package-only suite passed: 454 tests passed and 394 SQL tests skipped.
- Ruff lint and formatting checks passed.
- The wheel and source distribution build successfully, and the installed
  artifact smoke covers core, HTTP, Outlabs Auth, migration, and contract
  surfaces outside the source checkout.

## Compatibility and rollout

The a38 runtime accepts both SQL contracts 0.6.7 and 0.6.8. Runtimes older
than a38 do not accept 0.6.8, so deploy the exact a38 artifact to every API,
scheduler, worker, migration, operator, and load-lab process before applying
`0044`.

Migration `0044_continuation_flow_inheritance` is forward-only. Roll out the
a38 runtime to API, workers, migration, and operator processes before applying
the migration, verify SQL contract 0.6.8, and then enable workflows that rely
on inherited flow control.
