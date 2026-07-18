# outlabs-taskq external design review — Round 2

**Review date:** 2026-07-18  
**Scope:** the uncommitted documentation working tree, with ADR-001–010 treated as accepted authority  
**Evidence:** repository documents; outlabs-auth `0.1.0a24`; the Diverse, QDarte, and outlabsAPI source trees; PostgreSQL 16–18 documentation; current upstream peer documentation

## Verdict

**Do not start the first migration yet.** The product boundary and most architectural choices are strong, but the v1.5 fold-in is not Stage-0-ready. Eight P0 findings remain in the normative contract: the atomic-follow-up fold-in has the wrong validation order and non-executable error contract; worker-wide expiry can reap unrelated jobs; a pending operator cancel consumes failure budget through `fail_job`; the exact function DDL does not embody ADR-010 hardening; the facade housekeeper has no least-privilege role; the uuidv7 deadlock proof is not valid for all permitted IDs/producers; public/internal parameters bypass queue controls; and required 0.1 functions still exist only as names or prose.

The recommended disposition is **accept with blockers**:

1. Accept the product direction and ADR set.
2. Accept the protocol draft in [03-protocol-draft.md](./03-protocol-draft.md) as the working input to ADR-005's canonical protocol document.
3. Amend ADR-010 with a sixth non-login capability role, `taskq_housekeeper`, and the credential matrix in [04-sql-and-role-audit.md](./04-sql-and-role-audit.md).
4. Fix R2-01 through R2-08 in the documents before Stage 0 exits.
5. Decide the dedicated-database, read-model, and SSE proposals separately using [05-growth-proposals.md](./05-growth-proposals.md); none should block the 0.1 kernel.

## Findings summary

| ID | Priority | Finding | Recommended decision |
|---|---:|---|---|
| R2-01 | P0 | `complete_job` does not implement ADR-007's validate-before-mutation ordering, 0.1 has no executable `TQ501` gate, and its `TQ422` raises are really `P0001` | Replace §5.5's ordering and error emission before freezing the SQL contract |
| R2-02 | P0 | `expire_worker_leases` backdates one worker's jobs but calls a generic reaper that may consume unrelated rows | Capture the target ids and call the single-row reaper for those ids only |
| R2-03 | P0 | `fail_job` turns a pending operator cancel into a failed attempt and increments `failure_count` | Branch to cancelled before failure accounting; budget remains untouched |
| R2-04 | P0 | The normative DDL bodies still omit the ADR-010 `SET search_path`/revoke/grant mechanics | Make a migration manifest/wrapper render and verify every function's hardening atomically |
| R2-05 | P0 | The facade must tick, but its documented DB credential lacks operator authority; `_system` and PostgreSQL-version-specific maintenance credentials are also unresolved | Add `taskq_housekeeper`; publish the function/credential matrix and the PostgreSQL 16 vs 17–18 maintenance split |
| R2-06 | P0 | uuidv7 time ordering is not a proof that every parent sorts before every child | Prove lock order from immutable DAG topology, with UUID only as a sibling tie-breaker |
| R2-07 | P0 | Public/internal parameters and lease/retry overrides bypass queue controls or accept unsafe ranges | Remove `p_internal` from producer EXECUTE surface and validate all direct-SQL inputs |
| R2-08 | P0 | Several functions required by 0.1 exist only as names or prose | Freeze executable bodies and grants for every 0.1 command before migration 0001 |
| R2-09 | P1 | The 0.1 hardwired janitor pass is absent from `tick`, while many passages still seed schedules/`_system` in 0.1 | Define a due-state, bounded, reaper-first janitor pass and remove premature schedule seeding |
| R2-10 | P1 | There is no error registry or frozen transport outcome model | Adopt one envelope, one SQLSTATE-to-HTTP map, and one version-negotiation rule |
| R2-11 | P1 | The runtime promises hard cancellation/release for thread-offloaded sync handlers, which Python cannot safely stop | Make cancellation cooperative for threads; never release while a sync call can still write |
| R2-12 | P1 | Bulk enqueue cannot report all `created`/`existed` outcomes from `INSERT ... RETURNING` alone | Make the batch atomic, ordered, convergent, and one-result-per-input |
| R2-13 | P1 | Archive movement is prose-only and must preserve attempt history before FK cascade; archive objects are deferred to 0.3 | Stage the schema and specify select/aggregate/delete/insert ordering |
| R2-14 | P1 | SSE replay ignores event pruning and the LISTEN initialization race | Persist a prune watermark and define a reset event plus subscribe-then-replay handshake |
| R2-15 | P1 | Dedicated-DB enqueue correctly admits its crash gap, but a retry path is not a durable substitute for an outbox | Require a host outbox when domain intent must survive; define queue RPO/restore semantics |
| R2-16 | P1 | The read-model proposal lacks exact cursor/index/redaction contracts and has an unsafe global-list ambiguity | Freeze projections, keysets, queue authorization, payload policy, and query plans in ADR-005 |
| R2-17 | P1 | Auth wildcards work for service tokens, but not API-key scopes; the permission prefix is incorrectly configurable; outlabsAPI is pinned below the verified version | Keep service-token wildcards, enumerate API-key scopes, fix the namespace, and gate dogfood on the auth upgrade |
| R2-18 | P1 | 0.1 is sufficient for the named pilots only if the sync HTTP/runtime path and minimal job-result read are explicit acceptance items | Keep the accepted 0.1 scope; add those two release-gate statements and cut nothing else |
| R2-19 | P2 | Stale roles, dotted permissions, route shapes, retry jitter, module paths, staging tags, and two peer attributions remain | Apply the mechanical correction table in the consistency audit |

## Targeted-question answers

| Question | Answer |
|---|---|
| Service-token wildcard | **Verified yes** at outlabs-auth `0.1.0a24`: `check_service_permission` calls `PermissionService._permission_set_allows`; `resource:*` works. API-key *scope creation* separately rejects `*`. |
| Role × flow | There is a real gap. Add `taskq_housekeeper`; do not give the facade `taskq_operator`. External maintenance uses selected-table `MAINTAIN` only on PostgreSQL 17–18; PostgreSQL 16 needs an explicit DBA/owner-managed path. The full matrix is in review doc 04. |
| §5.5 ordering/redrive | Ordering is wrong in the body. 0.1 must gate non-empty follow-ups with `TQ501` after replay/fence recognition but before mutation; 0.2 validates with `TQ422` before mutation. Atomic complete-followups cannot have committed children on a failed parent, so ordinary failed-parent redrive does not duplicate chain steps. Handler side effects still rerun by the accepted at-least-once contract. |
| UUIDv7 lock order | Do not validate a version nibble as the deadlock fix. UUIDv7 is time-ordered, not a portable global causality proof. Base the proof on the immutable dependency DAG and use ids only to order siblings. |
| SSE after prune | Persist `max_pruned_event_id`; if a resume cursor is below it, send a typed `reset` event with the watermark and snapshot URL, then close. Equality is safe because the client has already seen the greatest pruned event. Never infer pruning from numeric gaps. |
| Dedicated DB | The crash gap is honestly named, but “idempotent re-trigger” is not durable recovery. Refusing public `session=` entirely in dedicated mode is correct; durable domain intent requires an app-side outbox. Cross-database joins/transactions and coordinated restore are also lost. |
| Janitor in tick | PL/pgSQL exception blocks isolate pass failure but do not create independent commits. Reaping must run first; janitor must be due-gated and independently row/time bounded so it cannot consume every tick's transaction budget. |
| TQ codes | No registry exists today. `TQ422` is currently only message text. The draft assigns every TQ code exactly one HTTP status and requires clients to dispatch on SQLSTATE/body code, never message text. |

## Review documents

- [01-findings.md](./01-findings.md) — ranked decision log with evidence and exact amendments
- [02-consistency-audit.md](./02-consistency-audit.md) — cross-document contradiction and stale-text sweep
- [03-protocol-draft.md](./03-protocol-draft.md) — Stage-0 command/outcome/HTTP contract draft
- [04-sql-and-role-audit.md](./04-sql-and-role-audit.md) — function-family audit, complete role matrix, deployment credentials
- [05-growth-proposals.md](./05-growth-proposals.md) — decisions for dedicated DB, read models, SSE, and 0.1 fit

## Evidence convention

- **VERIFIED** — checked against source or an official/upstream primary source named beside the claim.
- **PLAUSIBLE** — reasoned from the documented design but not executable yet because no implementation exists.
- **SPECULATIVE** — a possible risk that needs a test or measurement before it should drive a decision.

Historical Round 1 findings are not repeated except where their accepted ADR was folded into v1.5 incompletely. This review does not reopen the Postgres-only boundary, fixed schema, SQL-owned state machine, atomic follow-ups, transport parity, first-release scope, or capability-role direction.
