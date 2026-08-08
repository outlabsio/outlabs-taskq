# outlabs-taskq 0.1.0a28 release notes

**Base release:** 0.1.0a27
**SQL contract:** 0.6.6 (unchanged)
**Protocol document:** 1.0.17 (unchanged)
**Packaged migrations:** 0001–0042 (unchanged)

a28 is a CLI-only patch. It carries no schema, contract, or protocol change — only a fix to
the `taskq auth` command group, which was unusable in a27.

## Fix: `taskq auth plan` and `taskq auth apply` were broken in a27

In a27 every invocation of `taskq auth plan` and `taskq auth apply` failed with
`error[CLI_INTERNAL]: command failed with TypeError`, so the IAM permission-provisioning
surface could not be used at all. Both commands hash the provisioning plan into a
`plan_digest` through `_plan_digest()`, which renders the report with the CLI's `jsonable()`
helper and then `json.dumps`. `jsonable()` converted pydantic models, mappings, sequences,
and `datetime`/`UUID`/`Enum`, but not dataclasses — so the frozen `ProvisioningReport`
dataclass fell through unchanged and `json.dumps` raised
`TypeError: Object of type ProvisioningReport is not JSON serializable`.

The fix teaches `jsonable()` to serialize any dataclass instance (its fields, recursively).
That is the single rendering path every CLI command shares, so the same fix also hardens any
future dataclass a command returns. A regression test now drives the real `_plan_digest` with
a `ProvisioningReport`, covering the CLI-transport path that shipped untested in a27.

## No schema or protocol change

Databases already migrated to contract 0.6.6 by a27 need no migration for a28 — the upgrade
is package-only. The runtime still accepts contracts 0.3.1 through 0.6.6.

## Rollout

1. Pin every process and deployment command to the exact 0.1.0a28 artifact.
2. If you provision IAM through `taskq auth`, upgrade the package before your next
   `auth plan` / `auth apply`; there is no database step.
3. No worker, scheduler, or migration action is required for the fix itself.

Production mutations still require the exact installation ID, `--allow-production`, and
`--yes`. Runtime credentials remain separate from owner/migration credentials.

## Known issues

- The breaker's half-open wedge deadline is anchored to the trip time rather than the probe
  election time (carried from a27): on an idle queue where a probe is elected more than two
  cooldowns after the trip, that probe can be re-opened before it settles — one wasted open
  cycle, self-correcting. Deferred (a fix needs a dedicated timestamp column / contract bump).
- Migration 0042 lacks a self-check `DO` block; a future migration will add one. Cosmetic —
  `verify()` covers the installed state.
