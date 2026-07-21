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

The bridge retains the reserved read-model command identities as **DEFERRED**
responders. They answer typed `TQ501`, are absent from OpenAPI, and have no
generated client method; they do not expose a read-model success path on either
database state. The full generated facade cannot be the first deployed success
artifact: on a 0.1.2 database its new observer functions do not exist. Serving
them as active routes before migration would turn a valid request into an
implementation failure. This specification therefore separates the runtime
bridge from route exposure.

## 3. Two-artifact release and migration sequence

Every artifact is a new immutable wheel/sdist release with an exact URL and
SHA-256 pin in the host lock. No local path, branch, editable install, range, or
reused release tag is allowed.

| Step | Artifact/database posture | Permitted effect | Required proof |
|---|---|---|---|
| A | Publish **bridge** release `0.1.0a3` from audited source `40aa9b5` plus an isolated package-version release commit. That source supports `{0.1.2,0.1.3,0.1.4}` and keeps H-08/H-11 **DEFERRED**. | Package release only. | Wheel/sdist, optional-extra, closed-set acceptance/rejection, and exact deferred-responder/OpenAPI/client proof. |
| B | Repin and deploy the first host to exact `a3` while the database remains 0.1.2. | A normal host deployment; no database DML. | Health, existing tools-worker service, canonical existing-job read, `/meta` reporting 0.1.2, and H-08/H-11 reserved paths returning TQ501 while remaining OpenAPI-hidden/client-absent. This is the new rollback baseline. |
| C | Run `taskq migrate` then `taskq verify` directly under the PostgreSQL owner/admin identity using the exact `a3` artifact. Apply only immutable `0004` and `0005`; never run manual metadata DML. | The separately authorized contract migration to 0.1.4, with ready still inactive. | A current backup test-restored once to a disposable target before migration; migration ledger checksums; contract metadata exactly 0.1.4 with capabilities exactly `{"active":[]}`; `verify()` twice; existing tools queue/profile unchanged; and runtime health under `a3`. |
| D | Publish full read-model release `0.1.0a4` from accepted source `1610b5a` plus an isolated package-version release commit. Before the `a4` host deployment, use that exact artifact under the PostgreSQL owner/admin identity to apply immutable `0006` and `verify()` twice. Then repin/deploy the host to exact `a4`. | The metadata-only ready activation and generated observer-route exposure form one gated step. | Wheel/sdist plus source/OpenAPI proof that only generated observer GET routes are newly exposed; 0006 checksum; contract metadata 0.1.4 with capabilities exactly `{"active":["read_model_list_ready"]}`; no host-owned read route or operator transport. |
| E | Run the read-only production acceptance vectors in §6. | Read-only requests only. | Authorized profile/ready reads and all negative authorization/capability/envelope checks. |

The owner/admin credential is used only for C and D. The app runtime credential remains
non-superuser, has no operator membership, cannot `SET ROLE taskq_operator`, and never executes
migration or profile-update functions. The existing separate operator credential is not used by
this slice.

## 4. Rollback floor and stop rules

Before C, the pre-existing `a2` deployment remains a valid zero-DML rollback.
After C, it is permanently below the database's rollback floor and must not be
booted. The only valid application rollback is the deployed `a3` bridge
artifact: it supports 0.1.4 while its reserved read-model paths remain typed
TQ501. The old Stage-4 rollback tag is retained as historical evidence but is
not a post-C boot target.

The host must rehearse both boundaries:

1. **Pre-C:** an `a3` → `a2` deployment rollback while metadata remains 0.1.2;
   no migration or queue/job DML.
2. **Post-D:** an `a4` → `a3` deployment rollback while metadata remains 0.1.4
   with ready-active metadata; existing tools processing and health recover,
   `ready` returns the bridge's typed TQ501 responder, and no database mutation
   occurs.

Stop rather than improvise if the owner cannot reproduce the migration ledger,
the app cannot start under the declared set, `verify()` differs from the
phase-specific 0.1.4 capability posture, a required artifact hash differs, a
route would be exposed before D, a legacy tools row appears, or a request would
require an operator/runtime privilege broadening. A new SQL contract or metadata
change requires a new ADR/migration path; no setting may activate or deactivate
a view.

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
  metadata value; its reserved read-model paths answer TQ501, are absent from
  OpenAPI, and have no generated client method;
- `a4` starts on 0.1.4 after D; its facade OpenAPI exposes the generated GET
  paths but no operator/profile-write surface in the host; and
- a fresh/full local 0001→0006 proof on PostgreSQL 16.14 and 18.x remains
  green before any production action.

### B. Production migration evidence

- a successful current backup/checkpoint is test-restored to a disposable
  target once before C. This proves that backup artifact only; it does not
  claim general restore/PITR completion, which remains independently owned;
- C's ledger contains immutable 0004/0005 checksums, owner/grant/catalog
  verification passes twice, and metadata is exactly 0.1.4 with
  `{"active":[]}`;
- D's ledger adds immutable 0006; the `a4` owner/grant/catalog verification
  passes twice with metadata exactly 0.1.4 and
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
