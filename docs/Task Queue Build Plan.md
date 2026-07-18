# taskq — Build Plan

> **Status:** Operating plan — 2026-07-18. The single "what happens next, in order" document. Decisions live in `adr/`; contracts in the Protocol v1 + 0.1 Function Manifest; this file only sequences work and names exit gates. Update it as stages complete; never let it restate contract detail.

## Where the project stands

Design is complete and twice externally reviewed (round 1 → ADR-001..010; round 2 → ADR-011 + spec v1.6 with all 19 findings folded in). The wire contract (Protocol v1) and the 0.1 SQL surface (Function Manifest) are canonical. The repo history is clean (peer projects referenced generically; named provenance archived privately). No implementation exists beyond the package skeleton — deliberately: contracts froze before code.

## Stage 1 — secure SQL kernel (next)

Build, in one vertical slice against ephemeral PostgreSQL 18:

1. Migration ledger + advisory-locked `taskq migrate` / read-only `taskq verify` (ADR-004), sync adapter for Alembic hosts.
2. **Migration 0001** derived from the Function Manifest: schema, six capability roles (ADR-010/011), hardened functions (pinned search_path, PUBLIC revoked, manifest-tested ACLs), 0.1 seeding only (real queues + control-state rows; no `_system`, no schedules, no archive).
3. Harness alongside, not after: T1/T2 (contract + privilege/shadow suites, per-capability-role fixtures), the T4 stateful model skeleton, T3 choreographed races (dedup convergence, double-claim, fence `lost`, verb-aware `settle_conflict`, cap no-overshoot, same-millisecond reversed-uuid DAG cases), B1–B4 benchmark smoke.

**Exit gate:** all §16.3-gate cases green on PG16 + PG18; model and SQL agree on generated sequential transitions; untrusted roles can neither DML nor execute ungranted functions; `verify` catches deliberately corrupted ownership/signature/grant/checksum; claim plans stay index-backed at 1M rows.

## Stage 2 — Python runtime

Typed `Task[In, Out]` registry + stable wire names/aliases; `EnqueueResult`/handler-result unions; async SQL transport implementing `TaskqTransport`; SQLAlchemy `AsyncSession` transactional enqueue; worker supervisor (heartbeat-per-job, verb-aware settle retries, R2-11 split cancellation contracts, soft stop); NOTIFY listener + poll; `taskq worker` CLI; `taskq.testing` fixtures + inline transport. Usability gate: a new FastAPI service goes install → typed task <15 lines → transactional enqueue → one-command worker → diagnose a retry from CLI/logs without opening tables.

## Stage 3 — FastAPI + outlabs-auth

`taskq.http` router/runtime/DI per feature 14 + ADR-008 (embedded opt-in, budget printout); sync + async HTTP clients; protocol conformance suite running identical vectors against SQL and HTTP transports; `taskq.outlabs` catalog/authorizer/provisioning per ADR-006 (validated against the real outlabs-auth validator; service-token wildcards, API keys enumerate verbs); facade login = producer+runner+observer+housekeeper, operator pool separate (ADR-011). Gate: the R2 auth matrix (an `emails` token cannot touch `exports`, settle-with-lied-queue rejected) plus lifespan/multi-process budget tests.

## Stage 4 — outlabsAPI dogfood (first host)

One or two low-consequence lanes (tools, notifications) on the embedded runtime; durable run rows replace fire-and-forget; `GET job` result read backs the 202 flow (R2-18 gate). Prereq: outlabsAPI upgrades outlabs-auth to a supported a24+ range (R2-17) or starts on the static adapter. Exit: two normal deploy cycles + one forced failure recovered with zero manual table edits; rollback rehearsed; RabbitMQ retirement begins lane-by-lane.

## Stage 5 — QDarte pilot → Stage 6 — Diverse cutover

QDarte: sync HTTP client, queue-scoped service token, one non-chaining lane, shadow reads then canary (full cutover awaits 0.2 chains). Diverse: apply the required corrections (packaged migrations replace the embedded scaffold history; caller-asserted settlement fields demoted; hardened roles), then the existing staged runbook with protocol-adapter routes. Order stands: personal blast radius proves the pattern before the income realm.

## Stage 7/8 — 0.2 and 0.3 capabilities

0.2: lossless-atomic followups (`_enqueue_followup`), dependencies/workflows, schedules (+ seeded janitor row replacing the tick trigger), replace/by-args uniqueness one at a time, completion handles, SSE bridge (per amended Growth §5) — each behind a capability flag and `TQ501` until active. 0.3: partitioned archive (R2-13 ordering), redirect DLQs if ever needed, dedicated-DB topology docs+guard (accepted shape in Growth §3/R2-15), maintenance CLI with version-aware credentials.

## Standing rules while building

- ADRs and the two canonical contract docs win every conflict; changing them is a new ADR, not an edit.
- No feature ships without its manifest entry, privilege tests, parity vectors, and benchmark impact (the roadmap's definition of done).
- Third-party queue projects stay generically described in everything committed; the named archive lives in the private vault note.
- Every stage ends in a commit; external review cadence continues at stage boundaries (round 3 target: post-Stage-1, reviewing migration 0001 + harness against the manifest).
