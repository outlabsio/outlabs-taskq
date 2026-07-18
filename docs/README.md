# outlabs-taskq documentation

Canonical home for the taskq protocol and library design. Host apps (Diverse, QDarte) should treat this repo’s `docs/` as source of truth going forward.

## Reading order

0. **[ADRs](./adr/README.md)** — the accepted decision records (ADR-001..010, 2026-07-18). Where any doc below disagrees with an ADR, the ADR wins. Provenance: the [design review](./design-review/README.md) (7 docs, assessed and folded in same day).
1. **[Transport Protocol v1](./Task%20Queue%20Transport%20Protocol%20v1.md)** — CANONICAL wire contract (adopts review draft 03 with amendments; ADR-005 satisfied)
2. **[0.1 Function Manifest](./Task%20Queue%200.1%20Function%20Manifest.md)** — CANONICAL 0.1 function set: identities, grants, SQLSTATEs, executable bodies (R2-08 closed)
3. **[Task Queue — Unified Design Spec](./Task%20Queue%20%E2%80%94%20Unified%20Design%20Spec.md)** — SQL-first protocol, state machine, functions, ops runbook
4. **[Library Extraction Design Brief](./Task%20Queue%20Library%20Extraction%20Design%20Brief.md)** — package layout, auth layers, optional outlabs-auth
5. **[Authorization & Queue Permissions](./Task%20Queue%20Authorization%20%26%20Queue%20Permissions.md)** — queue-scoped facade authz, `taskq_{queue}:{action}` grammar, provisioning DX
6. **[Test & Benchmark Harness](./Task%20Queue%20Test%20%26%20Benchmark%20Harness.md)** — the package's own suites (T1–T8), CI matrix, benchmarks (B1–B13) + calibrated envelope gates
7. **[Growth, Topology & Live Visibility](./Task%20Queue%20Growth%2C%20Topology%20%26%20Live%20Visibility.md)** — retention profiles at millions-scale, optional dedicated queue database, read-model/stats API for frontends, SSE bridge (§3–§5 proposals pending acceptance)
8. **[Borrowed Features](./taskq-borrowed-features/README.md)** — normative product features (typed enqueue, job keys, snooze, embedded worker, …)
9. **[Peer Patterns Research](./Task%20Queue%20Peer%20Patterns%20Research.md)** — provenance only (do not implement from peer repos)
10. **[Gap Analysis](./Task%20Queue%20Gap%20Analysis.md)** — why taskq exists (Diverse + QDarte defects)
11. **[Staging Cutover Runbook](./Task%20Queue%20Staging%20Cutover%20Runbook.md)** — host cutover ops

## Doc roles

| Kind | Authoritative for |
|---|---|
| `adr/*` | Accepted decisions — override any conflicting passage below |
| `design-review/*` | Review provenance (assessed 2026-07-18; decisions extracted into ADRs) |
| Transport Protocol v1 + 0.1 Function Manifest | The wire contract and the 0.1 SQL surface — migration 0001 derives from these |
| Unified Design Spec | Correctness: statuses, CAS, SQL functions (destination design) |
| Extraction Design Brief | Repo/package/auth boundaries |
| Authorization & Queue Permissions | Facade authz: actions, queue scoping, outlabs-auth adapter + provisioning |
| Test & Benchmark Harness | The package's own test/bench machinery and CI gates |
| `taskq-borrowed-features/*` | Library DX features to ship |
| Peer research | Historical “why we borrowed X” |
| Gap analysis / cutover | Migration context for host apps |
