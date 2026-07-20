# taskq — outlabsAPI Legacy Tools Path Retirement Specification

> **Status:** Frozen by S4-POST-00; amended by S4-POST-L1-SPEC — 2026-07-20. The
> amendment closes Round-8 findings R8-02/03/05; L2 remains closed until L1 eligibility is accepted.
> **Tier:** 3 implementation design; subordinate to Protocol v1 document revision 1.0.4,
> Function Manifest 0.1.2, and ADR-001..018
> **Prerequisite:** independently accepted Host Branch Reconciliation Specification completion
> **Scope:** retire only the `tool_run` producer fallback and consumer capability in outlabsAPI.
> The shared `outbound_tasks` table, migration, queue service, and non-tools lanes remain.

## 1. Outcome and boundary

Stage 4 proved `umami` and `aerolineas` through taskq while retaining a mutually exclusive legacy
fallback. This slice removes that fallback after an observation gate, without claiming that the
host-wide legacy Postgres queue is obsolete.

The intended end state is:

- `POST /tools/{tool_name}/runs/queued` always uses the taskq producer for registered, allowlisted
  tools when the runtime is ready;
- a registered but non-allowlisted tool never falls back to `outbound_tasks`: it receives the same
  fixed host `503 {"detail":"Queued task processing is unavailable"}` posture as a disabled or
  not-ready taskq runtime, with no request echo or enrollment detail;
- taskq unavailability fails closed with the existing sanitized typed response and never publishes
  to `outbound_tasks`;
- the standing legacy worker no longer dispatches `tool_run` rows after the rollback window;
- synchronous tool execution remains unchanged;
- notification, contact, newsletter, and analytics work continues through `outbound_tasks`;
- historical legacy rows remain readable; no row is copied, rewritten, or deleted; and
- the table and migration `20260616_0005` remain because other active lanes own them.

This slice does not migrate a side-effecting lane. The future hard-kill lease-expiry drill remains a
mandatory gate before any such lane moves to taskq and is neither satisfied nor waived here.

## 2. Retirement eligibility

S4-POST-L1 begins only after branch reconciliation is independently accepted and deployed. It then
records at least seven consecutive production days including two normal deployments with:

1. both read-only tools configured in taskq mode and absent from any legacy producer allowlist;
2. taskq API/worker readiness and canonical authorized readback continuously available apart from
   recorded bounded incidents;
3. keyed `created`/`existed` evidence for each lane and exactly one terminal execution per job;
4. an external invocation ledger reconciled to taskq attempt/event state without payloads or
   credentials. The `umami` lane uses the self-hosted target-service access log as its independent
   invocation counter. The flight lane has no operator-controlled target or egress counter today;
   it therefore records a bounded host outbound-HTTP result counter alongside taskq attempt/event
   arithmetic, explicitly labelled as a non-independent downgrade that proves application delivery
   behavior but not independent target receipt. That downgrade is permitted only for this existing
   read-only lane and is not transferable to any side-effecting lane;
5. zero new `outbound_tasks(kind='tool_run')` rows from the cutover start;
6. zero automatic or operator-triggered fallback after ambiguous enqueue, auth 429/503, taskq 5xx,
   timeout, cancellation, worker restart, or deploy drain; and
7. zero active legacy `tool_run` rows (`pending` or `running`) at the removal boundary; and
8. a caller sweep that enumerates every operator-owned client of `/tools/{tool_name}/runs/queued`,
   records whether it tolerates the canonical `202 {job_id, disposition, status_url}` shape, and
   leaves no active caller dependent on the retired `200 {status, tool_name}` legacy projection.

The zero-insert oracle uses a frozen high-water mark plus count and maximum creation timestamp; it
does not infer absence from current queue depth. The taskq oracle checks jobs, attempts, and events,
while the external counter independently checks handler invocation. Logs are corroborative only.

Any newly observed legacy insertion resets the observation window and blocks retirement until its
cause is explained. Existing terminal legacy rows do not block the slice.

## 3. Two-step code and deployment removal

Retirement is deliberately split so rollback never depends on a consumer that has already vanished.

### S4-POST-L2 — remove the producer fallback

On authoritative `main`:

- remove the `legacy` branch from the queued-tools producer and its legacy response projection;
- make disabled/not-ready taskq return the existing bounded service-unavailable posture;
- retain `TASKQ_TOOLS_ALLOWLIST` as the explicit enrollment gate for registered tools, but remove
  `TASKQ_TOOLS_MODE`. A registered tool absent from that allowlist returns exactly
  `503 {"detail":"Queued task processing is unavailable"}`; it does not enqueue, use a background
  task, expose whether enrollment is missing, or return the retired 200 projection;
- retain no fallback after any enqueue attempt, including ambiguous transport outcomes;
- `TASKQ_ENABLED` remains the runtime mount/readiness control, not permission to publish through
  the retired path. The deployment documentation and settings examples change in the same commit:
  a prior rollback image may still use `TASKQ_TOOLS_MODE=legacy`, while a candidate image must not
  carry that setting;
- keep the legacy worker's `tool_run` handler temporarily for rollback and old queued rows; and
- rework `scripts/verify_restricted_runtime.py` so it retains the real ASGI boot, auth lifecycle,
  and negative capability proofs but inserts and settles a synthetic non-tool legacy row through
  the shared queue/worker harness. It must not import or call `enqueue_tool_task`, and its
  side-effect-free processor must assert the row kind before and after settlement;
- complete the caller sweep in §2(8), with any non-tolerant caller migrated or retired before L2;
- strengthen source and ASGI tests to prove the legacy publisher cannot be reached by tools.

Deploy API first while the old worker still understands `tool_run`. Prove canonical taskq execution,
sanitized failure behavior, and no new legacy rows. Rehearse rollback by deploying the prior API
image with the unchanged worker and settings, run one side-effect-free legacy tool proof, let it
settle naturally, then redeploy the candidate and repeat the taskq proof. No DML is permitted.

### S4-POST-L3 — remove legacy tool consumption

After L2 is independently accepted and one additional healthy deployment window passes:

- remove `tool_run` dispatch from the standing worker and shared processor;
- retain an explicit `tool_run` branch ahead of the generic unknown-task fallback: an unexpected
  historical row must never reach `TaskProcessor.process_unknown_task` or be marked done. It records
  the bounded `retired_tool_run` failure and is retried/terminalized by the existing legacy budget,
  with a critical safe log and metric for operator handling;
- update worker/API documentation, settings examples, and the production verification harness;
- keep the shared queue model, service, migration, worker, and all non-tools cases; and
- remove retired platform variables only after both API and worker run the accepted images.

Before L3 deployment, prove there are no active legacy tool rows. Deploy the worker before deleting
any retired setting. A full rollback after L3 means restoring the paired prior API and worker images;
the unchanged table and migration make that rollback zero-DML.

## 4. Security and authorization

The queued host route continues to require `tools:run`; canonical taskq reads continue to require
the queue-scoped read permission. Removing fallback must not add generic enqueue permission, an
operator credential, direct SQL readback, wildcard scope, or a host-owned copy of the taskq state
model. Invalid, denied, absent, rate-limited, and unavailable requests preserve the accepted
authenticate-first and sanitized-envelope behavior.

The runtime login remains non-superuser and never gains `taskq_operator`. Owner/operator credentials
stay outside API and worker pools. No queue profile, IAM catalog, taskq role, or grant changes are
part of retirement.

## 5. Data retention and operational posture

Retirement is code-path removal, not data destruction. Therefore this slice must not:

- drop or alter `outbound_tasks` or migration `20260616_0005`;
- delete terminal legacy tool rows;
- remove indexes used by remaining lanes;
- change retry semantics for remaining legacy work;
- purge taskq history; or
- claim host-wide queue retirement.

The restore/PITR rehearsal remains a host backlog item. Existing backup evidence is restated in the
completion packet, and the rollback window retains the prior paired images and settings snapshot.

## 6. Acceptance matrix

| ID | Required evidence |
|---|---|
| LR-01 | Seven-day/two-deploy eligibility ledger; `umami` target access-log reconciliation; and the flight lane's explicitly labelled non-independent bounded host-counter/taskq reconciliation |
| LR-02 | Frozen high-water oracle proves zero new legacy `tool_run` rows and zero active rows at cutover |
| LR-03 | ASGI/source tests make the tools legacy publisher unreachable in disabled, not-ready, non-allowlisted, error, and ambiguous cases; the first three share the fixed 503 response |
| LR-04 | Canonical keyed 202→GET succeeds for both lanes with one invocation and no fence/secret leakage |
| LR-05 | Auth denial plus 429/503 remain typed, sanitized, and fail closed without legacy insert |
| LR-06 | L2 rollback/re-enable uses paired supported images and zero DML |
| LR-07 | L3 removes only `tool_run` consumption; an unexpected historical row takes the bounded `retired_tool_run` failure path and can never reach the generic unknown-task success fallback |
| LR-08 | Non-tools enqueue, claim, retry, lease recovery, and terminal behavior remain green |
| LR-09 | Shared table/migration/indexes and terminal history remain unchanged |
| LR-10 | Host suite, Ruff, MyPy, Alembic, image builds, taskq local production-shape gate, health, and budgets green |
| LR-11 | No taskq source/SQL/migration/Tier-0/IAM/capability change |
| LR-12 | Completion packet names exact commits, images, settings digest, counts, rollback evidence, and residual risks |
| LR-13 | L1 caller inventory proves every operator-owned queued-tools caller tolerates canonical 202 or is migrated/retired before L2; the retired 200 shape has no active dependency |

S4-POST-L-AUDIT independently verifies the high-water and invocation oracles, exercises both rollback
directions, and confirms that non-tools legacy work still operates. Only its acceptance closes tools
legacy retirement.

## 7. Stop conditions

Stop before code or deployment if any active caller still depends on legacy `tool_run`, if the route
cannot return the fixed non-enrollment 503 without changing Protocol v1, if the flight-lane downgrade
is represented as independent evidence, if non-tools work shares an inseparable dispatch surface, if
rollback needs row mutation, or if an acceptance oracle requires payload/fence/credential exposure.
Record taskq contract conflicts in `TASKS.md`; record host topology questions in the host completion
packet. Do not expand this slice to side-effecting lanes.
