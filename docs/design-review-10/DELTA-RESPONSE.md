# External targeted review — first-host read-model adoption — Delta response

> **Reviewed:** the Round-10 remediation range `8b2b0e0..3af5559` and the
> amended Stage-5 host-adoption specification. The response is docs-only and
> left uncommitted as required.

## Verdict

**READY — the first-host read-model A→E sequence may begin.** All four
Round-10 preconditions are closed by the narrow documentation delta. The
two-artifact sequence is now physically executable: no artifact is asked to
apply or verify a migration/capability state it does not ship.

READY authorizes only the separately frozen A→E sequence, with every listed
release, migration, restore, deployment, and read-only acceptance proof still
required at its own step. It does not authorize `running` or `finished`, UI
work, retirement, side-effecting lanes, or Stage 5.

## Independent identities and scope

- Round-10 response SHA-256 recomputed as
  `285e134320715a4d67aaafa7079bb6f78a9787267d8777d6188ec48558ac44c6`.
- `8b2b0e0` is an ancestor of `3af5559`. The delta contains one trailered
  commit, `3af5559`, and changes only the Tier-3 adoption specification plus
  its same-commit board row.
- The current user-owned ADR-018/UI documentation commit is outside this delta
  and was not adjudicated. No contract, ADR, migration, source, artifact,
  host, database, IAM, deployment, or production state changed.

## Finding dispositions

### R10-01 — ACCEPTED

The former a3/0006 mismatch is removed.

- Bridge source `40aa9b5` has the closed runtime set
  `{0.1.2,0.1.3,0.1.4}`, contains immutable migrations 0001–0005, lacks 0006,
  and declares exact metadata seeds `0.1.4` plus `{"active":[]}`.
- Step C now requires exact a3 to apply/verify **only 0004 and 0005**, ending
  at that exact inactive-ready posture; verification is required twice.
- Full source `1610b5a` contains immutable 0006 and declares exact metadata
  seeds `0.1.4` plus `{"active":["read_model_list_ready"]}`.
- Step D pins a4 to `1610b5a` plus only an isolated release-version commit,
  requires that artifact to apply/verify 0006 twice, then permits its host
  deployment. This pairs activation with the artifact that can serve the
  resulting capability.

The owner/admin versus runtime separation remains unchanged: only C and D use
the owner/admin identity; the host runtime has no operator privilege.

### R10-04 — ACCEPTED

The bridge description is now exact rather than claiming nonexistence. At
`40aa9b5`, `GET_QUEUE` and `LIST_JOBS` are `HttpSurface.DEFERRED`; the facade
rejects non-active surfaces with typed capability failure and excludes deferred
commands from OpenAPI. The official clients reject deferred generic commands
and do not generate their methods.

Steps A/B now require proof of exactly that posture: TQ501 responders,
OpenAPI absence, and no client method. The post-D a4→a3 rollback is explicitly
zero-DML; against ready-active 0.1.4 metadata the bridge returns its typed
TQ501 response rather than serving an unverified success route.

### R10-02 — ACCEPTED

The plan now names a4 source `1610b5a` and requires the same isolated
package-version release discipline, immutable URL, SHA-256, and host lock pin
as a3. No floating revision, local path, range, branch, or reused release
identity is allowed.

### R10-03 — ACCEPTED

Before C, the current backup/checkpoint must be test-restored once to a
disposable target. The amended text accurately limits the conclusion to that
backup artifact and retains restore/PITR as an independently owned broader
durability gate. It is a Step-C gate, not a false claim that recovery is
otherwise complete.

## Contract questions

**None.** The fix is deployment sequencing and evidence discipline only. The
Protocol, Manifest, ADR-019/020/021, and immutable migrations remain unchanged.

## Evidence and limits

Reviewed with raw Git identity/ancestry/path checks; direct source inspection
of the a2/a3/a4 runtime, packaged migrations, metadata seeds, deferred facade
behavior, OpenAPI registration, and generated-client exclusion; and the
repository formatter/linter posture from the documentation range. No package
was built or published, no host or database was touched, and no production
operation occurred—appropriate limits for this docs-only delta check. Earlier
dual-major migration and B9 evidence remains standing context; the A→E sequence
requires fresh artifact and operational proof before any production action.
