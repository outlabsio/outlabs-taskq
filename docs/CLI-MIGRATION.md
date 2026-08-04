# Migrating to the resource-oriented TaskQ CLI

Release 0.1.0a25 intentionally replaces the alpha command grammar. There are
no compatibility aliases. Pinning exact prereleases prevents accidental
rollout; update automation and deployment examples before changing the pin.

| Before 0.1.0a25 | 0.1.0a25 and later |
|---|---|
| `taskq migrate` | `taskq db plan`, then `taskq db migrate --plan-digest … --yes` |
| `taskq verify` | `taskq db verify` |
| `taskq worker` | `taskq worker run` |
| `taskq scheduler` | `taskq scheduler run` |
| `taskq scheduler --once` | `taskq scheduler once` |
| `taskq scheduler doctor` | `taskq scheduler doctor` |
| `taskq schedule plan` | `taskq schedule manifest plan` |
| `taskq schedule apply` | `taskq schedule manifest apply` |
| `taskq schedule retire` for a manifest key | `taskq schedule manifest retire` |
| `taskq auth sync-permissions` | `taskq auth plan`, then `taskq auth apply` |
| `--json` | `-o json` |

Connected commands no longer infer an endpoint from a generic environment
variable. Add a secret-free context and pass `--context`, or pass complete
explicit connection flags. Mutating SQL commands also need an actor.

Deployment migration order:

1. Install and pin the same 0.1.0a25-or-newer artifact in every TaskQ runtime
   consumer while the database remains on contract 0.3.0.
2. Replace old command invocations and parse the `taskq.cli/v1` envelope.
3. Add and validate contexts; put only credential environment-variable names
   in the config file.
4. Run `doctor`, `target show`, and `db verify` read-only checks.
5. Review `db plan`, capture `data.plan_digest`, and apply with literal
   `--yes` (plus production gates when applicable).
6. Apply migration 0021 only after all consumers sharing the database run an
   artifact that supports both contract 0.3.0 and 0.3.1.

After database activation, rollback means restoring the previously pinned
artifact only if it supports the active contract. Migration 0021 is additive
and forward-only; database rollback requires restore or a forward fix.
