# taskq — Stage 5 first-host read-model adoption specification

> **Status:** Tier-3 proposal — 2026-07-21. This document is a release and
> deployment design only. It is subordinate to the [Transport Protocol v1](./Task%20Queue%20Transport%20Protocol%20v1.md),
> [Function Manifest 0.1.4](./Task%20Queue%200.1%20Function%20Manifest.md),
> ADR-019, ADR-020, ADR-021, and the Stage-4 host specification. It changes no
> host source, package release, database, IAM, queue profile, deployment, or
> production configuration until its targeted review is accepted.

## 1. Purpose and narrow boundary

The library has independently accepted the first bounded read-model surface:

- `GET /taskq/v1/queues/{queue}` — the observer-safe, 13-field profile; and
- `GET /taskq/v1/jobs?queue={queue}&view=ready` — the finite queue-scoped
  ready page.

The first host may adopt those **existing mounted facade routes** for its
existing `tools` queue. It must not copy their models, paths, SQL queries, or
authorization into host-owned routes.

This slice does not activate `running` or `finished`, add a user interface,
allow profile PUT, change tool producers/workers, modify the legacy-retirement
observation, alter queue/IAM policy, add a global/all-queue browser, or migrate
any side-effecting lane. The stage-4 L1 observation remains independent.

## 2. Verified starting state

The authoritative first host currently pins immutable `outlabs-taskq` release
`0.1.0a2`. That artifact starts only on SQL contract `0.1.2`; its production
database is correspondingly at migrations `0001`–`0003`. The host already
mounts a lifespan-free `/taskq` facade with an observer/runner/producer/
housekeeper runtime login and **no** operator transport. Its `tools` queue is
already provisioned and its canonical queue-scoped read permission exists.

The accepted library tip supports the exact closed set
`{0.1.2, 0.1.3, 0.1.4}` and includes migrations `0004`–`0006`. Applying 0004
raises the database rollback floor: `0.1.0a2` must never be restarted against
that database. This is ADR-020's deliberate expand → migrate → contract rule,
not an operational exception.

The full generated facade cannot be the first bridge artifact: on a 0.1.2
database its new observer functions do not exist. Serving those routes before
migration would turn a valid request into an implementation failure. This
specification therefore separates the runtime bridge from route exposure.

## 3. Two-artifact release and migration sequence

Every artifact is a new immutable wheel/sdist release with an exact URL and
SHA-256 pin in the host lock. No local path, branch, editable install, range, or
reused release tag is allowed.

| Step | Artifact/database posture | Permitted effect | Required proof |
|---|---|---|---|
| A | Publish **bridge** release `0.1.0a3` from audited source `40aa9b5` plus an isolated package-version release commit. That source supports `{0.1.2,0.1.3,0.1.4}` but predates the H-08/H-11 facade/client addition and exposes no read-model route. | Package release only. | Wheel/sdist, optional-extra, closed-set acceptance/rejection, and no read-model route/OpenAPI assertion. |
| B | Repin and deploy the first host to exact `a3` while the database remains 0.1.2. | A normal host deployment; no database DML. | Health, existing tools-worker service, canonical existing-job read, `/meta` reporting 0.1.2, and no H-08/H-11 route success path. This is the new rollback baseline. |
| C | Run `taskq migrate` then `taskq verify` directly under the PostgreSQL owner/admin identity using the exact `a3` artifact. Apply only immutable `0004`, `0005`, and `0006`; never run manual metadata DML. | The separately authorized production schema migration. | Pre/post backup checkpoint, migration ledger checksums, contract metadata exactly 0.1.4, capabilities exactly `{"active":["read_model_list_ready"]}`, `verify()` twice, existing tools queue/profile unchanged, and runtime health under `a3`. |
| D | Publish full read-model release `0.1.0a4` from the independently accepted library tip, then repin/deploy the host to exact `a4`. | Route exposure only after C. | Wheel/sdist plus source and OpenAPI proof that only the generated observer routes are newly exposed; no host-owned read route or operator transport. |
| E | Run the read-only production acceptance vectors in §6. | Read-only requests only. | Authorized profile/ready reads and all negative authorization/capability/envelope checks. |

The owner/admin credential is used only for C. The app runtime credential remains non-superuser,
has no operator membership, cannot `SET ROLE taskq_operator`, and never executes migration or
profile-update functions. The existing separate operator credential is not used by this slice.

## 4. Rollback floor and stop rules

Before C, the pre-existing `a2` deployment remains a valid zero-DML rollback.
After C, it is permanently below the database's rollback floor and must not be
booted. The only valid application rollback is the deployed `a3` bridge
artifact: it supports 0.1.4 while intentionally exposing no read-model route.
The old Stage-4 rollback tag is retained as historical evidence but is not a
post-C boot target.

The host must rehearse both boundaries:

1. **Pre-C:** an `a3` → `a2` deployment rollback while metadata remains 0.1.2;
   no migration or queue/job DML.
2. **Post-C:** an `a4` → `a3` deployment rollback while metadata remains 0.1.4;
   existing tools processing and health recover, `ready` is no longer exposed,
   and no database mutation occurs.

Stop rather than improvise if the owner cannot reproduce the migration ledger,
the app cannot start under the declared set, `verify()` differs from the exact
0.1.4/ready-only posture, a required artifact hash differs, a route would be
exposed before C, a legacy tools row appears, or a request would require an
operator/runtime privilege broadening. A new SQL contract or metadata change
requires a new ADR/migration path; no setting may activate or deactivate a
view.

## 5. First-host surface and authorization

The only new successful production requests are:

```text
GET /taskq/v1/queues/tools
GET /taskq/v1/jobs?queue=tools&view=ready&limit=1..100&cursor=optional
```

They remain generated facade endpoints under `/taskq`, authenticated by the
existing OutLabs adapter and authorized with `read(tools)`. A queue-scoped
credential may inspect only `tools`; a global read credential may still issue
only this one-queue command. No host route may turn this into a multi-queue
list, add a filter, rewrite the cursor, or query `taskq` tables directly.

The host has no operator transport. Therefore `PUT /taskq/v1/queues/tools` is
not newly enabled, and no profile update, bootstrap, queue creation, or IAM
reconciliation is part of this adoption. `running` and `finished` remain typed
`TQ501` with only `reason=read_model_view_inactive` and the requested `view`.

The profile result is exactly the Protocol's flat 13-field observer projection
plus its ETag. The page result is exactly the Protocol's bounded 13-field item
projection, `as_of`, and opaque `next_cursor`. Payloads, headers, fences,
worker/attempt identity, errors, results, progress, events, queue names in
items, and arbitrary metadata remain absent.

## 6. Acceptance evidence

The implementation/adoption packet must provide all of the following without
creating a production job solely for visibility testing:

### A. Release and compatibility evidence

- exact source commit, signed/tagged release identity, wheel and sdist hashes,
  host `pyproject.toml`/lock pins, and artifact-isolation proof for `a3` and
  `a4`;
- `a3` starts on a representative 0.1.2 database and rejects an unsupported
  metadata value; it has no read-model route or generated client method;
- `a4` starts on 0.1.4 after C; its facade OpenAPI exposes the generated GET
  paths but no operator/profile-write surface in the host; and
- a fresh/full local 0001→0006 proof on PostgreSQL 16.14 and 18.x remains
  green before any production action.

### B. Production migration evidence

- a successful current backup/checkpoint is recorded before C; restore/PITR is
  still an independently owned durability gate and is not falsely claimed here;
- migration ledger contains the immutable 0004/0005/0006 checksums, owner/
  grant/catalog verification passes twice, and no manual table or metadata DML
  occurred;
- `get_contract_meta()` returns exactly 0.1.4 with
  `{"active":["read_model_list_ready"]}`; `running` and `finished` are absent;
- the existing tools queue profile is unchanged before/after C, and the host's
  existing tools canary/worker health evidence remains successful; and
- the post-C `a4` → `a3` rollback rehearsal is a zero-DML application switch.

### C. Read-only facade evidence

- an authenticated `taskq_tools:read` principal receives 200 for the `tools`
  profile with exactly the allowed fields and a grammar-correct ETag;
- the same principal receives 200 for `ready`, whether the finite page is empty
  or contains already-existing production work; all returned items match the
  fixed projection and expose no sensitive field;
- pagination is proven on a disposable/local fixture, not by injecting
  production jobs; production proves a single read only;
- a wrong-queue credential follows the established hiding behavior, a
  malformed cursor/request ID cannot bypass authentication/authorization, an
  unknown authorized queue is TQ001, and `running`/`finished` are typed TQ501
  with only their safe details; and
- API and worker health, connection budget, legacy high-water oracle, and
  external tool-invocation counters show no new producer/consumer action.

## 7. Explicit non-goals and next gates

This does not complete L1/L2 retirement, the deferred credential cleanup,
restore/PITR testing, the side-effecting hard-kill drill, an operator UI, a
QDarte pilot, or any broader release claim. It does not make `ready` a host
dashboard or a general reporting API.

Targeted independent review of this specification is required before A. A
separate acceptance after E is required before treating the first-host
read-model adoption as complete. Each later host, production migration, view
activation, or UI use is its own bounded decision.
