# Flow-control 0.6.4–0.6.6 docs/operator review — 2026-08-07

- **Scope:** the operator-facing documentation of the flow-control features
  shipped in SQL contracts 0.6.3 → 0.6.6 (breaker streak/rate/latency
  tripping, the queue operator audit log, the audit prune verb), across three
  surfaces:
  - `docs/Flow Control Operator Runbook.md` (this repo)
  - `docs/CLI.md` (this repo)
  - `outlabs-taskq-docs` `content/4.operations/3.flow-control.md`
    (→ taskq.outlabs.io)
- **Method:** every documented command and SQL example was executed against a
  throwaway PostgreSQL 16.14 migrated 0001→0039 **through the real CLI**
  (`db plan` → `--yes db migrate --plan-digest …` → `target bind` → replan →
  migrate; client 0.1.0a26). Flags and defaults were diffed against `--help`
  output; grants claims were probed live with `SET ROLE`; stale-claim checks
  were made against the shipped migrations. Companion documents:
  `flow-control-0.6.4-0.6.6-review-2026-08-07.md` (SQL) and
  `flow-control-0.6.4-0.6.6-test-review-2026-08-07.md` (tests).

## Verdict: FIX-THEN-PUBLISH

The docs are impressively current on the substance: the three-trigger breaker
model with the enumerated `reason` field, the audit log, and prune are all
documented, and every flow-control command exists with exactly the documented
flags, defaults, and outcomes. But the flagship new maintenance example fails
as written on both doc surfaces (D1), the observability section is not
runnable under the roles the docs themselves prescribe (D2), and the runbook
contradicts itself in three places where 0.6.5 made older sentences false
(D3–D5).

| # | Class | Finding | Doc location | Contradicting code |
|---|---|---|---|---|
| D1 | wrong | `taskq maintenance prune-audit` documented without `--yes`; the CLI refuses it as destructive | Runbook:277; docs-site flow-control.md:156; CLI.md:149-151 omits it from the destructive list | `src/taskq/cli/app.py` (`maintenance.prune-audit` spec, `destructive=True`); live refusal `CLI_SAFETY: this destructive command requires --yes` |
| D2 | wrong | §9 observability SQL is permission-denied for the documented roles: 3 of 4 blocks fail under both taskq roles; `queue_health(NULL)` fails under `taskq_operator` | Runbook:199-219 (+ :22, :232 directing operators there); docs-site:116-131 | `src/taskq/sql/manifest.py:581` (`queue_health` → observer only); verifier `relation_privileges` (zero table grants); live `SET ROLE` probes |
| D3 | stale | Header says "SQL contracts 0.4.0–0.6.4" while the same docs describe 0.6.5/0.6.6 features | Runbook:3 (vs :242 "as of 0.6.6"); docs-site:8 | migrations 0037/0039; `manifest.py:13` `CONTRACT_VERSION = "0.6.6"` |
| D4 | stale | "A queue-scoped audit table that would cover those too is a documented follow-up" — the follow-up shipped in the same range | Runbook:221-223 | `0037_queue_audit.sql`; contradicted by Runbook §12 (:255-262) in the same file |
| D5 | stale | "Record what you set and why (there is no config-history view yet)" | Runbook:240 | `0037_queue_audit.sql:126-143` (`{before, after}` config-history); Runbook:260-261 |
| D6 | editorial | Unfinished self-correction shipped: "**Schedule smear:** `taskq queue`… no — schedules:" | Runbook:157 | — |
| D7 | missing | Audit `event_type` vocabulary never enumerated (`breaker_config_set`, `breaker_rate_set`, `breaker_latency_set`, `breaker_tripped`, `breaker_force_closed`, `aging_set`) | Runbook §12; docs-site:138-151 | `0037_queue_audit.sql:142,167,191,239,288,315` |
| D8 | missing | `queue audit` on an unknown/misspelled queue silently returns an empty page (CLI `{"items": []}`, exit 0; SQL 0 rows, no TQ001) — undocumented | Runbook §12; docs-site:143-149 | `0037_queue_audit.sql:80-90`; SQL-review finding F3 |
| D9 | missing | Incident guidance omits two shipped behaviors: `set-breaker --off` leaves rate/latency config dormant, and `close-breaker` after a rate/latency trip re-trips off the stale window (SQL-review F1) — "if it keeps re-tripping the threshold is too low" misdiagnoses that case | Runbook:59, :92-96; docs-site:34 | `0035_breaker_rate_tripping.sql:24-27` (dormant-config design); F1 reproduction in the SQL review |

## D1 — the documented prune invocation fails

Runbook:277 and docs-site:156 both show:

```bash
taskq maintenance prune-audit --older-than-hours 2160
```

and both call it "safe to run on a schedule." Executed verbatim (with
connection/actor/environment flags), the CLI refuses:
`mutation_refused / CLI_SAFETY — this destructive command requires --yes`.
With `--yes` it works (`{"deleted": 0}`). A cron job built from the docs would
refuse on every run. `docs/CLI.md:149-151` enumerates the destructive
commands requiring `--yes` ("schema migration, target bind/rotation, purge,
bulk redrive, workflow cancel, schedule retirement, lease expiry, janitor,
auth reconciliation") — `prune-audit` was marked destructive in its command
spec but never added to this list. Fix: add `--yes` to both examples, mention
the destructive gate, and add prune-audit to the CLI.md list.

## D2 — the observability section is not runnable by the documented roles

Both docs frame the §9 / "Observing flow control" SQL as the operator's
observability path (Runbook:22 "read the queue's current behavior … and the
queries in §9"; :232 baseline capture; the docs-site note at :10-12 says all
verbs require `taskq_operator`). Live `SET ROLE` probes against the migrated
database:

| Documented query | as `taskq_operator` | as `taskq_observer` |
|---|---|---|
| `SELECT … FROM taskq.queue_health(NULL)` | **permission denied** | works |
| `SELECT … FROM taskq.queue_flow WHERE queue=…` | permission denied | permission denied |
| `job_events ⋈ jobs` breaker timeline | permission denied | permission denied |
| `SELECT * FROM taskq.queue_counters …` | permission denied | permission denied |
| `SELECT * FROM taskq.flow_limits` | permission denied | permission denied |

This is the read-model discipline working as designed — application roles get
no table SELECTs (the verifier enforces exactly zero relation grants beyond
the three views), and `queue_health` is granted to `taskq_observer` only
(`manifest.py:581`). The docs never say these queries need a DBA/superuser
connection. Fix: label the raw-table blocks "DBA connection required", route
operator guidance through the granted surfaces (`taskq queue health`,
`taskq queue audit`, `taskq metrics`, `queue show`), and note that direct-SQL
`queue_health` needs the observer role.

## D3–D5 — sentences 0.6.5 made false

- Runbook:3 and docs-site:8 still bound the plane at "0.4.0–0.6.4" while §11
  (Runbook:242) says "as of 0.6.6" and both docs describe the 0.6.5 audit log
  and 0.6.6 prune. Bump both headers to 0.6.6.
- Runbook:221-223 still calls the queue-scoped audit table "a documented
  follow-up" — it shipped as migration 0037 and is documented twelve sections
  later in the same file (§12). Replace with a pointer: automatic transitions
  → `job_events`; operator actions → the §12 audit log.
- Runbook:240 still instructs "Record what you set and why (there is no
  config-history view yet)" — `taskq queue audit` with `{before, after}` is
  that view. Replace with "the audit log records it (§12)."

## What was verified accurate (credit)

- **Every documented command exists and behaves as documented.** Executed
  live: `queue set-breaker` (created), `set-breaker-rate` (updated),
  `set-breaker-latency` (updated / `--off` → cleared), `trip-breaker` (open),
  `close-breaker` (closed), `set-aging` (updated), `set-flow-limit`
  (created), `queue audit` (+ `--limit`, `--before-id` keyset paging),
  `queue health`, `maintenance prune-audit` (with `--yes`),
  `schedule set-smear` (exists; TQ001 on unknown schedule as expected).
- **Flags and defaults match the text exactly:** `--threshold-ms` (≥1),
  `--window-seconds` default 60 (1..86400), `--min-volume` default 10 (≥1),
  `--off` mutually exclusive with the value flag; `audit --limit` default 50
  (1..100), `--before-id` ≥1; `prune-audit --older-than-hours` ≥1 required.
- **SQL signatures as printed work:** `set_breaker_config('q',5,30,1,actor)`,
  `set_breaker_rate('q',0.5,60,20,actor)`,
  `set_breaker_latency('q',2000,60,10,actor)`,
  `list_queue_audit('q',50,NULL)`, `prune_queue_audit(2160)` (returns count).
  `_audit_queue` is correctly owner-internal (42501 for operator; verified in
  the SQL review).
- **Three-trigger model:** both docs describe streak/rate/latency with the
  `reason` field enumerated (`streak`, `rate`, `latency`) — matches
  `0038:132-134` and live trip events. Neither doc still says latency is a
  roadmap item.
- **Audit claims:** the six covered verbs are listed correctly
  (`set-flow-limit` correctly excluded as key-scoped, per `0037:23`); actor +
  `{before, after}` render exactly as documented (verified rows); "a failed
  verb writes nothing" is true (TQ001 and TQ422 both leave zero rows —
  verified here and in the test review); read roles operator+observer and
  prune roles housekeeper+operator match the shipped GRANTs
  (`0037:94-95`, `0039:42-43`).
- **Safety-model claims:** mutations without `--expected-environment` are
  refused (`CLI_SAFETY: mutation requires an expected environment`),
  matching Runbook:19-20 and docs-site:11; reads (`queue audit`) work without
  an actor.
- **Health detail:** `queue health` carries
  `detail.breaker {state, opened_total, tripped_at}` as claimed
  (Runbook:87-90; docs-site:114).
- **Tuning guidance** (Runbook:67-85; docs-site:46) matches implementation
  semantics: average-latency tumbling window, `min-volume` floor, one slow
  job among many not tripping, threshold-vs-p50 advice.

## Aside (out of flow-control scope, observed while following the docs)

The documented install flow's first `db migrate` on an unbound target fails
with an opaque `CLI_INTERNAL: command failed with DBAPIError` instead of
surfacing the underlying `0020 refuses an unbound taskq target; run taskq
target bind first` message — `docs/CLI.md:119-120` promises structured errors
with a remediation hint, and this path swallows exactly the hint the operator
needs. `target bind` also demands `--expected-installation-id` (fetchable via
`target show`), which the bind example flow in CLI.md does not mention.

## Not verified

- The HTTP transport surface (all new verbs are SQL/CLI-only by design; HTTP
  correctly raises capability errors per the transport code — not exercised
  against a live facade).
- The published rendering at taskq.outlabs.io (reviewed at source in
  `outlabs-taskq-docs@bd745e3`; the deployed site may lag the repo).
- `docs/CLI.md`'s non-flow-control sections (contexts, watch/wait, plan/apply
  beyond the migrate path actually exercised).
