# outlabs-taskq 0.1.0a25 release notes

**Base release:** 0.1.0a24
**SQL contract:** 0.3.1
**Protocol document:** 1.0.16
**Packaged migrations:** 0001–0021

## Complete operator read model

Migration `0021_cli_read_model.sql` activates bounded projections for:

- all finite job views: ready, scheduled, blocked, running,
  cancel-requested, failed, and finished;
- ordered job events with detail opt-in and no attempt IDs;
- global running/finished workflow pages;
- global active/paused/retired job-schedule pages.

The matching SQL transport, HTTP routes, ASGI facade, synchronous/asynchronous
clients, Pydantic models, capabilities, cursor authorization, and CLI commands
ship together. HTTP job-event authorization resolves the job's queue before
decoding its cursor; workflow listing requires global read and schedule listing
requires global control.

Only indexes proven necessary by bounded PostgreSQL 16 and 18 plan gates are
included. The release does not add payload/text search, arbitrary status
filters, raw table/event access, or a reporting query language.

## Rollout

1. Confirm every runtime consumer is pinned to 0.1.0a24 or newer.
2. Install the exact 0.1.0a25 artifact.
3. Run `taskq --context NAME db plan -o json` and review the target-bound
   digest and pending immutable migration.
4. Apply with `taskq --context NAME --yes db migrate --plan-digest DIGEST`.
5. Run `db verify`, `doctor`, and SQL/HTTP read-model parity probes.

The migration is additive and forward-only. Before migration, rollback is the
previously pinned artifact. After activation, rollback is database restore or a
forward fix plus an artifact compatible with contract 0.3.1.

## Release gates

- fresh install and 0.3.0→0.3.1 upgrade with exact catalog/grant/checksum
  verification;
- authorization-before-cursor-decode, cursor misuse, and race tests;
- bounded plan gates on PostgreSQL 16 and 18;
- full SQL and real-ASGI HTTP parity vectors;
- wheel and sdist install/acceptance outside the checkout;
- updated consumer deployment and public CLI reference documentation.
