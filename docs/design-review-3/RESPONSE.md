# outlabs-taskq external design review — round 3 response

**Review date:** 2026-07-18  
**Reviewed revision:** `e79c222` (Stage-1 implementation through `e9761b3`)  
**Method:** independent authority-first source audit, live catalog inspection, rollback-only corruption probes, and local PostgreSQL 18.3 test rerun

## 1. Verdict

**BLOCKED. Stage 2 may not begin.**

The core SQL implementation is substantial and the reported ordinary suite is reproducible, but the Stage-1 exit claim is not yet contract-complete. Two Tier-0 contradictions require explicit contract adjudication, and three high-severity implementation/test defects remain:

- the verifier accepts missing functions, extra functions, missing critical indexes, incorrect capability grants, direct table DML grants, and LOGIN capability roles;
- a failed migration inside a caller-managed transaction leaks its session advisory lock after the caller rolls back;
- the migration trusts pre-existing cluster roles by name and grants capabilities without proving that they are the required `NOLOGIN` roles.

I reproduced **58 passed, 1 skipped** against PostgreSQL 18.3 and both Ruff checks are clean. The opt-in million-row gate was not rerun in this pass; its implementation and previously reported evidence were inspected. PostgreSQL 16 was not locally rerun. The hosting-service workflow has not yet supplied run evidence.

## 2. Findings

### R3-01 — HIGH — `verify()` is not a manifest verifier and returns permissive false positives

**Evidence**

- ADR-004 requires `verify()` to compare objects, signatures, ownership, privileges, and checksums: `docs/adr/ADR-004-migrations-canonical.md:11-16`.
- ADR-011 requires a per-function manifest with signature, return shape, owner, security mode, path, volatility, PUBLIC state, exact grants, capability, command, SQLSTATEs, time budget, replay rule, and tests, and says `verify()` compares the live catalog against it: `docs/adr/ADR-011-housekeeper-role-credentials.md:16`.
- The minimum verify contract also names required tables/views, function identities, critical indexes, direct-DML denial, and manifest ACLs: `docs/taskq-borrowed-features/13-sql-packaging-conventions.md:82-90`.
- The implementation runs only five checks—schema existence, ledger rows/checksums, hardening of functions that happen to exist, PUBLIC EXECUTE, and role-name existence: `src/taskq/sql/__init__.py:452-467`.
- `_FUNCTIONS_SQL` has no expected catalog to compare against and fetches neither results, argument names/defaults, volatility/parallel attributes, nor exact non-PUBLIC ACLs: `src/taskq/sql/__init__.py:515-529`.
- `_check_roles()` checks names only: `src/taskq/sql/__init__.py:577-588`.
- T2 corrupts owner, path, PUBLIC, ledger checksum, and role existence, but not signatures, exact grants, table privileges, required tables/views/types/indexes, volatility, return shapes, defaults, role attributes, or extra surface: `tests/test_t2_contract.py:95-241`.

**Falsifiable counterexamples reproduced in rollback-only transactions**

Each of the following independently returned `verify().ok == True` with no failed checks:

1. `DROP INDEX taskq.jobs_claim_idx`.
2. `REVOKE EXECUTE ON FUNCTION taskq.enqueue(...) FROM taskq_producer`.
3. `DROP FUNCTION taskq.get_contract_meta()`.
4. `GRANT UPDATE ON taskq.jobs TO taskq_producer`.
5. `ALTER ROLE taskq_housekeeper LOGIN`.

An additional correctly hardened function would likewise be accepted because the verifier iterates the live set rather than comparing it with a closed expected set.

**Violated authority/invariant**

ADR-004 decision 2, ADR-011 decision 4, the Tier-0 manifest's closed-surface rule, and the minimum verify contract.

**Impact**

Startup or operator verification can report a corrupted or expanded schema as compatible. Missing commands can reach runtime; wrong grants can remove required capability or confer direct mutation authority; a hidden added definer function can remain undetected. The present corruption matrix proves only five narrow axes, not the claimed catalog parity.

**Smallest contract-correct remediation**

Create the machine-readable 0.1 manifest required by ADR-011, containing every expected object and per-function axis. Make `verify()` compare exact set equality and catalog identities, results, defaults, attributes, owner/path/security, exact ACLs, role attributes/memberships, required tables/views/types/constraints/index definitions, safe-view grants, base-table privilege denial, seed state, and external foreign keys. Keep every probe read-only.

**Regression test**

A parameterized rollback-only corruption matrix must mutate every manifest axis one at a time—including missing and extra objects—and require a precise named failure, then prove restoration green. Include all five counterexamples above.

### R3-02 — HIGH — failed caller-managed migrations leak the session advisory lock

**Evidence**

- `_migrate_impl()` always acquires a session lock: `src/taskq/sql/__init__.py:320-323`.
- When the caller owns the transaction, migration failure is re-raised without rollback: `src/taskq/sql/__init__.py:327-337`, correctly avoiding theft of caller transaction ownership.
- The `finally` block then attempts `pg_advisory_unlock` inside the already-aborted transaction and silently discards that failure: `src/taskq/sql/__init__.py:340-348`.
- The only installer test is already-applied idempotency; no failing caller-managed migration or concurrent installer recovery is exercised: `tests/test_t2_contract.py:63-82`.

**Falsifiable counterexample reproduced**

I called `_migrate_impl()` with a deliberately failing migration inside an explicit SQLAlchemy transaction, caught the error, rolled back the caller transaction, and queried `pg_locks` on the same still-open connection. One granted session advisory lock remained. It disappeared only after an explicit `pg_advisory_unlock_all()`/connection close.

**Violated authority/invariant**

ADR-004's advisory-locked migration semantics and the review requirement for failure recovery; the runner's own stated promise that the session lock is always released in `finally`, `src/taskq/sql/__init__.py:36-38`.

**Impact**

A pooled Alembic/host connection that survives the failed transaction can block every later installer indefinitely. The comment that session close is a backstop is insufficient for a returned pooled connection.

**Smallest contract-correct remediation**

Use transaction-scoped advisory locking for caller-managed transaction mode, while retaining a deliberately managed session lock only for the runner-owned multi-transaction path. Do not attempt to repair the caller's aborted transaction internally.

**Regression test**

Inject a failing second statement under both sync and async caller-managed paths; after caller rollback, assert no advisory lock remains and a second connection can immediately acquire the migration lock and recover deterministically.

### R3-03 — HIGH — pre-existing cluster roles are trusted by name, not established as capability roles

**Evidence**

- ADR-010 and ADR-011 require six package capability roles and explicitly define them as `NOLOGIN`: `docs/adr/ADR-010-db-roles-security-definer-maintenance.md:12-27`, `docs/adr/ADR-011-housekeeper-role-credentials.md:12-16`.
- Migration 0001 creates a role only when its name is absent, then immediately grants schema/function capabilities; it neither normalizes nor rejects an existing role with `LOGIN`, elevated attributes, or unsafe memberships: `src/taskq/sql/migrations/0001_initial.sql:124-166`.
- `verify()` checks role names only and accepted `ALTER ROLE taskq_housekeeper LOGIN` in a rollback-only probe: `src/taskq/sql/__init__.py:577-588`.

**Violated authority/invariant**

ADR-010's capability model and ADR-011's deployment credential separation.

**Impact**

On a shared cluster or repeat deployment, a pre-created login role with a reserved name silently receives taskq capabilities. If the pre-existing role is privileged, the installed role model is not the six-role model the verifier reports.

**Smallest contract-correct remediation**

Before any grant, validate every reserved role's `rolcanlogin` and prohibited elevated attributes/memberships; either normalize only the attributes the contract authorizes or fail atomically with a precise operator action. Extend verification to the same role manifest. Do not silently strip privileges that might belong to another installation.

**Regression test**

On an isolated cluster, pre-create each reserved name in turn as LOGIN and as an elevated/member role, run a clean install, and assert atomic refusal with no taskq grants. Also prove a conforming pre-existing six-role set installs idempotently.

### R3-04 — HIGH — the executable contract coverage claimed for Stage 1 is incomplete

**Evidence**

- The normative harness says T2 tests every documented function behavior and T8 covers installation, concurrency, interruption, compatibility, and catalog corruption: `docs/Task Queue Test & Benchmark Harness.md:18-27`.
- T2 has 15 tests, with behavior cases concentrated on enqueue, claim states, one complete/fail replay, release budget, pending-cancel fail, and the followup gate: `tests/test_t2_contract.py:244-484`.
- There is no direct contract-behavior test for `enqueue_many`, `cancel_running_job`, `worker_heartbeat`, any observer projection/metrics function, most operator functions, janitor retention/error isolation, or exact seed state. Many are touched only incidentally or not at all.
- The promised privilege/shadow suite contains three negative privilege calls and no shadow-object attempt: `tests/test_t2_contract.py:245-275`.
- The migration CI job runs only `TestMigrateAndVerify`, not a T8 suite: `.github/workflows/ci.yml:149-175`.
- No clean concurrent installers, failing/partial migration, sync adapter, CLI exit behavior, unknown/extra catalog object, or compatibility-window test exists.
- T4 is a real independent state machine for a useful subset, but it generates only successful current-fence operations; it does not generate heartbeat, stale/replayed/cross-verb attempts, targeted claims, bulk enqueue, worker cancel, or malformed public inputs: `tests/test_t4_model.py:102-318`.

**Violated authority/invariant**

Harness T2/T4/T8 obligations and the Stage-1 exit statement that all §16.3 gate cases are green.

**Impact**

The green count materially overstates protocol and migration coverage. The null-bound counterexamples in Contract question CQ-01 and verifier failures in R3-01 passed because the named contract rows have no tests.

**Smallest contract-correct remediation**

Add a manifest-driven parity suite that supplies at least identity/hardening/privilege/error-vector coverage for every function, plus direct normative behavior cases for every public function. Add the missing T8 runner matrix. Expand T4 with stale/replay/error operations only where the locked contract already defines them.

**Regression test**

Make the test collection fail if any manifest function, registered SQLSTATE, capability grant, replay rule, or required migration scenario lacks a declared executable vector.

### R3-05 — MEDIUM — CI import isolation does not test the built distribution

**Evidence**

- The normative job calls for installing the distribution without extras and importing the declared core modules: `docs/Task Queue Test & Benchmark Harness.md:66-72`.
- CI uses `uv sync` in the source checkout and imports `taskq`/`taskq.sql`; it neither builds a wheel/sdist nor installs the produced artifact: `.github/workflows/ci.yml:32-67`.
- No CI job runs `uv build` or inspects wheel contents. `pyproject.toml:62-73` declares both `src/taskq` and `bench`, but configuration alone does not prove artifact contents.

**Violated authority/invariant**

The packaging/import-isolation gate in the harness and review request.

**Impact**

Editable/source-tree success can hide missing SQL migration data, entry points, or package modules in published artifacts.

**Smallest contract-correct remediation**

Build wheel and sdist in CI, install each into a clean environment with no source checkout on `sys.path`, run core/HTTP isolation imports, invoke both entry points, and confirm the packaged migration checksum/catalog.

**Regression test**

An artifact smoke job that installs the actual files from `dist/`, discovers `0001_initial.sql`, runs a clean migration/verify, and executes `taskq --help` and `taskq-bench --help`.

### R3-06 — MEDIUM — benchmark isolation is weaker than the normative method and B4 can leave accepted work unsettled

**Evidence**

- The harness requires the database to be dropped/recreated between scenarios and records the method as such: `docs/Task Queue Test & Benchmark Harness.md:94-100`.
- The runner truncates taskq state and records that weaker reset in JSON: `bench/runner.py:519-521`, `bench/runner.py:570-576`.
- B4 stops producers and workers together and reports throughput from the settled counter without draining accepted rows: `bench/runner.py:431-489`.

**Violated authority/invariant**

Harness benchmark method rules and B4's accepted/settled workload accounting.

**Impact**

Prior bloat/statistics can bleed into later scenarios, and B4 throughput is partly a shutdown-race measurement: accepted rows remaining queued are not reconciled or reported as a conservation result. The artifacts remain useful smoke evidence but not yet reproducible full development evidence.

**Smallest contract-correct remediation**

Use a fresh database per full scenario, or explicitly change the normative method through the docs process if database recreation is not required. Stop producers first, drain workers to a bounded terminal condition, and report accepted, terminal, remaining active, and conservation equality.

**Regression test**

Assert a full B4 run ends with `accepted = terminal + explicitly_reported_active`, no running attempts, and a reset fingerprint proving no prior scenario state/bloat was inherited.

### R3-07 — LOW — plan evidence is structurally useful but does not execute the exact hot-path functions under `EXPLAIN`

**Evidence**

- The plan gate seeds a realistic million-row mixed table and legitimately runs `VACUUM (ANALYZE)` to establish statistics and visibility: `tests/test_plans.py:46-118`.
- It recursively rejects a `jobs` sequential scan and requires named indexes: `tests/test_plans.py:23-43`.
- Claim, dedup, reap, and stats assertions explain hand-copied representative subqueries; only `refresh_stats_snapshot()` is later executed without plan inspection: `tests/test_plans.py:128-205`.

**Violated authority/invariant**

No direct contract violation; this is an evidence limitation against the review request's demand that named hot paths themselves remain index-backed.

**Impact**

A later function-body edit can diverge from the copied query while the plan gate stays green.

**Smallest contract-correct remediation**

Keep the structural subquery checks, but add a drift guard tying each representative query to the function definition or use supported nested-statement plan capture in an isolated plan test.

**Regression test**

Deliberately alter a hot-path function in a rollback-only transaction to a full scan and prove the plan gate detects it.

## 3. Contract questions

### CQ-01 — Tier-0 bounds and closed SQLSTATE promises conflict with the executable bodies for explicit `NULL`

The Tier-0 Function Manifest says:

- `claim_jobs` validates batch 1–50 (`docs/Task Queue 0.1 Function Manifest.md`, runner table in §3);
- `release_job` has delay 0–86400 and raises `TQ422` (`§3`, exact body);
- `redrive_failed` is bounded to 1–500 (`§5`, exact body and prose);
- the manifest enumerates public raises, while Transport Protocol H-06 defines a closed registered-error boundary.

The exact Tier-0 `release_job` body uses `IF p_delay_seconds < 0 OR ...`, and the exact `redrive_failed` body uses `IF p_limit NOT BETWEEN 1 AND 500`; SQL three-valued logic lets explicit `NULL` bypass both checks. `LIMIT NULL` is unbounded.

Live rollback-only counterexamples:

- `claim_jobs(queue, worker, NULL)` returned `empty` rather than `TQ422`;
- after creating two failed rows, `redrive_failed(queue, NULL, actor)` returned `redriven=2`, rather than rejecting the unbounded request;
- `release_job(..., p_delay_seconds => NULL)` raised native `23502`, not registered `TQ422`.

This is a contradiction inside the locked 0.1 contract, not permission to choose an implementation interpretation. Resolve by Tier-0 erratum/new ADR and version discipline before changing SQL.

### CQ-02 — the Tier-0 2KB stored-error ceiling conflicts with normative bodies that store unbounded reason text

Transport Protocol H-09 freezes `stored error <=2KB` (`docs/Task Queue Transport Protocol v1.md:23-24`). The Function Manifest delegates `cancel_job` to Unified Spec §5.9, whose normative body assigns `jobs.error = COALESCE(p_reason, ...)` without bounding it (`docs/Task Queue — Unified Design Spec.md:1357-1362`). The manifest's `snooze_job` reference body assigns `job_attempts.error = p_reason` without bounding it (`docs/Task Queue — Unified Design Spec.md:1250-1260`, incorporated by the manifest §3 pointer).

Live rollback-only probes stored 10,000 bytes in both `jobs.error` through `cancel_job` and `job_attempts.error` through `snooze_job`. Other bodies use `left(..., 2000)`, which is character-counted rather than byte-counted and can also exceed 2KB for multibyte input.

The contract must decide whether the ceiling covers both job and attempt error columns and whether KB is a byte limit; the current Tier-0 limit and adopted normative bodies cannot both be satisfied as written. Do not silently select truncation semantics in implementation.

No other contract questions were found.

## 4. Manifest coverage matrix

### Catalog result

The installed PostgreSQL 18.3 catalog contains the claimed **11 tables, 3 composites, and 39 functions**. All 39 live functions were independently inspected: every one is owned by `taskq_owner`, is `SECURITY DEFINER`, has `search_path = pg_catalog, taskq, pg_temp`, and lacks PUBLIC EXECUTE. Application ACLs match the family grants visible in migration 0001. The expected public families are producer 2, runner 8, observer 5, operator 13, and housekeeper 2; nine helpers are owner-only, including the batch reaper described beneath the manifest's helper table.

This is a point-in-time manual result, not evidence that `verify()` enforces it; R3-01 proves it does not.

Legend: **D** = direct behavior test; **I** = incidental/model/benchmark exercise; **G** = catalog-wide hardening check only; **Gap** = no meaningful behavior vector found.

| Function | Identity/result and contract role | Live hardening/grant | Executable evidence | Disposition |
|---|---|---|---|---|
| `uuid7()` | `uuid`, owner helper, volatile/parallel-safe | correct, owner-only | IDs exercised incidentally | I/G |
| `backoff_seconds(text,int,int,int)` | `int`, owner helper | correct, owner-only | retry/reap paths | I/G |
| `emit_event(uuid,uuid,text,text,text,jsonb)` | `void`, owner helper | correct, owner-only | lifecycle rows incidentally | I/G |
| `has_capability(text)` | `boolean`, owner helper, stable | correct, owner-only | followup gate | I/G |
| `reap_job(uuid)` | `boolean`, one reclaim authority | correct, owner-only | T3 targeted expire; T4 expiry | D |
| `reap_expired(int=100)` | `int`, bounded helper | correct, owner-only | claim/tick indirectly | I/G |
| `finalize_cancel_stragglers(int)` | `int`, owner helper | correct, owner-only | stress/tick only | I |
| `claim_janitor_due()` | `boolean`, owner helper | correct, owner-only | no asserted due-state behavior | Gap |
| `refresh_stats_snapshot()` | `void`, owner helper | correct, owner-only | plan test executes it | D (plan shape copied) |
| `enqueue(...)` | table `(job_id,created)`, producer | correct, producer only | T2/T3/T4/stress/bench | D |
| `enqueue_many(text,jsonb)` | ordered table result, producer | correct, producer only | B2 smoke | I; contract matrix gap |
| `claim_jobs(...)` | `claim_batch`, runner | correct, runner only | T2/T3/T4/stress/bench | D; NULL gap/CQ-01 |
| `heartbeat(...)` | typed table result, runner | correct, runner only | T3 fence race | D, narrow |
| `complete_job(...)` | `settle_result`, runner | correct, runner only | T2/T3/T4/stress/bench | D |
| `fail_job(...)` | `settle_result`, runner | correct, runner only | T2/T4/stress/bench | D |
| `snooze_job(...)` | `settle_result`, runner | correct, runner only | T4/stress | I; CQ-02 |
| `release_job(...)` | `settle_result`, runner | correct, runner only | T2/T4/stress | D; NULL gap/CQ-01 |
| `cancel_running_job(...)` | `settle_result`, runner | correct, runner only | stress only | I; replay/error gap |
| `worker_heartbeat(...)` | shutdown table result, runner | correct, runner only | none found | Gap |
| `get_authorization_projection(uuid)` | four-field table, observer/stable | correct, observer only | none found | Gap |
| `get_job(...)` | frozen safe projection, observer/stable | correct, observer only | none found | Gap |
| `get_queue_stats(text=NULL)` | snapshot table, observer/stable | correct, observer only | none found | Gap |
| `get_contract_meta()` | version/capabilities, observer/stable | correct, observer only | verifier probe only | Gap |
| `metrics()` | name/labels/value, observer/stable | correct, observer only | none found | Gap |
| `cancel_job(uuid,text,text=NULL)` | typed result table, operator | correct, operator only | T2/T4/stress | D; CQ-02 |
| `redrive_job(uuid,text,bool=false)` | boolean/TQ409, operator | correct, operator only | T4 | D, narrow |
| `expire_job(uuid,text)` | typed text, operator | correct, operator only | T3 | D |
| `expire_worker_leases(text,text)` | matched/reaped/skipped JSON, operator | correct, operator only | none found | Gap |
| `ensure_queue(text,jsonb,text)` | canonical profile table, operator | correct, operator only | fixture use only | I; validation/result gap |
| `pause_queue(text,text,text=NULL)` | typed text, operator | correct, operator only | T2/T3 | D |
| `resume_queue(text,text)` | typed text, operator | correct, operator only | fixture cleanup/incidental | I |
| `set_concurrency_limit(text,int,text)` | typed text, operator | correct, operator only | T3 cap | D |
| `request_worker_shutdown(text,text,text)` | matched count, operator | correct, operator only | none found | Gap |
| `purge_queued(text,int,text,text=NULL)` | bounded count, operator | correct, operator only | none found | Gap |
| `run_now(uuid,text)` | typed text, operator | correct, operator only | none found | Gap |
| `reprioritize(uuid,smallint,text)` | typed text, operator | correct, operator only | none found | Gap |
| `redrive_failed(text,int,text)` | bounded result table, operator | correct, operator only | no suite vector | Gap; CQ-01 |
| `janitor()` | JSON, housekeeper+operator | correct, exact dual grant | invoked by due tick without retention assertions | I/Gap |
| `tick(int=200)` | JSON, housekeeper+operator | correct, exact dual grant | T3/T4/stress | D, lifecycle subset |

### Other contract-visible catalog

| Axis | Result | Evidence/gap |
|---|---|---|
| Six capability roles | Present and currently `NOLOGIN`/non-elevated | migration trusts pre-existing names; verifier checks only existence (R3-03) |
| 11 tables | Present with expected owners | no exact verifier or exhaustive constraint test (R3-01/R3-04) |
| 3 composites | Live shapes match migration | no exact verifier; no additive-shape regression vector |
| Critical indexes | Present, including claim, active idempotency, running-attempt uniqueness | million-row structural test is good; verifier accepts dropped indexes |
| Views | `queue_stats`, `dead_jobs`, `worker_status` present and observer-selectable | no projection/privilege behavior tests; verifier ignores views |
| Triggers | none expected or installed for 0.1 | consistent |
| Base-table DML | current capability roles lack DML | sampled live and producer negative test; verifier accepted an injected DML grant |
| Schema/PUBLIC | schema PUBLIC privileges revoked; capability USAGE present | current catalog correct; exact schema ACL not verified |
| Seeds | `tick`, `janitor_daily`, `stats_snapshot`; contract version `0.1`; active capabilities empty | current catalog consistent; no exact verify/test gate |
| Deferred surface | no deferred functions/schedule/archive objects observed | verifier would accept extra hardened functions |
| Registered SQLSTATEs | intended TQ codes appear in public bodies | no manifest-driven closed-set/vector test; CQ-01 demonstrates native `23502` |
| Fencing/replay | attempt token and verb ledger implemented; T2/T3 cover key complete/fail race | other settle verbs/reaper replay not exhaustively vectored |
| Authoritative lookup | job-targeted SQL uses row lookup; observer projection has four fields | facade auth is later-stage; projection itself untested |
| Dedup/budget/caps | partial unique indexes and core bodies agree; T3/T4 evidence is meaningful | malformed/boundary and bulk behavior coverage incomplete |

## 5. Exit-gate assessment

| Gate | Assessment |
|---|---|
| PostgreSQL 18 | **Reproduced:** 58 passed, 1 opt-in skipped on 18.3; Ruff check/format clean |
| PostgreSQL 16 | **Configured and historical evidence only:** Stage-1 record says 54/54 on 16.14; not rerun here; permanent CI matrix includes 16 |
| Privileges | **Current catalog mostly correct, gate not proven:** basic negative tests pass, but no shadow suite and verifier accepts wrong grants/direct DML/LOGIN roles |
| Verifier corruption | **Fail:** R3-01 falsifiable false positives |
| Migration recovery | **Fail:** R3-02 lock leak; no concurrency/failure/sync/CLI T8 matrix |
| Concurrency | **Strong but partial:** T3 barriers and held transactions force the named races; waits are observed, not guessed. Stress is seed-replayable and checks conservation/attempts. Coverage does not replace missing function vectors |
| Stateful model | **Strong subset:** genuine Hypothesis state machine with durable invariants and scratch-only owner helper; operation/replay/error space remains narrower than harness claim |
| Million-row plans | **Pass with evidence limitation:** realistic mixed seed, legitimate visibility/statistics stabilization, recursive no-`jobs`-seq-scan checks, named indexes; copied-query drift risk R3-07 |
| Benchmarks | **Smoke only, as honestly labeled:** B1–B4 exist with rich JSON; reset/conservation issue R3-06 prevents stronger evidence claim |
| Packaging | **Not gated on built artifacts:** R3-05 |
| CI | **Configuration is coherent but incomplete:** 3.12/3.13, PG16/18, races/model, migration subset, benchmark smoke. No hosting-service run evidence was supplied; accidental skips are mostly avoided by DSN, but T8 and artifact gates are absent |

## 6. Residual risks and required Stage-2 preconditions

Stage 2 should open only after all of the following are true:

1. Resolve CQ-01 and CQ-02 through the contract process; record exact adopted semantics before SQL changes.
2. Land R3-01's machine-readable manifest and exact read-only verifier, with the full corruption matrix green.
3. Fix R3-02 and prove sync/async, runner-owned/caller-owned, concurrent, failure, and recovery semantics.
4. Harden or atomically reject pre-existing reserved roles per R3-03.
5. Close R3-04's public-function and T8 coverage gaps, making collection-to-manifest completeness executable.
6. Add built-artifact packaging gates; correct benchmark reset/conservation before treating full runs as development evidence.
7. Re-run the complete corrected suite on PostgreSQL 16 and 18, including all non-opt-in Stage-1 contract/migration tests, and retain hosting-service CI evidence.

The remaining risk is not that the principal queue state machine is superficial—it is not. The risk is that the project currently has a strong implementation guarded by a verifier and coverage story materially weaker than the locked contracts say, while two locked bodies disagree with their own boundary rules. Those are precisely the defects a Stage-boundary external review should stop.
