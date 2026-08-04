# Unreleased 0.1.0a24 candidate record

> `0.1.0a24` was never tagged or published. The CLI foundation described here
> shipped together with the 0.3.1 read model in `0.1.0a25`. This file is kept
> only as a design-history record and must not be used as an installation or
> rollout target.

**Base release:** 0.1.0a23
**SQL contract:** 0.3.0, unchanged
**Protocol document:** 1.0.16 client models published dormant
**Packaged migrations:** 0001–0020, unchanged

## CLI foundation

This candidate replaced the seven-command argparse surface with the
resource-oriented Click CLI documented in [`CLI.md`](CLI.md). It adds explicit
secret-free contexts, SQL/HTTP transport parity, target preflight for every
mutation, production/destructive acknowledgement gates, plan digests, stable
structured output/errors, bounded pagination, watch/wait, stdin input, command
discovery, schemas, and completions.

The alpha grammar changes intentionally and has no compatibility aliases. See
[`CLI-MIGRATION.md`](CLI-MIGRATION.md).

The candidate prepared typed models and client/transport methods for the 0.3.1
read model while leaving capability-gated commands unavailable on 0.3.0. The
published `0.1.0a25` artifact supports both the pre-migration 0.3.0 contract and
the post-migration 0.3.1 contract.

## Rollout gate

Pin every runtime consumer to the same published `0.1.0a25`-or-newer artifact
and migrate all deployment commands before applying database migration 0021. Exercise discovery, context
validation, doctor, queue/job inspection, stdin enqueue, wait, and one gated
operator action from the installed wheel or sdist.
