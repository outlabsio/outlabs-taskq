# outlabs-taskq 0.1.0a27 release notes

**Base release:** 0.1.0a26
**SQL contract:** 0.3.0 and 0.3.1
**Protocol document:** 1.0.16
**Packaged migrations:** 0001–0021, unchanged

## Fresh-database bootstrap closeout

The OutlabsAPI and Créditos consumer rehearsals found two defects in the
documented target-binding sequence. This patch makes that sequence stable for
operators and coding agents without changing the database contract.

- The intentional migration stop before `0020` now exits `2` with error code
  `CLI_TARGET_BINDING_REQUIRED` and category `target_binding_required`, rather
  than presenting a retryable internal database failure.
- `target show` now permits inspection when the database reports `unbound` and
  the selected context already declares the environment that will be bound.
- A configured expected installation UUID is still enforced, and every other
  target mismatch remains fail-closed.
- Driver exception text, SQL, and credentials remain absent from the stable
  error envelope.

## Rollout

1. Pin every TaskQ process and deployment command to the exact 0.1.0a27
   artifact before applying migration 0021.
2. Run `db plan`, review `data.plan_digest`, and apply it with `--yes`.
3. On code `CLI_TARGET_BINDING_REQUIRED`, run `target show`, review the safe
   fingerprint, and bind with the exact installation ID and binding version.
4. Create a new plan, apply its digest, and run `db verify`.
5. Start one worker and one scheduler clock, then observe a complete job.

Production mutations still require the exact installation ID,
`--allow-production`, and `--yes`.
