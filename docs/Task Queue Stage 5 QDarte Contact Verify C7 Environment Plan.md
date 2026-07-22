# Task Queue Stage 5 — QDarte Contact Verify C7 Environment Plan

**Status:** frozen C7-00 proposal; no production action authorized
**Authority:** subordinate to Protocol v1 revision 1.0.8, Function Manifest /
SQL contract 0.1.5, ADR-020, ADR-022, ADR-023, and the accepted C6
Compatibility and Cutover Specification
**Scope:** one contact-verification lane only

## 1. Decision and boundary

C7 proposes **Mac-mini local production on `mini87`** as the first lasting
QDarte environment for the accepted contact package lane. This is the correct
target because it is the authoritative QDarte API and worker environment, it
already owns the contact domain effects, and its database/media durability is
bound to `/Volumes/Server87`. The cloud intake environment does not host the
full QDarte API or worker fleet. The MacBook `qdarte-dev` stack remains a
rehearsal environment and cannot supply production evidence.

This document is a plan, not an execution record. It creates no database,
role, token, service, queue, backup, job, provider call, deployment, or source
branch. C7-01 remains closed until a targeted review accepts this plan.

C7 keeps the C6 rules intact:

- one producer per request, selected from the closed
  `legacy | draining | package` mode;
- no active-row import, row copy, cross-backend retry, or direct fallback;
- durable reserve-before-plan admission and admitted replay without
  replanning;
- a separate closed worker with one queue and one task type;
- stable QDarte effect idempotency before terminal settlement; and
- zero-DML rollback with package history preserved.

## 2. Named environment and topology

| Item | Frozen C7 target |
| --- | --- |
| Environment identity | `production` on host `mini87` |
| Runtime root | `/Users/mini87/Documents/projects/qdarte-runtime/docker/local-production` |
| Durable root | `/Volumes/Server87` |
| Existing domain database | `qdarteapi`, PostgreSQL 18/PostGIS, existing local-production cluster |
| New package database | `qdarte_contact_verify`, separate database on that same cluster |
| Package queue/type | `qdarte_contact_verify` / `qdarte.contact_verify.scope` only |
| Package facade | one separate lifespan-owned ASGI process, never mounted in the normal QDarte app |
| Package worker | one separate closed process, HTTP transport only, fixed queue/type |
| Public exposure | none; package facade binds host loopback only and is not tunnelled or proxied publicly |
| Initial application mode | `legacy`; package database/service disabled by default |

The package database is separate so taskq ownership, migrations, runtime
grants, queue history, restore, and future retirement remain auditable without
placing package base tables inside the QDarte domain database. It uses the
existing PostgreSQL cluster rather than introducing another database server.
This means cluster-wide connection and role budgets must include both
databases.

The proposed service path is:

1. normal QDarte API receives the retained cutover request;
2. in effective `package`, its same-process direct-drain controller authorizes
   one call to the loopback package facade;
3. the package facade reserves/finishes one durable admission in
   `qdarte_contact_verify`;
4. the closed worker claims only `qdarte.contact_verify.scope` over HTTP;
5. its trusted reporter calls the private result path on the package facade;
6. that path verifies the current package attempt and applies the one stable
   QDarte effect through a separately capped domain session; and
7. the taskq runtime alone settles the job.

The ordinary QDarte app receives no package database password. The worker
receives no database password or enqueue credential. The facade receives no
direct-queue mutation authority.

## 3. Source convergence before deployment

The accepted implementation is isolated and must not be deployed as a stale
whole-tree replacement. Remote refs sampled during C7-00 on 2026-07-22 were:

| Repository | Accepted C6 tip | Current remote facts |
| --- | --- | --- |
| QDarte API | `7a744582b0d824a559aa29dfaf03ef1081058064` | `origin/staging` `9364dd0d9b74cfba7d1f0dfaaf2582977e786d55` is an ancestor; C6 tip is 30 commits ahead. `origin/main` is `5e25ab695b6c8f7c5bf92c649c0f78413553e467` and diverges 30/57 from C6. |
| QDarte workers | `21bd880d5f2688f04cf323326512e6b630073d70` | `origin/staging` `02ea8fe124883955f238d1b1c824e3728ebf130c` is an ancestor; C6 tip is 14 commits ahead. `origin/main` is `f7427cb7ffd759eb7d2a0ec7d00a1dd830b23497` and diverges 14/45 from C6. |
| QDarte runtime | no C6 production service yet | `origin/staging` `0921e46112cfa9c9dd06bac2367ec90ac11c24a5`; `origin/main` `a6117c6e22a855ce1d1f57ed059be0eeda7b15fa`; the refs diverge 48/3. |

C7-01 must first read the live `/health` build identity and the three checkouts
on `mini87`. It must name the actually deployed branch and commit; the words
“main”, “staging”, or “production” are not evidence.

Then it must construct reviewed integration candidates from the deployed
line. Each C6 commit receives an explicit disposition: already present,
forward-ported with a source/behavior oracle, or superseded by named current
code. Commit-message similarity is not evidence. The candidate must retain all
deployed-line changes and reproduce the complete C6 focused suites. Directly
deploying the isolated pilot branch is forbidden.

The runtime candidate adds the two new dedicated services and backup coverage
without changing unrelated worker-fleet, intake, media, or public-site
topology. C7-01 stops if any source path is unclassified or if a candidate
would require a force push or discard deployed work.

## 4. Identity and credential ownership

All durable credentials are created on `mini87`, stored only in the
environment mechanism already used by local production with owner-only file
permissions, and excluded from build arguments, image history, Git, command
arguments, and evidence logs.

| Identity | May hold | Must not hold |
| --- | --- | --- |
| PostgreSQL cluster owner/admin | database creation, packaged migration and `verify()` execution | application or worker runtime use |
| `qdarte_contact_operator` login | `taskq_operator` for explicit queue/profile administration only | producer, runner, normal application pool, worker use |
| `qdarte_contact_facade` login | producer, runner, observer, housekeeper capability memberships in `qdarte_contact_verify` | operator, owner, superuser, CREATEROLE, CREATEDB, BYPASSRLS, QDarte-domain access |
| `qdarte_contact_domain_runtime` login | only the OutLabs authorization reads and QDarte contact-effect reads/writes proven necessary by the private reporter path | package database, direct queue mutation, schema ownership, broad schema grants, role/database administration |
| QDarte API service principal | enqueue on `qdarte_contact_verify` only | run, operator, another queue, raw SQL |
| closed worker service principal | run on `qdarte_contact_verify` only | enqueue, operator, another queue, database password |
| audit observer principal | read on `qdarte_contact_verify` only | enqueue, run, operator, raw base-table access |

Owner/admin and operator actions are separate one-off commands with named
credentials. No migration, queue provisioning, or IAM apply may silently fall
back to a runtime login. C7-01 must execute negative vectors for role switching,
operator functions, base-table reads, cross-queue actions, and administrative
attributes under every runtime identity.

The current contact harness is development-only. Production enablement must be
an explicit source change with all of the following construction guards:

- exact environment `production` and matching durable database identity;
- a dedicated `QDARTE_TASKQ_CONTACT_ALLOW_PRODUCTION=true` acknowledgement;
- exact database name `qdarte_contact_verify`;
- non-superuser taskq and domain logins;
- one expected ASGI process;
- loopback-only package URL/bind;
- admission capability and SQL contract 0.1.5 verified at startup; and
- the C6 direct-drain proof repeated on every process start.

Absent or contradictory input fails startup. There is no permissive default.

## 5. Connection arithmetic

The package worker is HTTP-only and adds **zero** PostgreSQL connections. The
single package-facade process is capped as follows:

| Pool | Per-process cap |
| --- | ---: |
| taskq request pool (`qdarte_contact_verify`) | 1 |
| taskq operator pool | 0 |
| taskq housekeeper pool | 0 (housekeeper disabled for this service) |
| taskq listener connections | 0 (poll-only) |
| embedded worker pool | 0 |
| separately constructed QDarte domain/auth pool | 2, `max_overflow=0` |
| **Total incremental cluster capacity** | **3** |

The present global QDarte engine uses an uncapped default pool and therefore
cannot be reused unchanged in the production contact facade. C7-01 must add a
contact-specific domain/auth session dependency capped at 2 with zero
overflow; inability to cap it is a stop condition.

C7-01 measures PostgreSQL `max_connections` as **M** and the normal-production
non-C7 peak connection count over a declared observation window as **H**. It
reserves 20 further connections for administration, recovery, and variance.
The gate is:

```text
H + 3 <= M - 20
```

The taskq runtime is configured with ceiling `M` and effective reserve
`H + 2 + 20`; its one taskq connection must fit the remainder. Both the raw
measurement and the runtime snapshot are evidence. A point-in-time idle count
alone is insufficient. Any database proxy/pooler is classified before use;
unclassified transaction semantics force poll-only direct PostgreSQL.

## 6. Backup, restore, and durability

The 2026-07-20 production backup copied into local development remains useful
historical evidence: `qdarteapi.dump` is 187,323,048 bytes and has SHA-256
`7cb30b9a4eb5c7e1c3ab39841d534a643312784a1b4220817a02ea83269132d4`.
It is not a C7 backup: it predates C7, and the imported copy does not include
the manifest-listed `globals.sql`.

Before any C7 source or database deployment, C7-01 must:

1. create a fresh `qdarteapi` custom-format dump, `globals.sql`, manifest, and
   checksums from local production;
2. verify both the local backup and its copies under the mounted Server87
   backup roots;
3. restore `qdarteapi` into a disposable database and run the content,
   migration-head, auth, direct-contact-ledger, and stable-effect checks;
4. create only a disposable package database, run immutable taskq migrations
   0001–0007 and `verify()` twice, dump it, restore it under another disposable
   name, and run `verify()` again; and
5. drop only the two named disposable restore databases after recording that
   the live databases were unchanged.

Before C7-02, the production backup runner must atomically cover both
`qdarteapi` and `qdarte_contact_verify` plus globals in one timestamped
manifest. A successful test restore of both is mandatory. Package rows are
durable business history; a backup job that protects only `qdarteapi` is not
enough.

## 7. Direct-lane and effect baseline

Immediately before any lasting package database is created, C7-01 captures a
read-only production baseline for direct `contact_verify_scope` jobs,
attempts, and events. The primary oracle is the canonical in-database
`jsonb_agg(to_jsonb(row) order by primary_key)` SHA-256 for every persisted
column, plus counts, status counts, maximum ids/timestamps, active jobs, and
running leases as diagnostics.

The same capture covers:

- `qdarte_ops.contact_verify_result_applications`;
- `qdarte_places.place_contact_methods`; and
- `qdarte_ops.discovery_usage_counters`.

The baseline records the exact production database identity and source tips.
Any active direct contact job or running lease blocks the direct-drain proof.
Any direct insert after package admission begins stops the cohort and returns
the application to `draining`; it never triggers a package-to-direct replay.

## 8. Bounded cohort and independent counters

C7-02 is limited to one predeclared `scope_kind=place` / exact place UUID that
the provider-free planner proves yields exactly one entity. Zero or more than
one planned entity stops the cohort. The idempotency key is recorded before
the first request and reused byte-for-byte for the created/existed pair.

Evidence must include:

- retained caller response: `created` then `existed`, same job id and receipt;
- authorized canonical taskq read to one terminal result;
- raw package job/attempt/event/admission rows;
- unchanged direct-ledger digest and zero direct insert;
- exactly one stable QDarte effect application;
- the contact-method before/after row;
- the usage-counter delta; and
- a separate host-controlled egress-proxy access ledger for the closed
  worker's external verification traffic.

The worker's existing explicit proxy seam is used with a dedicated C7 proxy
identity. The counter logs bounded timestamp, worker/exercise label,
destination host, disposition, and count—never a phone number, credential,
response body, or full URL. C7-01 first proves the proxy is fail-closed and
that bypassing it is impossible for the cohort process. An internal usage row
alone is not represented as an independent provider counter.

## 9. C7 execution order

### C7-01 — preflight only

1. verify live source/deployment identity and build integration candidates;
2. rerun API, worker, runtime, taskq, package, and artifact gates;
3. create and test-restore the fresh pre-change backup;
4. measure connection budget and construct the capped domain pool;
5. prove owner/operator/runtime/service-principal boundaries in disposable
   databases;
6. add the disabled package facade/worker topology and backup support;
7. migrate and verify the lasting package database twice under the owner;
8. provision IAM and queue under the operator, with the queue initially
   paused; and
9. deploy code/config with contact mode still `legacy`, package worker stopped,
   and prove health plus zero package publish.

C7-01 performs no provider call and enqueues no lasting package job. It stops
for targeted acceptance before C7-02.

### C7-02 — one bounded cohort

After separate acceptance, capture the direct/effect baseline; enter package
mode through the same-process drain; prove the package queue/service posture;
unpause only after the direct producer is disabled; submit the one keyed pair;
run only the closed worker; reconcile every counter; then stop the worker and
record one of the frozen rollback postures. Any ambiguity stops automatic
processing.

### C7-03 and C7-AUDIT

Only after cohort acceptance: two normal deployment cycles, a zero-insert
direct window, backup/restore continuity, and rollback rehearsal. The final
audit may open only a separate direct-retirement specification.

## 10. C7-01 stop conditions

C7-01 must not start, or must stop immediately, if any of the following is
true:

1. live deployed commit/branch or database identity is unknown;
2. an isolated pilot tree would replace or discard deployed-line work;
3. a source-forward-port path is unclassified or its focused gates are red;
4. the taskq or domain runtime login is superuser/admin, can become operator,
   or can read a forbidden base table;
5. the domain/auth pool cannot be capped at 2 with zero overflow;
6. `H + 3 > M - 20` or the connection observation is not representative;
7. a fresh complete backup or two-database restore drill is missing;
8. the package database backup is not part of the recurring production job;
9. the direct contact baseline is active, changes during drain, or cannot be
   recomputed exactly;
10. the package facade is public, the ordinary app mounts its routes, or the
    worker receives a database/enqueue credential;
11. the egress counter can be bypassed or would log sensitive data;
12. the bounded planner does not yield exactly one declared entity;
13. any fallback, dual publication, row copy, manual queue DML, or direct
    recreation is proposed; or
14. production state would change before the owning task and review explicitly
    authorize it.

## 11. Acceptance and scope opened

A targeted review must derive the actual Mini87 topology and source graph,
recompute the connection formula, challenge the backup/restore design,
privilege boundaries, direct baseline, counter independence, cohort bound, and
rollback sequencing, and confirm every unknown is assigned to a fail-closed
C7-01 measurement.

READY opens only C7-01 preflight under this sequence. It does not authorize
C7-02, a provider call, package cohort, direct retirement, non-contact work,
or Stage 6.
