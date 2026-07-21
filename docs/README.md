# outlabs-taskq documentation — the map

Canonical home for the taskq design corpus. Host apps (Diverse, QDarte, outlabsAPI) treat this `docs/` as source of truth. **This page is the constitution: every document has exactly one tier, and lower tiers yield to higher ones. If two documents disagree, the higher-tier one is right and the lower-tier passage is a bug to fix — never the reverse.**

## Authority chain

    Tier 0  LOCKED CONTRACTS   ->  change requires a new ADR + version bump
    Tier 1  DECISIONS (ADRs)   ->  supersede only by writing a new ADR
    Tier 2  OPERATING PLAN     ->  living; sequences work, never restates contracts
    Tier 3  DESIGN             ->  destination detail; subordinate to Tiers 0-1
    Tier 4  HISTORICAL         ->  immutable provenance; never edited, never authoritative

## Tier 0 — locked contracts

| Doc | Contract |
|---|---|
| [Transport Protocol v1](./Task%20Queue%20Transport%20Protocol%20v1.md) | The wire contract: commands, outcomes, HTTP mapping, TQ registry, retry matrix, version negotiation |
| [0.1 Function Manifest](./Task%20Queue%200.1%20Function%20Manifest.md) | The 0.1 SQL surface: every function's identity, grants, SQLSTATEs, executable body. **Migration 0001 derives from this**; where it differs from Tier-3 spec text, the manifest wins for 0.1 |

## Tier 1 — decisions

[ADR-001..021](./adr/README.md). Accepted 2026-07-18 across the design reviews, contract adjudications, and the Stage-2/Stage-3 gates; ADR-021 (read-model conformance repairs) was accepted 2026-07-20. Reopening one requires new evidence and a new ADR.

## Tier 2 — operating plan

Two living docs: [`TASKS.md`](../TASKS.md) (repo root — the task board, cold-start steps, definition of done; **agents start there**) and the [Build Plan](./Task%20Queue%20Build%20Plan.md) (stages 1–8 with exit gates). Board = tasks; plan = stages; nothing else records progress.

## Tier 3 — design (destination)

| Doc | Scope | Standing |
|---|---|---|
| [Unified Design Spec](./Task%20Queue%20%E2%80%94%20Unified%20Design%20Spec.md) (v1.6) | Full destination design: state machine, DDL, SQL semantics, ops model, PG strategy, migration plan | Authoritative for **semantics and rationale**; the 0.1 Function Manifest wins for 0.1 SQL specifics |
| [Library Extraction Design Brief](./Task%20Queue%20Library%20Extraction%20Design%20Brief.md) | Package layout, import boundaries, host adoption | Subordinate to ADRs |
| [Stage 2A Typed Enqueue Specification](./Task%20Queue%20Stage%202A%20Typed%20Enqueue%20Specification.md) | S2-01..03 Python models, registry, SQL transport, transaction ownership, acceptance matrix | Implementation design; subordinate to Tier 0 and ADRs |
| [Stage 2B Worker Runtime Specification](./Task%20Queue%20Stage%202B%20Worker%20Runtime%20Specification.md) | S2-04A..D worker execution, heartbeat, cancellation, settlement replay, soft stop, acceptance matrix | Implementation design; subordinate to Tier 0 and ADRs |
| [Stage 2C Claim Loop and Worker CLI Specification](./Task%20Queue%20Stage%202C%20Claim%20Loop%20and%20Worker%20CLI%20Specification.md) | S2-05 notification-as-hint polling, capacity-safe claim admission, presence, CLI/configuration, observability, fault/race and PG acceptance matrix | Implementation design; subordinate to Tier 0 and ADRs |
| [Stage 2D Consumer Testing Specification](./Task%20Queue%20Stage%202D%20Consumer%20Testing%20Specification.md) | S2-06 fake client, enqueue assertions, direct work, inline execution, bounded drain, packaging and PG acceptance matrix | Implementation design; subordinate to Tier 0 and ADRs |
| [Stage 3 FastAPI and Authorization Specification](./Task%20Queue%20Stage%203%20FastAPI%20and%20Authorization%20Specification.md) | S3-01..04/AUDIT generated HTTP clients/facade, queue authorization, long poll, lifespan/runtime, embedded worker, OutLabs provisioning, parity and packaging gates | Implementation design; subordinate to Tier 0 and ADRs; Stage 3 independently accepted |
| [Stage 4 outlabsAPI Dogfood Specification](./Task%20Queue%20Stage%204%20outlabsAPI%20Dogfood%20Specification.md) | S4-01..03/AUDIT immutable dependency/auth preflight, one-queue embedded canary, canonical result readback, actual-cluster proof, deploy/failure/rollback evidence | Implementation design; subordinate to Tier 0 and ADRs; S4-CQ-02/03 approved, restricted-runtime proof gates rotation |
| [Read Model Specification](./Task%20Queue%20Read%20Model%20Specification.md) | H-08/H-11 queue-profile and finite job-page activation, authorization, cursor, index, and acceptance evidence | Accepted design by ADR-019 / Protocol 1.0.5 / Manifest 0.1.3; migration 0004 and per-view B9 activation remain implementation gates |
| [Host Branch Reconciliation Specification](./Task%20Queue%20Host%20Branch%20Reconciliation%20Specification.md) | Post-Stage-4 commit ledger, exact-tree reconciliation, authoritative branch cutover, rollback | Complete and independently accepted; `main` is the deployed authoritative line |
| [Legacy Tools Path Retirement Specification](./Task%20Queue%20Legacy%20Tools%20Path%20Retirement%20Specification.md) | Tools-only fallback/consumer retirement, observation gate, zero-DML rollback | L1 eligibility design amended; shared legacy table and non-tools lanes explicitly remain |
| [Authorization & Queue Permissions](./Task%20Queue%20Authorization%20%26%20Queue%20Permissions.md) | Queue-scoped authz, `taskq_{queue}:{action}` grammar, provisioning DX | Detail behind ADR-006/011 |
| [Test & Benchmark Harness](./Task%20Queue%20Test%20%26%20Benchmark%20Harness.md) | Suites T1–T8, exact CI matrix, benchmarks B1–B14 (implemented subset named in the doc) | Implements §16.3 + the review test programs; performance remains report-only |
| [Growth, Topology & Live Visibility](./Task%20Queue%20Growth%2C%20Topology%20%26%20Live%20Visibility.md) | Retention profiles, dedicated-DB topology, read models, SSE | §1–§2 adopted; **§3–§5 PROPOSALS** (decide via future ADRs; R2-14/15/16 amendments accepted) |
| [Borrowed Features 01–14](./taskq-borrowed-features/README.md) | Product feature contracts | Its release-staging table (ADR-009) beats individual headers |

## Tier 4 — historical (immutable)

| Doc | What it preserves |
|---|---|
| [Peer Patterns Research](./Task%20Queue%20Peer%20Patterns%20Research.md) | Why each borrowed pattern exists. Peers described generically by standing rule — the named archive lives outside this repo |
| [Gap Analysis](./Task%20Queue%20Gap%20Analysis.md) | The production defects taskq exists to kill (2026-07-06 audit) |
| [Review round 1](./design-review/README.md) / [round 2](./design-review-2/README.md) / round 3 [request](./design-review-3/REQUEST.md) + [response](./design-review-3/RESPONSE.md) / round 4 [request](./design-review-4/REQUEST.md) + [response](./design-review-4/RESPONSE.md) / round 5 [request](./design-review-5/REQUEST.md) + [response](./design-review-5/RESPONSE.md) / round 6 [request](./design-review-6/REQUEST.md) + [response](./design-review-6/RESPONSE.md) / round 7 [request](./design-review-7/REQUEST.md) + [evidence addendum](./design-review-7/EVIDENCE-ADDENDUM.md) + [response](./design-review-7/RESPONSE.md) + [delta request](./design-review-7/DELTA-REQUEST.md) + [delta response](./design-review-7/DELTA-RESPONSE.md) / round 8 [request](./design-review-8/REQUEST.md) + [response](./design-review-8/RESPONSE.md) + [R-AUDIT request](./design-review-8/R-AUDIT-REQUEST.md) + [response](./design-review-8/R-AUDIT-RESPONSE.md) / round 9 [request](./design-review-9/REQUEST.md) + [response](./design-review-9/RESPONSE.md) | External reviews and requests, verbatim as received or sent; every accepted finding lives on in ADRs/contracts — read those, not these |
| [Staging Cutover Runbook](./Task%20Queue%20Staging%20Cutover%20Runbook.md) | Diverse host-side cutover ops (updated at Stage 6, not before) |

## Rules of the corpus

1. **New contract content** goes in a Tier-0 doc via ADR; **new decisions** are new ADRs; **new design detail** extends the owning Tier-3 doc; **status/progress** goes only in `TASKS.md` (tasks) and the Build Plan (stages). No new top-level docs without a tier assignment here.
2. **Tier-4 files are never edited** (their supersession is recorded in ADRs, not in them).
3. **Third-party queue projects are never named** anywhere in this repo — generic descriptors only (standing rule since the initial commit).
4. Route/URL sketches outside the Protocol doc are illustrative by definition.
5. The `taskq_worker`/`taskq_admin` names in older passages read as "the relevant capability role" (ADR-010/011).
