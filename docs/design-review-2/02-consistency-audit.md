# Round 2 cross-document consistency audit

This table excludes `docs/design-review/`, which is intentionally historical. “Fix” means a future amendment if the maintainer accepts this review; no source document was changed during Round 2.

## ADR and release-staging contradictions

| Location | What it says | Conflicts with | Proposed fix |
|---|---|---|---|
| `docs/README.md`, line 10 | Canonical permission is `taskq.{queue}:{action}` | ADR-006 lines 12–18 fixes `taskq_{queue}:{action}` | Replace the dotted example |
| `Task Queue Library Extraction Design Brief.md`, line 283 | Extended note names `taskq.{queue}:{action}` | ADR-006 | Replace with underscore grammar |
| Extraction Brief, line 403 | Provisioner seeds `taskq.{queue}:{action}` | ADR-006 | Replace with `taskq_{queue}:{action}` and global fallback |
| Extraction Brief, lines 15, 127, 303, 311, 319 | A single `taskq_worker`/`taskq_admin` role is the target model | ADR-010 lines 12–22 replaces it with capabilities | Replace target prose/diagram with capability roles; mention legacy umbrella only in migration notes |
| Extraction Brief, lines 669 and 683 | Acceptance/quote bank still asserts `taskq_worker` | ADR-010 | Assert no capability role has direct DML; name the exact role fixtures |
| Authorization doc, line 12 | SQL layer is `taskq_worker` | ADR-010 | Say capability roles are queue-global and IAM-agnostic |
| Authorization doc, lines 102–113 | `resource_prefix` permits renamed/multiple taskq namespaces | ADR-006 fixes the permission namespace; ADR-002 defers multiple installations to 1.x | Remove the parameter and rationale in 0.x |
| Authorization doc, lines 139–175 | Catalog and CLI accept `resource_prefix`/`--prefix` | ADR-006/ADR-002 | Remove both options; use separate databases for isolated installs |
| Authorization doc, lines 46–60 | Route table is labelled normative and gives concrete paths | ADR-005 line 15 says all route listings remain illustrative until the protocol exists | Keep action/queue-source mapping normative; relabel paths illustrative or replace them with protocol command names |
| Authorization doc, lines 32, 38, 59 | `tick` is a public global `control` HTTP route | Unified Spec §11.4 lines 1615–1618 says the facade deliberately exposes no tick route | Remove public tick; housekeeper calls SQL internally; operator CLI is the manual surface |
| Unified Spec, line 23 | Daily janitor is seeded as a schedule at install | ADR-009 lines 12–16 defers schedules to 0.2 and hardwires 0.1 tick | State the staged trigger explicitly |
| Unified Spec, lines 521–527 | Installer always creates `_system` and janitor schedule | ADR-009 | Move this migration block to the 0.2 section |
| Unified Spec, line 1868 | Phase 0 seeds `_system` and `taskq-janitor` | ADR-009 | 0.1 seeds only real queues/profiles and control-state due marker; 0.2 adds `_system`/schedule |
| Unified Spec, line 2027 | Synthesis still says schedule row at install | ADR-009 | Add release qualifier or remove the stale graft bullet |
| Unified Spec, lines 2051–2055 | `_system` worker placement is an open/current 0.1 concern | ADR-009 | Move to the 0.2 schedule section; resolve its housekeeper credential per R2-05 |
| Staging Cutover Runbook, lines 20–27 and 218–225 | First lane enables `_system`/`taskq.janitor` | ADR-009's 0.1 slice | For package 0.1, enable only `courts/missouri_casenet`; retain old values only under a clearly labelled legacy-scaffold checkpoint |
| Staging Runbook, lines 50–59, 153–154, 254–262 | Readiness/evidence requires the janitor schedule | ADR-009 | Require the control-state daily-janitor marker and tick result in 0.1; schedule evidence starts in 0.2 |
| Borrowed-features README, lines 65–66 | Feature 14 is the recommended `_system` claimer in the first runtime phase | ADR-009 | Qualify this as 0.2; 0.1 runs janitor directly from housekeeper tick |
| Unified Spec, lines 1437, 1977, 2000 | Stale `taskq_worker` appears in current scheduling/tradeoff/failure text | ADR-010 | Use `taskq_housekeeper`, the relevant capability role, or “runner credential” as appropriate |
| Unified Spec, lines 552–558 | Comment says timeouts apply to every capability role, SQL alters only runner | ADR-010 capability model | List `ALTER ROLE` statements for producer/runner/observer/operator/housekeeper or state role-specific values |
| Unified Spec, line 1830 | Client-generated ids are accepted enqueue parameters | Actual §5.2 signature lines 591–611 has no id; ADR-era lock proof assumes generated v7 | Remove caller ids in 0.x and remove the causality claim per R2-06 |
| Harness, line 64 | Every property-test operation runs as `taskq_worker` | ADR-010 and Harness line 40's own capability fixtures | Dispatch each generated operation through its exact capability role |
| Borrowed feature 13, line 87 | `verify` checks `taskq_worker` no-DML | ADR-010 | Check every application capability role, plus PUBLIC and housekeeper |

## Normative-body and contract contradictions

| Location | What it says | Conflicts with | Proposed fix |
|---|---|---|---|
| Unified Spec §5.5, lines 959–980 vs 1006–1026 | Parent CAS happens before follow-up validation | ADR-007 lines 14–15 says validation before any parent state change | Reorder per R2-01 |
| Unified Spec §5.5, lines 1004–1005 | 0.1 raises `TQ501` | No branch in the displayed body does so | Add an executable 0.1 capability gate |
| Unified Spec §5.5, lines 1008–1024 | Message text begins `TQ422` | PostgreSQL emits default SQLSTATE `P0001` without `ERRCODE` | Set SQLSTATE explicitly and register it |
| Unified Spec §5.6, lines 1121–1163 | Pending cancel through `fail_job` marks failed and increments failure count | ADR-003/ADR-007 budget semantics and §5.7 cancel branches | Branch to budget-free cancelled before failure accounting |
| Unified Spec §5.9, lines 1417–1425 | Worker expiry is synchronous targeted reclaim | Generic `reap_expired(N)` may select other worker rows | Capture ids and call targeted `reap_job` |
| Unified Spec §4, lines 541–548 vs displayed function DDL | Every function has pinned path/revoked PUBLIC/minimal grant | The “exact” declarations omit `SET search_path`; helpers have no public/internal posture | Make the migration manifest mechanically emit/verify hardening |
| Unified Spec §5.2, lines 591–638 | Producer-granted `enqueue` exposes `p_internal` | Depth bypass is intended only for owner-internal follow-ups | Remove it from the public signature |
| Unified Spec §5.3/§5.4, lines 766–774, 859–873, 912–925 | Worker lease override is unchecked | Queue/job lease constraints are 15–86400 at lines 215/280 | Validate the override at each public SQL boundary |
| Unified Spec §4/§5.2, lines 211 and 749–751 | Queue names may be 63 bytes and become `taskq_{queue}` channels | PostgreSQL identifiers default to 63 bytes; the prefix can make a 69-byte channel | Cap queue names at 57 ASCII bytes or use one fixed notification channel |
| Unified Spec §5.2, lines 628–638 | `max_depth=N` probes at `OFFSET N` | This permits active row N+1 before rejecting and `max_depth` lacks a positive CHECK | Probe at N-1 when depth already equals N; constrain NULL-or-positive and keep concurrency caveat |
| Unified Spec §5.5, lines 988–990 | Derived key guarantees exactly once even when the job “re-runs” | Exactly-once is only enqueue-state transition; at-least-once handler effects can repeat, and a failed complete has no committed children | Narrow wording to exactly-once child acceptance per successful parent completion transaction |
| Unified Spec §5.2, line 761 | Bulk `RETURNING` reports every created/existed outcome | `DO NOTHING RETURNING` reports inserted rows only; ADR-009 requires one typed result per input | Add set-based convergence and ordered result contract |
| Unified Spec §11.4, lines 1565–1612 | This is the 0.1 tick | ADR-009 requires a daily janitor pass; body also calls 0.2 dependency/workflow finalizers | Define a release-specific 0.1 body: reap, cancel stragglers, stats, due-gated janitor |
| Unified Spec §11.5, lines 1624–1630 | Operator runbook selects raw `jobs`, `job_events`, and `control_state` | ADR-010 grants observer only safe views/read functions and denies table access | Replace raw selects with safe, indexed functions/views and make any owner-only forensic path explicit |
| Unified Spec §13.2, lines 1745–1746 | Attempts are aggregated during `DELETE … RETURNING` archive move | Attempts cascade from job deletion at lines 360–363; no normative ordering/body exists | Aggregate from locked live rows before delete; add conservation tests |
| Unified Spec §13.5, line 1753 | Janitor remains backup-before-janitor | A facade tick cannot itself guarantee a host backup has run | Apply backup-before-janitor to scheduled/manual full retention operations; state what the 0.1 hardwired bounded delete guarantees |
| Unified Spec §13.5, line 1753; ADR-010 lines 26–27 | External maintenance says only “admin credentials” across PostgreSQL 16–18 | PostgreSQL 17–18 support narrow table `MAINTAIN`; PostgreSQL 16 instead requires relation ownership for REINDEX | Specify versioned credentials: selected-table `MAINTAIN` on 17–18; DBA/owner-managed plan or an explicit isolated owner-backed exception on 16 |
| Unified Spec §20.2, lines 2051–2055 | Runner handles a `taskq.janitor` job | Janitor is operator-tier under ADR-010; typical runner lacks that grant | Designated `_system` runtime must hold housekeeper, never broad operator |

## Library surface, routes, and staging-label drift

| Location | What it says | Conflicts with | Proposed fix |
|---|---|---|---|
| Borrowed feature 02, lines 131–140 | HTTP route is `POST /taskq/enqueue` | ADR-005 and proposed `/taskq/v1/queues/{queue}/jobs` | Label illustrative now; replace after protocol acceptance |
| Borrowed feature 07, lines 85–89 | Redrive path is `/taskq/jobs/{id}/redrive` | ADR-005 versioned protocol | Label illustrative or use canonical command name |
| Borrowed feature 14, line 106 | Result read is `GET /taskq/jobs/{id}` | ADR-005 | Label illustrative; keep the required read capability |
| Authorization doc route table, lines 50–60 | Uses a third route family (`/{queue}/claim`, `/enqueue`) | ADR-005 cites route divergence as the reason for a protocol | Replace all route families from one generated protocol table |
| Staging Runbook, lines 63–87 | Uses Diverse legacy `/api/v1/taskq` routes | Not a contradiction because lines 85–87 label them compatibility routes | Keep, but link each legacy route to its canonical command and sunset rule |
| Borrowed feature 14, lines 30–62 | Imports from `taskq.contrib.fastapi` | Extraction Brief consistently exposes facade/runtime under `taskq.http` (lines 233–272) | Choose one module before package skeleton; recommended `taskq.http` exports plus submodules, no third namespace |
| Feature 05, lines 17–36 | Queue profile includes `retry_jitter_ratio=0.2` and several names that differ from schema | Unified SQL stamps fixed ±15% jitter and has no jitter column (`Unified Spec` lines 573–585, 210–228) | Either add a real stamped ratio with SQL bounds or remove it; recommended fixed 15% in 0.1 |
| Feature 09, lines 26–32 and 45–50 | RetryStrategy stamps `jitter_ratio` | Unified SQL has fixed ±15%, no stamp | Same decision as feature 05; one field/name vocabulary only |
| Feature 05, line 36 | `dead_letter_queue` is a normative queue field | ADR-009 defers redirect DLQ to conditional 0.3 | Add “0.3 conditional” to the row or omit from 0.1 model/migration |
| Feature 13, line 3 | SQL packaging is `NICE` | ADR-004/ADR-009 require canonical migrations in 0.1; borrowed README line 72 stages it in 0.1 | Change header to “MUST for 0.1” |
| Feature 13, lines 95–96; Harness line 39 | “double install” is idempotent | ADR-004 says migrations are canonical and snapshot is not an installer | Say “double invocation of `migrate()` is idempotent”; never imply replaying `schema.sql` upgrades live DBs |
| Borrowed README, lines 19–23 vs 68–76 | Generic MUST/SHOULD/NICE definition can imply first-cut scope | The table says ADR-009 wins and individual headers are partly corrected | Keep the authority note, but update every header so readers need no conflict resolution |
| Feature 11, lines 19–23, 50–58; Feature 14 lines 92–97 | Hard-cancel thread then release | Running Python thread cannot be cancelled; releasing permits concurrent side effects | Split async and sync shutdown contracts per R2-11 |

## Authorization wording that needs precision, not reversal

| Location | What it says | Verified reality | Proposed fix |
|---|---|---|---|
| Authorization, lines 16–19, 83–92, 189–198 | `taskq_{queue}:*` is a useful worker grant | Correct for service-token embedded permissions at outlabs-auth a24 | Add source citation; keep it |
| Authorization, lines 177–198 | API keys and service tokens are discussed together but wildcard-scope difference is unstated | API-key policy rejects any scope containing `*`; service token matcher accepts `resource:*` | Tell API-key issuers to enumerate exact actions |
| Authorization, lines 161–169 | Standard IAM role names resemble DB role names | They are distinct trust layers but easy to confuse | Prefix table heading with “OutLabsAuth IAM roles (not PostgreSQL roles)” |
| Authorization, line 212 | Worker `actor` comes from `worker_id` | With shared fleet token, request `worker_id` is caller-asserted/advisory | Server derives principal actor; store worker label separately; bind it to subject only for per-worker credentials |

## Peer-research corrections

| Location | Current claim | Verification | Proposed fix |
|---|---|---|---|
| Peer Research §7, lines 265–278 | Whole slice is a knowledge pass pending verification | the Elixir/Postgres job framework telemetry/testing, the Rails-native Postgres queue pause/recurring config, the Rails in-process Postgres queue in-process async mode, a lean Redis/asyncio job library hooks/UI/group admission, and a Redis/asyncio task library defer/result retention are now supported by upstream sources | Replace blanket caveat with per-row source links and verification date |
| Peer Research, line 274 | the Postgres message-queue extension has “archive-instead-of-delete with partition retention” | Upstream verifies visibility/delete/archive; partitioned queues are optional/separate, not implied retention for every archive | Say “archive-instead-of-delete; optional partitioned queues” |
| Round 1 peer addendum, line 9 | the newer Python/Postgres queue library v1.2.0 on 2026-07-15 | Verified by PyPI trusted-publisher record | Keep; add the PyPI link |
| Round 1 peer addendum, line 43 | a pure-SQL event-stream project exists as distinct event stream | Verified upstream; the project calls itself a pure-SQL fan-out event/message queue, not a job framework | Keep; add upstream link and preserve non-borrow conclusion |

## Deliberately not treated as contradictions

- Round 1 files retain pre-ADR analysis because they are historical provenance.
- The Unified Spec may describe the 0.2/0.3 destination; the bug is only when a passage claims those objects install in 0.1 or lacks an activation badge.
- Diverse legacy route paths may coexist during the strangler because the runbook labels them compatibility paths. Their semantics, not their URL, must match the canonical protocol.
- The fixed PostgreSQL schema does not forbid a host-owned *display name* or database choice; it forbids dynamic SQL schema installation and a configurable OutLabsAuth taskq namespace in 0.x.
