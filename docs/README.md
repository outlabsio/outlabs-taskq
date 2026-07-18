# outlabs-taskq documentation

Canonical home for the taskq protocol and library design. Host apps (Diverse, QDarte) should treat this repo’s `docs/` as source of truth going forward.

## Reading order

0. **[ADRs](./adr/README.md)** — the accepted decision records (ADR-001..010, 2026-07-18). Where any doc below disagrees with an ADR, the ADR wins. Provenance: the [design review](./design-review/README.md) (7 docs, assessed and folded in same day).
1. **[Task Queue — Unified Design Spec](./Task%20Queue%20%E2%80%94%20Unified%20Design%20Spec.md)** — SQL-first protocol, state machine, functions, ops runbook
2. **[Library Extraction Design Brief](./Task%20Queue%20Library%20Extraction%20Design%20Brief.md)** — package layout, auth layers, optional outlabs-auth
3. **[Authorization & Queue Permissions](./Task%20Queue%20Authorization%20%26%20Queue%20Permissions.md)** — queue-scoped facade authz, `taskq_{queue}:{action}` grammar, provisioning DX
4. **[Test & Benchmark Harness](./Task%20Queue%20Test%20%26%20Benchmark%20Harness.md)** — the package's own suites (T1–T8), CI matrix, benchmarks (B1–B13) + calibrated envelope gates
5. **[Growth, Topology & Live Visibility](./Task%20Queue%20Growth%2C%20Topology%20%26%20Live%20Visibility.md)** — retention profiles at millions-scale, optional dedicated queue database, read-model/stats API for frontends, SSE bridge (§3–§5 proposals pending acceptance)
6. **[Borrowed Features](./taskq-borrowed-features/README.md)** — normative product features (typed enqueue, job keys, snooze, embedded worker, …)
7. **[Peer Patterns Research](./Task%20Queue%20Peer%20Patterns%20Research.md)** — provenance only (do not implement from peer repos)
8. **[Gap Analysis](./Task%20Queue%20Gap%20Analysis.md)** — why taskq exists (Diverse + QDarte defects)
9. **[Staging Cutover Runbook](./Task%20Queue%20Staging%20Cutover%20Runbook.md)** — host cutover ops

## Doc roles

| Kind | Authoritative for |
|---|---|
| `adr/*` | Accepted decisions — override any conflicting passage below |
| `design-review/*` | Review provenance (assessed 2026-07-18; decisions extracted into ADRs) |
| Unified Design Spec | Correctness: statuses, CAS, SQL functions |
| Extraction Design Brief | Repo/package/auth boundaries |
| Authorization & Queue Permissions | Facade authz: actions, queue scoping, outlabs-auth adapter + provisioning |
| Test & Benchmark Harness | The package's own test/bench machinery and CI gates |
| `taskq-borrowed-features/*` | Library DX features to ship |
| Peer research | Historical “why we borrowed X” |
| Gap analysis / cutover | Migration context for host apps |
