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

[ADR-001..011](./adr/README.md). Accepted 2026-07-18 across two review rounds. Reopening one requires new evidence and a new ADR.

## Tier 2 — operating plan

Two living docs: [`TASKS.md`](../TASKS.md) (repo root — the task board, cold-start steps, definition of done; **agents start there**) and the [Build Plan](./Task%20Queue%20Build%20Plan.md) (stages 1–8 with exit gates). Board = tasks; plan = stages; nothing else records progress.

## Tier 3 — design (destination)

| Doc | Scope | Standing |
|---|---|---|
| [Unified Design Spec](./Task%20Queue%20%E2%80%94%20Unified%20Design%20Spec.md) (v1.6) | Full destination design: state machine, DDL, SQL semantics, ops model, PG strategy, migration plan | Authoritative for **semantics and rationale**; the 0.1 Function Manifest wins for 0.1 SQL specifics |
| [Library Extraction Design Brief](./Task%20Queue%20Library%20Extraction%20Design%20Brief.md) | Package layout, import boundaries, host adoption | Subordinate to ADRs |
| [Authorization & Queue Permissions](./Task%20Queue%20Authorization%20%26%20Queue%20Permissions.md) | Queue-scoped authz, `taskq_{queue}:{action}` grammar, provisioning DX | Detail behind ADR-006/011 |
| [Test & Benchmark Harness](./Task%20Queue%20Test%20%26%20Benchmark%20Harness.md) | Suites T1–T8, CI matrix, benchmarks B1–B13, envelope gates | Implements §16.3 + the review test programs |
| [Growth, Topology & Live Visibility](./Task%20Queue%20Growth%2C%20Topology%20%26%20Live%20Visibility.md) | Retention profiles, dedicated-DB topology, read models, SSE | §1–§2 adopted; **§3–§5 PROPOSALS** (decide via future ADRs; R2-14/15/16 amendments accepted) |
| [Borrowed Features 01–14](./taskq-borrowed-features/README.md) | Product feature contracts | Its release-staging table (ADR-009) beats individual headers |

## Tier 4 — historical (immutable)

| Doc | What it preserves |
|---|---|
| [Peer Patterns Research](./Task%20Queue%20Peer%20Patterns%20Research.md) | Why each borrowed pattern exists. Peers described generically by standing rule — the named archive lives outside this repo |
| [Gap Analysis](./Task%20Queue%20Gap%20Analysis.md) | The production defects taskq exists to kill (2026-07-06 audit) |
| [Review round 1](./design-review/README.md) / [round 2](./design-review-2/README.md) | External reviews, verbatim as received; every accepted finding lives on in ADRs/contracts — read those, not these |
| [Staging Cutover Runbook](./Task%20Queue%20Staging%20Cutover%20Runbook.md) | Diverse host-side cutover ops (updated at Stage 6, not before) |

## Rules of the corpus

1. **New contract content** goes in a Tier-0 doc via ADR; **new decisions** are new ADRs; **new design detail** extends the owning Tier-3 doc; **status/progress** goes only in `TASKS.md` (tasks) and the Build Plan (stages). No new top-level docs without a tier assignment here.
2. **Tier-4 files are never edited** (their supersession is recorded in ADRs, not in them).
3. **Third-party queue projects are never named** anywhere in this repo — generic descriptors only (standing rule since the initial commit).
4. Route/URL sketches outside the Protocol doc are illustrative by definition.
5. The `taskq_worker`/`taskq_admin` names in older passages read as "the relevant capability role" (ADR-010/011).
