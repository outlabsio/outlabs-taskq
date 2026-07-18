# SQL correctness and role × deployment audit

This document proposes the complete least-privilege matrix requested in Round 2. It is an amendment to ADR-010 because the existing five-role model cannot express the already-required facade housekeeper flow without granting operator authority to the web process.

## 1. Proposed database role model

All package-created capability roles should be `NOLOGIN`; deployment logins receive membership. This prevents a role name from becoming a credential by accident.

| Role | Capability | Explicitly cannot |
|---|---|---|
| `taskq_owner` | Own schema/objects; execute internal helpers; migration target via `SET ROLE` | Login; serve application traffic |
| `taskq_producer` | Queue/profile lookup needed by enqueue; single/bulk enqueue | Claim, settle, operate, read arbitrary jobs, bypass depth |
| `taskq_runner` | Claim, heartbeat, presence, fenced settle/cancel; optional fenced worker event append | Pause, redrive, expire another worker, run janitor, alter profiles |
| `taskq_observer` | Safe projections/functions/views and metrics only | See attempt fences; mutate; select base tables |
| `taskq_housekeeper` **(new)** | `tick`, 0.1 due-gated janitor, 0.2 schedule coordination | Operator controls, queue/profile changes, direct DML, external REINDEX |
| `taskq_operator` | Queue/job/fleet control; queue profiles/admin; manual transactional janitor/tick | Base-table DML; own DDL; run external index maintenance without the version-appropriate separate maintenance credential |

The new role is justified by verified deployment flow, not hypothetical future flexibility: Unified Spec §11.4 requires the facade process to tick (lines 1615–1618), Diverse's current service calls `taskq.tick()` (`~/Documents/projects/diverse-data-api/src/diverse_data_api/domains/queue/taskq_service.py`, lines 308–311), and ADR-010 gives its usual login no operator membership (lines 14–22).

### Role invariants

- No application capability role has INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/CREATE on taskq base objects.
- Observer gets SELECT only on named safe views where appropriate; never blanket `SELECT ON ALL TABLES`.
- Internal helpers get no EXECUTE grant to application roles. Nested calls succeed because the hardened outer SECURITY DEFINER function runs as owner.
- Every function has owner `taskq_owner`, pinned `search_path`, fully qualified references, PUBLIC EXECUTE revoked in its creation transaction, and a manifest-tested ACL.
- HTTP IAM `admin` is not a PostgreSQL role. It selects an operator/admin route, which executes through an optional operator DB pool after facade authorization.

## 2. Function → capability role → deployment credential

This matrix enumerates every function named by the current design plus the safe public wrappers required to remove raw-table flows. “Owner-only” means no application-role EXECUTE. A function not present in the activated release migration does not exist; it is not a success-returning stub.

### 2.1 Internal helpers

| Function | Release | EXECUTE | Called by | Decision |
|---|---:|---|---|---|
| `taskq.uuid7()` | 0.1 | owner-only | defaults/enqueue/claim | Locality/id generation only; never part of deadlock proof |
| `taskq.backoff_seconds(...)` | 0.1 | owner-only | fail/reaper | One fixed 15% jitter in 0.1 unless a real stamped column is added |
| `taskq.emit_event(...)` | 0.1 | owner-only | lifecycle/operator functions | Add event-ledger NOTIFY hint when SSE work lands; callers do not forge actor/type |
| `taskq.reap_job(...)` | 0.1 | owner-only | reaper/expire functions | One target-aware reclaim authority; accept expected id/worker/fence where needed |
| `taskq.cancel_dependents(...)` | 0.2 | owner-only | fail/cancel/reaper | Bound traversal depth/work per call; tick converges skipped work |
| `taskq.finalize_cancel_stragglers(...)` | 0.1 | owner-only | tick | Exact bounded body required for migration 0001 |
| `taskq.finalize_dep_stragglers(...)` | 0.2 | owner-only | tick | Absent in 0.1 |
| `taskq.finalize_workflows(...)` | 0.2 | owner-only | tick/janitor | Absent in 0.1 |
| `taskq.refresh_stats_snapshot(...)` | 0.1 | owner-only | tick | Bounded/index-backed; snapshot carries `as_of` |
| `taskq._enqueue_followup(...)` | 0.2 | owner-only | complete | Holds the depth exemption; replaces public `p_internal` |
| `taskq.archive_jobs(...)`, prune/re-home helpers | 0.3 | owner-only | janitor | Select/aggregate/insert/delete ordering is atomic and bounded |

### 2.2 Producer and runner functions

| Function | Release | EXECUTE role | Deployment credential(s) | Required result/security note |
|---|---:|---|---|---|
| `taskq.enqueue(...)` | 0.1 | producer | host request login; facade runtime; direct producer | Typed `created|existed`; no `p_internal`; bounds/SQLSTATE validation |
| `taskq.enqueue_many(...)` | 0.1 | producer | same | Atomic, ordered one-result-per-input; one queue; no dependencies in 0.1 |
| `taskq.claim_jobs(...)` | 0.1 | runner | embedded/direct worker; facade runtime for HTTP workers | Typed queue state; attempt id only in result; queue missing distinct from paused |
| `taskq.heartbeat(...)` | 0.1 | runner | worker/facade runtime | Match job+attempt+worker label; validate lease/progress limits; typed lost |
| `taskq.complete_job(...)` | 0.1 | runner | worker/facade runtime | R2-01 ordering; follow-ups inactive until 0.2 |
| `taskq.fail_job(...)` | 0.1 | runner | worker/facade runtime | R2-03 cancel branch; validated retry-after; typed replay |
| `taskq.release_job(...)` | 0.1 | runner | worker/facade runtime | Exact body required; budget-free; pending cancel → cancelled |
| `taskq.snooze_job(...)` | 0.1 | runner | worker/facade runtime | Bound delay; budget-free; pending cancel → cancelled |
| `taskq.cancel_running_job(...)` | 0.1 | runner | worker/facade runtime | Exact fenced body required; matching worker; budget-free |
| `taskq.worker_heartbeat(...)` | 0.1 | runner | embedded/direct worker; facade runtime on behalf of HTTP worker | Advisory presence only; server separates principal actor from worker label |
| `taskq.append_worker_event(...)` (if retained) | 0.1+ | runner | worker/facade runtime | Must be attempt-fenced, closed event vocabulary, actor server-derived; never grant `emit_event` |
| `taskq.create_workflow(...)` | 0.2 | producer | producer/facade runtime | Global IAM `enqueue`; idempotent workflow key |

An embedded handler that explicitly enqueues unrelated jobs needs producer membership in addition to runner. Atomic settle follow-ups do not: `complete_job` calls the owner-only helper.

### 2.3 Observer functions and views

| Function/view | Release | Access | Credential(s) | Required projection rule |
|---|---:|---|---|---|
| `taskq.get_contract_meta()` / capability projection | 0.1 | observer EXECUTE | all runtime/read clients | No secrets; version/capabilities/limits |
| `taskq.get_authorization_projection(job_id)` | 0.1 | observer EXECUTE | facade runtime/operator pool | Only id, queue, job_type, status; no payload/fence |
| `taskq.get_job(job_id, include...)` | 0.1 | observer EXECUTE | facade/read clients | Bounded safe detail; include flags policy-checked in facade |
| `taskq.list_jobs(queue, filters, cursor, limit)` | minimal in 0.1 or gated later | observer EXECUTE | facade/operator | Queue required without global IAM; exact keyset/index |
| `taskq.get_queue_stats(queue)` / snapshot view | 0.1 | observer EXECUTE/SELECT safe view | facade/metrics/operator | Snapshot timestamp; queue filter applied before return |
| `taskq.metrics()` | 0.1 | observer EXECUTE | metrics exporter/facade | Targeted indexes/snapshot, no full hot-table aggregate |
| `taskq.worker_status`, safe worker read | 0.1 | observer safe view/function | facade/operator | No connection/token data |
| `taskq.dead_jobs` safe read | 0.1 | observer safe view/function | operator/facade | Queue-filterable; no attempt fence |
| `taskq.workflow_status` | 0.2 | observer safe view/function | facade/operator | Eventual rollup explicitly timestamped |
| archive/lineage projections | 0.3 | observer safe functions | operator/facade | Queue authorization and bounded partition/time filters |

Replace Unified Spec §11.5's raw `SELECT taskq.jobs/job_events/control_state` examples (lines 1624–1630) with these functions/views. Base-table SELECT is not required for ordinary 2am diagnosis. An emergency owner/superuser forensic session is a separate, audited break-glass path.

### 2.4 Operator and configuration functions

| Function | Release | EXECUTE | Deployment credential(s) | Outcome rule |
|---|---:|---|---|---|
| `taskq.ensure_queue(...)` / profile update | 0.1 | operator | bootstrap login; optional facade operator pool | Typed created/updated/unchanged; profile version recommended |
| `taskq.pause_queue(...)`, `resume_queue(...)` | 0.1 | operator | operator CLI/pool | Idempotent; actor server-derived over HTTP |
| `taskq.cancel_job(...)` | 0.1 | operator | operator CLI/pool | Immediate queued/blocked; request-only running |
| `taskq.redrive_job(...)`, `redrive_failed(...)` | 0.1 | operator | operator CLI/pool | Typed not-failed/collision; bounded bulk |
| `taskq.expire_job(...)` | 0.1 | operator | operator CLI/pool | Backdate+targeted reap same transaction |
| `taskq.expire_worker_leases(...)` | 0.1 | operator | operator CLI/pool | Captured ids; `{matched,reaped,skipped}` |
| `taskq.purge_queued(...)` | 0.1 | operator | operator CLI/pool | Bounded cancel, never delete |
| `taskq.run_now(...)`, `reprioritize(...)` | 0.1 | operator | operator CLI/pool | Queued/blocked only; typed conflict otherwise |
| `taskq.set_concurrency_limit(...)` | 0.1 | operator | bootstrap/operator CLI/pool | Auth action `admin`; 0 means resource pause |
| `taskq.request_worker_shutdown(...)` | 0.1 | operator | operator CLI/pool | Bounded selector semantics; actor audited |
| `taskq.tick(...)` | 0.1 | housekeeper **and operator** | runtime housekeeper; manual CLI | No public HTTP route; reaper first |
| `taskq.janitor(...)` | 0.1 | housekeeper **and operator** | due-gated tick; manual CLI | Transactional, bounded passes only |
| `taskq.cancel_workflow(...)` | 0.2 | operator | operator CLI/pool | Bulk stamp/cancel through bounded convergence |
| schedule create/update/pause/delete wrappers | 0.2 | operator | bootstrap/operator pool | IAM `admin` on schedule queue; never raw DML |
| `taskq.rotate_archive_partitions(...)` | 0.3 | operator (direct escape); owner internally | operator CLI; janitor nested call | Identifier input comes only from validated internal metadata; bounded lock plan |

The operator HTTP routes are **opt-in**. Mounting them requires a distinct `operator_engine`/pool whose login holds observer+operator. The ordinary facade runtime pool never holds operator. This is the only way for HTTP IAM to broker operator commands without giving every compromised producer/runner request path the same database capability. Tiny trusted hosts may consciously use one combined login, but the default and production guidance stays split.

### 2.5 Housekeeper and schedule functions

| Function | Release | EXECUTE | Credential(s) | Fence/transaction rule |
|---|---:|---|---|---|
| `taskq.tick(...)` | 0.1 | housekeeper, operator | runtime; CLI | Advisory xact lock, short transaction, reap first, due-gated janitor |
| `taskq.janitor(...)` | 0.1 | housekeeper, operator | nested tick; CLI | Per-pass savepoint/error record, plus total row/time budget |
| `taskq.claim_due_schedules(...)` | 0.2 | housekeeper | runtime housekeeper | Returns row version/expected `next_fire_at`; short transaction protocol |
| `taskq.fire_schedule(...)` | 0.2 | housekeeper | runtime housekeeper | CAS on expected row version/time; enqueue+advance atomic |
| `taskq.schedule_error(...)` | 0.2 | housekeeper | runtime housekeeper | CAS on expected row version/time; bounded retry-at |

The current §6 protocol assumes the caller keeps schedule row locks across several function calls. Add an expected `next_fire_at` or row-version CAS anyway. It prevents an auto-commit/misbehaving direct client from double-advancing a schedule, while the occurrence-derived idempotency key prevents duplicate jobs. The cron math remains client-side as accepted.

## 3. Deployment credential matrix

| Deployment identity | PostgreSQL membership/privilege | Why | Must not hold |
|---|---|---|---|
| Package migration login | membership in owner; explicitly `SET ROLE taskq_owner` for migrate | Creates/owns canonical objects under advisory migration lock | Used by app/runtime; stored in worker env |
| Read-only verify login | CONNECT + catalog visibility; optionally membership in owner **without SET ROLE** if platform needs metadata visibility | `taskq verify` is read-only and checks owner/ACL/checksum | Mutation or auto-migrate at app startup |
| Co-resident host request login | producer; observer only if app endpoints read results | Transactional `session=` enqueue with domain writes | Runner/operator/owner unless same tiny-host risk is explicitly accepted |
| Facade runtime login/pool | producer+runner+observer+housekeeper | HTTP enqueue/worker calls + internal tick | Operator, owner, maintenance authority |
| Optional facade operator login/pool | observer+operator | Mounted control/admin routes only after IAM authorization | Producer/runner/housekeeper/owner by default |
| outlabsAPI embedded runtime (simple profile) | producer+runner+observer+housekeeper | One process, own embedded lanes and housekeeper | Operator/owner/maintenance authority |
| Direct DB worker | runner+observer; producer only if handlers explicitly enqueue | Claims/settles and safe reads | Housekeeper unless designated; operator/owner |
| HTTP QDarte/Diverse worker | **no DB credential**; OutLabsAuth service token `taskq_{queue}:run` + exact read | Facade owns SQL credential; queue IAM scopes worker | Database DSN; control/admin grants |
| Designated `_system` runtime (0.2) | runner+housekeeper+observer | Claims package-owned schedule jobs and can invoke janitor | Operator/owner; ordinary fleet subscription |
| Operator CLI human/service login | observer+operator | Diagnosis/control/manual tick/janitor | Owner, producer/runner, maintenance authority unless a separate approved session |
| External maintenance login (PostgreSQL 17–18) | `USAGE` on schema + `MAINTAIN` on selected taskq tables; autocommit | Threshold-driven `REINDEX ... CONCURRENTLY` | Owner membership, facade use, generic operator functions unless separately intended |
| PostgreSQL 16 maintenance job | DBA/owner-managed plan by default; optional explicitly provisioned, isolated owner-authorized credential | PostgreSQL 16 has no narrow `MAINTAIN` grant; REINDEX requires relation ownership | Persistent app/facade use; silent fallback to owner; pretending this is least privilege |
| Metrics exporter | observer or a narrower metrics-only wrapper | Reads `taskq.metrics()` | Base table SELECT/mutation |

The version split is material. PostgreSQL 17–18 grant relation maintenance through `MAINTAIN`; PostgreSQL 16 does not list that privilege and requires the relation owner for REINDEX. All three versions prohibit concurrent reindex inside a transaction ([PostgreSQL 16 privileges](https://www.postgresql.org/docs/16/ddl-priv.html), [PostgreSQL 16 REINDEX](https://www.postgresql.org/docs/16/sql-reindex.html), [PostgreSQL 17 privileges](https://www.postgresql.org/docs/17/ddl-priv.html), [PostgreSQL 17 REINDEX](https://www.postgresql.org/docs/17/sql-reindex.html), [PostgreSQL 18 REINDEX](https://www.postgresql.org/docs/18/sql-reindex.html)). The CLI must detect server version, refuse an under-authorized or unexpectedly broad credential, execute each approved concurrent operation in autocommit, serialize with a session/advisory lock, and log its plan/result. On PostgreSQL 16 its safe default is a dry-run/DBA plan, not an automatic owner escalation.

## 4. Function-by-function SQL audit

| Function/section | Round 2 assessment | Verification | Decision before its release |
|---|---|---|---|
| `uuid7` (§4 lines 175–188) | Suitable for id/locality; not causal across producers | VERIFIED, PG18/RFC 9562 | R2-06 graph-based order; arbitrary-id race tests |
| `emit_event` (§4 lines 403–409) | Message truncation is good; external EXECUTE/actor forging and SSE nudge unspecified | VERIFIED | Owner-only; closed worker wrapper if needed; later event-id NOTIFY |
| `backoff_seconds` (§5.1) | Math is bounded; fixed ±15% conflicts with profile `jitter_ratio=.2` | VERIFIED | Keep fixed 15% in 0.1 or add one real stamped field—never both |
| `enqueue` (§5.2) | Dedup convergence is strong; internal flag, depth off-by-one, channel length, input validation, and archive staging need fixes | VERIFIED | Apply R2-07/R2-13; keep transactional semantics |
| `enqueue_many` (§5.2 prose) | No body; `RETURNING` alone cannot report all existing ids | VERIFIED/PLAUSIBLE | Exact atomic ordered convergence per R2-12 |
| `claim_jobs` (§5.3) | SKIP LOCKED/cap try-lock shape is sound; unknown/paused collapse, null/bounds, lease override, and result shape remain | VERIFIED | Typed claim state; bounds; H-01/H-02 closure |
| `heartbeat` (§5.4) | CAS shape good; unchecked override/progress size and worker-binding policy remain | VERIFIED | Validate; server bind label where possible; lost abort rule by execution type |
| `complete_job` (§5.5) | Fold-in ordering/error defects; dependency lock proof rests on UUID claim | VERIFIED | R2-01/R2-06 replacement before 0.1 migration |
| `fail_job` (§5.6) | Replay/retry stamping strong; cancel consumes budget; delay unbounded | VERIFIED | R2-03 branch; bounds; exact cross-verb replay rule |
| `snooze_job` (§5.7) | Pending cancel handled; negative delay silently clamps while other callers error | VERIFIED | Choose registered validation; recommended reject negative with TQ422 |
| `release_job` (§5.7 prose) | Required behavior is clear but no exact body | VERIFIED | Exact body/grants/tests before 0.1 |
| `reap_expired`/`reap_job` (§5.8) | Poison/budget model strong; factored target body absent | VERIFIED | Exact body; internal only; per-target expected-state support |
| `cancel_job` (§5.9) | Basic transition sound; result text needs canonical typed composite | VERIFIED | Protocol outcomes; actor derived; bounds |
| `cancel_dependents` (§5.9) | SKIP LOCKED convergence is thoughtful; recursive depth/work is unbounded | VERIFIED/PLAUSIBLE | 0.2 limit graph depth or use bounded iterative frontier + tick convergence |
| cancel/dep stragglers (§5.9 prose) | Correctness depends on missing bodies | VERIFIED | Release-specific bodies; dep pass absent in 0.1 |
| `redrive_job` (§5.9) | Unique collision translation good; false/not-found/not-failed collapse is ambiguous | VERIFIED | Typed not-found/not-redrivable/collision; document chain proof |
| `expire_job` (§5.9) | Synchronous target concept is correct; depends on missing `reap_job` | VERIFIED | Expected fence/state and targeted body |
| `expire_worker_leases` (§5.9) | Generic reaper target bug | VERIFIED | R2-02 captured-id loop |
| operator signature-only functions | Needed and correctly ban raw DML; bodies/results absent | VERIFIED | Complete 0.1 subset before migration; stage others |
| schedule trio (§6) | Enqueue+advance transaction is right; client-held lock assumption lacks CAS defense | PLAUSIBLE | Expected row version/time on fire/error; idempotent occurrence key |
| `tick` (§11.4) | Advisory dedupe and exception isolation are good; release mix and janitor incomplete | VERIFIED | 0.1-specific reaper-first body; housekeeper grant |
| `janitor`/archive (§13) | Transactional split from REINDEX is correct; exact pass bodies, due-state, attempt aggregation absent | VERIFIED/PLAUSIBLE | R2-09/R2-13; row/time budgets and stage boundaries |
| read/metrics functions (§12/Growth §4) | Correct principle, insufficient exact projections/indexes | VERIFIED/PLAUSIBLE | R2-16 and protocol H-07/H-08 |

## 5. Lock-order replacement

The safe invariant is graph-based:

1. A dependency edge is created only from a newly inserted dependent to already-existing parent jobs.
2. Edges are immutable until deletion, so cycles cannot be introduced through the public enqueue contract.
3. Operations acquire ancestor/parent rows before dependent rows. When several rows are at the same graph frontier, order by UUID bytes as a deterministic tie-breaker.
4. No function may use creation time, uuid version, or an external generator as a correctness premise.
5. Functions using `SKIP LOCKED` remain convergent through bounded tick passes; functions that wait must share the same graph/frontier order.

The migration harness must generate diamonds, multiple parents, long chains, same-millisecond UUIDv7 values whose lexical order is reversed, and arbitrary UUID versions if owner/test fixtures inject them. PostgreSQL recommends consistent multi-object ordering as the deadlock defense: [PostgreSQL 18 explicit locking](https://www.postgresql.org/docs/18/explicit-locking.html).

## 6. 0.1 migration manifest gate

Migration 0001 must list, for every present function:

- exact identity arguments and return composite;
- owner, security mode, pinned path, volatility/parallel flags;
- PUBLIC revoke and all grants;
- release capability and protocol command;
- documented TQ raises and typed outcomes;
- statement/transaction time budget;
- idempotency/replay rule;
- corresponding T2/T3/T4/T6 test ids.

`verify()` compares the live catalog against that manifest. This simultaneously closes R2-04, makes the role matrix executable, and prevents future route/client hand-mirroring.
