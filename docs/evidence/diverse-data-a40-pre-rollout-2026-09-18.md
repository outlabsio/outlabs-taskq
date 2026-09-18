# Diverse Data a40 pre-rollout evidence

**Date:** 2026-09-18
**Status:** pre-rollout; implementation and local compatibility evidence only
**No live claim:** no package publication, production deployment, migration,
queue-owner mutation, or canary is recorded here

## Topology under test

- `diverse-data-api` is the only database-facing TaskQ host. It owns the SQL
  release, target attestation, same-transaction domain admission, mounted HTTP
  facade, maintenance authority, operator control, and schedule topology.
- `diverse-data-workers` is an HTTP-only execution fleet. Each process uses one
  lane-scoped API key and must not receive a TaskQ database role or DSN.
- TaskQ queue admission ownership protects the API's PostgreSQL producer
  identity. Diverse tenant, API-key, domain-route, provider-spend, and business
  authorization remain in the host application.

This split is intentional. It makes the database owner check independent of
the worker HTTP authorization check and prevents a generic worker credential
from becoming a producer or schema-migration credential.

## Shared candidate evidence

TaskQ candidate commit
`733cb988782d2b20327b8b37ce0fdc4d17588812` passed:

- 148 focused ownership, upgrade, security, and recovery tests;
- 964 passed and 12 skipped on each of PostgreSQL 16, 17, and 18;
- wheel and source-distribution smoke tests on Python 3.12 and 3.13 across
  core, HTTP, Outlabs, and complete dependency modes; and
- exact upgrade from the immutable migration-0046 state with application data
  and object identities preserved.

The contract suite covers direct and replay admission, bind/admission races,
role rename/drop, paused/quiesced adoption and rotation, terminal redrive,
runner continuation, separate-housekeeper schedule fire, exact grants, and
closed catalog verification.

## Consumer discovery evidence

The local a40 wheel was overlaid onto earlier canonical consumer snapshots to
find Python-surface regressions before changing consumer pins:

| Consumer snapshot | Result | Scope limit |
|---|---:|---|
| `diverse-data-api@5ea1f8509e9f97d3259c972e969c3fe801c00c62` | 1,722 passed, 32 skipped | Functional suite after excluding the expected stale exact a37 pin assertion |
| `diverse-data-workers@6511b6bfb1fe6f0c11ef67ac28175933db7f8bba` | 934 passed | Functional suite after excluding the expected stale exact a37 pin assertion |

These snapshots predate the current a38 / SQL 0.6.8 consumer baseline. They
prove useful compatibility breadth and identify the expected release-pin work;
they do not approve current source, published locks, deployment artifacts, or a
production rollout.

## Required closing evidence

Before promotion, append an immutable evidence packet containing:

1. published a40 wheel/source hashes and the TaskQ release record;
2. current API/workers source commits, regenerated lock hashes, deployment
   artifact hashes, installed package versions, and full test results;
3. API prestart and boot evidence that SQL `0.6.8` is an explicit
   compatible-behind state for the installed a40 runtime;
4. proof every older API, worker, scheduler, operator, migrator, and maintenance
   process is fenced before migration;
5. exact migration-0045–0048 fingerprints, post-migration
   `outlabs-release attest`, and independent `taskq db verify`;
6. queue-by-queue adoption to the API producer role, including role OID,
   `owner_present`, target attestation, and actor/reason audit evidence;
7. owner/non-owner LOGIN admission and replay tests, separate-housekeeper
   schedule fire, lane-scoped positive/negative HTTP authorization, worker
   claim/settlement, and response-loss recovery; and
8. one bounded queue canary with job/workflow identity, terminal counts,
   committed domain effect, provider-side-effect reconciliation, and the
   forward-recovery command.

The production verdict remains hold until every item exists for the final
published artifacts and current consumer revisions.
