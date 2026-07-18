# taskq — Build Plan

> **Status:** Operating plan — 2026-07-18. The single "what happens next, in order" document. Decisions live in `adr/`; contracts in the Protocol v1 + 0.1 Function Manifest; this file only sequences work and names exit gates. Update it as stages complete; never let it restate contract detail.

## Where the project stands

Design is complete and has passed two design reviews (round 1 → ADR-001..010; round 2 → ADR-011 + spec v1.6 with all 19 findings folded in). The wire contract (Protocol v1) and the 0.1.x SQL surface (Function Manifest) are canonical. Stage 1's implementation reached its internal exit gates, but the [round-3 implementation review](./design-review-3/RESPONSE.md) returned **BLOCKED**. ADR-012 resolves its two Contract questions as SQL contract 0.1.1; all seven findings are now closed, with only the final PG16/PG18 portability audit remaining before the Python runtime begins.

## Stage 1 — secure SQL kernel (ROUND-3 REMEDIATION IN PROGRESS — CONTRACT QUESTIONS RESOLVED 2026-07-18)

**Landed:** immutable migrations `0001_initial.sql` + `0002_contract_0_1_1.sql` (6 validated capability roles, 11 tables, 3 composites, 40 hardened functions, byte-safe diagnostics, 0.1-only seeding); the ADR-004 runner (`migrate`/`migrate_sync`/`verify` + CLI) with transaction-safe lock ownership; the exact machine-readable catalog verifier; manifest-complete T2/T8/T4 coverage; T3 deterministic and randomized concurrency, function-bound million-row structural plans, fresh-database/conservation-proven B1–B4 benchmarks, and source plus built-artifact CI. The regular suite is **126/126 green against PostgreSQL 18.3**, the wheel/sdist four-environment smoke is green, and the opt-in million-row plan gate is green; the final cross-version remediation rerun is in progress. Manifest §8/§9 record the integration errata and contract patch; no Stage-1 contract questions remain open.

Build, in one vertical slice against ephemeral PostgreSQL 18:

1. Migration ledger + advisory-locked `taskq migrate` / read-only `taskq verify` (ADR-004), sync adapter for Alembic hosts.
2. **Migration 0001** derived from the Function Manifest: schema, six capability roles (ADR-010/011), hardened functions (pinned search_path, PUBLIC revoked, manifest-tested ACLs), 0.1 seeding only (real queues + control-state rows; no `_system`, no schedules, no archive).
3. Harness alongside, not after: T1/T2 (contract + privilege/shadow suites, per-capability-role fixtures), the T4 stateful model skeleton, T3 choreographed races (dedup convergence, double-claim, fence `lost`, verb-aware `settle_conflict`, cap no-overshoot, same-millisecond reversed-uuid DAG cases), B1–B4 benchmark smoke.

**Exit gate:** all §16.3-gate cases green on PG16 + PG18; model and SQL agree on generated sequential transitions; untrusted roles can neither DML nor execute ungranted functions; `verify` catches deliberately corrupted ownership/signature/grant/checksum; claim plans stay index-backed at 1M rows.

## Stage 2 — Python runtime

Typed `Task[In, Out]` registry + stable wire names/aliases; `EnqueueResult`/handler-result unions; async SQL transport implementing `TaskqTransport`; SQLAlchemy `AsyncSession` transactional enqueue; worker supervisor (heartbeat-per-job, verb-aware settle retries, R2-11 split cancellation contracts, soft stop); NOTIFY listener + poll; `taskq worker` CLI; `taskq.testing` fixtures + inline transport. Usability gate: a new FastAPI service goes install → typed task <15 lines → transactional enqueue → one-command worker → diagnose a retry from CLI/logs without opening tables.

**Stage 2A specification drafted 2026-07-18:** the [typed-enqueue implementation specification](./Task%20Queue%20Stage%202A%20Typed%20Enqueue%20Specification.md) freezes the S2-01..03 module/API boundary and acceptance matrix. Runtime implementation remains closed until the required Stage-1 remediation passes PostgreSQL 16 and 18.

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
