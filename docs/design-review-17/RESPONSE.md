# Round 17 response — QDarte C7-01 production preflight

## Verdict

**READY.** C7-01's disabled Mini87 production preflight is accepted. The
already-frozen C7-02 one-country/one-place cohort may be planned and executed
as its own bounded slice. This verdict does not itself enqueue a job, start the
closed worker or egress gateway, authorize a provider request, retire the
direct lane, expand to another lane, or open Stage 6.

## Provenance

This is an owner-authorized **internal, non-independent** review performed by
the implementation session because the usual separate reviewer was
unavailable. I treated the request and evidence packet as claims to falsify,
re-read the authority and source, re-ran live read-only/backup checks, and did
not use the packet as an oracle. That provenance is weaker than an external
review and is stated rather than disguised.

Final evidence tips inspected:

- outlabs-taskq `ff5b6813d0e8870f0e11f78b15d2de31a7791365`;
- QDarte API `380c33c92b853badbc5adda352744d14ac4a88f5`;
- QDarte workers `c8c03bbd369e5af09951183dcd5ab979a6c4fc55`;
- QDarte runtime `76b8e513403b81301e55767a70ba0b1a906075bd`.

The deployed code/image identities remain the request's pinned `65fbd22`,
`c8c03bb`, `36bfe69`, and image
`sha256:bb734fa568d44657b4edf7dd249f0a4c7e0019c44c0bc819bbdcfac8dbd89f88`.
Later host commits are typing or evidence documentation only.

## Attack-program dispositions

### 1. Source convergence and artifact identity — PASS

All three candidate branches derive directly from their stated `origin/main`
tips and are pushed and clean. Their final one-sided ranges are 39 API commits,
18 worker commits, and 17 runtime commits. The C6 package boundary is present
in source rather than represented by commit-message similarity. The local
compressed artifact recomputed to
`9fa8b04434e2d92ddabe685f99cfdb6df377eaa09a35a2c50c1d1cfb83cccfb5`,
and the loaded production image recomputed to the pinned `bb734f...` ID. Image
history contains no credential assignment or credential-bearing DSN. The API
lock uses immutable taskq a6 at SHA-256 `a731a6...5419`.

### 2. Incumbent privilege conversion — PASS

The production manifest, not a sample grant list, derives the API and worker
roles. Live equality verification returns `{"manifest_version":1,"ok":true}`.
Both logins are non-superuser, non-owner, non-operator, non-CREATEROLE,
non-CREATEDB, and non-BYPASSRLS. `PUBLIC CONNECT` and cross-database access are
revoked; future owner defaults are included in the equality oracle. API
startup no longer migrates, and runtime containers have no owner/migration or
backup secret, Docker socket, backup mount, or broad secret-bearing projects
mount. Worker desired state is secret-free; its DSN/token enter only through
the controller-owned mode-0600 file.

The restored-production exercise used the exact logins for real API boot,
ordinary worker/database families, backup/restore, and negative admin paths.
The retained owner DSN exists only in the operations rollback store.

### 3. Exact contact identities — PASS

The domain role has named grants only on the six contracted contact/auth
tables, with exact per-table privileges and no schema-wide table/default,
sequence, or function grant. The package controller equality-checks facade and
operator attributes, capability memberships, both database ACL directions,
base-table denial, operator-function denial, and schema-creation denial. Its
live verification returns `ok=true`, `ensure=unchanged`, `pause=unchanged`.
Signed principals remain queue-scoped: enqueue and run are separate and cannot
authorize the opposite action or another queue.

### 4. Auth lifecycle and continuity — PASS

Source explicitly initializes OutLabsAuth before domain readiness and shuts it
down before disposing the engine. This was a real defect found by the restored
clone and corrected before deployment. Live human login and `/iam/users/me`
both return 200. The service-token path passes. A representative domain write
was exercised only in a transaction deliberately rolled back. The real
ordinary worker binary, using the restricted worker credential, obtained
health 200 and claim 200, reported `No claimable jobs`, and left the active
legacy count at zero.

### 5. Database chain and disabled state — PASS

The host is at Alembic `20260721_0076`. The lasting package database contains
immutable migrations 0001–0007, reports contract 0.1.5, and passes repeated
`verify()`. Its active capabilities are exactly the admission reservation and
ready read-model capabilities expected from migration 0007. Queue creation
and pause ran through the operator identity. Current raw equality is:

```text
jobs=0, attempts=0, events=0, admissions=0, paused=true
```

No runtime identity can administer that state.

### 6. Topology and secret boundary — PASS

The default graph does not start C7 services. The explicit profile starts only
the unpublished facade at the exact private origin; the main API does not own
its routes. Production mode is still `legacy`. The contact worker and egress
gateway are absent as containers. Source fixes the future worker to one
queue/type on an internal-only network with no DB/enqueue credential or host
port; the gateway is the only dual-homed verifier path. API and facade
environment-key checks show owner/run/egress secrets absent in the required
directions.

### 7. Connection arithmetic — PASS

The recorded observation is 180 samples over 15 minutes, `M=100`, and non-C7
peak `H=16`. Source caps package taskq/domain pools at 1+2 with zero overflow,
and the worker is HTTP-only. Therefore `H+3=19 <= M-20=80`, leaving 61
connections of headroom. The corroborating disabled sample contains exactly
one API, one contact-domain, and one facade session; it was not substituted for
the observation window.

### 8. Durability — PASS

Backup `20260722-185358` contains API, package, Intake, globals, and relative
checksums. All archive checks pass, external copies exist, and the restore
drill reproduced API counts/head, taskq contract 0.1.5 with seven migrations
and zero rows, and Intake counts/head before dropping only its three named
temporary databases.

The addendum closes the sharper scheduler question. The repository installer
persisted the package flags in the mode-0600 wrapper and registered the 03:15
LaunchAgent. Executing that exact installed wrapper at `20260722-191547`
created the package-inclusive manifest, copied both backup sets externally,
uploaded five API/package and four Intake files, and exited zero. Fresh
checksums all pass.

### 9. Conservation — PASS

The direct lane remains five completed and one canceled job, with zero active
rows and running leases. I recomputed the six canonical full-row digests; they
match the packet, including zero result applications and the exact direct
job/attempt/event plus contact-method/usage tables. The package database is
empty. Source contains no active-row import, package-to-direct fallback, row
copy, or dual-publish path, and no manual queue DML occurred.

### 10. Readiness and failure behavior — PASS

Facade startup binds exact contract/capability metadata and both package and
domain storage. Domain readiness executes a real bounded permission query.
Wrong identities, unavailable DBs, absent capabilities, and missing
authorization initialization fail startup or health. The health endpoint is
not a static environment echo.

The broad API run remains honestly red at 1,553 passed, 155 environment skips,
and eight failures. I inspected the failures: order-dependent global route
registration, absent optional integration tables/tokens/media roots, and a
staging packet fixture. None executes a C7 contact path, and the same classes
predate this range. The C7 boundary suite is 34/34 and specifically covers
disabled mounting, production guards, domain lifecycle/readiness, token
separation, and cutover configuration. The broad baseline is debt, not a C7
safety failure.

### 11. Gates and resource honesty — PASS

Reproduced results:

- taskq: 505 passed, 1 opt-in skip; Ruff and format clean;
- runtime: 1,144 passed; Ruff clean; MyPy 194 source files;
- workers: 627 passed; Ruff clean; MyPy 53 source files;
- API C7 boundary: 34 passed; focused Ruff/format/MyPy clean;
- API full Ruff and MyPy over 193 app source files: clean.

Mini87 has about 12 GiB system-disk headroom and substantial reclaimable image
state. The image was built locally and transferred compressed; no remote build
or unapproved prune occurred. The 8011 OrbStack host forward still resets, but
the operational 8061/8067 paths and the private C7 service origin are healthy;
C7 does not depend on 8011.

### 12. Scope — PASS

No package admission/job, provider request, result application, package worker
or egress start, direct retirement, non-contact lane, or Stage-6 action
occurred. The queue remains paused and the application remains in `legacy`.

## Findings

### LOW R17-01 — one inherited host commit lacks the trailer

Worker commit `33db1c2855d063da06fbc08920964d51474d3999` is a test
typing/formatting cleanup without the normal co-author trailer. Every other
commit in the three reviewed ranges carries it, and every taskq board commit
does. Rewriting the published/deployed descendant chain solely for metadata
would create more provenance risk than recording this exception. Future
commits remain trailer-gated.

### LOW R17-02 — broad API baseline is not a release-green suite

The eight failures are unrelated to C7, but the host repository should not
represent the broad suite as green until its environment/order contract is
repaired. C7 acceptance relies on its focused source gates plus live proofs,
not a false full-suite claim.

### LOW R17-03 — Mini87 storage pressure remains operational debt

No preflight step filled the disk, and C7-02 needs no image rebuild. Still,
system headroom is only about 12 GiB. Any later build or broad deployment must
measure first; pruning remains a separate owner-approved operation.

## Contract questions

None. The SQL/wire contracts, ADRs, C6 design, and C7 plan are mutually
consistent with the implementation and live disabled state.

## Scope opened

READY opens only a separately tracked C7-02 execution of the frozen one-country
scope, one exact place, `limit=1` cohort: capture fresh baselines; enter the
same-process package selector; start the already-built egress gateway and
closed worker; unpause only after the direct producer is disabled; reuse one
recorded idempotency key for created/existed; reconcile raw package, direct,
effect, usage, and independent egress counters; then stop and record rollback.

This response authorizes no broader cohort, no second place, no non-contact
lane, no direct retirement, and no Stage 6.
