# ADR-009 — First-release scope and deferred policies

**Status:** Accepted 2026-07-18
**Resolves:** D-08, D-12 — with the three review carve-outs adjudicated 2026-07-18

## Context

The unified design describes the destination: kernel + workflows + schedules + archive + uniqueness modes + embedded runtime. As a first *public contract* that is too much surface to freeze at once. The design-review's cut was accepted with three corrections: it left the 0.1 janitor with no trigger (the schedule row that structurally fires it was deferred), omitted concurrency caps entirely (QDarte's single most load-bearing need), and re-proposed schedule backfill that the spec already has (§6 `catchup_policy`).

## Decision

**`0.1` ships:** migrate/verify/lock tooling (ADR-004); queue definitions + profiles; enqueue (single/bulk), claim, heartbeat, complete, fail, release, snooze, **fenced cancel** (ADR-007); idempotency in **reject** mode with typed `created/existed` results; retry budgets, lease recovery, poison quarantine; **per-resource concurrency caps** (`concurrency_key` + `taskq.concurrency_limits` + try-lock admission — carve-out: already designed, deadlock-free, and required by the QDarte pilot; deferring it would re-stage Stage 5, not save risk); pause/resume, retry/redrive, operator cancel; NOTIFY-nudge + poll; worker runtime with soft stop; migrate-break channel (feature 12 — promoted from NICE); direct SQL + HTTP transports over the frozen protocol (ADR-005); FastAPI + outlabs-auth integrations (ADR-006/008); safe ops views + metrics; the full correctness/security harness (T1–T8, B1–B4 minimum).

**`0.1` janitor trigger (carve-out):** schedules defer to 0.2, so the housekeeper tick hardwires a daily janitor pass (advisory-lock-deduped, same passes, bounded) plus the `taskq janitor` CLI. When schedules land in 0.2, the seeded `taskq-janitor` row replaces the hardwired trigger and the spec's "structurally always called" property is restored. 0.1 retention is **bounded deletes** past `retention_hours` (the partitioned archive is 0.3); enqueue's archived-dependency resolution activates with the archive.

**Deferred:** schedules + backfill (`0.2` — semantics already specified as §6 `catchup_policy skip|fire_once|fire_all` + `max_catchup`; the review's `skip|latest|all_bounded` are the same three policies, no new design); follow-ups, dependencies, workflows (`0.2`, per ADR-007's design); `replace`/`preserve_run_at`/`by_args` uniqueness (`0.2+`, one at a time with full transition specs); redirect DLQs (`0.3` if a real need appears — lineage-on-row is the default posture, feature 07); partitioned archive + retention automation (`0.3`, before the full Diverse cutover, which resolves lineage against archived jobs); exact depth enforcement (stays an advisory metric/backpressure signal — already the spec's posture); blueprints/namespaces (reassess once typed task objects exist); rate/resource admission (only with a demonstrated workload).

## Consequences

- Feature docs carry staging notes; the borrowed-features README maps features → releases.
- QDarte full cutover (chains) waits for `0.2`; its Stage-5 pilot picks non-chaining lanes.
- Capability labels describe maturity, not dates; a feature moves earlier only with complete invariants + tests + benchmarks (roadmap's definition of done).
