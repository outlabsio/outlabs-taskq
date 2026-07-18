# taskq — Build Plan

> **Status:** Operating plan — 2026-07-18. The single "what happens next, in order" document. Decisions live in `adr/`; contracts in the Protocol v1 + 0.1 Function Manifest; this file only sequences work and names exit gates. Update it as stages complete; never let it restate contract detail.

## Where the project stands

Design is complete and has passed four review rounds. The wire contract (Protocol v1) and the 0.1.x SQL surface (Function Manifest) are canonical. Stage 1 and Stage 2C have reached their internal exit gates, including every finding from the round-3 and [round-4](./design-review-4/RESPONSE.md) implementation reviews. ADR-012/013 resolve the contract questions through SQL contract 0.1.2; S2-06 is the remaining Stage-2 consumer-testing surface before Stage 3.

## Stage 1 — secure SQL kernel (COMPLETE)

**Landed:** immutable migrations `0001_initial.sql` + `0002_contract_0_1_1.sql` + `0003_contract_0_1_2.sql` (6 validated capability roles, 11 tables, 3 composites, 40 hardened functions, byte-safe diagnostics, effective lease projection, 0.1-only seeding); the ADR-004 runner (`migrate`/`migrate_sync`/`verify` + CLI) with transaction-safe lock ownership; the exact machine-readable catalog verifier; manifest-complete T2/T8/T4 coverage; T3 deterministic and randomized concurrency, function-bound million-row structural plans, fresh-database/conservation-proven B1–B4 benchmarks, and source plus built-artifact CI. The kernel gates pass PostgreSQL 16.14 and 18.3, including the opt-in million-row plan suite. Manifest §8–§10 record the integration errata and contract patches; no Stage-1 contract questions remain open.

Build, in one vertical slice against ephemeral PostgreSQL 18:

1. Migration ledger + advisory-locked `taskq migrate` / read-only `taskq verify` (ADR-004), sync adapter for Alembic hosts.
2. **Migration 0001** derived from the Function Manifest: schema, six capability roles (ADR-010/011), hardened functions (pinned search_path, PUBLIC revoked, manifest-tested ACLs), 0.1 seeding only (real queues + control-state rows; no `_system`, no schedules, no archive).
3. Harness alongside, not after: T1/T2 (contract + privilege/shadow suites, per-capability-role fixtures), the T4 stateful model skeleton, T3 choreographed races (dedup convergence, double-claim, fence `lost`, verb-aware `settle_conflict`, cap no-overshoot, same-millisecond reversed-uuid DAG cases), B1–B4 benchmark smoke.

**Exit gate:** all §16.3-gate cases green on PG16 + PG18; model and SQL agree on generated sequential transitions; untrusted roles can neither DML nor execute ungranted functions; `verify` catches deliberately corrupted ownership/signature/grant/checksum; claim plans stay index-backed at 1M rows.

## Stage 2 — Python runtime

Typed `Task[In, Out]` registry + stable wire names/aliases; `EnqueueResult`/handler-result unions; async SQL transport implementing `TaskqTransport`; SQLAlchemy `AsyncSession` transactional enqueue; worker supervisor (heartbeat-per-job, verb-aware settle retries, R2-11 split cancellation contracts, soft stop); NOTIFY listener + poll; `taskq worker` CLI; `taskq.testing` fixtures + inline transport. Usability gate: a new FastAPI service goes install → typed task <15 lines → transactional enqueue → one-command worker → diagnose a retry from CLI/logs without opening tables.

**Stage 2A specification drafted 2026-07-18:** the [typed-enqueue implementation specification](./Task%20Queue%20Stage%202A%20Typed%20Enqueue%20Specification.md) freezes the S2-01..03 module/API boundary and acceptance matrix. The required Stage-1 remediation passes PostgreSQL 16 and 18, which opened S2-01.

**Stage 2A implementation:** S2-01 provides immutable generic tasks, collision-safe canonical/alias registration, closed enqueue and TQ values, fence-redacted claim models, and SQLSTATE-only public error normalization. S2-02 adds the complete 30-function async SQL transport, typed adapters, least-capability role probes, and owned transaction rollback/cancellation; the **201/201** suite passes PostgreSQL 16.14 and 18.3. S2-03 transactional enqueue is the final Stage 2A item.

**Stage 2A complete:** S2-03 adds the `TaskQ` typed facade, canonical task/retry compilation, explicit raw escape hatch, and exact caller-owned `AsyncSession`/`AsyncConnection` single and bulk enqueue. Commit, rollback, autobegin, savepoint, cancellation/error ownership, no-background-work, and clean wheel/sdist core-import gates are green. The full **212/212** suite passes PostgreSQL 16.14 and 18.3; Stage 2B opens at S2-04.

**Completion audit:** a trust-but-verify pass found protocol command metadata split between modules and several permanent-evidence gaps. S2-AUDIT-01 moves all 30 command identities, roles, closed outcomes, TQ error/retryability, and replay metadata into `taskq.protocol` and independently proves parity with the Tier-0-derived SQL manifest. Stage 2B is temporarily closed until S2-AUDIT-02 finishes the remaining evidence gates.

**Completion audit green:** S2-AUDIT-02 adds transport-level concurrent dedup, captured fence-log and zero-resource-leak assertions, domain/job/event transaction conservation, full-suite PG16/PG18 CI lanes, and explicit core/HTTP/outlabs isolation for source on Python 3.12/3.13 plus every wheel/sdist on Python 3.12. The local mirror is **216/216 plus the million-row plan gate on PostgreSQL 16.14 and 18.3**, with the clean Python-3.13 unit lane also green. Stage 2A is now independently proven complete and Stage 2B reopens at S2-04.

**Outcome audit green:** S2-AUDIT-03 closes the final adapter-level gap by validating each scalar and composite result against that command's own protocol-owned outcome set, so a value that belongs to another command is a closed `TQ500` failure rather than an accepted cross-command drift. The full **217/217** suite passes PostgreSQL 16.14 and 18.3, with the million-row plan gate green on both.

**Stage 2B contract gate resolved docs-first:** ADR-013 and the Tier-0 contracts advance the design to SQL contract 0.1.2 by appending the effective `lease_seconds` to the claim projection. Workers schedule from that duration monotonically and never derive it from an absolute expiry plus client wall time. Immutable migration 0003 plus PG16/PG18 fresh/upgrade evidence remains the implementation gate before S2-04-SPEC; S2-05 and Stage 3 remain closed.

**Contract 0.1.2 implementation green:** immutable migration 0003, the exact verifier, independent ordered catalog assertion, Python transport decoding, queue-default/task-stamped/claim-override vectors, and the full fresh plus `0001 → 0002 → 0003` upgrade paths pass on PostgreSQL 16.14 and 18.3. The full **221/221** suite and million-row plan gate are green on both. S2-04-SPEC is open; S2-05 and Stage 3 remain closed.

**S2-04 specification frozen:** the [Stage 2B Worker Runtime Specification](./Task%20Queue%20Stage%202B%20Worker%20Runtime%20Specification.md) fixes the worker-only boundary, closed handler intents, execution context, cancellation precedence, monotonic heartbeat state machine, verb-aware settlement replay, bounded sync/async execution, soft stop, deterministic harness, and the S2-04A..D/audit acceptance matrix. S2-04A is open; claiming/NOTIFY/CLI remain S2-05 and integrations remain Stage 3.

**S2-04A implementation:** core now exports the frozen handler intents, thread-safe cancellation token, and fence-free checkpoint context; registration accepts the specified sync/async payload and context forms. Private manual-clock and scripted lost-response utilities establish the deterministic harness without pre-empting `taskq.testing`. The full **233/233** suite passes PostgreSQL 18.3; S2-04B is next.

**S2-04B implementation:** `WorkerSupervisor.run_job` dispatches registered async/sync handlers under one exact-duration monotonic heartbeat, batches checkpoints, recovers from one or two transient heartbeat failures, suppresses every settlement on typed/three-strike ownership loss, enforces operator-cancel grace, and releases unknown types without budget burn. All heartbeat/grace/handler tasks are joined; **243/243** pass PostgreSQL 18.3. S2-04C is next.

**S2-04C implementation:** every settlement path now retries only its original protocol verb with bounded monotonic backoff, validates the verb's closed outcome set, and keeps heartbeating until an authoritative response arrives or certainty is exhausted. Programmable response-loss tests prove handlers execute once and committed mutations converge through `already_settled`; invalid follow-ups take the frozen terminal-fail escape and capability skew is fatal. The full **256/256** suite passes PostgreSQL 18.3; S2-04D is next.

**S2-04D implementation:** the supervisor now reserves bounded capacity synchronously, rejects duplicate active attempts, exposes capacity waiting, and owns one lazy bounded sync executor while heartbeating work queued for a thread. Soft stop atomically closes intake, shares one idempotent drain, supports immediate escalation and monotonic deadlines, releases hard-cancelled async jobs budget-free, and never releases a live sync handler; fatal reports automatically close and drain the supervisor. The full **264/264** suite passes PostgreSQL 18.3; S2-04-AUDIT is the remaining Stage 2B gate.

**Stage 2B complete:** S2-04-AUDIT makes every worker acceptance row permanent with repeated barrier races, real-SQL budget/attempt/event conservation, committed-response replay, zero task/exception/thread/pool leakage, Python 3.12/3.13 import isolation, and worker-aware smoke checks for every wheel/sdist core/HTTP/OutLabs install. The exact full suite is **279/279 plus the million-row plan gate on PostgreSQL 16.14 and 18.3**, and the clean Python-3.13 worker/unit lane is **149/149**. S2-05 stays closed until the round-4 review of Stage 2B and the contract-0.1.2 upgrade path is adjudicated.

**Round-4 handoff ready:** the immutable [review request](./design-review-4/REQUEST.md) requires an independent contract-0.1.2 catalog/upgrade audit and adversarial worker review, with R2-11 live-sync safety, settlement replay, fatal stop, races, SQL conservation, resource cleanup, packaging, and CI as explicit targets. S2-05 remains closed until its response is adjudicated.

**Round-4 verdict blocked:** the immutable [response](./design-review-4/RESPONSE.md) verified the database safety and contract-0.1.2 upgrade core, then executed counterexamples for heartbeat loss during settlement and incorrect external-task cancellation. R4-01..08 must land as a worker-local remediation with permanent race/oracle evidence on PostgreSQL 16 and 18; S2-05 remains closed and no further full review round is required for that gate.

**Round-4 remediation green:** R4-01..08 now keep heartbeats live through settlement, convert external cancellation to shielded shutdown release plus re-raise, expose abandoned-sync process-exit requirements, dispatch from registry-frozen arity, prove replay argument identity, and normalize special-path transport failures. The identical **299/299** suite passes on PostgreSQL 18.3 and 16.14 with one pre-existing opt-in plan skip; S2-05 may open in the next slice but was not started here.

**S2-05 specification frozen:** the [Stage 2C Claim Loop and Worker CLI Specification](./Task%20Queue%20Stage%202C%20Claim%20Loop%20and%20Worker%20CLI%20Specification.md) fixes notification-as-hint plus authoritative polling, reconnect catch-up, fair capacity-safe claim admission, advisory presence and remote drain, `taskq worker` lifecycle, `pydantic-settings` configuration, structured diagnostics, deterministic races, packaging isolation, and the PG16/PG18 acceptance matrix. Implementation opens at S2-05A; no runtime or Stage-3 surface landed with the specification.

**Stage 2C complete:** S2-05A–C add the dedicated reconnectable listener, authoritative fair poll loop, shielded claim admission, advisory presence/remote drain, unified shutdown, frozen secret-safe settings, explicit registry loader, bounded worker CLI, structured diagnostics, and unsafe-sync process-exit boundary. The audit closes the round-4 residue, runs real SQL and subprocess lifecycle probes, makes B8/B13 executable report-only scenarios, schedules the million-row plan gate, and keeps every source/artifact import lane isolated. The identical **350/350** suite passes PostgreSQL 16.14 and 18.3 with one opt-in skip; the 2/2 PG18 plan gate, Python-3.13 unit lane, and wheel/sdist × core/HTTP/OutLabs matrix are green. Stage 3 remains untouched; S2-06 is next.

**S2-06 specification frozen:** the [Stage 2D Consumer Testing Specification](./Task%20Queue%20Stage%202D%20Consumer%20Testing%20Specification.md) fixes the test-runner-neutral fake client, replacement scope, safe enqueue assertions, shared handler normalization, inline/followup limits, transaction-bound PostgreSQL work and drains, fence/resource/import boundaries, and the S2-06A/B/audit matrix. Implementation opens at S2-06A; Stage 3 remains closed.

**S2-06A implementation:** core now ships the fence-free `FakeTaskQClient` with typed producer/runner results, bounded SQL-non-emulating state, safe matcher assertions, and replay-aware settlement records. `TaskQ.replace_client` restores exact non-owned transports across normal, error, nesting, and cancellation boundaries without importing testing or optional packages. S2-06B is next; Stage 3 remains closed.

## Stage 3 — FastAPI + outlabs-auth

`taskq.http` router/runtime/DI per feature 14 + ADR-008 (embedded opt-in, budget printout); sync + async HTTP clients; protocol conformance suite running identical vectors against SQL and HTTP transports; `taskq.outlabs` catalog/authorizer/provisioning per ADR-006 (validated against the real outlabs-auth validator; service-token wildcards, API keys enumerate verbs); facade login = producer+runner+observer+housekeeper, operator pool separate (ADR-011). Gate: the R2 auth matrix (an `emails` token cannot touch `exports`, settle-with-lied-queue rejected) plus lifespan/multi-process budget tests.

## Stage 4 — outlabsAPI dogfood (first host)

One or two low-consequence lanes (tools, notifications) on the embedded runtime; durable run rows replace fire-and-forget; `GET job` result read backs the 202 flow (R2-18 gate). Prereq: outlabsAPI upgrades outlabs-auth to a supported a24+ range (R2-17) or starts on the static adapter. Exit: two normal deploy cycles + one forced failure recovered with zero manual table edits; rollback rehearsed; legacy-broker retirement begins lane-by-lane.

## Stage 5 — QDarte pilot → Stage 6 — Diverse cutover

QDarte: sync HTTP client, queue-scoped service token, one non-chaining lane, shadow reads then canary (full cutover awaits 0.2 chains). Diverse: apply the required corrections (packaged migrations replace the embedded scaffold history; caller-asserted settlement fields demoted; hardened roles), then the existing staged runbook with protocol-adapter routes. Order stands: personal blast radius proves the pattern before the income realm.

## Stage 7/8 — 0.2 and 0.3 capabilities

0.2: lossless-atomic followups (`_enqueue_followup`), dependencies/workflows, schedules (+ seeded janitor row replacing the tick trigger), replace/by-args uniqueness one at a time, completion handles, SSE bridge (per amended Growth §5) — each behind a capability flag and `TQ501` until active. 0.3: partitioned archive (R2-13 ordering), redirect DLQs if ever needed, dedicated-DB topology docs+guard (accepted shape in Growth §3/R2-15), maintenance CLI with version-aware credentials.

## Standing rules while building

- ADRs and the two canonical contract docs win every conflict; changing them is a new ADR, not an edit.
- No feature ships without its manifest entry, privilege tests, parity vectors, and benchmark impact (the roadmap's definition of done).
- Third-party queue projects stay generically described in everything committed; the named archive lives in the private vault note.
- Every stage ends in a commit; external review cadence continues at stage boundaries (round 3 target: post-Stage-1, reviewing migration 0001 + harness against the manifest).
