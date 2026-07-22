# Round 19 response — QDarte C7 production contact lane

## Verdict

**READY.** C7-01 through C7-03 satisfy the frozen production-evidence gate.
The contact lane may proceed only to a separately written and accepted
direct-retirement specification.

This response is owner-authorized, internal, and **not independent**. The same
implementation session executed C7-03 and performed this completion review
because the usual separate reviewer remains unavailable. I regenerated Git,
artifact, live database, topology, backup, restore, and test evidence rather
than treating the implementation packet as independent corroboration.

READY does not retire the direct producer or consumer, unpause or enqueue the
package lane, migrate another lane, broaden the worker, or open Stage 6.

## Evidence regenerated

The C7-03 packet at QDarte API `78d5ce5` hashes to exactly
`a85a015a988e9845c3d33d5addc6664f214c1439954554c9d3c3e44f87ed35f5`.
The API, worker, runtime, and taskq evidence/request tips are committed, pushed,
and clean; every new commit carries the required trailer. The Mini execution
checkouts remain on their source-bearing pre-evidence tips because the later API
and runtime commits are docs-only. This is source identity preservation, not
deployment drift.

Fresh live state reports:

- the supported host Caddy route returns health 200, build
  `45c05cb8c017582736796a5f03f32f88f959f0cd`, environment `production`, and
  database instance `45677dd9-2717-4d80-bdf7-a09a94a95221`;
- API mode `draining`, package queue paused, and the private facade healthy
  with no published host port;
- contact worker and gateway absent;
- PostgreSQL `max_connections=100` with 12 sessions at review time; the frozen
  peak formula remains `16 + 3 = 19 <= 80`;
- every API/worker/facade/domain/operator login is non-superuser with no role,
  database, or RLS administration; only the facade holds the four runtime
  taskq capabilities and only the contact operator holds `taskq_operator`;
- one package job remains `succeeded / 3 attempts / 2 failures / 0 releases`.

The full-row conservation oracles regenerate exactly:

| Relation | Rows | SHA-256 |
| --- | ---: | --- |
| direct contact jobs | 6 | `820b8bf1590a57ca4bc8d0699f3af0c47c41347b8266cdd20a314422cfb64d5f` |
| direct contact attempts | 6 | `6800267d1ace9a027568e4e99f79e02b5a71ba7b4b9884c2be8528ec5a9c6f39` |
| direct contact events | 2,264 | `979e95d238969c48fcdc91c71e519e4c5478a1234b520a390a207b5472ebfe77` |
| package jobs | 1 | `27ecd8cb86b6e7878a253850fa42a7c003a0dff629916ab25dfe38c8954e727d` |
| package attempts | 3 | `20245aef403e25bc8fec86190526148ae196daacd9def7539560ccc40b75d62a` |
| package events | 7 | `a73cd129c3c2e9bc0f80785a1412e91820059774be26bed0d3451076bd0efecf` |
| package admissions | 1 | `4f26495a582f69ebbf8fe619505c751de452a8de8ecb794c5e64cd2863f7571b` |
| stable applications | 1 | `9ac85fe57675acdbaed16cae0261960a697609bc1d73090fde2d3c8b0a5265a3` |
| place contact methods | 484 | `a3e51670782d98a27b8f401a027a2e60d45ae9aa285de0512e7e8054146f98ab` |
| usage counters | 12 | `5bd62c1cd10b83153bd9efade9fc56b1b29eaf082c83298637754f9fc3af2d46` |

Direct status remains five completed, one canceled, and zero running attempts.

Fresh gates in this session are taskq 505 passed with one opt-in skip under the
authenticated Redis shape, workers 628, runtime 1,144, and the expanded API
contact boundary 60. Taskq and workers are format-clean; relevant Ruff and
MyPy scopes pass for all four repositories. Runtime retains its already
recorded unrelated repository-wide formatter drift and this slice changes no
runtime Python file.

## Attack-program dispositions

1. **C7 sequence and source identity — PASS.** Git reproduces the frozen order,
   local/remote tips, source-bearing Mini tips, deployed build/images, and
   evidence hashes. C7-03 added only evidence/status/request documents; no
   unclassified source path or image entered production.
2. **Privileges, topology, and budget — PASS.** Live roles, memberships,
   private facade ports/networks, absent worker/gateway, owner/control
   separation, and the three-connection increment match C7-01. Current session
   count 12 remains below the accepted measured peak 16; no package route is
   publicly mounted.
3. **C7-02 retained truth — PASS.** The one admitted job, one provider access,
   one application/method/usage unit, and raw `3/2/0` history remain exact. The
   evidence retains both private-gateway failures and does not call this a
   first-attempt success.
4. **Two normal cycles — PASS.** Containers `445af5f...` and `577eaee...` are
   distinct replacements on the same immutable API image/build/database. Each
   was healthy in `package`, with the queue paused and worker/gateway absent.
   Every single-service command recorded explicit `--no-deps`.
5. **Zero-insert and durable conservation — PASS.** All ten direct, package,
   and domain counts/hashes above equal both ends of the window. There is no
   insertion, update, deletion, cross-backend replay, second effect, or
   unexplained counter.
6. **Recurring backup — PASS WITH LOW R19-01.** The installed host wrapper made
   timestamp `20260722-203544`, atomically included API/contact/globals and the
   matching Intake/globals set, copied both to Server87, uploaded five plus four
   objects, and passed every local/external checksum. Retention removed only
   the named expired `20260621-192213` sets.
7. **Restore and globals — PASS.** The supported drill restored and validated
   all three databases and removed them. Two additional `--network none`
   PostgreSQL 18 containers loaded the actual globals, recovered all 12 named
   roles, restored API/contact and Intake, preserved `taskq_owner` ownership,
   and were removed with no remaining restore database/container/volume.
8. **Structured egress residue — PASS.** The mode-0600 artifact hashes to
   `f061b8d...`, contains exactly one bounded counter, and was generated by the
   final image under `--network none` with an injected no-I/O verifier. It is
   correctly labelled an artifact proof, not a second provider call.
9. **Rollback and final posture — PASS.** The mode-only `package -> draining`
   replacement used `--no-deps`, no queue DML, no direct recreation, and no
   cross-backend fallback. The supported Caddy host route and internal health
   are 200; the known direct OrbStack `8011` host reset is already documented
   and host workers use the Caddy route. The queue remains paused and all
   history/effects are preserved.
10. **Failure honesty — PASS WITH LOW R19-02.** The syntax-only artifact command
    failed before import; the first restore invocation exited at its Docker
    guard; and the first isolated API/contact attempt returned nonzero with an
    empty globals error log. Exact cleanup removed their named artifacts, no
    production database changed, and the successful replacements exercised
    stronger real paths. The last attempt is not stated in the C7-03 packet and
    is preserved here as a finding rather than rewritten away.
11. **Gates and hygiene — PASS.** Counts, lint/type/format scopes, trailers,
    local/remote identity, clean trees, and hashes reproduce. The range changes
    no Tier-0 contract, ADR, SQL, migration, prior Tier-4 file, or package
    source.
12. **Scope — PASS.** C7-03 created no job, provider request, domain effect,
    direct-lane mutation, retirement, other-lane work, broad worker, or Stage-6
    change.

## Findings

### LOW — R19-01: the reinstalled LaunchAgent has not reached its next schedule

The exact installed wrapper completed the fresh C7-03 backup, and the external
roots contain the prior daily series, but `launchctl` reports zero runs since
the wrapper was reinstalled earlier on 2026-07-22. The next direct-retirement
specification owns a non-blocking eligibility row to record the next scheduled
03:15 run before destructive retirement implementation. This does not weaken
the wrapper, backup, object-store, checksum, or restore proof executed here.

### LOW — R19-02: one cleaned-up isolated restore attempt is absent from the packet

The first network-isolated API/contact restore command returned nonzero without
an emitted milestone. Its trap removed the exact disposable container and
volume, its globals error file was empty, and later checks proved no restore
database or container remained. A fresh network-isolated run then loaded the
same globals and restored both databases successfully. The implementation
packet should have disclosed the first attempt; this immutable response now
does. No remediation or rerun is required.

No MEDIUM, HIGH, or BLOCKER finding remains.

## Contract questions

None. The two findings concern operational scheduling/evidence completeness,
not a conflict between Tier-0 authorities.

## Scope opened

READY opens only the design and review of a direct contact producer/consumer
retirement specification. That specification must preserve the shared legacy
ledger and unrelated lanes, bind R19-01 before destructive implementation, and
define its own zero-DML rollback. It may not infer authorization to retire,
delete history, migrate another lane, broaden the worker, or enter Stage 6.
