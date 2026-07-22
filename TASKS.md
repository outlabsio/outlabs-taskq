# outlabs-taskq — Execution Tracker

> **Tier 2 (live).** Task-level truth for the implementation: what is in flight, what is next, what is done. The [Build Plan](docs/Task%20Queue%20Build%20Plan.md) owns stage strategy and exit gates; this file owns the granular work. **Update this file in the same commit as the work it describes** — a task not updated here didn't happen.

## Cold start (any agent, from zero)

1. Read `AGENTS.md` (hard rules) → `docs/README.md` (tier map — Tier-0 contracts beat everything) → this file.
2. Environment: Python 3.12+, `uv`, and a local PostgreSQL. A dev Postgres 18 usually runs via docker (`docker ps` → container from localDevServices, `postgres/postgres@localhost:5432`). Create/reuse the scratch DB:
   `psql postgresql://postgres:postgres@localhost:5432/postgres -c "CREATE DATABASE taskq_stage1_test"` (ignore exists-error).
   Caveat: migration 0001 creates six cluster-wide `taskq_*` roles on that server — expected on a dev cluster; never point tests at a shared/production server.
3. Run everything:
   ```bash
   uv sync --extra dev --extra http --extra outlabs
   uv run pytest tests/ -q                                   # T1 only (no DSN)
   TASKQ_TEST_DSN="postgresql://postgres:postgres@localhost:5432/taskq_stage1_test" \
     uv run pytest tests/ -q                                 # T1 + T2 (must be 42/42 before you start)
   uv run ruff check .
   ```
4. Pick the topmost unchecked task in **Now**, or the next in **Next**. Work it to its acceptance criteria.
5. Definition of done, every task: suite green (no skips you introduced), `ruff check` clean, docs amended if the task's row says so, this file updated (move the task, one-line result note), one commit ending with the repo's `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` trailer convention.

**Standing rules (non-negotiable):** Tier-0 contracts and ADRs win every conflict — if implementation reveals a contract bug, STOP, record it under **Contract questions** below, and fix docs-first (errata/ADR), never code-around. Never name third-party queue projects. Never edit Tier-4 historical docs. New SQL functions require a Function Manifest entry first.

## Status snapshot

| | |
|---|---|
| Stage | **Post-Stage-4 retirement eligibility + Stage-5 durable admission** — `main` is the authoritative deployed host line; the legacy-tools observation continues independently; ADR-023 resolves the QDarte C6 replay gap and S5-AR-01 is the active library slice before local C6 may resume |
| Suite | 475/475 regular on fresh local PG18.3 with 1 opt-in skip (CI-shaped Redis); 469/469 last independently verified on PG16.14 with 1 opt-in skip; 304/304 DB-free on Python 3.12; 289/289 last run on Python 3.13; PG18 million-row plan gate 2/2; artifact matrix 12/12; host 72/72 regular with 5 pre-existing opt-in skips; MyPy 64 files |
| Contracts | Protocol v1 document revision 1.0.8 + Function Manifest 0.1.5 (+ ADR-012..023); ADR-018 locks operator UI stack (React/Vite/TanStack/Base UI) |
| Next review | S5-AR-AUDIT must accept the dual-PG admission primitive before QDarte repins; targeted L1 acceptance separately gates legacy-tools L2 |

## Now

- [x] **S5-AR-SPEC · Durable two-phase admission frozen** — ADR-023 accepts a reusable queue-native `(queue, idempotency_key)` reservation ledger rather than a QDarte mapping wrapper. Protocol 1.0.8 and Manifest 0.1.5 freeze client-generated retry-stable handles, SHA-256 intent binding, `reserved | pending | admitted`, atomic finish with immutable bounded receipt, reservation-only cancellation, database-clock expiry/retention, producer-scoped authorization, bridge/rollback-floor ordering, and immutable migration 0007. QDarte is the first integration but owns no durable mapping. No SQL, migration, source, package release, host database, worker, provider, deployment, production, or direct-queue change occurs in this docs-first task.

- [ ] **S5-AR-01 · Admission SQL kernel and bridge proof** — implement immutable `0007_admission_reservations.sql`, closed 0.1.5 bridge membership, exact metadata/grants/catalog verification, fresh/full upgrade paths, bounded cleanup, and the full concurrent SQL race matrix on PostgreSQL 16/18. Stop on any Tier-0 mismatch; no HTTP/QDarte/production action in this task.

- [ ] **S5-AR-02 · Generated admission transports and parity** — add strict typed SQL/HTTP reserve/finish/cancel clients, capability-safe facade mounting, retry-stable orchestration, authorization/hiding, response-loss, and SQL/HTTP parity evidence. No QDarte repin or worker action until this slice is green.

- [ ] **S5-AR-AUDIT · Admission primitive completion gate** — dual-PG race/resource/packaging/plan evidence and targeted independent review. Acceptance opens only an isolated QDarte repin and C6-03 replay proof; it authorizes no production migration or direct retirement.

- [x] **S4-POST-L1-SPEC · Legacy-tools retirement eligibility frozen** — amended the Tier-3 retirement plan to close Round-8 R8-02/03/05 before observation starts: `TASKQ_TOOLS_ALLOWLIST` remains an enrollment gate after `TASKQ_TOOLS_MODE` removal; disabled, not-ready, and registered non-allowlisted tools share the exact fail-closed `503 {"detail":"Queued task processing is unavailable"}` response and never enqueue legacy work; `umami` uses a target access-log counter while the read-only flight lane's host-counter/taskq reconciliation is explicitly non-independent; and the retired 200 response now has an explicit caller-sweep gate. L2 owns the restricted-runtime proof rewrite and compatible settings/documentation update. No host source, taskq SQL/wire/IAM/capability, deployment, database, or producer/consumer behavior changed.

- [ ] **S4-POST-L1 · Seven-day tools-retirement eligibility observation** — Day 0 opened in host commit `b7cda5c`: the authoritative production API is healthy and in taskq mode for `umami,aerolineas`; the frozen legacy oracle is count 2 / max `2026-07-20 11:37:34.886547` / zero active rows. The runtime login's denied raw `taskq.jobs` read is retained as capability evidence, so taskq observations use the authorized canonical read plus the audit oracle. Six further consecutive days, two normal authoritative-host deploys, lane invocation reconciliations, and final caller attestation remain; any legacy insert resets the window. Stop for targeted independent acceptance before S4-POST-L2; no producer or consumer removal is authorized in this task.

## Later

- [x] **S5-QD-P0 · QDarte local-first pilot design frozen** — added the Tier-3 [Stage 5 QDarte Pilot Specification](docs/Task%20Queue%20Stage%205%20QDarte%20Pilot%20Specification.md) after a source-backed audit of QDarte's API-owned worker ledger, HTTP worker fleet, shared registry, and isolated compose smoke. It selects a separate `qdarte_pilot` queue and a non-chaining adapter over deterministic empty-input `cluster_research_scope`; no existing QDarte queue row, content/provider/browser/writeback lane, or production stack participates. It fixes exact a3 bridge pinning, owner-only 0001–0005 local provisioning, capability-sized runtime/worker identities, pure shadow digest, keyed canary, response-loss/local hard-kill recovery, and zero-DML disablement, while preserving the future side-effecting hard-kill gate. Targeted review required before source/local DB/IAM/worker/compose change.

- [x] **S5-QD-REVIEW-REQUEST · QDarte pilot targeted review assembled** — [Round 11 request](docs/design-review-11/REQUEST.md) requires independent derivation from the current QDarte source baseline rather than the potentially stale local clones. It attacks the legacy `qdarte_ops` isolation boundary, a3/0001–0005 bridge, identities and connection arithmetic, pure handler claim, keyed/replay/hard-kill oracles, compose isolation, and zero-DML disablement. It authorizes no source, local DB/IAM, worker, compose, deployment, production, existing-lane, side-effecting, retirement, or Stage-6 change.

- [x] **S5-QD-REVIEW-RESPONSE · QDarte pilot targeted review accepted** — registered the immutable Round-11 response verbatim. It independently confirms a3 is the correct route-free 0001–0005 bridge and source-confirms the pilot handler's pure deterministic path and the structural separation from `qdarte_ops`. R11-01..04 land docs-first in this commit: a dedicated non-superuser facade DSN/pool, fixed synthetic payload, explicit legacy closed-literal/shared-registry non-touch stop, and a six-table count/max-id/max-updated-at drift oracle. READY opens P0–P5 in isolated disposable `qdarte-dev` only; production, Mac-mini/cloud, existing-lane migration/retirement, external effect, chaining, UI/read models, and Stage 6 remain closed.

- [x] **S5-QD-P0 · QDarte isolated-dev preflight accepted** — the amended baseline is intentionally QDarte-only: guarded PG18/Redis/qdarteAPI/MinIO health plus the pure no-network `cluster_research_scope` drill. `intake-worker` and the broad multi-worker smoke are excluded because their non-pilot lanes have un-sandboxed egress/storage/write effects; it was never started. The guarded local PostgreSQL is 18.4 with `max_connections=100`, and the API currently uses the `postgres` superuser, confirming R11-01’s dedicated-facade-role requirement. The pure drill passed while its worker was temporarily narrowed; because cleanup restored the broad legacy allowlist, it was stopped immediately. QP-09 now uses a stable complete-row digest as the six-table mutation oracle (with high-waters diagnostic only). P1 must use current-source local checkouts, a distinct non-superuser facade DSN/pool, and a pilot-only worker allowlist fixed by construction.

- [x] **S5-QD-P0B · QDarte direct-queue disposition frozen** — S5-QD-CQ-01 is resolved as Option B, not a cleanup or compatibility project: the current QDarte direct-SQL contact-verify queue remains untouched in `qdarteapi_dev`; the package pilot owns only a newly created disposable `qdarte_pilot_dev` database on the same guarded local cluster. The fixed `taskq` schema therefore remains package-owned within its database, without a schema/catalog overlap or a renamed schema. Round 11's safety findings remain binding, but its greenfield/no-collision inventory is superseded by current staging source. P0B's targeted re-check confirms the old schema is confined to `qdarteapi_dev` and the pilot database is absent until P2; it creates no database, role, queue, IAM, migration, worker, or source change.

- [x] **S5-QD-HOST-GATE-01 · QDarte fresh migration baseline** — repaired the incumbent source migration narrowly: `20260715_0070_host_native_worker_lanes` accepts only the inherited fresh-chain `media=1` seed created by revisions 0044/0053, then performs its existing normalization to six zero-desired host-native lanes; every other nonzero state remains fail-closed and requires explicit scale-down. A newly created disposable `qdarteapi_p1_test` reached 0075 and showed that exact lane posture. This is not a package migration, created no `qdarte_pilot_dev`, and left QDarte's incumbent direct queue untouched.

- [x] **S5-QD-P1 · QDarte disabled host boundary** — exact a3 wheel URL/SHA pins now land separately in fresh API and worker worktrees. The API's optional, disabled-by-default mount accepts only a development `postgresql+asyncpg` DSN for `qdarte_pilot_dev` under a dedicated non-superuser login, uses its own one-connection runtime pool, and never falls back to the incumbent API DSN; disabled boot leaves the facade unmounted and opens no pilot-database connection. The worker has no process or handler yet, but its future pilot configuration has an immutable one-item allowlist: `qdarte.cluster_research.pilot` on queue `qdarte_pilot`; it cannot inherit or widen the broad legacy worker allowlist. Focused API/worker tests, Ruff, format, and MyPy pass. The direct contact-verify queue and copied `/ops/taskq`/`/worker/taskq` surface are untouched; no pilot database, IAM, public producer, or worker started. P2 only may create/provision `qdarte_pilot_dev`.

- [x] **S5-QD-P2 · QDarte isolated pilot provisioning** — created only the disposable local `qdarte_pilot_dev` database on guarded PG18.4; immutable package migrations 0001–0005 and two `verify()` runs passed. A one-queue `qdarte_pilot` profile was created through a distinct local operator login. The facade login has only producer/runner/observer/housekeeper membership and independently failed `SET ROLE taskq_operator`, `ensure_queue`, direct job reads, and role creation. QDarte's real service-token signer/verifier proved exact `read`/`run` scope behavior; its `outlabs_auth` schema was read only, with a byte-identical pre/post canonical digest (`1e6d6523…139ff878`) and no catalog record added. No worker, facade boot, public producer, incumbent queue/schema access, or production action occurred.

- [x] **S5-QD-P3 · QDarte deterministic pilot adapter** — factored the incumbent calculation into a side-effect-free `compute_cluster_research_scope(payload)` and registered only `qdarte.cluster_research.pilot` in a closed package registry. The frozen AR synthetic input rejects every alternate shape; its bounded taskq-only output has no payload echo/followups and pins the inherited-result digest `14b7f6ef…63d4971`. Focused source tests prove it remains absent from QDarte’s legacy handler map and default `JobType` set. No worker, producer, HTTP client, database connection, queue claim, legacy enqueue, QDarte domain write, or shared-registry mutation occurred.

- [x] **S5-QD-P4 · QDarte isolated worker canary** — a3’s conforming omitted-nullable projection exposed a client decode defect, corrected only in immutable exact-a3-baseline release `v0.1.0a3.post1` (`sha256:bbf5c1fa…6764aecf`); the broader read-model `v0.1.0a4` remains deliberately unadopted. Both QDarte components exact-repinned the post release. The local-only harness issued distinct environment-only one-day `enqueue`/`read`/`run` self-contained credentials, proving cross-action and wrong-queue denial; the run credential only bootstrapped metadata and drove fixed worker `qdarte-pilot-p4-002`. Key `qdarte-pilot:p4-canary-20260721-002` returned `created` then `existed` for `019f8651-966e-7492-8a0e-5668defb33b5`; canonical authorized `read` reached `succeeded`, with exactly one succeeded raw attempt, three events, and zero failures/releases/expiry. Ordered full-row digests of six `qdarte_ops` legacy-ledger tables were byte-identical before/after. The local facade/worker were stopped cleanly and their command lines contained no token. P5 later closed replay/hard-kill recovery; no public producer, broad worker, or side-effecting lane is authorized.

- [x] **S5-QD-P5 · QDarte isolated recovery and rollback** — local evidence proves both recovery obligations through the real mounted facade in disposable `qdarte_pilot_dev`: a committed-response-loss drill replayed the original settlement (`handler_calls=1`, `complete_calls=2`) and reached one successful attempt; then a held pure handler was frozen for six seconds past its five-second soft-stop grace and force-killed without a release. Its actual 15-second lease expired, after which normal poll-driven micro-reap reclaimed the same job id under a second closed worker and reached `succeeded` with two conserved attempts (`expired/lease_expired`, then `succeeded/success`) and events `enqueued=1, claimed=2, lease_expired=1, succeeded=1`. P4's full-row digest of all six protected `qdarte_ops` tables remains byte-identical, the local pilot facade/workers were stopped with no remaining active facade connection, and the isolated API/Postgres/Redis/MinIO stack stayed healthy. The self-contained auth tokens remained process-only; the immediate canonical auth digest was unchanged across teardown. This pure-lane hard kill does not waive a future side-effecting-lane hard-kill gate. Host evidence: `qdarteAPI/docs/taskq-pilot-p5-local-evidence.md`. P5 authorizes no incumbent direct-queue change, cloud/Mac-mini/production target, or broad worker start.

- [x] **S5-QD-CONSOLIDATION-SPEC · QDarte direct-queue convergence proposal frozen** — current QDarte source and read-only local catalog inspection establish that the host-owned direct contact-verify catalog cannot share a database with immutable package `taskq`; it has a public execute grant and no current local jobs. The new Tier-3 consolidation proposal selects a future one-way package migration through a separate package database, keeps direct contact verification authoritative now, bans dual publishing/active-row import/cross-backend fallback, and treats result application plus probe usage as an independently idempotent side-effect boundary. It defines C1–C7 compatibility, preflight, effect, hard-kill, rollback, and production gates. No QDarte source, DB, IAM, worker, route, queue state, deployment, or production behavior changed.

- [x] **S5-QD-CONSOLIDATION-REVIEW · Targeted direct-queue decision review** — Round 12 reconstructed the direct catalog/routes/worker/result path, challenged separate-database and mode-exclusivity claims, and found three docs-first preconditions. The immutable response and targeted delta are recorded: the server-owned result bridge, exact direct-catalog inventory, and effective-base-path matrix close them. READY opens a separate implementation specification only; it authorizes no current QDarte source, database, IAM, worker, route, deployment, provider, retirement, cloud, or production change.

- [x] **S5-QD-CONSOLIDATION-R12-REMEDIATION · Round-12 docs-first closure** — recorded the immutable Round-12 response verbatim and corrected the Tier-3 proposal without changing QDarte source, local databases, IAM, workers, routes, deployments, or production. The incumbent inventory now names thirteen functions and its measured source/live role discrepancy; C1 freezes direct-origin versus `/content-api` proxy joined paths plus authenticated claim/result vectors; and §5.1 fixes the server-owned runner-heartbeat plus observer-projection result bridge, stable job/entity effects, and lost/reclaimed-attempt behavior. Targeted delta acceptance is still required before implementation planning.

- [x] **S5-QD-CONSOLIDATION-IMPLEMENTATION-SPEC · Isolated-local contact-verify sequence frozen** — following Round-12 delta acceptance, added the Tier-3 CV-01..CV-05 sequence: compatibility/base-path evidence first; stable server-owned result bridge and host idempotency before package admission; disposable least-privilege package preflight; one closed-worker controlled effect canary; then response-loss/hard-kill recovery and local rollback. Each slice has an explicit stop condition; no QDarte source, database, IAM, worker, route, deployment, provider, direct-queue, retirement, cloud, or production change occurs in this docs-only task. CV-01 is next.

- [x] **S5-QD-CV-01 · Direct contact compatibility and base-path evidence** — source-backed C1 closes the direct-worker URL ambiguity without starting a worker or touching a database: QDarte worker commit `433b447` proves credential-bearing claim and contact-result requests for both `http://<api-origin>/worker/taskq/...` and `http://<admin-origin>/content-api/worker/taskq/...`; its full suite is 567/567 with Ruff clean. QDarte API’s `/worker/*` permission routing remains covered by 27 focused allowlist vectors, and the existing direct catalog/role/grant/high-water inventory remains the read-only Round-12 record. No QDarte queue, auth record, route, deployment, provider, or production state changed. CV-02 may implement host-side stable result idempotency.

- [x] **S5-QD-CV-02 · Stable package result bridge and domain ledger** — QDarte API commit `d883371` adds the additive `qdarte_ops.contact_verify_result_applications` ledger keyed by `(job_id, entity_key)`, so reservation, place/contact writes, and monthly usage consumption share one transaction and a reclaimed attempt cannot reapply the effect. Its fresh disposable full migration chain creates the exact primary key/index and is dropped afterward. The new server-owned runner/observer bridge heartbeats before authoritative payload validation, rejects lost/cancelled/wrong-queue/unplanned results before a domain write, and never settles for the worker. Focused tests are 41/41, Ruff is clean, and changed-file MyPy is clean; the unconfigured repository-wide MyPy invocation still reports unrelated baseline debt. Nothing is mounted, provisioned, or enabled; CV-03 alone may bind this component to the new disposable package runtime.

- [x] **S5-QD-CV-03-DESIGN · Contact preflight topology frozen** — CV-03 now fixes the disposable package database as `qdarte_contact_verify_dev`, the one package queue/type as `qdarte_contact_verify` / `qdarte.contact_verify.scope`, and the local-only result adapter as `POST /internal/taskq/contact-verify/jobs/{job_id}/results`. The normal QDarte application never mounts `/taskq` or a generic package producer route; only the checked-in local harness has the package facade, and it has no enqueue credential before CV-04. These names must not enter the incumbent direct client, worker map, or copied `/worker/taskq/*` API. This docs-first boundary authorizes only the CV-03 source/preflight work that follows.

- [x] **S5-QD-CV-03 · Isolated contact package preflight** — QDarte API commits `1b01f24` and `d19b7dd` add only the disabled local contact harness and its evidence record. The harness accepts solely a development `postgresql+asyncpg` DSN for disposable `qdarte_contact_verify_dev`, rejects the incumbent/pilot databases, superuser, non-development, and simultaneous-pilot configurations, and leaves the normal QDarte application without `/taskq` or a package result route. On guarded PG18.4 (`max_connections=100`), immutable a3.post1 migrations `0001`–`0005` and two owner `verify()` passes succeeded; the distinct operator provisioned only `qdarte_contact_verify`. The dedicated facade login has producer/runner/observer/housekeeper roles and proved denial of operator assumption/administration, base-job reads, role/database creation, and RLS bypass; the real harness lifecycle used one package connection against usable budget 80 (headroom 79) and closed it cleanly. The queue has zero jobs, attempts, workers, and events. Focused host tests are 26/26 with Ruff/format and changed-file MyPy clean. No worker, enqueue credential, provider, direct-queue, deployment, or production action occurred. A harmless stray empty profile was accidentally added to retained `qdarte_pilot_dev` during signature inspection and is explicitly documented for separately authorized cleanup; it did not touch the contact or direct databases. CV-04 alone may issue an ephemeral local harness enqueue credential and start the closed contact worker.

- [x] **S5-QD-CV-04A · Trusted effect reporter kernel** — ADR-022’s worker extension is now implemented without exposing a fence through `JobContext`: a handler gets only bounded async `report_effect()` (8KB JSON object), while the optional runtime-owned reporter alone receives an immutable active-attempt record. The supervisor rejects reports after cancellation/ownership loss/settlement, replays the identical report on retryable response loss under its existing bounded backoff, and remains the sole terminal-settlement owner. Deterministic regressions cover absent-reporter/cancellation bounds, handler fence absence, exact active-attempt identity, response-loss replay, ownership loss, and normal completion (27 focused worker tests; 304 DB-free tests, Ruff, and format clean). This is Python-only: no SQL, migration, wire, facade, client, or package runtime credential changed. CV-04 still needs a closed QDarte reporter/worker and controlled local canary.

- [x] **S5-QD-CV-04B-DESIGN · Reporter-side effect sequence frozen** — ADR-022’s QDarte use is now precise: the existing private local result path accepts only reporter-owned `inspect` (current-attempt plus authoritative-plan validation → `pending` or stable committed domain result) and `apply` (the same validation → one idempotent application). A closed handler asks `inspect` before any provider call and skips an already committed result; the trusted reporter, never the handler, supplies/retries the active attempt and never settles. This is a Tier-3 local-harness clarification only: no public route, producer, direct-worker reuse, database credential, wire-contract, SQL, migration, or current package/database state changes.

- [x] **S5-QD-CV-04C · Trusted-reporter pre-release** — immutable `v0.1.0a5` targets `4652cdf` and publishes wheel SHA-256 `a667bf53aefc743c6fdaf9aaaa9509a590276d6d812d1dea9c34999268d57d49` (sdist `f5ac7822…94ec6d32`). It contains the ADR-022 reporter kernel and no SQL-contract, migration, Protocol-v1, facade-route, or generated-client change; its installed core artifact smoke passes outside the checkout. QDarte must exact-pin this wheel before it configures the closed contact reporter/worker; no local package contact database migration or canary is implied by this release.

- [x] **S5-QD-CV-04D · QDarte reporter result adapter** — QDarte API commit `6b9e263` turns the one private local result path into the frozen closed reporter union: `inspect` reuses runner-heartbeat and authoritative payload/entity validation before it reads the stable domain-effect ledger, returning only `pending` or its bounded committed response; `apply` repeats validation before the existing idempotent application. A stale/lost attempt cannot read the ledger, the handler never selects queue/type/place authority, and neither operation settles a package job. Focused bridge/harness/idempotency tests are 20/20 with Ruff clean. The ordinary app remains unmounted; no worker, enqueue credential, package database mutation, direct queue, provider, deployment, or production state changed. The next CV-04 increment exact-pins a5 and adds the one closed HTTP reporter/worker.

- [x] **S5-QD-CV-04E · Closed contact package worker** — QDarte workers commit `605828c` adds a separate async package registry containing only `qdarte.contact_verify.scope` on `qdarte_contact_verify`. Its loopback-only reporter holds the process-local run token, sends only the frozen private `inspect`/`apply` requests, and overwrites nested/top-level attempt identity from the runtime-owned `WorkerEffectAttempt`; the handler cannot supply a fence or use a database/direct-worker client. It probes the stable effect before a provider call and skips a committed result. Focused worker tests (16/16) prove the closed registry, loopback/token guard, and reporter identity binding. No process was started, no enqueue credential was issued, and no provider/direct queue/database/deployment/production action occurred. CV-04 now needs its explicit service configuration and single controlled local canary.

- [x] **S5-QD-CV-04F · Controlled local contact canary** — QDarte API commits `de25515` and `77f6d82` close the safe first-canary blocker: the local normal-QDarte DSN is explicit for the harness, checked-in migration `20260721_0076` supplies the CV-02 result ledger, and the private reporter route now binds its run authorization as a real FastAPI dependency rather than an accidental required query parameter (19/19 focused DB-free boundary/token/bridge tests, Ruff, and format clean). A final loopback-only worker with its fixed one package queue/type proved cross-action and wrong-queue 403s, then keyed `qdarte-contact:cv04-canary-20260721-007` converged `created` → `existed` and an authorized canonical read reached `succeeded`. Raw package state has one succeeded attempt and zero failure/release/expiry events; the QDarte stable-effect oracle has exactly one application, contact-method, and probe-usage effect. All incumbent direct `taskq` tables remain zero-row, and protected legacy-table counts/latest-write bounds remain unchanged from the pre-canary baseline. Harness and worker stopped; failed diagnostics remain auditable package history. Evidence: `qdarteAPI/docs/taskq-contact-cv04-local-evidence.md`. CV-05 alone may run the separately bounded response-loss and hard-kill recovery drills; no production, broad worker, retirement, or non-contact action is authorized.

- [x] **S5-QD-CV-05 · Contact side-effect recovery and local rollback** — QDarte workers commit `abeaac1` adds a local-only recovery harness over the same closed contact registry: it drops only the first post-commit `apply` response, or holds only after a committed apply for an explicit force-kill. Focused recovery/contact tests are 10/10 with Ruff and format clean. In guarded local `qdarte-dev`, the response-loss job replayed exactly two identical apply reports yet finished with one succeeded attempt and one stable effect row. A separate held job was hard-killed after apply commit; its real lease expired, a different closed worker reclaimed the same ID, `inspect` returned the durable effect, and the final result recorded `replayed_entities=1`, `completed_entities=0`. Its raw attempts are `expired/lease_expired` then `succeeded/success`, with exactly one effect row and no release. The current probe usage counter is three across CV-04/CV-05’s three controlled provider calls, and every incumbent direct `taskq` table remains zero-row. Harness/workers stopped; rollback remains zero-DML and never recreates a direct job. Evidence: `qdarteAPI/docs/taskq-contact-cv05-local-evidence.md`. This completes isolated-local CV-01..CV-05 evidence only; the direct lane remains authoritative and C6/C7, production, retirement, broad workers, and non-contact work remain separately gated.

- [x] **S5-QD-C6-SPEC · Contact compatibility and cutover sequence frozen** — added the Tier-3 [C6/C7 Compatibility and Cutover Specification](docs/Task%20Queue%20Stage%205%20QDarte%20Contact%20Verify%20Compatibility%20and%20Cutover%20Specification.md) after CV-05. It sequences C6-00 inventory, closed `legacy`/`draining`/`package` modes, direct-drain/package-admission interlock, caller-compatible scoped adapter, and three no-row-copy rollback exercises into a targeted C6 acceptance. It also boards C7’s later environment/preflight/cohort/two-cycle/audit gates, each still separately authorized. This docs-only task changes no QDarte source, direct or package database, IAM, route, worker, provider, deployment, production configuration, or retirement behavior. C6-00 alone is next.

- [x] **S5-QD-C6-00 · Direct contact compatibility ledger and high-water baseline** — the Tier-3 [Compatibility Ledger](docs/Task%20Queue%20Stage%205%20QDarte%20Contact%20Verify%20Compatibility%20Ledger.md) records the exact QDarte API/worker source revisions, router authorization, request/response shapes, direct worker/result paths, and the current route-level compatibility delta: `/ops/cutover/...` still selects the incumbent legacy versus host-owned direct-taskq backend at request time, so it cannot be silently carried into the frozen closed `legacy`/`draining`/`package` model. A guarded read-only `qdarte-dev` observation confirmed the durable database identity is development and the direct `contact_verify_scope` lane has zero jobs, active rows, attempts, and events. No QDarte source, queue row, database, credential, worker, provider, deployment, or production state changed. C6-01 alone may design the closed-mode/no-fallback local implementation.

- [x] **S5-QD-C6-01-DESIGN · Contact package mode boundary frozen** — C6-01 now assigns package contact selection to one new exact `QDARTE_CONTACT_VERIFY_MODE` setting, defaulting only to `legacy` and accepting only `legacy`/`draining`/`package`; it cannot read or reinterpret the incumbent `QDARTE_TASKQ_*` staging switch. Invalid/mixed values fail startup, `draining` is a fixed safe refusal, and `package` remains unavailable until the later drain-attestation and scoped-adapter slices exist. The legacy `/ops/cutover` route discriminator is explicitly owned by C6-03 rather than preserved accidentally. This docs-only decision changes no QDarte source, queue state, credential, worker, package admission, provider, deployment, or production behavior. C6-01 implementation may now add the parser and no-producer refusal vectors locally.

- [x] **S5-QD-C6-01 · Closed local contact mode boundary** — QDarte API commit `1379f3f` installs the exact contact-only mode parser and validates it before authentication initializes: `legacy` is the sole default; malformed/mixed values reject boot; `draining` cannot combine with the incumbent contact selector and returns only the fixed host-owned 503 before either producer is constructed; and grammar-valid `package` is deliberately rejected at startup until C6-02 drain evidence and C6-03’s scoped adapter exist. The `/ops/cutover/...` contact route now ignores the incumbent `QDARTE_TASKQ_*` selector and dispatches direct legacy only in `legacy`, eliminating request-time fallback to the host-owned direct-taskq backend. Focused config/route/lifespan vectors are 41/41 with changed-file Ruff/format and MyPy clean; the broader taskq-related host suite is 199/199 with 12 pre-existing opt-in skips when its documented test-only token variables are present. An ad-hoc full host run still has unrelated baseline environment/migration/media-root and repository-wide formatter drift, recorded without changing them. No local service was started, and no QDarte database, queue row, package admission, worker, provider, deployment, credential, or production state changed. C6-02 alone may implement the fresh direct-drain/package-admission interlock.

- [x] **S5-QD-C6-02-DESIGN · Direct-drain interlock mechanics frozen** — C6-02 uses no mutable flag, queue-table write, or reusable evidence file: a process-owned opaque attestation is issued only in `draining` mode for one named local exercise, verified development database identity, and source revision. It requires two direct-only observations 1–60 seconds apart with no active/leased contact work and equal job/attempt/event counts and high-waters; every later package admission must re-observe the same posture and expires within five minutes. Restart, tampering, mode/identity change, or any direct insert invalidates and evicts it. No QDarte source, database, queue, worker, provider, deployment, credential, or production state changes in this docs-first decision. C6-02 implementation may add only the local direct-ledger observer and refusal vectors; package admission remains unavailable until C6-03.

- [x] **S5-QD-C6-02 · Direct drain and package-admission interlock** — QDarte API commit `145ca1a` adds a route-free, write-free observer over only the incumbent `qdarte_ops` contact job/attempt/event ledger and a process-owned opaque attestation registry. It issues only in explicit `draining` mode after two stable bounded observations, is bound to one development database identity/exercise/source revision, expires within five minutes, vanishes on restart, and re-observes before every future package admission. The six new vectors reject active work, bad bounds, forged/expired records, cross-mode use, a direct insertion during observation, and a direct insertion after issuance; the query assertion proves no package table or payload read. Focused C6/config/route/lifespan vectors are 48/48 with Ruff, format, and changed-file MyPy clean under the documented unreachable integration DSN. No service was started, no direct producer was disabled in a lasting environment, no package job/route/database write/worker/provider/deployment/credential/production action occurred. C6-03 may use only the resolved same-process lifecycle; it must not weaken the process-owned proof or add a fallback.

- [x] **S5-QD-C6-03A · Canonical admission boundary and local package controller** — QDarte API commit `c0940fb` replaces the retiring `/ops/cutover/jobs/contact-verify-scope` backend discriminator with the frozen opaque canonical admission (`job_id`, `created | existed`, supplied-or-derived key, bounded planned count). Its direct path derives the same key before admission and refuses a differently keyed active scope rather than disguising coalescing. A configured development-only package process validates loopback/token/exercise/revision inputs, begins internally in draining, earns the C6-02 proof in its own lifespan, and re-observes before each one-way keyed HTTP admission to the isolated contact facade. Package failure maps to the fixed host 503 and never constructs the direct producer; `/ops/taskq/*` and `/worker/taskq/*` remain untouched. Focused config/route/drain/controller/adapter vectors are 57/57 with Ruff, format, and changed-file MyPy clean under the documented unreachable integration DSN. No service, database, queue, worker, provider, credential, deployment, or production action occurred. C6-03 still requires its isolated local created/existed and raw-ledger evidence before C6-04 can open.

- [x] **S5-RM-DESIGN · H-08/H-11 read-model activation proposal prepared** — added the Tier-3 [Read Model Specification](docs/Task%20Queue%20Read%20Model%20Specification.md): queue-scoped finite `ready|running|finished` keyset pages; fixed safe job projection; observer-safe queue profile; and a real version/ETag conditional-update path that preserves bootstrap `ensure_queue`. It names the docs-first ADR/Protocol/Manifest/migration sequence plus PG16/PG18 B9, SQL/HTTP parity, redaction, authorization, pagination, and conflict evidence. It changes no current contract, SQL, host, UI, producer, consumer, or L1 observation behavior; both deferred routes remain `TQ501` pending ADR acceptance.

- [x] **S5-RM-ADR · H-08/H-11 contract reactivation accepted docs-first** — ADR-019 accepts Protocol v1 document revision 1.0.5, Function Manifest / SQL contract 0.1.3, and migration `0004_read_models.sql` identity before implementation. It fixes the 13-field queue-scoped job page, three independently gated views with explicit `TQ501` fallback, observer-safe versioned queue profile, ETag/`If-Match` matrix, `TQ409 profile_version_conflict` carrying only `current_version`, and direct-SQL/HTTP projection parity. R5-29 is closed by this package. No SQL, migration, generated client, facade, host, or L1 observation behavior changes in this docs-only task.

- [x] **S5-RM-01A · SQL-contract bridge runtime** — ADR-020's closed membership check replaces the sole exact startup pin: `TaskqRuntime.start()` accepts only `0.1.2` or `0.1.3`, while a preserved simulated pre-bridge `{0.1.2}` pin rejects `0.1.3` with the same typed version error/details. The bridge adds no read-model capability, generated command, facade route, client call, or 0004 function call; CLI/verify, metadata clients, and host preflight retain their exact reporting/verification roles. No migration or production database action occurred.

- [x] **S5-RM-01B · Read-model migration and catalog parity** — immutable `0004_read_models.sql` adds `profile_version`, the four manifest composites, hardened observer `list_jobs`/`get_queue_profile`, and operator `update_queue_profile`; all H-08 capabilities remain inactive and no B9 index is introduced. The machine manifest, `verify()`, parity/grant/error ledgers, fresh install, and full 0001→0004 upgrade chain now assert the 16-column queue shape, 43-function catalog, 0.1.3 metadata, grants, and exact inactive `TQ501` disposition. Fresh direct-SQL vectors prove profile create/unchanged/update/conflict and no observer base-table read. Full suites pass on PG18.3 and a disposable exact PG16.14 container (457 passed, 1 opt-in skip each); production migration, generated HTTP/client work, and B9 activation remain out of scope.

- [x] **S5-RM-02A · Generated SQL read-model transport** — added the H-08/H-11 typed domain models and the three manifest-backed commands to the same generated SQL transport ledger: observer `list_jobs`/`get_queue_profile` and operator `update_queue_profile`. The SQL transport remains capability-sized and decodes only the fixed composites; the closed registry and observer capability-surface oracle now pin all 33 commands and Protocol 1.0.6. No HTTP route, official HTTP-client method, capability activation, index, production migration, or B9 claim occurs in this increment.

- [x] **S5-RM-02B · Read-model facade, HTTP clients, parity, B9, and ready activation** — Protocol 1.0.7’s generated `GET /queues/{queue}` and `GET /jobs?queue=&view=` identities mount in the facade and both official clients. The dispatcher authenticates then queue-authorizes the query queue before cursor decoding; queue profile GET stays flat with ETag while conditional PUT returns only the canonical `{"profile": {...}}` envelope plus ETag. The independent live parity vector runs the `ready` page through direct SQL and mounted ASGI HTTP, then checks every projection field against `taskq.jobs`. Immutable metadata-only 0006, justified by B9 evidence `7fe2c6b`, activates exactly `read_model_list_ready`; its 0005→0006 transition and exact `verify()` posture run on PostgreSQL 16/18. `running` and `finished` remain structurally rejected by B9. Stop for targeted review before host adoption.

- [x] **S5-RM-REVIEW-REQUEST · Targeted read-model review gate assembled** — Round 9 pins the complete `7826cbc..c1fac41` range and demands independent contract/catalog derivation, 0004→0006 immutability and upgrade evidence, exact ready-only capability verification, SQL/HTTP/auth/cursor/redaction parity, profile ETag conformance, and B9 plan evidence on both PostgreSQL majors. The request authorizes neither host adoption nor production migration; its immutable response decides whether a separately specified adoption slice may open.

- [x] **S5-RM-REVIEW-RESPONSE · Round 9 recorded BLOCKED** — immutable Tier-4 response identifies R9-01..05: conditional PUT unknown-queue conformance, stale opt-in B9 assertion, formatter/CI publication, missing wire vectors, and inactive-view details. No contract question or architectural redesign is required; a narrow remediation range followed by targeted delta review remains the only path to a later host-adoption decision.

- [x] **S5-RM-R9-01-05 · Read-model error conformance** — conditional profile PUT now maps the SQL NULL composite for an authorized missing queue to typed `TQ001`/404 instead of crashing while decoding `current_version`; the generated SQL/protocol error ledgers record the existing Tier-0 missing-queue outcome. Inactive list views retain typed `TQ501` and the facade supplies only the contracted safe `reason` + requested `view` details, never SQL text. Direct transport and mounted-wire regressions pin both paths.

- [x] **S5-RM-R9-02 · B9 post-activation gate corrected** — the opt-in million-row plan gate now asserts the immutable 0006 post-state exactly (`read_model_list_ready` only), alongside its existing ready-plan and rejected-view structural assertions. It no longer freezes the superseded all-inactive metadata posture.

- [x] **S5-RM-R9-04 · Read-model wire evidence completed** — mounted-facade vectors cover list success and cursor pagination, malformed/foreign/oversized/duplicate cursor/query rejection, stale `If-Match` TQ409 with current-version-only details, weak ETag rejection, and the version-bearing canonical PUT envelope. The official async client decodes that published `{"profile": {...}}` shape with `profile_version` intact, satisfying S5-CQ-02’s standing compatibility condition.

- [x] **S5-RM-R9-03A · Formatter drift repaired** — applied the repository-pinned Ruff formatter to the two range-owned files identified by Round 9 (`http/facade.py` and `test_s3_facade.py`); `ruff format --check .` and `ruff check .` are clean. Publication/CI remains coupled to the final remediation range after all evidence gates pass.

- [x] **S5-RM-R9-03B · Artifact migration ledger repaired** — the installed-wheel/sdist smoke script now asserts the complete immutable 0001→0006 migration chain and the current 43-function catalog rather than the historical 0001→0003 / 40-function state. Fresh core, HTTP, and OutLabs artifact installs pass outside the checkout; the corrected range is republished for CI.

- [x] **S5-RM-R9-DELTA-REQUEST · Targeted remediation review assembled** — pins the published `8b1547a..1610b5a` delta and the immutable Round-9 response hash. It requires only R9-01/02/03/04/05 evidence: missing-queue PUT, safe inactive-view details, post-0006 B9 state on PostgreSQL 16/18, full mounted-wire/client vectors, and published CI/artifact/formatter proof. It cannot authorize host adoption, production migration, further activation, UI work, retirement, or Stage 5.

- [x] **S5-RM-R9-DELTA-RESPONSE · Read-model slice independently accepted** — immutable delta response accepts all R9 remediation: typed unknown-queue profile PUT, safe inactive-view details, exact post-0006 B9 state, mounted wire/client regressions, formatting, artifact ledger, and published CI. Its dual-PG full-chain rerun records 469 passed with 1 opt-in skip on each major and zero Contract questions. Only a future separately specified host-adoption decision for the already-active `ready` view may open; production 0004→0006 migration, further activation, UI, retirement, and Stage 5 remain closed.

- [x] **S5-RM-HOST-00 · First-host read-model adoption frozen** — the new Tier-3 plan resolves the a2→0.1.4 deployment discontinuity without weakening ADR-020: immutable route-free bridge artifact `a3` deploys before 0004→0006, becomes the post-migration zero-DML rollback floor, and only then may an immutable full `a4` expose generated `tools` profile/`ready` GET routes. It forbids host-owned read paths, operator/profile-write exposure, production pagination injection, manual metadata DML, and any impact on L1 retirement observation. Round 10 must independently attack the artifact ordering, privilege boundary, rollback rehearsals, and read-only authorization vectors before package, host, or database work begins.

- [x] **S5-RM-HOST-REVIEW-REQUEST · First-host adoption gate assembled** — Round 10 may review only the frozen deployment specification. Its response cannot authorize a release, pin, deployment, production migration, queue/IAM mutation, producer/consumer behavior, UI, retirement, side-effecting lane, or Stage-5 pilot.

- [x] **S5-RM-HOST-REVIEW-RESPONSE · Round 10 recorded BLOCKED** — immutable response accepts the two-artifact architecture, rollback floor, credential/host boundary, and read-only oracle design, but finds the Step-C artifact mismatch: a3 (`40aa9b5`) cannot apply or verify 0006. It requires a docs-only resequencing (a3 applies/validates 0004–0005; a4 applies/validates 0006), an exact a4 base identity, one pre-C backup test-restore, and precise deferred-route evidence. A targeted delta review is the only path to READY; no release, host, database, or production action is authorized.

- [x] **S5-RM-HOST-R10 · Round-10 adoption-plan remediation** — Step C now uses a3 only for immutable 0004→0005, verifies exact 0.1.4 / empty capabilities twice, and requires a backup test-restore to a disposable target. Step D pins a4 to accepted `1610b5a`, applies/verifies immutable 0006 under that exact artifact, then exposes generated `ready` routes; post-D rollback is a4→a3 with the bridge’s typed TQ501 responders. a3 evidence now asserts DEFERRED TQ501, OpenAPI absence, and client-method absence rather than an imprecise “no route.” No artifact, host, database, or production mutation occurred; targeted delta review remains required.

- [x] **S5-RM-HOST-R10-DELTA-REQUEST · Targeted adoption-plan delta assembled** — pins only the Round-10 remediation range and immutable response hash. It must prove a3’s 0004→0005/empty-capability verifier posture, a4’s `1610b5a` provenance and 0006/ready-active posture, the precise deferred bridge response and zero-DML rollback, and the one-time pre-C backup test-restore claim. It cannot authorize any package, host, database, production, UI, retirement, side-effecting, or Stage-5 action.

- [x] **S5-RM-HOST-R10-DELTA-RESPONSE · First-host A→E sequence READY** — immutable targeted response accepts the corrected two-artifact sequence with zero Contract questions: a3 verifies only 0004→0005 at 0.1.4/empty capabilities and exposes only deferred TQ501 responders; a4 is pinned to `1610b5a`, alone verifies 0006 at ready-active metadata, and becomes the first route-exposure artifact. READY authorizes only the frozen A→E sequence and its per-step evidence; `running`/`finished`, UI, retirement, side-effecting lanes, and Stage 5 remain closed.

- [x] **S5-RM-HOST-A3 · Route-deferred bridge published** — immutable `v0.1.0a3` targets release commit `899defc` on the isolated `codex/read-model-a3-bridge` branch, based on frozen source `40aa9b5` with only package-version metadata plus this task record. The public wheel SHA-256 is `2b01b056c234548afe59fc34bad1d95eb591795f0cae0b4a724ffba4113b4209` and sdist SHA-256 is `5756e71d6b3f70e2e66bb22ffe6f3606676f245a07b6d1a83306346e74ce1cfe`; both were re-downloaded and matched. Fresh/full 0001→0005 chain vectors passed on PG18.3 and exact PG16.14, the installed wheel proved typed deferred H-08/H-11 metadata with no official client methods, and no 0006 migration is present. The historical a3 source has one unrelated formatter drift under the current Ruff formatter (`tests/test_contract_0_1_3.py`); lint is clean and the frozen source was not reformatted to preserve its exact bridge identity. This authorizes only the host a3 pin and local validation before the separately evidenced production bridge deployment.

- [x] **S4-POST-F01 · Coolify build-secret containment** — every configured API (39) and worker (21) variable is runtime-only in Coolify, so no runtime secret is available during image build; the Dockerfiles contain no secret `ARG` instruction. The restricted runtime PostgreSQL login was re-proven, the runtime DB credential plus host auth-signing and documentation secrets were rotated, API rollout health/public health passed, and the worker replacement started without a recorded deployment failure. The new deployment transcripts contain neither an affected environment-variable name nor a Docker build-argument record; deployment-log access remains restricted. Host evidence is recorded in `outlabsAPI` as `docs/taskq-s4-post-f01-build-secret-remediation.md`. The owner explicitly accepted deferral of Redis, Umami, Telegram, and unused `TOOLS_API_KEY` cleanup for this low-value host; the record makes no claim those older credentials are invalidated. No taskq SQL, wire, capability, or application-source change occurred.

- [ ] **S4-POST-F02 · Deferred low-value-host credential cleanup** — owner-accepted residual from F01: rotate Redis, Umami, and Telegram credentials and remove the unused `TOOLS_API_KEY` configuration. This is nonblocking for the current tools lane, but must be re-evaluated before a host expansion or new side-effecting lane treats the historical build exposure as fully remediated.

- [x] **ADR-018 · Operator UI tech stack locked** — React + Vite + TypeScript + TanStack Router/Query/Table + Base UI (OutlabsAuthUI / qdarte-admin family); Bun + Cloudflare static deploy; standalone app first, embeddable mount later; Nuxt stays docs-only. Does not accept Growth §4/§5 endpoint designs — console waits on read-model ADR/H-11.

*(subsequent stages remain sequenced by the Build Plan)*

## Contract questions (STOP-and-record before coding around)

### S5-QD-C6-CQ-01 — Static closed modes cannot consume a process-owned drain attestation *(open)*

**Blocking evidence:** C6-01 freezes `QDARTE_CONTACT_VERIFY_MODE` as a
startup-validated `legacy | draining | package` selector. C6-02 correctly
issues its opaque direct-drain attestation only while the selected mode is
`draining`; it correctly removes that attestation on restart or mode change.
C6-03 would need the selected mode to become `package` before it can admit its
first package job. With the current static configuration that transition
requires a restart, which intentionally erases the only valid attestation.
Therefore no process can satisfy both preconditions: the package path is
unreachable without weakening the proof, persisting/hand-editing it, or
inventing a fallback.

**Recommended adjudication:** amend the Tier-3 C6 mode semantics docs-first:
`legacy` and explicit `draining` remain steady states, while a configured
`package` process starts internally in a non-serving draining posture, performs
the complete C6-02 direct observation in that same process, and atomically
opens its package selector only after the in-memory attestation is issued.
Every restart repeats the drain before serving; failure leaves no package
producer callable and fails startup or remains a fixed draining refusal. The
mode is still sampled once per request after this one startup transition, no
opaque record crosses a route/environment/database boundary, and no direct or
package fallback is introduced. Alternative: choose a different, explicitly
reviewed durable attestation mechanism. Do not start C6-03 until one of these
semantics is adopted docs-first.

**Resolution:** adopted the recommended same-process lifecycle transition.
`package` is a requested terminal posture: before FastAPI serves any request,
the process behaves internally as `draining`, disables the direct producer,
and performs the complete C6-02 observation. It atomically opens package
admission only while retaining that process-owned proof. A restart repeats the
observation; a failure exposes no package producer. Explicit `draining`
remains a fixed refusal, while no route/config/database record can forge or
preserve the transition. C6-03 may implement exactly this local lifecycle
controller and no alternative durable/fallback path.

### S5-QD-C6-CQ-02 — The existing cutover response and idempotency semantics are backend-specific *(resolved: canonical admission)*

**Blocking evidence:** `POST /ops/cutover/jobs/contact-verify-scope` currently
returns `ContactVerifyCutoverEnqueueResponse(route, legacy_job | taskq_job)`.
The legacy producer returns an incumbent `WorkerJobDetail`, checks an explicit
idempotency key only when supplied, and also has an active-scope coalescing
path. The incumbent direct taskq producer instead derives
`contact_verify_scope:<scope_kind>:<scope_key>` when the caller omits a key and
returns a typed `created` disposition, queue/type, key, and planned count. The
package producer has neither an honest legacy-job projection nor an authority
to masquerade as the host-owned direct taskq catalog. Retaining one of those
shapes silently would either expose a fake backend, alter deduplication, or
break callers that branch on the discriminator.

**Recommended adjudication:** make the existing authorized cutover URL's
package-era response deliberately backend-neutral: a bounded canonical
admission result (`job_id`, `created | existed`, canonical idempotency key, and
planned entity count), with no route/queue/job-type projection. Freeze one
canonical key rule for both modes before package admission; migrate or retire
any discriminator-dependent caller as a C6-03 acceptance row. The old
backend-specific `/ops/taskq/*` and `/worker/taskq/*` paths remain incumbent
only and are not aliases or fallbacks. Alternative: approve a versioned public
API response. Do not implement C6-03's producer until the public shape and
key rule are chosen docs-first.

**Resolution:** adopted the recommended canonical admission. The existing
authorized cutover URL retains its request grammar but returns only opaque
`job_id`, `created | existed`, the canonical supplied-or-derived idempotency
key, and bounded planned entity count. Both modes derive the supplied key or
exactly `contact_verify_scope:<scope_kind>:<scope_key>` before admission;
there is no route discriminator, queue/type projection, backend impersonation,
or fallback after package ambiguity. Legacy active-scope coalescing that does
not share that canonical key is a typed host refusal until a later explicit
caller contract. C6-03 must migrate or retire discriminator callers, prove the
canonical response in both modes, and leave `/ops/taskq/*` and
`/worker/taskq/*` incumbent-only.

### S5-QD-C6-CQ-03 — A package keyed replay cannot depend on a fresh volatile direct plan *(resolved: ADR-023 queue-native admission)*

**Blocking evidence:** the local C6-03 exercise at QDarte API `c0940fb`
started only a loopback package facade and a package-mode caller API; no worker
or provider ran. The direct ledger was stable at five completed
`contact_verify_scope` jobs, five attempts, and twenty events. A keyed
`country:AR` request returned the frozen canonical `created` result and added
one queued package job with zero package attempts/events; the direct counts
and high-waters remained unchanged. The identical keyed replay then returned
host `422` before package enqueue. Source explains the counterexample:
`ContactVerifyPackageAdapter.admit()` rebuilds the volatile direct candidate
plan before it can call package `enqueue`, while the legacy canonical-admission
method checks its idempotency key before planning. If candidates, operator
quota, or ordering change between calls, the package adapter cannot reach its
authoritative keyed `existed` outcome. A cache, direct fallback, or a new
untracked host mapping would only hide the broken replay contract.

**Required adjudication:** decide a *durable atomic admission* primitive before
further C6 code. A lookup alone is not enough: `taskq.enqueue` currently
deduplicates only an active row, so a matching job can settle between lookup
and later publish. The contract-first queue-native option is a two-stage,
key-scoped reservation/admission protocol: reserve returns an existing opaque
job ID or a short-lived opaque admission handle; finishing that handle creates
exactly one package job from the computed payload; replay returns the same
existing job/handle without re-planning; expiry/cancellation and response-loss
semantics are explicit. It requires the normal Protocol/Manifest/SQL migration
sequence and a new bridge release if the package database floor moves.

The alternative is a separately specified durable host admission ledger that
owns the canonical request/key, payload snapshot, package job identity,
response-loss replay, retention, and eventual retirement. Neither an
in-memory cache nor a read-only lookup can provide the cross-process,
settlement-race guarantee. Do not change the canonical response,
re-plan-on-replay behavior, use direct queue lookup, add a fallback, start a
worker, or open C6-04 until one choice is made docs-first.

**Resolution:** adopted the queue-native option as a general library feature,
not a QDarte ledger or permanent wrapper. ADR-023, Protocol document revision
1.0.8, Manifest/SQL contract 0.1.5, and the Durable Admission Reservation
Specification freeze a durable `(queue, idempotency_key)` authority with a
pre-plan SHA-256 intent binding, retry-stable UUID handle, single planning
owner, atomic job+receipt finish, typed pending/expiry/cancellation, bounded
retention/cleanup, and bridge-first migration order. QDarte must call reserve
before planning, return an admitted receipt without replanning, and keep no
host mapping/cache or direct fallback. C6-04 remains closed until S5-AR-01/02
and S5-AR-AUDIT complete and the isolated C6-03 created/existed proof is rerun
against the accepted package release.

### S5-QD-CV-CQ-01 — A package contact-result bridge needs the active attempt, but the safe worker handler context intentionally withholds it *(resolved: ADR-022 trusted reporter)*

**Blocking evidence:** CV-02's server-owned bridge correctly requires the
package `job_id`, current `attempt_id`, and `worker_id` to heartbeat before it
will authorize a QDarte domain write. The existing package `WorkerService`
correctly constructs a fence-free `JobContext`: it exposes the job identity,
payload, headers, and cancellation state to a handler but never the active
attempt/fence. This is a deliberate Stage-2 safety contract, not an accidental
redaction. Therefore a normal closed registry handler cannot call the CV-02
result adapter. A raw HTTP claim loop could see the attempt, but it would make
QDarte reimplement worker supervision, cancellation, heartbeat, and
settlement-replay behavior that the package already owns.

**Decision adopted:** ADR-022 adds a runtime-owned trusted side-effect reporter
plus bounded async `JobContext.report_effect()`. The worker passes the current
attempt only to that configured reporter; user handlers never receive a fence.
The reporter does not settle, while `WorkerService` retains heartbeat,
cancellation, ownership-loss, unsafe-sync exit, and fixed-verb settlement
replay. QDarte must use the reporter to ask its stable-effect ledger before an
external probe and to apply the result afterwards. Do not expose an attempt or
fence through `JobContext`, weaken the bridge heartbeat, or add an ad-hoc
QDarte raw claim/settle loop. CV-04 may implement this package extension and
one closed local contact worker; CV-05 remains its response-loss/hard-kill
gate.

### S5-QD-CQ-05 — The run-only pilot worker cannot negotiate its mandatory HTTP metadata read *(resolved: metadata-bootstrap exception)*

**Blocking evidence:** the official `AsyncTaskqHttpClient` calls
`GET /taskq/v1/meta` before its first claim. Tier-0 Protocol v1 pins that
route to the `read` action, and the QDarte host adapter correctly maps it to
`taskq_qdarte_pilot:read`. The approved P4 worker token carries only
`taskq_qdarte_pilot:run`, so its startup receives a typed `AUTH403` before it
can write presence or claim. The isolated facade was then stopped; no job,
worker, QDarte auth row, or legacy-ledger mutation occurred.

**Decision adopted — metadata-bootstrap exception:** the Protocol command
identity remains `meta → read`; the QDarte pilot host adapter may authorize a
`taskq_qdarte_pilot:run` token for that deployment-scoped metadata negotiation
only. It must not translate `run` into a queue-scoped `read` grant: profile,
job detail, job pages, queue stats, and every other `read` command remain
denied to the worker. P4 must prove the positive metadata startup plus direct
negative job/profile reads under the run-only credential. It does not skip
compatibility negotiation, add a broader scope, or modify Tier-0 command
identity.

### S5-QD-CQ-04 — P4 requires a keyed harness producer but freezes no authorized producer identity *(resolved: local-only enqueue token)*

**Blocking evidence:** P3/P4 require an internal/local keyed harness producer to
prove the `created` then `existed` canary, while the pilot privilege model
freezes only two self-contained service-token scopes: the worker receives
`taskq_qdarte_pilot:run` and the acceptance principal receives
`taskq_qdarte_pilot:read`. The same section expressly forbids a public enqueue
route, and P2 evidence proves generic enqueue is rejected by the host-owned
authorizer. No P3 harness exists in either QDarte checkout. Issuing an
unmentioned `:enqueue` token, borrowing the facade database login, or writing
directly through SQL would each introduce a producer path without an accepted
authority and would weaken the isolated HTTP/capability proof.

**Decision adopted — local-only enqueue token:** P4 may issue a third,
short-lived self-contained `taskq_qdarte_pilot:enqueue` service token only to
the checked-in local harness. The harness calls the mounted package facade
over HTTP and owns no route, API setting, database credential, direct SQL, or
persistent token record. It is disposed with the P4 local configuration. The
worker remains `run`-only and the acceptance principal remains `read`-only;
positive enqueue plus every cross-action/wrong-queue denial are required
evidence. The token may not reach a public producer path, use the facade's
PostgreSQL login as a bypass, or access `qdarteapi_dev.taskq` / `qdarte_ops`.

### S5-QD-CQ-01 — Current QDarte staging already carries an incompatible direct-SQL taskq surface *(resolved: Option B)*

**Blocking evidence:** the fresh authoritative staging checkouts contradict the Round-11 source
inventory. `qdarteAPI@9364dd0` contains migration
`20260709_0061_add_taskq_schema.py`, a direct `TaskqClient` that calls a separate
function/catalog family (`taskq.enqueue`, `claim_jobs`, `heartbeat`, `complete_job`, and others),
and copied `/ops/taskq/*` plus `/worker/taskq/*` routes. `qdarte-workers@02ea8fe` contains the
matching direct HTTP worker loop. The guarded local `qdarteapi_dev` database already has a
`taskq` schema. The Stage-5 pilot instead requires the immutable `v0.1.0a3` 0001→0005 contract,
a package-owned mounted facade, and no copied taskq SQL or wire surface. Treating the two as
compatible without proof risks a catalog collision and violates the explicit pilot boundary.

**Decision adopted — Option B:** retain QDarte's direct implementation untouched in
`qdarteapi_dev`; the package pilot uses only a newly created, disposable `qdarte_pilot_dev`
database on the same guarded local cluster. The package keeps its fixed `taskq` schema name,
but the two schemas are in different databases and therefore never share a catalog, route
ownership, credentials, worker, or migration ledger. The dedicated non-superuser facade DSN
targets the pilot database only. P0B confirms the current direct schema remains confined to
`qdarteapi_dev` and the pilot database is absent before P2; P2 alone may create and migrate it.
The existing direct client/routes are neither reused nor retired by this pilot. A later,
separately reviewed convergence decision may evaluate whether QDarte's active contact-verify
queue should migrate to the package. The immutable Round-11 response remains historical; its
`no taskq` inventory was based on a stale source baseline and cannot override this
current-source finding.

### S5-QD-CQ-02 — P2's isolated-database boundary conflicts with P1's QDarte-auth binding *(resolved: Option A)*

**Blocking evidence:** P1's mounted facade deliberately constructs
`OutlabsQueueAuthorizer(auth=app.auth.auth, session_dependency=get_async_session)`. Both
objects are bound to QDarte's existing `outlabs_auth` schema and SQLAlchemy engine in
`qdarteapi_dev`. The adapter resolves that session for every authentication and
queue-authorization check. P2, however, requires queue-scoped worker/read permissions issued
through QDarte's existing service-token lifecycle while also forbidding any query or grant
against `qdarteapi_dev`. Those requirements cannot all hold: the existing authorizer needs that
database for authorization, and the required permission catalog/token scopes cannot be created
there without a narrow IAM mutation. QDarte's own `app.auth` also records that the current
generic OutLabsAuth dependency can consume a valid service token through the ordinary JWT path
and reject it before its service-token backend runs; its host routes use a separate explicit
service-token wrapper to compensate. `OutlabsQueueAuthorizer` invokes the generic dependency,
so the frozen worker-service-token path cannot be assumed to authenticate at the mounted facade.

**Superseded by S5-QD-CQ-03:** the former additive-catalog branch is retained here as
the evidence that exposed the lifecycle mismatch. P2 now uses the verifier-only posture
adopted below; it performs only QP-03's read-only digest against `qdarteapi_dev.outlabs_auth`.

### S5-QD-CQ-03 — Option-A pilot IAM cannot meet the byte-identical teardown oracle through QDarte's supported public service *(resolved: verifier-only self-contained tokens)*

**Blocking evidence:** Option A permits P2 to add the exact
`taskq_qdarte_pilot:read` and `taskq_qdarte_pilot:run` records through QDarte's
public OutLabsAuth permission service, while QP-10 requires owner-only teardown
to delete exactly those pilot records and restore QP-03's full auth content
digest byte-identically. In the pinned QDarte OutLabsAuth artifact,
`PermissionService.delete_permission()` is a soft archive: it retains the row,
sets its status inactive/archived, invalidates caches, and appends a
permission-definition-history event. It also refuses system permissions
entirely. Therefore neither `is_system=True` nor `is_system=False` can restore
the pre-P2 permissions/history digest through the approved public API; direct
SQL cleanup would violate Option A's no-ad-hoc authorization-bypass rule.

**Decision adopted — verifier-only self-contained tokens:** QDarte's supported
service-token verifier is retained, but P2 does not provision a QDarte auth
catalog, role, API key, or persisted token record. The future local worker and
read principal receive distinct ephemeral credentials carrying only the exact
`taskq_qdarte_pilot:run` or `:read` scope; the host-owned `QueueAuthorizer`
validates and checks that embedded scope through QDarte's supported service.
No wildcard, generic-dependency bypass, or operator permission is introduced.
The QDarte auth database is mutation-out-of-scope: QP-03 and QP-10 prove its
digest byte-identical before and after the pilot by construction, using only
the canonical read-only digest. P2 may resume only for the disposable queue
database and its package IAM; P4 alone may issue the ephemeral local credentials
after P3 opens.

### S5-CQ-02 — H-11 flat profile response conflicts with the existing generated PUT envelope

**Blocking evidence:** Protocol v1 §2.5 says canonical `PUT /taskq/v1/queues/{queue}` success
data is the same flat queue-profile projection returned by the new GET. The existing generated
`ensure_queue` command for that identical route, however, has shipped a distinct H-13 model
`EnsureQueueWireData` whose data shape is `{ "profile": { ... } }`; both official HTTP clients
decode that wrapper. The new conditional-update function can supply the profile and ETag, but it
cannot decide whether the established wrapper is replaced, retained as a compatibility envelope,
or split into a new identity without a Tier-0 compatibility decision. Treating the old wrapper as
"close enough" violates §2.5's exact field set; silently replacing it would break existing clients.

**Decision required:** amend the Protocol docs-first to name the canonical H-11 success shape and
the explicit compatibility/rollout posture for the existing generated PUT command, including the
ETag and `If-Match` cases. The decision must say whether clients accept both shapes, whether a new
route/command identity is required, and how old clients behave. Do not add a facade special case or
make the client decoder permissive until that authority is frozen.

### S5-CQ-01 — SQL-contract compatibility window for migration 0004 is unspecified

**Blocking evidence:** `0004_read_models.sql` must advance
`taskq.meta.contract_version` to `0.1.3` (Manifest §11), but the existing
`TaskqRuntime.start()` accepts only exact `0.1.2`. Applying the migration to a
running supported host would therefore make its runtime fail startup. Protocol
§3 requires compatibility-window tests, while the accepted S5 sequence defers
HTTP/client work; neither Tier-0 document defines whether the existing runtime
must accept `0.1.2..0.1.3`, whether migration and a strict runtime bump must be
released atomically, or the supported rollback posture.

**Resolution:** ADR-020 accepts a general closed supported-contract-set rule.
The bridge runtime declares `{0.1.2,0.1.3}` and exposes no read-model surface;
the historical `{0.1.2}` pin remains a regression proof. Applying 0004 raises
the database rollback floor to the bridge. Production application is a later,
separately gated deployment decision after the bridge is both deployed and the
rollback baseline; it is not authorized by S5-RM-01. The runtime decides exact
membership from the database-reported version, with no wire change.

### S5-CQ-03 — active H-08 list function cannot distinguish an unknown queue from an empty view

**Blocking evidence:** Protocol v1 §2.5 requires an authorized missing queue to return `TQ001`,
and requires direct SQL and HTTP to share the same bounded-page semantics. Immutable migration 0004's
`taskq.list_jobs(text,text,integer,jsonb)` instead checks the per-view capability and then queries
`taskq.jobs` without establishing that `taskq.queues.name = p_queue` exists. Once a view capability
is active, an unknown authorized queue therefore returns a successful empty page. A facade-side
`get_queue_profile()` preflight would make HTTP differ from direct SQL and would be an impermissible
workaround.

**Decision required:** authorize a docs-first repair path: a new Manifest/SQL-contract revision and
immutable migration 0005 which keeps the `list_jobs` identity and fixed page composite but raises
typed `TQ001` for an unknown queue before the capability gate/query. The decision must define the
runtime bridge set and production rollback floor for the additional migration. Do not activate or
expose the list route/client until that authority, fresh/full-chain proofs, and SQL/HTTP parity are
frozen.

### S5-CQ-04 — approved H-11 revision number is already occupied by the bridge amendment

**Blocking evidence:** the approved envelope correction names Protocol document revision `1.0.6` /
amendment 13, but the current locked Protocol log already assigns revision `1.0.6` to ADR-020's
supported-contract-set bridge (amendment 13 in the existing log). Reusing that revision would
silently overwrite an accepted compatibility decision and make the document revision non-unique.

**Decision required:** confirm that the approved H-11 envelope correction is the next additive
Protocol revision **1.0.7** (with the next sequential amendment-log number), retaining every
approved envelope/ETag/drafting-error condition. No wire-major or SQL identity changes follow from
this numbering correction. Do not reuse or edit the already accepted 1.0.6 amendment.

**Resolution:** ADR-021 records the approved correction as Protocol document revision 1.0.7 /
amendment 14. It keeps the existing generated `{"profile": {...}}` PUT response as the single
canonical success shape, leaves GET flat, preserves the ETag/If-Match matrix, and records the
revision-1.0.5 flat-PUT statement as a drafting error. The same docs-first ADR reserves Manifest /
SQL contract 0.1.4 and immutable migration 0005 for `list_jobs` existence-before-capability
conformance; no 0004 edit or new wire identity is authorized.

### S5-CQ-05 — approved `ready` B9 evidence has no frozen activation vehicle

**Blocking evidence:** B9 passed for `read_model_list_ready` on PostgreSQL 16 and 18, while
`running` and `finished` remain rejected. ADR-021 / Manifest §12 deliberately say migration 0005
does **not** activate a view, and the manifest exposes no operator function that may mutate
`taskq.meta.capabilities`. The generated facade and direct SQL now correctly return `TQ501` outside
the isolated parity vector, but no immutable migration identity or deployment authority says how an
approved capability becomes active. Updating metadata manually would evade the migration ledger and
would make verification unable to distinguish the approved posture from drift.

**Decision required:** freeze a docs-first activation vehicle and rollback posture for the
ready-only capability. The narrow candidate is an immutable metadata-only migration 0006 under the
existing SQL contract 0.1.4, named in the Manifest before implementation, which asserts 0.1.4
metadata and sets exactly `{"active":["read_model_list_ready"]}`. It must preserve `running` and
`finished` inactive, extend fresh/full-chain PG16/18 and `verify()` proofs, and state whether a
post-0006 database has a new runtime rollback floor. Do not enable the capability through manual
SQL, an HTTP configuration route, or a facade-side exception before this authority is frozen.

**Resolution:** S5-CQ-05 is approved. Manifest §13 reserves immutable, metadata-only migration
0006 under unchanged SQL contract 0.1.4. It asserts 0.1.4 metadata and writes exactly
`{"active":["read_model_list_ready"]}` on the committed `7fe2c6b` B9 evidence; `verify()` and
the PostgreSQL 16/18 fresh/full-chain transition vectors must assert that exact posture. A future
deactivation requires a successor metadata migration, never manual DML.

### S4-CQ-04 — Canonical OutLabs authorization rejects the live system-integration API key

**Blocking evidence:** before any canary enqueue, production was switched to taskq mode with only
the read-only `umami` lane allowlisted. The pre-existing `TOOLS_API_KEY` is not a valid OutLabsAuth
credential: both the host queued-tools dependency and `/taskq/v1/meta` returned 401. A replacement
ephemeral system-integration principal/key was then created through exact a24's public services with
only `tools:run` and `taskq_tools:read`. The host route authenticated it and reached its post-auth
validation boundary (422 for deliberately invalid parameters), proving `tools:run`; the canonical
taskq facade returned `TQ503` with fixed reason `auth_infrastructure_unavailable` on every one of
three retries for the same `X-API-Key`. Bearer presentation returned 401 and presenting both did not
change the typed 503. This contradicts the accepted Stage-3/Stage-4 posture that one supported
OutLabs system-integration credential can enqueue through the host route and perform canonical
queue-scoped readback. No enqueue request was sent, worker/tool invocation markers remained zero,
the ephemeral principal and owned key were archived through the public service, and its temporary
file was deleted. The production producer was restored to `legacy`, the temporary production and
preview allowlist variables were deleted, the rollback deployment finished healthy, and the live
container reports health 200, taskq enabled, mode `legacy`, and no allowlist.

**Recommended adjudication:** reproduce the exact a24 system-integration-key path against
`OutlabsQueueAuthorizer` with its real session dependency and Redis-backed auth configuration, then
repair the canonical adapter or the pinned OutLabsAuth dependency at the failing supported surface.
The regression must prove authenticate → queue-scoped authorize for `taskq_tools:read`, denial for a
key lacking that scope, principal-fingerprint stability across the two phases, and unchanged typed
429/503 normalization. Do not authorize from the host's custom `require_outlabs_api_key` helper,
weaken fail-closed rate limiting, substitute direct SQL readback, or grant a global/wildcard scope.
If taskq source changes, publish a new immutable alpha and update the host URL/hash pin before
resuming the exact pre-enqueue probe. Production remains in legacy mode until the canonical 202→GET
path passes with the real supported credential.

**Resolution:** closed in taskq commit `36db7cf` and immutable release `v0.1.0a2` (wheel SHA-256
`d3c37b0e30dbc75cbbb279c3e3f64a7df7416bf51ca1acfd016544c03e745f42`). The adapter now obtains
and caches OutLabsAuth's checker on the first post-startup request instead of freezing the
pre-initialization service; an exact a24/Redis-backed regression proves the real system-integration
key path, queue-scoped allow/deny, stable two-phase fingerprint, and unchanged sanitized 429/503
failure posture. Host commit `76ff5e1` pins the exact release artifact. A production ephemeral key
then returned 200 from `GET /taskq/v1/stats/queues/tools` and 403 from undeclared global
`GET /taskq/v1/meta`; every proof principal/key was revoked and archived. No SQL, migration,
contract, ADR, role, grant, or wildcard scope changed.

### S4-CQ-03 — Immutable migration cannot execute after `SET ROLE taskq_owner`

**Blocking evidence:** S4-CQ-02 condition 4 requested `taskq migrate/verify` under the owner via
`SET ROLE taskq_owner`. An executed local scratch-database probe connected as the owner, granted
database `CREATE` to the existing `NOLOGIN taskq_owner`, ran `SET ROLE taskq_owner`, and invoked the
packaged installer. Migration 0001 failed with `InsufficientPrivilegeError: permission denied to
alter role` at its required capability-role hardening. This is structural: the immutable migration
must create/validate and alter the producer, runner, observer, operator, and housekeeper roles;
`taskq_owner` correctly has neither CREATEROLE nor admin membership, and adding either would violate
the locked reserved-role manifest. Do not change migration 0001, broaden `taskq_owner`, or improvise
a partial production install.

**Recommended adjudication:** name the PostgreSQL owner/admin login as the execution credential for
`taskq migrate` and `taskq verify`, without `SET ROLE`. The immutable migration itself assigns all
taskq objects to the `NOLOGIN taskq_owner`, revokes PUBLIC access, grants only capability roles, and
`verify()` independently proves those ownership/grant facts. Retain the approved restricted runtime
and operator login boundaries unchanged. This is the smallest correction and matches ADR-004,
ADR-010, the Function Manifest, and the already executed S4-01/S4-03B migration evidence. Making
`taskq_owner` capable of managing roles would require a new contract/ADR/migration design and is not
recommended.

**Resolution:** approved. `taskq migrate` and `taskq verify` execute directly as the PostgreSQL
owner/admin without `SET ROLE`. The immutable migration remains responsible for assigning every
object to `taskq_owner`, and `verify()` remains the independent ownership/grant oracle. No role
attribute, SQL, migration, manifest, ADR, runtime, or operator-boundary change is authorized or
needed. The restricted-login real-boot proof and rotation may resume.

### S4-CQ-02 — The production app pool is PostgreSQL superuser

**Blocking evidence:** the approved same-cluster preflight connected through the exact deployed
`POSTGRES_DSN` and measured `current_user=postgres`, `rolsuper=true`, `rolcreatedb=true`, and
`rolcreaterole=true`. Taskq's six contract roles are correctly `NOLOGIN`, but PostgreSQL superuser
bypasses their privilege boundaries. Therefore the ordinary application pool can execute
operator-only functions even if it is not granted `taskq_operator`; this directly contradicts
ADR-011 and Stage-4 specification §4.1's requirement that the operator credential is never present
in the app pool. Do not migrate the production database, provision production IAM/queue state, or
set `TASKQ_ENABLED=true` while the runtime DSN remains superuser.

**Recommended adjudication:** keep the approved one-database topology, but introduce a dedicated
non-superuser host runtime login. Grant it only the existing host runtime access plus
`taskq_producer`, `taskq_runner`, `taskq_observer`, and `taskq_housekeeper`; prove it cannot
`SET ROLE taskq_operator`, call `ensure_queue`, create roles, create databases, or bypass row-level
security. Rotate the application's `POSTGRES_DSN` to that login. Run host/auth/taskq migrations and
operator provisioning through an owner/operator credential used only by an explicit one-off
pre-deploy action and never injected into the running application. Before rotation, derive and test
the exact public/outlabs-auth runtime grants on a disposable database; after rotation, prove host
health and the disabled taskq posture before production taskq migration. Keeping the superuser app
pool would require explicitly reopening the accepted privilege-separation design and is not
recommended.

**Resolution:** approved. Before rotation, a disposable same-cluster database must boot the real
application and worker under the proposed login, pass startup, one authenticated request, and a
legacy `outbound_tasks` operation, and prove denial of operator `SET ROLE`/`ensure_queue`,
CREATEROLE, CREATEDB, and RLS bypass. The prior owner DSN remains available outside the running app
for immediate env-flip rollback. The deploy record names every credential: host Alembic and Auth
migrations use the owner; taskq migrate/verify use the owner with taskq-owner authority; queue
ensure and IAM use an operator-only login; the app and workers use only the restricted runtime.
Sequence is grants proof → rotate → healthy disabled posture → production taskq provisioning →
legacy-mode enablement. Restore/PITR rehearsal stays an explicit host backlog item, and the audit
packet records the rotation as a host-security improvement.

### S4-CQ-01 — The live production database is not the frozen Neon target

**Blocking evidence:** the accepted Stage-4 specification states that production is managed
PostgreSQL behind the canonical Neon DSN, and S4-01 proved migrations, split taskq roles, IAM,
queue profile, TLS, and `max_connections=901` only on a disposable Neon branch. The first real
Coolify deployment showed that the production application actually tracks `staging-prep` and its
`POSTGRES_DSN` points to Coolify's internal PostgreSQL service. Host commit `d1b00fe` is now deployed
healthy with taskq explicitly disabled; `alembic current` is `20260616_0005 (head)`, the public
health check is 200, `/taskq/v1/meta` is 404, unauthorized enqueue is 401, and a read-only query in
the running container reports `to_regnamespace('taskq') IS NOT NULL = false`. Enabling against the
current DSN would therefore provision a database that the frozen production proof never covered;
switching the whole host to Neon would be a separate data migration; adding a taskq-only DSN would
be a new topology. Do not set `TASKQ_ENABLED=true`, run taskq migrations, or reconcile taskq IAM in
production until this is adjudicated.

**Recommended adjudication:** amend the living Stage-4 deployment target to the actual Coolify
PostgreSQL service for this first-host dogfood, then repeat the S4-01 production preflight in place:
record server/TLS/pool facts and `max_connections`, prove the migration owner can create the split
roles, run immutable migrations plus `verify()`, reconcile the exact IAM catalog and `tools` queue
profile, and only then acknowledge production enablement in legacy mode. This avoids an unrelated
application-data migration and preserves the one-database embedded-host topology. If Neon remains
mandatory, authorize and spec either the host data migration or a separate taskq DSN before source
or production changes.

**Resolution:** approved for the actual Coolify-internal PostgreSQL service. The Tier-3 specification
records the deployed `staging-prep` reality and stale-default-branch audit, the Postgres-backed legacy
path, no-WhatsApp boundary, deployed gate counts, mutual-exclusion/R6-06 analysis, and superseded
Neon-specific facts. Before enablement, a disposable database on the same cluster must reproduce the
complete S4-01 role/migration/verify/IAM/profile proof, record connection/TLS plus backup/durability
posture, and be removed by dropping only that database. The Neon mechanics remain evidence; its
pooler/TLS-proxy/901 facts are non-applicable. Stage 4 also owns post-acceptance reconciliation of
`main` and the production line.

### S3-CQ-01 — HTTP worker presence is absent from Protocol v1

**Blocking evidence:** the Tier-0 Function Manifest 0.1.2 exposes runner command `taskq.worker_heartbeat(...)` with closed `continue | shutdown_requested` semantics, ADR-011 requires the facade runtime to call it on behalf of HTTP workers, and the completed Stage-2 `WorkerService` depends on it for advisory presence and remote drain. But the Tier-0 Protocol v1 adopted HTTP table defines claim, per-job heartbeat, worker reads, and shutdown requests without a canonical worker-presence HTTP command. ADR-005 makes route shape, authorization inputs, outcomes, and HTTP mapping contract-owned; S3-00 cannot invent them in Tier 3 or claim SQL/HTTP worker parity without this closure.

**Recommended adjudication:** accept ADR-014 plus an additive Protocol-v1 revision defining `POST /taskq/v1/workers/heartbeat`: body `worker_id`, non-empty distinct `queues`, and bounded safe presence fields; authenticate first, authorize `run` for every distinct declared queue, treat `worker_id` as an advisory validated label while actor remains the authenticated subject, call `worker_heartbeat`, and return HTTP 200 with typed `continue | shutdown_requested`. The route must never accept actor, credentials, attempts, payloads, or fences. Add it to the H-13 generated HTTP-client/conformance surface. SQL contract 0.1.2 and the Function Manifest remain unchanged; the adjudication must state the required additive protocol-document version marker before S3-00 resumes.

**Resolution:** accepted ADR-014 and additive Protocol v1 document revision 1.0.1. The canonical route is `POST /taskq/v1/workers/heartbeat`; every distinct declared queue requires `run`; `worker_id` remains advisory while the authenticated subject is the actor; the two typed success outcomes are `continue | shutdown_requested` on HTTP 200. Worker presence extends no lease and carries no fence. H-13 generation and SQL/HTTP parity include the command. SQL contract 0.1.2 and the migration chain are unchanged.

Resolved history: ADR-014 resolves S3-CQ-01 as Protocol v1 document revision 1.0.1; ADR-013 resolves S2-CQ-01 as contract 0.1.2; ADR-012 resolves round-3 CQ-01/CQ-02.

### S3-CQ-02 — Active queue-profile read route has no SQL-safe backing

**Blocking evidence:** the adopted Tier-0 Protocol v1 base declares
`GET /taskq/v1/queues/{queue}` as an active `read` command backed by a “safe queue projection.”
Function Manifest 0.1.2 has no queue-profile read function, and migration 0001 exposes exactly three
observer views: `queue_stats`, `dead_jobs`, and `worker_status`. The observer role has no base-table
`SELECT`; ADR-010/011 forbid broadening the ordinary facade credential or falling back to its
separate operator pool. `taskq.ensure_queue` is operator-only and mutating, so it cannot honestly
serve a GET. Unlike `list_jobs`, this route is not marked deferred by H-08 or another capability.

**Recommended adjudication:** accept ADR-015 plus additive Protocol v1 document revision 1.0.2 that
marks queue-profile GET unavailable in 0.1 (`TQ501`, capability inactive) and defers its exact safe
projection plus optimistic-concurrency contract to the already-deferred H-11 interactive-admin
slice. `PUT /taskq/v1/queues/{queue}` remains the bootstrap/admin command and returns its canonical
profile. This closes Stage 3 without changing SQL contract 0.1.2 or adding migration 0004. The
alternative is a docs-first Function Manifest 0.1.3 addition for an observer-safe
`get_queue_profile(text)` plus immutable migration and PG16/PG18 fresh/upgrade evidence.

**Resolution:** accepted ADR-015 and additive Protocol v1 document revision 1.0.2. The queue-profile
GET moves visibly into the deferred-routes section, returns `TQ501` while inactive, and is excluded
from H-13's active generated client/OpenAPI/conformance surface. H-11 must reactivate it through the
Growth §4 / R2-16 exact observer projection and read-model design. Observers retain queue stats;
administrators receive canonical profiles from idempotent ensure. SQL contract 0.1.2 and migrations
0001–0003 are unchanged; there is no migration 0004.

### S3-CQ-03 — Remaining active wire models are not fully implementable

**Blocking evidence:** the final H-13 model derivation found three independent gaps in the adopted
Tier-0 wire text. First, every response requires `request_id` from a “validated inbound correlation
header,” but the header name, accepted grammar, length, and generation/echo behavior are absent.
Second, active `PUT /taskq/v1/queues/{queue}` promises a canonical profile **plus version**, while
`taskq.ensure_queue` returns the canonical profile with no version column or value; H-11 explicitly
defers optimistic concurrency. Third, active `GET /taskq/v1/workers` promises a safe presence
projection but freezes neither fields nor pagination; the only SQL backing is observer-granted
`worker_status`, whose `w.*` includes hostname, pid, and arbitrary direct-SQL `meta`, so forwarding
the view would violate the no-secret/network-detail promise and an invented projection would violate
R2-16/H-13. These are protocol-owned inputs/outputs, not Tier-3 implementation choices.

**Recommended adjudication:** accept ADR-016 plus additive Protocol v1 document revision 1.0.3 as
the final Stage-3 wire normalization: (1) reserve `Taskq-Request-Id`, accept 1–128 ASCII characters
matching `[A-Za-z0-9._:-]+`, generate a lowercase UUID when absent, and echo the value in the body
and response header; (2) correct queue ensure's 0.1 response to the exact canonical profile with no
version, reject `If-Match` as H-11-inactive (`TQ501`), and add version/If-Match only with H-11; (3)
move worker list into the explicit deferred-routes section with `TQ501`, excluded from H-13's active
generated surface, until Growth §4/R2-16 freezes a bounded observer projection, redaction, cursor,
authorization, and plan evidence. Keep per-worker presence writes and worker shutdown/expiry
commands active; no SQL contract or migration changes.

**Resolution:** accepted ADR-016 and additive Protocol v1 document revision 1.0.3. The canonical
`Taskq-Request-Id` is bounded, server-minted when absent, and echoed without unbounded persistence;
queue ensure returns its exact version-free SQL profile and rejects premature `If-Match` with
`TQ501`; worker list remains declared in H-13 behind a typed `TQ501` gate with no success schema
until R2-16 freezes the safe projection. Queue detail remains deferred out because its whole read
model is absent, while worker list stays declared because only its public projection is pending.
SQL contract 0.1.2 and migrations 0001–0003 remain unchanged.

### R5-CQ-A — General job list has no adjudicated 0.1 disposition

**Blocking evidence:** Protocol amendment 3 calls the adopted `GET /taskq/v1/jobs` row
“operator-minimal, pre-H-08,” while the Protocol exit status says H-08 is deferred behind a
capability gate. Function Manifest §7 implies an operator-minimal form exists, but migration 0001
explicitly records `list_jobs: absent`, and the exact 0.1.2 catalog contains no function or view that
can serve any list form. Tier 3 cannot decide whether the route is active, gated, or deferred.

**Recommended adjudication:** ADR-017 / Protocol document revision 1.0.4 applies ADR-016's
undesigned-command rule: defer `GET /taskq/v1/jobs` out of H-13, add it visibly to the §2.2 deferred
table with H-08's Growth §4/R2-16 projection/cursor/index/plan reactivation gate, correct amendment
3, and state in the Function Manifest that no `list_jobs` exists in 0.1. A reserved-path negative
vector returns typed `TQ501` while remaining absent from OpenAPI/client success surfaces. No SQL or
migration change.

**Decision needed:** approve the recommended deferral or select a contract-backed 0.1 disposition.
Do not amend Tier 3 or start S3-01 first.

**Resolution:** accepted ADR-017 and additive Protocol v1 document revision 1.0.4. The general list
route is deferred out with a hidden typed `TQ501` responder, no success model or generated client /
OpenAPI operation, and an H-08 Growth §4/R2-16 reactivation gate. Protocol H-08 and amendment 3 plus
Manifest §7/errata now agree that no `list_jobs` exists in 0.1. SQL contract 0.1.2 and migrations are
unchanged.

### R5-CQ-B — Enqueue `created_at` has no contract-backed source

**Blocking evidence:** the adopted Protocol base promises `created_at` in the single-enqueue
`created` response, while `taskq.enqueue(...)` returns only `(job_id, created)`. The core model has no
`created_at`; its queue/job-type/idempotency/schedule fields are request echoes, not durable row
truth—especially for `existed`. A follow-up observer read would mix capabilities and add a round
trip; a facade timestamp would be invented.

**Recommended adjudication:** the same ADR-017 manifest-wins amendment removes `created_at` from
0.1 enqueue responses. The wire result contains durable `job_id` plus created/existed disposition;
any request-echo fields are explicitly labeled non-authoritative and cannot masquerade as stored
state. Clients needing timestamps use authorized job detail. H-13's independent catalog oracle
asserts exact response-field sets per command. No SQL or migration change.

**Decision needed:** approve the manifest-backed response or authorize a different Tier-0 source.
Do not add an observer lookup or client-clock field in Tier 3.

**Resolution:** accepted ADR-017 and additive Protocol v1 document revision 1.0.4. Single enqueue
returns exact envelope outcome `created | existed` and authoritative `data.job_id` only; queue is
implied by the path, `created_at` and request echoes are absent, and authorized job detail owns stored
timestamps/state. The same amendment completes invalid request-id mint/reject ordering. SQL contract
0.1.2 and migrations are unchanged.

## Round-5 finding dispositions

The immutable response verdict was **BLOCKED**. ADR-017 resolves R5-CQ-A/B and R5-09. The amended
Stage-3/Auth/Harness designs close R5-01..08, R5-10/11, and R5-16 with the approved mechanisms and
acceptance vectors. No SQL, migration, grant, or source change was required. Residual findings are
owned explicitly rather than treated as closed:

- **S3-01:** R5-27, R5-37, R5-38, R5-39, R5-40, R5-41, R5-43 (artifact/package boundary, exact capability methods and view
  close behavior, retry request IDs, sync thread safety, 1-based bulk index, worker-owned settle retry).
- **S3-02 (closed):** R5-14, R5-17, R5-18, R5-19, R5-22, R5-23, R5-24, R5-33, R5-42 (hiding equality, diagnostic truncation,
  dynamic listener/disconnect races, stats semantics, envelope wording, metrics default, gated-worker
  action before activation, normative long-poll sequence).
- **S3-03 (closed):** R5-20 (runtime-owned unsafe-sync process-exit actor and live-ASGI evidence).
- **S3-04 (closed):** R5-12, R5-13, R5-15, R5-31, R5-32, R5-34, R5-35, R5-36 (auth 429/503, session lifecycle, queue input grammar,
  seed side effects, API-key wildcard honesty, legacy candidates, alpha/API names, transaction savepoint).
- **S3-AUDIT (closed):** R5-21, R5-25, R5-26, R5-28, R5-30 plus the accepted S3-02 §8 route-mechanism wording correction (independent oracle proof, exact CI/artifact claims, raw-read
  parity mutation, front-door freshness, documentation accuracy). R5-29 is closed by S5-RM-ADR's exact Growth §4 / H-08/H-11
  reactivation contract; implementation evidence remains owned by S5-RM-01 and its follow-on surface/B9 tasks.

## Round-4 finding dispositions

The response verdict was **BLOCKED**. R4-01..12 are accepted as source-backed implementation, evidence, or CI findings; no Tier-0 conflict exists. R4-01..08 were the worker-kernel remediation gate; the Stage-2C audit closes R4-09..12 with the pre-0.1.2 decode pin, SQL claim bounds, cancelled-stop-waiter ledger, and scheduled million-row plan lane.

## Round-3 finding dispositions

All seven findings are **accepted as source-backed**; ADR-012 resolved the two Contract questions. R3-01, R3-02, and both Contract questions were independently reproduced after the response landed; R3-03..07 agree with the cited ADR/harness/source gaps. R3-07 is an evidence-hardening item rather than a direct contract violation. No finding is rejected or deferred into Stage 2.

## Done

- [x] **T-HARNESS-01 · Capability-fixture ordering pinned** — `role_conn` now explicitly depends on the per-test `pg` truncation fixture, preventing pytest-asyncio from initializing capability sessions before the state reset and erasing a freshly provisioned queue mid-test; the scratch-only truncation also retries a transient deadlock while prior capability connections unwind. This is harness-only: no taskq SQL, wire, capability, application, or production behavior changed.

- [x] **S4-POST-R3 · Authoritative-main and deployment-branch cutover** — R8A-01's immediate recheck passed; `main` advanced without force from `7df6b7f` to exact-tree candidate `2ed736b`, and Coolify API plus standing worker now run that identical revision with unchanged settings digests. A keyed read-only Aerolineas request proved `created`→`existed` same-id convergence and authorized canonical `succeeded` readback; a validation-only newsletter probe left all three legacy rows unchanged. The annotated `3f50b7d` rollback tag, pinned to its peeled SHA for a platform-verifiable revision, booted both resources against the unchanged PG16.14/Alembic database, preserved auth, health, worker command, zero active depths, and required no manual DML; both resources were then restored to exact `main@2ed736b`. One simultaneous worker rebuild hit a transient BuildKit snapshot-cache failure and succeeded on sequential retry without replacing the running worker. Host evidence commit `6f566c1` records the complete transcript; independent BR-06..10 acceptance remains the only open gate.

- [x] **S4-POST-R-AUDIT-RESPONSE · Candidate independently accepted** — registered the targeted response byte-for-byte as immutable Tier 4 (SHA-256 `2e86e692b35d62f70b0aa4d96f103035ac47367c5b002ed432f23b9337c5b78f`). Raw Git regeneration confirms candidate `2ed736b`, exact ordered parents, accepted tree `ded6d43`, empty recursive diff, both histories as ancestors, true fast-forward eligibility, 27/3 ledger counts, all four ledger checksums, source-backed default dispositions, zero forward ports, and all three annotated remote tags. Lock, host 72/72 plus five skips, Ruff, 64-file MyPy, Alembic/import gates, exact dependency pins, and same-tree harness inheritance pass; zero Contract questions. R8A-01 binds an immediate pre-move recheck of refs, Coolify branch/revision, and live health because platform policy state was not inspectable. READY authorizes only `main` fast-forward and frozen deployment cutover; retirement, deletion, side-effecting lanes, and Stage 5 remain closed.

- [x] **S4-POST-R2 · Exact-tree two-parent candidate constructed** — host candidate `2ed736b` on non-deploying `codex/s4-post-r2-reconcile` has parent 1 `9348f85`, parent 2 old `main` `7df6b7f`, and exact tree `ded6d43ace2fced88600f19128dedcfcfe9fe0be`; raw and name-status diffs from the accepted parent are empty, both histories are ancestors, and current `main` is fast-forward eligible. Three remote annotated rollback tags peel exactly to old main, deployed `3f50b7d`, and accepted evidence `9348f85`. Host evidence commit `a2500a4` records the construction separately and is not a candidate input. Lock, 72/72 plus five-skip suite, Ruff, 64-file MyPy, offline Alembic, taskq/configured-host imports, and API/worker images (`e84309e`, `b1dd914`) are green. `origin/main` remains `7df6b7f`, `origin/staging-prep` remains `3f50b7d`; no Coolify/deployment/database/environment/production, retirement, deletion, side-effecting-lane, or Stage-5 change occurred.

- [x] **S4-POST-R-AUDIT-REQUEST · Targeted pre-move gate assembled** — the request requires independent R1 regeneration, raw Git parent/tree/diff/ancestry proof, annotated tag peeling, remote/deployment non-mutation, exact dependency and host gates, explicit local-harness sufficiency judgment, Contract questions, and BR/R8-01 dispositions. The reviewer may create only `docs/design-review-8/R-AUDIT-RESPONSE.md`; no authoritative ref, deployment, production, retirement, deletion, side-effecting-lane, or Stage-5 action is authorized.

- [x] **S4-POST-R1 · Host reconciliation ledger derived** — host evidence commit `b78ca5e` records all 27 production/evidence-only and three default-only commits, each with affected surfaces, one allowed disposition, semantic evidence, a named wrong-disposition oracle, and independent-review status. The three default changes are superseded or already present: there are zero forward ports and zero rejected production behaviors. R8-01 is frozen to base parent `9348f85`, old-main parent `7df6b7f`, and expected tree `ded6d43ace2fced88600f19128dedcfcfe9fe0be` with no allowed differing path. The host ledger commit is evidence-only on the non-deploying branch and is deliberately not the future candidate parent/tree. Host gates remain 72/72 regular plus five infrastructure skips, Ruff clean, and MyPy clean across 64 files. No merge candidate, tag, branch/default ref movement, deployment, database command, environment change, or production probe occurred.

- [x] **S4-POST-R8-RESPONSE · Round-8 response recorded; reconciliation READY** — registered the external response byte-for-byte as immutable Tier 4 (SHA-256 `957dbb3cad99a13b87ec1ee9eee5c72d5434e30d8ca070086c69395f90678732`). The reviewer independently reproduced the graph, complete legacy-tools surface, taskq 450/450 plus one opt-in skip, host 72/72 plus five infrastructure skips, and clean linters; it returned READY with zero Contract questions and no R1 preconditions. R8-01 binds R1/R-AUDIT to candidate-tree equality with the accepted tree plus only named forward ports and zero unclassified paths. R8-02/03/05 require docs-first retirement amendments before L1; R8-04/06 belong to L2. READY authorizes reconciliation only—no retirement, branch deletion, side-effecting lane, or Stage 5.

- [x] **S4-POST-R8-REQUEST · Round-8 gate assembled** — the immutable request pins taskq `fef775e..9feaf79` and independently re-derivable host identities (`a0019cd`, `7df6b7f`, `3f50b7d`, `9348f85`). It requires an authority-first governance sweep, independently generated branch and legacy-call inventories, adversarial exact-tree/fast-forward/tag/branch-cutover analysis, high-water and invocation-oracle falsification, all four mixed-version producer/consumer windows, security/data/non-tools preservation, BR-01..10 and LR-01..12 dispositions, and explicit Contract questions. The reviewer may create only `docs/design-review-8/RESPONSE.md`; no implementation, ref movement, deployment, production mutation, retirement, side-effecting-lane migration, or Stage-5 work is authorized.

- [x] **S4-POST-00 · Host convergence and tools-retirement plans frozen** — added separate Tier-3 specifications for (1) production-derived, ledger-driven branch reconciliation and (2) tools-only legacy producer/consumer retirement. Reconciliation starts from host common ancestor `a0019cd`, stale default `7df6b7f`, deployed `3f50b7d`, and accepted evidence `9348f85`; it forbids blind merge/rebase/force-push and requires a two-parent exact-tree oracle, fast-forward-only `main`, identical-commit deployment-branch cutover, rollback tags, and independent audit. Retirement follows only after accepted reconciliation, requires seven days/two deploys with zero new legacy `tool_run` rows, removes producer then consumer across separate rollback windows, and explicitly preserves the shared table, migration, worker, non-tools lanes, and future hard-kill gate. Pre-change gates reproduce taskq 450/450 plus one opt-in skip and host 72/72 plus five infrastructure skips, with Ruff and host MyPy clean. No source, branch, deployment, SQL, migration, Tier-0, IAM, or production change occurred.

- [x] **S4-AUDIT-ACCEPT · Stage 4 independently accepted** — registered the targeted delta response byte-for-byte as immutable Tier 4 (SHA-256 `982ec8594b8f621089f4963486a7e2487ed1d9e1b5b4e51e474f145db0b6405d`). The reviewer independently reproduced all five delta checks and declared `ACCEPTED — Stage 4 complete`: the production Aerolineas `created`→`existed`→canonical `succeeded` chain and one-attempt raw oracle, honest 28-connection usable headroom, corrected graceful-release versus hard-kill semantics, docs-only scope, response identity, and both repositories' green gates. This acceptance authorizes neither legacy retirement nor branch reconciliation; each requires a separate specification, and the hard-kill lease-expiry drill remains mandatory before any side-effecting lane migrates.

- [x] **S4-R7-DELTA-GATE · Targeted acceptance packet assembled** — the immutable delta request pins taskq `5fef55c..96194a8`, host `7c60229..9348f85`, and the byte-identical round-7 response. It limits re-review to R7-01, R7-02/R7-04, exact hygiene, and unchanged-source gates; acceptance explicitly authorizes neither legacy retirement nor branch reconciliation. Taskq passes 450/450 with one opt-in skip against PostgreSQL 18 and a disposable CI-shaped Redis plus Ruff clean; host passes 72/72 with five existing infrastructure skips plus Ruff and 64-file MyPy clean.

- [x] **S4-R7-02 · Cycle-2 production canonical closure recorded** — host `9348f85` corrects the earlier local-versus-production wording and records one live safe Aerolineas request submitted twice with the same idempotency key: HTTP 202 `created` then 202 `existed`, identical job `019f7f95-3c93-71ce-9c8a-7c610212dead`, followed by authorized canonical HTTP 200 `succeeded`. A separate read-only production-table oracle proves exactly one successful attempt, zero failures/releases/expiry streak, and `enqueued -> claimed -> succeeded`; no sensitive columns were selected, and the temporary key/principal were revoked/archived. The same packet now computes 52 connections against the usable 80 ceiling-minus-reserve budget, leaving honest headroom 28. Targeted delta acceptance remains the only S4-AUDIT gate.

- [x] **S4-R7-01 · Frozen controlled-failure drill corrected** — living Stage-4 §6 now states the mechanism production actually proved: a graceful rolling replacement releases the held async job as budget-free `worker_shutdown`, then a different worker process reclaims the same job id and succeeds. It no longer claims that a graceful stop can prove lease expiry. Before any side-effecting lane migrates, the named future side-effecting-lane expansion slice must hard-kill the owning process past platform grace and produce a read-only `expired/lease_expired` → same-id reclaim → terminal convergence oracle with correct budget arithmetic and zero manual DML.

- [x] **S4-R7-RESPONSE · Round-7 response recorded** — registered the external response byte-for-byte as immutable Tier 4 (SHA-256 `d110e13a7edd3300bfe9f911a22edd58cd2867aa2abbf74cc4e5267e19370bdd`). Its verdict is BLOCKED by exactly two documentation/evidence preconditions: R7-02 requires one production Aerolineas keyed `created`→`existed` pair plus canonical succeeded GET and honest 28-connection budget headroom wording; R7-01 requires the frozen §6 drill text to distinguish graceful worker-shutdown release from lease-expiry reap and to gate every future side-effecting lane on a true hard-kill drill. Everything else is accepted in substance; S4-AUDIT, legacy retirement, and branch reconciliation remain closed pending the targeted delta acceptance.

- [x] **S4-AUDIT-EVIDENCE-DELTA · Independent production oracles recorded** — host `7c60229` and the immutable round-7 evidence addendum record a read-only production-table oracle for the controlled-failure job (`attempt_count=2`, `failure_count=0`, `release_count=1`, `released/worker_shutdown` then `succeeded/success`, and `enqueued -> claimed -> released -> claimed -> succeeded` across two worker actors) without selecting payloads, results, errors, messages, attempt ids, or fences. The deliberately retained legacy proof row naturally exhausted attempt 5 of 5 and became terminal `failed`, unleased, at `2026-07-20T12:20:10.105189Z`; no retry acceleration or manual DML occurred. Round-7 acceptance remains required.

- [x] **S4-AUDIT-EVIDENCE · Production completion packet assembled** — host `5a8cb78` records immutable release/host identities, actual PG16.14 provisioning and durability facts, full connection arithmetic, both normal cycles, the 25.434478-second same-job attempt-2 recovery, canonical queue-scoped readback, and the complete producer-switch → zero-depth → disabled-runtime → authenticated legacy enqueue → corrected taskq re-enable transcript. The invalid intermediate `tools_mode=true` candidate is disclosed: strict settings rejected it before readiness and Coolify retained the old healthy container. Final production is healthy at `3f50b7d`, taskq mode is restored with both selected read-only tools, the private probe is absent, active depth is zero, one new worker is online, and temporary credentials were revoked/archived. No manual DML, schema/IAM repair, legacy retirement, branch reconciliation, taskq SQL/migration/Tier-0/ADR/source change, or performance claim occurred. Taskq passes 450/450 plus one opt-in skip on PostgreSQL 18.3 and isolated 16.14 with Ruff clean; host passes 72/72 plus five existing infrastructure skips, Ruff, and 64-file MyPy. Round 7 owns independent acceptance; S4-AUDIT remains open until its response is recorded.

- [x] **S4-03F · Local acceptance matrix and platform-drain completion** — host commit `97b154c` adds an idempotent local production-shape setup and real mounted-route harness. Against restricted PostgreSQL and real Redis it proves 20-way keyed convergence with one invocation, queue-hiding equality and no denied mutation, committed-response-loss settlement replay with one invocation, typed depth refusal, sub-five-second endpoint responsiveness during held work, immediate poll-only recovery after a killed SQL connection, same-id budget-free soft-stop recovery, and a zero-session post-shutdown ledger. The host passes 72/72 regular tests with five pre-existing infrastructure skips, Ruff, and 64-file MyPy; taskq passes 450/450 on PostgreSQL 18.3 with one opt-in skip and Ruff clean. A normal Coolify rolling deployment then held job `019f7f21-59e3-7683-8a77-bc875a5c49bf`; replacement health preceded old-container removal, which completed in 25.434478 seconds inside the 35-second grace. The same job succeeded on attempt 2 with zero failures and no manual DML. Host commit `1fd5050` records the transcript. A final healthy deployment applied `TASKQ_DOGFOOD_PROBE_ENABLED=false`, and the running container reports `probe_flag=false probe_registered=false`. S4-03 is complete; rollback/re-enable and independent acceptance remain S4-AUDIT-owned.
- [x] **S4-03E · Cycle-2 host hardening and local production-shape proof** — production first exposed two host-only defects after `aerolineas` joined the allowlist: the optional credential field was serialized as null, then the public flight gateway rejected the default programmatic-client fingerprint; host `b1b5604` and `8084dfc` omit the absent field and send the official public-web channel headers. A real external 200 then exposed the 8KB result boundary: the oversized result made settlement fail closed, stopped the embedded worker, and changed health to 503. Host `3f50b7d` now converts bulky successful tool output into a 247-byte honest omission record (`result_omitted`, original byte count, SHA-256), with a regression below H-09. A fresh isolated local environment ran host/Auth/taskq migrations, exact IAM and queue provisioning, restricted runtime grants, real Redis, the mounted API, and embedded worker. Its canonical Aerolineas flow returned 202, authorized GET `succeeded`, one attempt, zero failures, a 247-byte result, and health 200. A private 60-second hold job then soft-stopped in 20.25 seconds, became queued with a null lease and no budget charge, and the same job id succeeded after restart on attempt 2; no manual DML occurred. Production is healthy on `3f50b7d`, but the platform-specific drain transcript and remaining S4-03 adversarial vectors stay open; the private probe must be disabled after that transcript.
- [x] **S4-CQ-04 · Real OutLabs system-key remediation** — taskq `36db7cf` lazy-binds the exact a24 checker after startup and ships as immutable `v0.1.0a2`; the host pins its exact wheel/hash at `76ff5e1`. Local real-Redis and production ephemeral-key proofs establish queue-scoped 200/403 authorization with stable identity and fail-closed sanitized 429/503 handling. Production cycle 1 then exposed a host-only FastAPI response-model 500 after a committed enqueue; host `464965d` adds the ASGI regression and union response projection. The redeployed keyed canary returned canonical 202, authorized GET 200, and terminal `succeeded`; temporary credentials were revoked/archived. No taskq SQL, migration, Tier-0, ADR, role, grant, or wildcard-scope change occurred.
- [x] **S4-03D · Credential-log remediation and Redis credential rotation** — host commit `ffad218` installs an exact-source filter for the upstream auth Redis logger before application startup and renders any Redis userinfo as `[redacted]`; its 68/68 regular tests plus five existing infrastructure skips, Ruff, 64-file MyPy, formatting, and deployment gates are green. The fix was deployed before rotation and startup proved the retired credential absent while the authority appeared only in redacted form. The replacement credential was then staged for both API and worker, the Coolify Redis password metadata was updated and persisted across a real Redis restart, and marker-only terminal proofs showed environment-based and direct replacement authentication succeeded while the retired credential was rejected. Both consumers redeployed successfully: API health is 200, the worker is running, taskq remains enabled in `legacy` mode with an empty allowlist, and exact checks prove neither retired nor replacement credential appears in API or worker logs. No canary traffic ran during remediation; the allowlisted canary is now unblocked.

- [x] **S4-03C · Restricted-runtime proof, production rotation, and legacy-mode taskq base** — a clean same-cluster disposable database ran host/Auth/taskq migrations twice, exact IAM report→apply→idempotent report, queue `created`→`unchanged`, the real API health/login/logout flow, and a real legacy enqueue/claim/settle through the separate worker under `outlabs_api_runtime`; all operator role-switch/queue-admin, role/database creation, superuser/CREATEDB/CREATEROLE/RLS-bypass negatives held, and the exact disposable database was dropped and proved absent. Production runtime/operator grants were then applied under the retained owner, the API pre-deploy hook became an explicit no-op, and both API and legacy worker deployed commit `0e6417c` with only the restricted DSN. The disabled checkpoint proved API health 200, taskq meta 404, `current_user=outlabs_api_runtime`, all elevated flags false, unchanged legacy row count, and no taskq schema. Direct owner taskq migrate/verify converged twice; operator IAM converged without conflicts and queue `tools` returned `created`→`unchanged`; the runtime retained all negative capability proofs. The final healthy deployment enabled taskq with connection ceiling 100, reserve 20, one expected production process, tools mode `legacy`, empty allowlist, and production acknowledgement. Live evidence is health 200, taskq meta 401, one visible queue-stats row, a persistent restricted worker session from its deployment, zero taskq jobs by the owner oracle, and the unchanged single legacy row. The prior owner credential remains outside both running pools for rollback. The proof also exposed a credential-bearing Redis connection URI in upstream auth logs; S4-03D blocks canary traffic until logging is sanitized and that credential is rotated.
- [x] **S4-03B · Actual-cluster disposable-database preflight** — on the exact Coolify PostgreSQL service, measured PostgreSQL 16.14, direct internal port 5432, TLS disabled, `max_connections=100`, and superuser/CREATEDB/CREATEROLE migration authority. A disposable same-cluster database ran taskq 0001→0002→0003, `verify: ok`, no-op second migrate, and a second green verify; OutLabs Auth reached `20260715_0020` twice; IAM converged report→14 creates→14 existing with zero changes/conflicts; and the complete poll-only `tools` profile returned `created`→`unchanged` with an authoritative-field oracle. Production stayed at app head `20260616_0005`, one legacy `outbound_tasks` row, and no `taskq` schema before/after. The exact disposable database was dropped and proved absent; only the six contract `NOLOGIN` roles remain cluster-wide. Coolify has a named PostgreSQL data volume and a successful daily S3 backup from 2026-07-19. The drill exposed S4-CQ-02: the app DSN itself is superuser, so enablement remains paused.

- [x] **S4-CQ-01 · Actual production database adjudicated docs-first** — approved the Coolify-internal PostgreSQL service for first-host dogfood rather than introducing a host data migration or second taskq DSN. The living Stage-4 specification now records the real `staging-prep`/`d1b00fe` production line, 53/53 plus five-skip host gate, interim Postgres `outbound_tasks` legacy path, removed WhatsApp boundary, stale S4-00 inventory, exact no-dual-execution/R6-06 posture, and superseded Neon-only facts. A complete same-cluster disposable-database proof plus backup/durability record gates enablement, only that database may be dropped, and post-Stage-4 branch reconciliation is explicit. No source, SQL, migration, Tier-0, ADR, or Tier-4 file changed.

- [x] **S4-03A · Disabled production deployment** — reconciled the accepted three-host-commit taskq slice onto Coolify's actual `staging-prep` production line while preserving its newer Postgres-backed legacy publisher and removed broker/domain code; the merged host passes 53/53 with five pre-existing infrastructure skips, Ruff, 61-file MyPy, lock, offline Alembic through `20260616_0005`, and image build. The first guarded candidate failed safely before replacement because the production image does not ship `uv`; Coolify retained the healthy old container, the pre-deploy command was corrected to `alembic upgrade head`, and a regression note was added. A second guarded candidate exposed OutLabs Auth a24's required Redis namespace; host commit `d1b00fe` adds the explicit setting/pass-through/test, Coolify now persists `OUTLABS_AUTH_REDIS_KEY_PREFIX=outlabs-auth:production:outlabs-api` and `OUTLABS_AUTH_AUTO_MIGRATE=false`, and the rolling deployment completed healthy on its first health attempt. Production evidence is health 200, application migration head `20260616_0005`, taskq meta 404, unauthenticated enqueue 401, and no `taskq` schema. S4-CQ-01 blocks enablement; no taskq production migration, role, IAM, queue, job, worker, or canary invocation occurred.

- [x] **S4-02-ACCEPT · Disabled host integration independently accepted** — the reviewer reproduced host 62/62 with three pre-existing infrastructure skips, taskq 449/449 with one opt-in skip, Ruff, 111-file MyPy including Alembic, the offline full-upgrade compile, live `alembic current` at `20260313_0004`, the exact Docker image digest, and the live scratch-database active-window/post-settlement harness with a raw-table oracle. Source inspection accepted the real `NonRetryable`/`Retry` mapping, classification-only durable errors, recursive credential rejection, single-snapshot producer policy, flag-only private probe, exact 202/no-fallback behavior, health/CORS/OpenAPI vectors, and unchanged contracts. Both worktrees were clean; taskq matched origin and the host remained deliberately three commits ahead/unpushed with nothing deployed. S4-03 is open, but its first host push is an explicit production deployment action.
- [x] **S4-02 · Disabled-by-default outlabsAPI integration** — host commit `7df6b7f` adds a frozen fail-fast Stage-4 policy, exactly one canonical tools task plus the flag-only private probe, the poll-only single-process embedded runtime, host-first composed lifespan, authorized lifespan-free `/taskq` mount without operator transport, generated OpenAPI composition, exact CORS headers, and backlog-independent health readiness. The existing queued route samples mode/allowlist once, validates bounded credential-free params, awaits taskq enqueue only for enabled allowlisted requests, returns exact 202/readback fields, and never falls back after an ambiguous error; disabled/non-allowlisted requests remain legacy-only. Handler outcomes use real `NonRetryable`/`Retry` types with classification-only durable errors, and the raw Umami auth-body leak is removed. The pre-existing Alembic ghost import is deleted, MyPy now covers `alembic`, and an offline full-upgrade test permanently imports the migration environment. Host verification is 62/62 with the same three infrastructure skips, Ruff clean, MyPy clean across 111 files, lock exact, Docker image green, and `alembic current` at `20260313_0004`. The live local harness independently observed active-key convergence, post-settlement new execution, and two raw one-attempt succeeded rows through the actual embedded worker. No host deployment, production mutation, taskq SQL/migration/contract/ADR/source change, or unrelated lane migration occurred; independent acceptance is required before S4-03.
- [x] **S4-01-ACCEPT · Stage-4 preflight independently accepted** — external verification reproduced taskq 449/449 with one opt-in skip, the host's 44/44 regular tests with three gated skips, Ruff, configured-scope MyPy, the immutable a1 release/tag/hash/host lock chain, both FastAPI router-surface lanes, the scoped credential-rendering fix, byte-identical round-6 record, and every R6-02..R6-15 living-spec closure. Managed-role/IAM/profile evidence and the persistence-verified 35-second Coolify setting were accepted; independent Neon deletion verification was unavailable because the review credential lacked the organization, with provider auto-expiry and S4-AUDIT final-state evidence retained as backstops. The review also reproduced a pre-existing `alembic/env.py` ghost import that makes `alembic current`/`upgrade` fail before database access; S4-02 owns its removal and a permanent import/CI guard. No Contract question was raised, and S4-02 remains open.
- [x] **S4-R6-DOC · Round-6 documentation closure** — amended the living Stage-4 specification for every R6-02..R6-15 finding without touching source, SQL, migrations, contracts, ADRs, or immutable review files. The handler now names real `NonRetryable`/`Retry` results and sanitized durable errors; keyed replay is limited to the active deduplication window; queued credentials are forbidden; cross-path replay risk is explicit and read-only-bounded; probe-registry and rollback-drain observables are exact; the cycle-2 failure drill is S4-AUDIT-owned; production enablement keys are complete; and the S4-02/S4-03/AUDIT rows name health, classification, payload secrecy, independent invocation, responsiveness, depth, and platform-grace oracles. S4-02 is open.
- [x] **S4-01B · Immutable dependency and managed-platform preflight** — published `outlabs-taskq==0.1.0a1` as an immutable GitHub release wheel (`sha256:01ac3129866a8db34281688d65a95e9f30437b52739cec75c287c69e4d11a6ab`) after the managed drill exposed and pinned the auth CLI's display-redacted-password defect. Host commit `ef084ab` locks that exact wheel and `outlabs-auth==0.1.0a24`, rewrites the two router-internals tests against application OpenAPI, and passes them under FastAPI 0.135.1 and 0.139.2; the locked host passes 44/44 regular tests with three opt-in infrastructure skips, Ruff, MyPy, Docker build, and import checks. A disposable Neon PG18.4 branch proved the a20→a24 auth upgrade, taskq 0001→0003 migrate/verify/idempotency, separated runtime/operator membership, exact IAM reconciliation, queue-profile idempotency, direct unpooled transport, TLS observation, and `max_connections=901`; the branch was deleted and production data was untouched. Host commit `90fa63d` records the live Coolify `outlabs API` application reloaded with a 35-second Stop Grace Period, exceeding the image's 30-second ASGI grace and 20-second soft stop. S4-01 is complete; S4-R6-DOC opens before any host integration.
- [x] **S4-01A · Managed-auth artifact correction** — the real password-authenticated managed preflight found that `taskq auth sync-permissions` passed SQLAlchemy's display-redacted `***` URL into OutLabs Auth. The CLI now renders its owned asyncpg DSN with `hide_password=False`, a special-character regression proves the driver/password/query are preserved without logging the value, and the package advances to `0.1.0a1` so the already-published a0 remains immutable rather than being replaced. No SQL, migration, contract, ADR, facade, worker, or permission semantics changed.
- [x] **S4-00-R6 · Round-6 response recorded** — registered the external response byte-for-byte as immutable Tier 4. Its READY verdict opens S4-01 with no Contract questions, BLOCKERs, HIGHs, or preconditions; the exact a24 resolution/FastAPI test repair and platform-grace check belong to S4-01, while seven remaining MEDIUM and eight LOW wording/vector findings are board-owned before S4-02. The reviewer independently reproduced 448/448 taskq tests with one opt-in skip, the host's 44/44 regular tests with three gated infrastructure skips, the known two-test resolver failure, source inventory, profile/wire producibility, and clean review scope. Neither repository source, dependency lock, SQL, migration, contract, ADR, or prior Tier-4 file changed.
- [x] **S4-00 · First-host dogfood plan frozen** — added the Tier-3 outlabsAPI specification and round-6 adversarial gate after inspecting both clean repositories at taskq `8a13262` and host `a0019cd`. The plan resolves R2-17 with an exact a24 upgrade (the host's complete 47-test collection remains 44 green/3 opt-in skips under a real a24 overlay), requires an immutable hashed taskq alpha rather than a local path, and makes managed-PostgreSQL role/pooler/SSL/ceiling/migration proof a preview-branch precondition. One `tools` queue and canonical `outlabs.tools.run` task migrate only allowlisted read-only tools through a mutually exclusive producer switch; HTTP 202 returns job id/disposition/canonical authorized result URL, keyed replay is honest, callers receive read but never generic enqueue, and external-effect lanes stay untouched. The poll-only one-process embedded topology has explicit pool/grace/health/CORS/IAM arithmetic; two deploy cycles, a side-effect-free process-termination probe, zero-DML rollback/re-enable, and delayed legacy retirement form the exit gate. No host source/dependency/deployment, taskq SQL/migration/Tier-0/Tier-1, or existing Tier-4 file changed; S4-01 stays closed pending round 6.
- [x] **S3-AUDIT-ACCEPT · Stage 3 independently accepted** — external verification reproduced the identical 448/448 suite with one opt-in skip on exact PostgreSQL 18.3 and 16.14, 289/289 DB-free on Python 3.12 and 3.13.9, the 2/2 million-row plan gate, Ruff/format, and representative wheel/sdist dependency corners. Source inspection accepted both deliberate oracle-drift proofs, the nullable redaction decode fix, exact CI/harness/front-door corrections, legitimate ADR-014 context-only attribution repair, and real-path B11/B14 evidence. All round-5 findings are closed or Growth-owned; no SQL, migration, Tier-0, Tier-4, or Stage-4 host change exists. Stage 3 is complete and S4-00 may proceed.
- [x] **S3-AUDIT · Stage-3 completion evidence** — added the contract-derived live SQL↔mounted-ASGI scenario and raw-table/function read oracles, with deliberate generated-catalog and projection mutations proving the two oracles fail independently. That path found and pinned one implementation defect: redacted nullable job-detail fields now decode when absent, matching the accepted projection contract. The existing security, malformed-input, fence, authorization, long-poll, lifespan, cancellation, process-exit, and resource suites run in the dedicated warnings-as-errors Stage-3 CI gate; full SQL lanes install the exact OutLabs extra, artifacts now matrix Python 3.12/3.13, and the scheduled million-row gate remains explicit. B11 and B14 are executable report-only scenarios through the real runtime/generated-client→ASGI→SQL paths; the toy audit reported B11 facade-only/embedded median p99 2.664/1.831 ms (the negative delta is environmental noise, not a win) and B14 SQL/client median p99 1.378/3.516 ms with 2.084 ms facade overhead. The identical suite passes 448/448 on PostgreSQL 18.3 and 16.14 with one opt-in skip; DB-free passes 289/289 on both Python versions; wheel+sdist × core/HTTP/OutLabs × Python 3.12/3.13 is 12/12; Ruff/format and the 2/2 million-row plan gate are green. Harness/front-door wording now matches the repository. No SQL, migration, Tier-0, Tier-4, Stage-4 host, or future-capability implementation changed.
- [x] **S3-04-ACCEPT · S3-04 independently accepted** — external verification reproduced 443/443 on live PostgreSQL 18.3 with one opt-in skip, 288/288 DB-free, Ruff/format, wheel+sdist, and fresh core/HTTP/OutLabs wheel isolation against exact `outlabs-auth==0.1.0a24`. Source inspection accepted all eight owned round-5 remediations: opaque 429/503 mapping, owned three-shape session resolution with subject revalidation, strict queue plus real permission validation, side-effect-free imports/config-free seeding, deterministic API-key policy notes, explicit legacy candidates, exact public alpha APIs, and SAVEPOINT/caller-transaction semantics. The real-schema first-apply/idempotency/drift/reconcile and Enterprise/Simple policy vectors passed; the Tier-3 edits are as-built precision, SQL/migrations/Tier 0/Tier 4 are unchanged, and S3-AUDIT may proceed while Stage 4 remains closed.
- [x] **S3-04 · OutLabs authorizer, catalog, provisioning, and auth CLI** — added the explicitly imported `taskq.http.outlabs` boundary against exact `outlabs-auth==0.1.0a24`: real-validator queue/global/legacy any-of authorization with concurrent checker caching, bounded subject-derived actors, owned awaitable/async-generator/context-manager session scopes, and sanitized auth 429/503 envelopes with `Retry-After`. The strict pure catalog emits five global plus five per canonical queue; explicit report/apply/reconcile provisioning uses `include_config=False`, non-system standard roles, the public role service, caller-owned transactions, and a SAVEPOINT, with deterministic policy notes for wildcard/API-key/SimpleRBAC limits. The lazy `taskq auth sync-permissions` CLI and non-atomic queue/IAM composition report partial failure without secrets. A real isolated-schema OutLabs installation proves first apply, idempotency, public-service drift conflict/reconciliation, and no global logging leakage; Enterprise/Simple policy, session, error, rollback, artifact, and import boundaries close all eight owned round-5 residuals. PG18.3 passes 443/443 with one opt-in skip and the DB-free lane passes 288/288; Ruff/format, wheel/sdist, and installed core/HTTP/OutLabs isolation are green. SQL contract 0.1.2, migrations, Tier 0, and Tier 4 are unchanged; S3-AUDIT is open and PG16 remains its gate.
- [x] **S3-03-ACCEPT · S3-03 independently accepted** — external verification reproduced 428/428 on live PostgreSQL 18.3 with one opt-in skip, 274/274 DB-free, Ruff/format, wheel+sdist, both core and HTTP artifact-isolation proofs, clean worktree, and trailer/board hygiene. Source scrutiny accepted the SQL/HTTP stop split, runtime budgets and unwind, dynamic listener registration, response-loss settlement replay, nullable progress decode, and R5-20's live-thread process-exit evidence; no Tier-3 drift or SQL/migration/Tier-0/Tier-4 change exists. S3-04 may proceed while PG16 remains honestly deferred to S3-AUDIT.
- [x] **S3-03 · Composable runtime, housekeeper, embedded/HTTP workers, and process budgets** — added the idempotent `TaskqRuntime` state machine, exact host-first lifespan composition/app-state restoration/DI, compatibility/readiness snapshots, five-second jittered housekeeper with transient recovery and fatal cleanup, lazy reconnectable long-poll listener ownership, and explicit resource ownership. Embedded execution is default-off and acknowledgement-gated, reuses the Stage-2 worker unchanged over separate runner pool/LISTEN resources, reports single/multi-process pool/handler/listener arithmetic, refuses database-ceiling oversubscription, and warns on unknown budgets or inverted ASGI grace. The worker CLI now selects exactly one SQL or secret-safe HTTP transport; HTTP mode forbids LISTEN and multi-queue long polling, cancels only its in-flight long-poll claim on stop, and retains worker-owned settlement replay. Live mounted PostgreSQL proves ordinary presence/settlement, dynamic long-poll wake, duplicate-housekeeper advisory-lock safety, HTTP response-loss convergence/remote drain, and R5-20's runtime process-exit actor firing while an ASGI-hosted sync thread remains live. A nullable claim projection decode found by that real HTTP path is pinned. The unchanged SQL/migration/Tier-0/Tier-4 surface passes 428/428 on PG18.3 with one opt-in skip and 274/274 DB-free; Ruff/format, wheel/sdist, and core/HTTP artifact isolation are green. S3-04 is open; PG16 remains for S3-AUDIT.
- [x] **S3-02-ACCEPT · S3-02 independently accepted** — external verification reproduced 411/411 on live PostgreSQL 18.3 with one opt-in skip, 262/262 DB-free, Ruff/format, wheel+sdist, core/HTTP artifact isolation, clean worktree, trailer/board hygiene, and exact absence of SQL/migration/Tier-0/Tier-4 drift; the phased authorization, envelope/hiding/fence boundaries, long-poll hub, dynamic listener lifecycle, pool split, all nine owned round-5 residuals, and the legitimate order-independent Stage-2 import test were accepted. The non-blocking stale §8 `TaskqRoute` wording is owned by S3-AUDIT; S3-03 may proceed while PG16 remains honestly deferred to that audit.
- [x] **S3-02 · Mounted facade, authoritative authorization, pool split, and long poll** — added a lifespan-free FastAPI sub-application whose generated active/gated/deferred routes own every envelope and OpenAPI projection; phased static, bearer, callable, legacy, and explicit-test authorizers authenticate before parsing and authorize authoritative queue sources without exposing fences or lookup oracles. Operator routes require a separate transport/authorizer pair, metrics use global read, worker presence checks every declared queue, and queue stats preserve the empty snapshot posture. A generation-safe in-process wait hub plus dynamically reconnectable notification channels implement the exact capture/claim/subscribe/recheck/wait sequence with disconnect, shutdown, cancellation, stale-listener, and cleanup evidence. Mounted live SQL proves enqueue/claim/presence/settlement parity and 2,048-byte diagnostic truncation. All nine owned round-5 residuals are closed; the unchanged SQL/migration/Tier-0/Tier-4 surface passes 411/411 on PG18.3 with one opt-in skip, Ruff/format, wheel/sdist, and core-only artifact isolation. S3-03 is open; PG16 remains for S3-AUDIT.
- [x] **S3-01-ACCEPT · S3-01 independently accepted** — external verification reproduced 390/390 on live PostgreSQL 18.3 with one opt-in skip, Ruff/format, wheel+sdist, clean worktree, and a fresh core-only wheel proof; the hand-derived oracle, retry/fence/wire/client/capability boundaries and all seven owned round-5 residuals were accepted, no SQL/Tier-0/Tier-4 drift exists, and S3-02 may proceed while the PG16 Stage-3 delta remains honestly unclaimed until CI/audit.
- [x] **S3-01 · Capability protocols, generated wire surface, and HTTP clients** — split the SQL intersection into exact producer/runner/observer/authorization/operator/housekeeper protocols with non-owning close-safe views; added the independently-oracled Protocol-v1.0.4 HTTP catalog, strict bounded request/result models, fence-only claim wire projection, and metadata-driven active/gated/deferred generation; shipped side-effect-free sync/async HTTP clients with exact credentials, protocol/request-id negotiation, typed SQL-domain normalization, fresh-per-attempt retry IDs, keyed-only producer replay, worker-owned settlement replay, no claim replay, owned/borrowed cleanup, cancellation/fork/thread guards, and typed `TQ501`; moved the benchmark runner under `taskq`, removed wheel placeholders/top-level `bench`, and strengthened artifact missing-extra smoke evidence. The unchanged SQL/migrations pass 390/390 on PG18.3 with one opt-in skip, Ruff/format and wheel/sdist builds are clean; S3-02 is open.
- [x] **S3-R5-DELTA · Round-5 remediation delta accepted** — independent review of `49c0d0b..11bba1a` confirmed the nine-path docs-only range, both trailers and same-commit board updates, byte-identical round-5 response hash, every ADR-017/remediation condition and acceptance vector, explicit residual ownership, clean worktree, Ruff, and 366/366 PG18 tests with one opt-in skip; S3-01 is open without a full round 6.
- [x] **S3-R5-DOC · Round-5 documentation remediation** — froze the mounted lifespan-free sub-application and complete envelope ownership, operator-only queue ensure/pool/authorizer split, explicit five-name admin role plus reconcilable non-system roles, mode-honest personal-key policy, hidden deferred-route responders, single-queue-only HTTP long poll with scoped stop cancellation, timeout→`ClaimState.EMPTY`, generated retry classification, canonical authorization matrix, B14 benchmark identity, and the required S3-02/S3-04 vectors; every remaining R5 finding has an owning board slice and no source/SQL/grant/migration changed.
- [x] **S3-R5-CQ · Round-5 Contract questions adjudicated** — accepted ADR-017 / Protocol document revision 1.0.4 defers the SQL-unbacked general list behind a hidden `TQ501`, corrects every surviving operator-minimal statement, removes the unproducible enqueue `created_at` and all non-authoritative echoes, and pins authenticated/non-reflective invalid-request-id behavior; the manifest records no 0.1 `list_jobs`, while SQL contract 0.1.2, grants, source, and migrations remain unchanged.
- [x] **S3-R5-RESPONSE · Round-5 response recorded** — registered the 235-line external response byte-for-byte as immutable Tier 4; verdict BLOCKED, architecture and scope accepted, two Contract questions plus three BLOCKER/five HIGH documentation findings gate S3-01, and the board sequences ADR-017 before docs-only remediation and a targeted delta check.
- [x] **S3-00-R5 · Round-5 design gate assembled** — the immutable Tier-4 request pins the Stage-2 baseline through S3-00 and requires an independently derived Protocol-v1.0.3 route/backing/action/outcome catalog, ADR-014..016 governance audit, H-13/capability feasibility, fence/client/retry security, authorization and credential split, long-poll/lifespan/R2-11 races, OutLabs source validation, packaging/CI/benchmark honesty, scope proof, and an explicit S3-01 verdict; no implementation landed.
- [x] **S3-00-SPEC · Stage-3 integration contracts frozen** — the Tier-3 specification fixes capability-sized transport boundaries, H-13-generated active/gated/deferred HTTP surfaces, exact envelopes/client replay and ownership, authoritative queue authorization with separate operator credentials, connection-free long polling, composable housekeeper/embedded runtime and process budgets, OutLabs catalog/provisioning, and the S3-01..04/AUDIT acceptance matrix; no integration code or SQL change landed.
- [x] **S3-CQ-03 · Final HTTP wire models normalized docs-first** — accepted ADR-016 and Protocol v1 document revision 1.0.3 define bounded request-id mint/echo behavior, correct queue ensure to the exact version-free SQL profile, and retain worker list as a generated typed-capability gate pending R2-16, with the declared-vs-deferred rule explicit and no SQL or migration change.
- [x] **S3-CQ-02 · Queue-profile read contradiction adjudicated docs-first** — accepted ADR-015 and Protocol v1 document revision 1.0.2 visibly defer the unbacked GET route to H-11's Growth §4/R2-16 read-model design, exclude it from H-13's active generated surface, pin `TQ501`, retain stats/admin-ensure as the honest interim posture, and leave SQL contract 0.1.2 plus migrations 0001–0003 unchanged.
- [x] **S3-CQ-01 · HTTP worker presence adjudicated docs-first** — accepted ADR-014 and Protocol v1 document revision 1.0.1 define the canonical route, all-declared-queue `run` authorization, advisory label/authenticated actor split, typed 200 outcomes, presence/job-heartbeat non-confusion rule, shared-fleet honesty edge, and H-13 generation/parity obligation without changing SQL contract 0.1.2 or adding a migration.
- [x] **S2-06-AUDIT · Stage 2D permanent completion evidence** — repeated cancellation, followup, drain-cap, and task-ledger probes return transports and asyncio resources to baseline; CI collects the consumer suite on Python 3.12/3.13 and imports testing without pytest; wheel/sdist × core/HTTP/OutLabs artifact smokes exercise the installed fake/assertion surface. The identical full suite is 366/366 with one opt-in skip on PostgreSQL 18.3 and 16.14, the PG18 million-row gate is 2/2, the clean Python-3.13 no-DB lane is 219/219, Ruff/format are clean, and the exact slice changes no SQL migration, Tier-0/Tier-4, HTTP, OutLabs, listener, CLI, or Stage-3 source.
- [x] **S2-06B · Consumer work, assertion, inline, and drain helpers** — added shared-supervisor synthetic and caller-transaction PostgreSQL `work`, fixed-text safe `require_enqueued`, immediate inline execution with record-only/opt-in bounded followups and cancellation-safe restoration, and sequential real/fake drains that reject unbounded or runaway work; SQL runner adapters now accept an optional borrowed connection without changing transport ownership.
- [x] **S2-06A · Fake client and replacement boundary** — added a core-isolated, fence-free fake with typed single/bulk enqueue, active-key dedup, FIFO due claim, heartbeat, replay-aware settlement intents, safe nested matchers, loud unsupported-command/closed behavior, and exact non-owning `TaskQ.replace_client` restoration across normal, exceptional, nested, and cancellation exits.
- [x] **S2-06-SPEC · Consumer testing contracts frozen** — the Tier-3 Stage-2D specification fixes the test-runner-neutral fake client, exact replacement ownership, fence-free enqueue matchers, shared handler normalization, inline/followup bounds, caller-owned PostgreSQL work/drain transactions, packaging isolation, and the S2-06A/B/audit acceptance matrix; no runtime or Stage-3 code was added.
- [x] **S2-05-AUDIT · Stage 2C permanent completion evidence** — repeated notification/poll, reconnect/close, fatal-admission, cancellation, and resource races join the existing ten-family matrix; live SQL proves poll-only, notification reconnect/wake, fair queues, remote drain, and CLI signal/process-exit paths; R4-09..12 are closed, B8/B13 run as honest fresh-database report-only scenarios, and wheel/sdist × core/HTTP/OutLabs plus Python 3.13 gates pass. The identical suite is 350/350 on PG18.3 and PG16.14 with one opt-in skip, the PG18 million-row gate is 2/2, and Ruff/format are clean; Stage 3 remains untouched.
- [x] **S2-05C · pydantic-settings, worker CLI, and observability** — added core `pydantic-settings`, frozen secret-safe environment/CLI precedence and deployment interlocks, explicit instance/factory registry loading before database construction, bounded SQL/listener ownership, unique worker ids, temporary soft/hard signal handling with unsafe-sync process exit, stable structured events, and fence-free monotonic snapshots; 330/330 pass on PG18 with one opt-in skip and Ruff clean.
- [x] **S2-05B · Capacity-safe claim, presence, and shutdown** — advisory presence now completes before first claim, reports bounded safe metadata, drives degraded/recovered readiness and sticky remote drain; claim-to-submit admission survives graceful/hard stop ordering, fatal reports auto-stop the service, and external `run()` cancellation performs shielded cleanup then re-raises.
- [x] **S2-05A · Notification and authoritative poll kernel** — added a dedicated reconnectable PostgreSQL notification source plus a core worker service with generation-safe coalesced nudges, mandatory monotonic polling, fair queue rotation, capacity-bounded immediate submission, poll-only degradation, and listener catch-up/reconnect; deterministic option, wake, fairness, and reconnect vectors keep notification payloads non-authoritative.
- [x] **S2-05-SPEC · Claim loop and worker CLI contracts frozen** — the Tier-3 Stage-2C specification fixes notification-as-hint plus authoritative monotonic polling, reconnect catch-up, fair capacity-bounded claim admission, advisory presence/remote shutdown, `taskq worker` lifecycle, `pydantic-settings` precedence/interlocks, fence-safe observability, deterministic fault/race machinery, packaging boundaries, and the S2-05A/B/C/audit matrix; no runtime or Stage-3 code was added.
- [x] **R4-AUDIT · Round-4 remediation completion evidence** — the identical 299-test suite passes with one pre-existing opt-in plan skip on PostgreSQL 18.3 and an isolated PostgreSQL 16.14 lane; Ruff and diff hygiene are clean, R4-01..08 are closed, no contract question was opened, and the worker surface stops before S2-05.
- [x] **R4-F04 · Replay oracle and error normalization (R4-07/R4-08)** — the scripted ledger now retains every semantic settlement argument behind fence-safe representations and replay tests assert exact equality; validation/capability failures in no-handler release and invalid-follow-up escape now return fatal runtime reports, pinned for both typed error classes.
- [x] **R4-F03 · Process-exit honesty and dispatch arity (R4-04/R4-05/R4-06)** — lease loss now marks a still-live sync handler as `abandoned_sync`, exposes immediate process-exit necessity, and preserves that history in the terminal report; dispatch consumes registry-frozen positional arity, while regressions cover sync/async keyword-only dispatch, competing capacity waiters, post-deadline heartbeat, fatal auto-drain, and external `run_job` cancellation.
- [x] **R4-F02 · External cancellation (R4-02/R4-06)** — cancelling a submitted job now initiates soft stop, completes shutdown release inside a shielded critical section, and re-raises `CancelledError`; a cancellation callback recovers the before-first-step window, with deterministic mid-handler and immediate-cancel regressions.
- [x] **R4-F01 · Settlement-liveness heartbeat (R4-01/R4-03)** — heartbeat lifetime is now controlled by terminal settlement rather than handler completion; retry backoff is interruptible by lease loss, and deterministic long-backoff vectors prove heartbeat interleaving plus `settling → ownership_lost` suppression.
- [x] **S2-04-R4-RESPONSE · Round-4 response recorded** — registered the external response verbatim as immutable Tier 4; its executed counterexamples leave the SQL safety core intact but block S2-05 on settlement-heartbeat liveness, external-cancellation semantics, process-exit honesty, dispatch arity, and their regression oracles (285/285 baseline on PG18, Ruff clean).
- [x] **S3-PREP-03 · Batch boundary adapters and measured delta** — module-level adapters now validate bulk-enqueue items in one `TaskQ` boundary call and decode each SQL claim batch as one state-checked projection. Fixed-seed toy B2/B3 runs used five repetitions and fresh databases before/after: B2 median throughput 33,029.69→33,216.71 rows/s (+0.57%), worst p99 30.35→30.95 ms (+2.00%); B3 median throughput 798.42→799.90 rows/s (+0.18%), worst p99 3.76→3.09 ms (-17.63%). B2/B3 call SQL directly and do not traverse these Pydantic adapters, so all deltas are recorded as harness/environment noise, not a performance win (285/285 on PG18).
- [x] **S3-PREP-02 · Tagged protocol result unions** — split enqueue dispositions on `status` and all six fenced settlement dispositions on `result` into Pydantic discriminated unions with public concrete variants and module-level parsers; Tier-0 parity proves the tag sets equal the closed protocol outcomes, while eight frozen representative vectors prove byte-identical JSON with no wire-contract change, ADR, or version bump (283/283 on PG18).
- [x] **S3-PREP-01 · Direction-aware extras policy** — documented the ADR-005 boundary rule in `taskq.protocol`: inbound enqueue command/bulk-item models now forbid unknown fields so typos fail locally, while outbound projections/results explicitly ignore additive fields for forward-compatible decoding; typo and unknown-result vectors bring PG18 to 281/281 without changing wire or SQL contracts.
- [x] **S2-04-R4 · Round-4 review packet** — registered an immutable, contract-first adversarial request covering the contract-0.1.2 additive upgrade and verifier, every S2-04 execution/heartbeat/replay/lifecycle acceptance row, mandatory R2-11 live-sync counterexamples, repeated races, real-SQL conservation, resource cleanup, artifact/import isolation, CI collection, and strict absence of S2-05/Stage-3 scope; the reviewer may add only `docs/design-review-4/RESPONSE.md` and must decide whether S2-05 may open.
- [x] **S2-04-AUDIT · Stage 2B permanent completion evidence** — five repeated, barrier-choreographed race families cover both winner orders without correctness sleeps; live SQL vectors prove complete/retry/snooze/cancel/shutdown/no-handler budget and exact event conservation plus committed-response replay; task, exception, executor-thread, and SQL-pool ledgers return to baseline; source CI imports the worker on Python 3.12/3.13 and every fresh wheel/sdist core/HTTP/OutLabs install smokes it outside the checkout. The exact full suite is 279/279 plus the million-row plan gate on PostgreSQL 18.3 and 16.14, with 149/149 in the clean Python 3.13 worker/unit lane.
- [x] **S2-04D · Bounded concurrency and soft stop** — added synchronous slot reservation, duplicate-attempt rejection, capacity waiting, lazy bounded sync execution with active heartbeat while thread-queued, atomic intake close, cooperative/infinite drain, monotonic deadline, shared escalation, async shutdown release, honest live-sync process-exit signaling, fatal auto-stop, and complete task/executor joining; 8 deterministic vectors bring PG18 to 264/264.
- [x] **S2-04C · Verb-aware settlement replay and fault injection** — settlement now retries only the original verb with bounded exponential backoff, validates command-specific outcomes, converges after a committed-but-lost response, keeps heartbeats live until certainty, classifies exhausted certainty as fatal, and applies the frozen invalid-follow-up terminal escape; 13 deterministic vectors bring PG18 to 256/256 and prove one semantic settlement plus one handler invocation under response loss.
- [x] **S2-04B · Monotonic heartbeat and fenced per-job supervision** — added the core worker options/clock/state/report API, exact `lease_seconds/3` cadence, one heartbeat coroutine per active handler, generation-safe checkpoint flush, two-failure recovery/third-failure ownership loss, typed loss, operator grace cancellation, non-retryable runtime failure, no-handler release, async/sync dispatch, and joined lifecycle; 10 deterministic vectors bring PG18 to 243/243 without reading absolute expiry for scheduling.
- [x] **S2-04A · Execution primitives and deterministic harness** — added frozen closed handler intents, thread-safe escalating cancellation, fence-free `JobContext` with generation-safe 2KB checkpoints, exact sync/async one-/two-argument handler registration, public core exports, and private manual-clock/scripted-response-loss utilities; 12 boundary/concurrency vectors bring the PG18 suite to 233/233 with no optional imports or construction-time work.
- [x] **S2-04-SPEC · Worker-runtime contracts frozen** — the new Tier-3 specification fixes the S2-04-only module/API boundary, closed result normalization, cancellation precedence, monotonic lease-derived heartbeat state machine, verb-aware replay, R2-11 sync honesty, bounded supervisor/soft stop, deterministic harness, and A/B/C/D/audit acceptance matrix; S2-05 and Stage 3 remain excluded.
- [x] **S2-CI-01 · Contract 0.1.2 implemented and proven** — immutable migration `0003` appends `claimed_job.lease_seconds`, returns the exact effective duration, advances meta without changing the 40-function surface, and is decoded by the Python transport; `verify()` plus an independent ordered catalog assertion, default/stamped/override vectors, fresh install, and the full `0001 → 0002 → 0003` upgrade chain pass on PG18.3 and PG16.14 (221/221 plus the million-row plan gate on both).
- [x] **S2-CQ-01 · Effective claimed lease adjudicated docs-first** — accepted ADR-013 and amended Protocol v1, the Function Manifest, and Unified Spec §14 before SQL: contract 0.1.2 appends the exact effective `lease_seconds`, retains `lease_expires_at`, and bans client-wall-clock duration derivation; implementation was separately gated as S2-CI-01.
- [x] **S2-AUDIT-03 · Function-specific outcome enforcement** — every scalar and composite transport result is checked against its command's own protocol-owned outcome set; rollback-only wrong-command outcomes become `TQ500` even when the value is valid for a different command (217/217 on PG18 and PG16, plus the plan gate on both).
- [x] **S2-AUDIT-02 · Permanent acceptance evidence** — transport-level 20-way dedup proves one `created`/19 `existed`, captured logs remain fence-free, SQL construction/commands leave no background tasks or checked-out connections, transaction vectors conserve domain/job/event rows, and CI now runs the full suite on PG16/PG18 plus explicit core/HTTP/outlabs isolation on Python 3.12/3.13 and on every Python-3.12 wheel/sdist; all local mirrors pass (216/216 + plan gate on both PG versions, 73 Python-3.13 unit tests).
- [x] **S2-AUDIT-01 · Protocol single-source correction** — `taskq.protocol` now owns the closed 30-command names, SQL identities, capability roles, outcomes, TQ errors/retryability, and replay rules; typed settle/job/operator enums reject invented values, while independent parity proves exact agreement with the Tier-0-derived machine manifest (214/214 on PG18).
- [x] **S2-03 · Typed facade and transactional enqueue** — `TaskQ` compiles registered canonical tasks and retry stamps exactly once, keeps raw enqueue explicitly opt-in, and executes single/bulk enqueue on the caller's exact `AsyncSession`/`AsyncConnection` without owning its lifecycle; commit, rollback, autobegin, savepoint, cancellation/error ownership, non-SQL rejection, and no-background-work contracts pass on PG16/PG18, while clean wheel/sdist core installs import the complete Stage 2A surface (212/212 each).
- [x] **S2-02 · Complete async SQL transport** — runtime-checkable `TaskqTransport` and lazy `SqlTaskqTransport` cover all 30 manifest-public functions with fixed bound calls, typed/fence-safe adapters, no table DML or implicit retries, owned/borrowed engine semantics, SQLSTATE-only failures, malformed-bulk invariants, and transaction rollback/cancellation; every method passes through its least-capability role with cross-role denials on PG16 and PG18 (201/201 each).
- [x] **S2-01 · Typed task registry and protocol values** — immutable generic Pydantic task metadata validates canonical names, queues, aliases, stamped retry policy, handler annotations, and JSON payloads; collision-atomic deterministic registration preserves rename dispatch; the closed enqueue/TQ models, fence-redacted claim projection, SQLSTATE-only typed errors, safe public exports, and 62 unit/property vectors bring the PG18 suite to 188/188.
- [x] **R3-F08 · Cross-version exact-catalog normalization** — the exact constraint axis now excludes PostgreSQL 18's version-specific `NOT NULL` projection while table shapes continue to close nullability; the identical 126-test suite and opt-in million-row plan gate pass on PostgreSQL 16.14 and 18.3.
- [x] **R3-F07 · Plan-query drift detection** — every representative million-row structural query is now bound to normalized fragments from the actual owning function definition; a rollback-only full-scan mutation proves the regular guard fails on function drift and recovers after rollback (126/126 plus the opt-in gate on PG18).
- [x] **R3-F06 · Benchmark reset and conservation** — every B1–B4 scenario now creates/migrates/fingerprints/drops its own fresh database; B4 stops and joins producers before a bounded worker drain, then records and asserts accepted = terminal + active with zero active/running jobs or attempts (all four toy smokes green, no databases leaked).
- [x] **R3-F05 · Built-artifact CI gate** — CI builds wheel + sdist, installs each core and HTTP extra into clean environments outside the checkout, proves optional-import isolation and installed-package provenance, exercises both entry points, asserts the packaged 0001+0002/40-function manifest, and performs a fresh database CLI migrate + exact verify; the identical four-environment smoke is green locally.
- [x] **R3-F04 · Manifest-complete T2/T8 coverage** — closed ledgers cover all 30 public functions, registered errors, replay declarations, and exact grants; direct vectors fill bulk/runner/observer/operator/housekeeper gaps, assert safe views and shadow resistance, add concurrent install + CLI gates, reuse failure/sync/upgrade/corruption T8 evidence, and extend T4 with heartbeat and worker-cancel replay transitions (125/125 on PG18).
- [x] **R3-F03 · Reserved-role validation** — migration preflight now rejects colliding reserved names with LOGIN, SUPERUSER, CREATEROLE, CREATEDB, REPLICATION, BYPASSRLS, or inherited membership before target-database DDL; seven fresh-database probes prove atomic refusal and lock cleanup, while the exact verifier enforces the installed role manifest (113/113 on PG18).
- [x] **R3-F02 · Migration lock failure recovery** — caller-owned migrations now use a transaction advisory lock while runner-owned multi-transaction applies retain an explicitly released session lock; async/sync-adapter × caller/runner failure probes leave zero locks and prove immediate second-connection recovery (106/106 on PG18).
- [x] **R3-F01 · Exact machine-readable manifest + verifier** — the independent 0.1.1 catalog projection closes the 40-function surface and exact role/relation/type/index/constraint/view/ACL/seed axes; read-only verification rejects 36 rollback-only corruptions, including all five R3-01 counterexamples, then proves restoration green (102/102 on PG18).
- [x] **R3-CI · Implement contract 0.1.1** — immutable migration `0002_contract_0_1_1` adds the owner-only byte-safe truncation helper, applies ADR-012 null boundaries and diagnostic caps, advances the contract version, and passes fresh-chain plus `0001` upgrade vectors (64/64 on PG18).
- [x] **R3-CQ · Contract questions adjudicated docs-first** — accepted [ADR-012](docs/adr/ADR-012-null-boundaries-byte-safe-diagnostics.md) makes explicit null invalid (`TQ422`), caps stored diagnostics by UTF-8 bytes with settlement-safe truncation, adds the owner-only helper to the Function Manifest before SQL, and advances the immutable migration chain to contract 0.1.1/`0002`.
- [x] **R3-01 · External response processed** — the immutable [round-3 response](docs/design-review-3/RESPONSE.md) was independently adjudicated: verdict BLOCKED; all 7 findings accepted; CQ-01/CQ-02 recorded above; S2-01 remains closed.
- [x] **S2-00 · Stage-2A implementation specification** — the new Tier-3 spec fixes the typed task/registry boundary, closed 0.1 outcomes and TQ errors, complete async SQL transport scope, caller-vs-transport transaction ownership, fence/import safety, and the S2-01..03 acceptance matrix; it remains subordinate to the blocked round-3 remediation.
- [x] **Design phase** — spec v1.6, ADR-001..011, two review rounds folded, Protocol v1 + Function Manifest canonical, docs constitution (`6cf6793`..`e1237c5`)
- [x] **S1 opening slice** — migration `0001_initial.sql` (6 roles, 39 hardened functions, self-checking), ADR-004 runner (`migrate`/`migrate_sync`/`verify` + CLI), T1 (26) + T2 (15) suites, 42/42 green vs PG 18.3, wheel packaging fixed, single-writer ledger + typed-cancel reconciliations in manifest errata §8 (`3e7d55d`)
- [x] **S1-01 · T3 choreographed races** — six advisory-barrier/hold-open race cases run deterministically for 20 rounds each: same-key convergence, double-claim exclusion, post-reap fence loss, cross-verb settle conflict, ten-way cap admission, and the single permitted pause slip.
- [x] **S1-02 · T3-R randomized stress** — seed-replayable, env-scalable producer/worker/operator load mixes all 0.1 settle verbs, then drains and asserts durable duplicate-claim, attempt-token, conservation, terminal-state, and no-wedge invariants (30s default run green with seed `424242`).
- [x] **S1-03 · T4 stateful model** — Hypothesis drives enqueue/claim/complete/fail/release/snooze/cancel/lease-rewind+tick/redrive through capability roles; every step reconciles budget, fence, attempt-ledger, terminal-shape, dedup, and conservation invariants (20×40 default green with seed `24680`).
- [x] **S1-04 · verify corruption matrix** — T2 now corrupts and restores each hardening axis; `verify()` precisely names missing pinned paths, PUBLIC EXECUTE, wrong ownership, ledger checksum drift, and a missing capability role, then proves the restored catalog green.
- [x] **S1-05 · PG16 lane** — the identical 54-test suite passes on PostgreSQL 16.14 and 18.3, including the uuid7 fallback, races, stress, model, and verifier corruption matrix; no PG16 manifest caveat was required.
- [x] **S1-06 · 1M-row plan checks** — opt-in `tests/test_plans.py` seeds mixed states, stabilizes stats/visibility, runs `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`, and structurally asserts claim/dedup/reap/stats index families, bounded hot-path rows, and no full `jobs` scan (two consecutive PG18 runs green).
- [x] **S1-07 · B1–B4 benchmark smoke** — packaged `taskq-bench` runs single enqueue, 1000-row bulk, empty/deep claim→settle, and mixed producer/worker load for ≥3 repetitions; toy tests and the CLI print/write JSON with method, machine/PG/settings, WAL/storage/tuple/lock/connection, latency/throughput, event-loop, and structural EXPLAIN evidence. No baseline was created.
- [x] **S1-08 · CI wiring** — GitHub Actions now gates Ruff check/format, Python 3.12/3.13 core+HTTP import isolation and T1, PostgreSQL 16/18 SQL contracts, PG18 races/T4, migrations, and B1–B4 smoke; README records the required branch-protection checks.
- [x] **S1-09 · Stage-1 exit review packet** — the Build Plan records every exit gate green and the immutable Tier-4 [round-3 request](docs/design-review-3/REQUEST.md) gives Andi a contract-first audit program for migration 0001, runner/verifier, SQL suites, plans, benchmarks, packaging, and CI.
