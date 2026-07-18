# ADR-006 — outlabs-auth permission grammar + authoritative authorization lookup

**Status:** Accepted 2026-07-18
**Resolves:** D-01, D-06; amends the Authorization & Queue Permissions doc (amended same day) and the Diverse scaffold's settlement authorization

## Context

outlabs-auth `0.1.0a24` validates permission names at creation (`utils/validation.py::validate_permission_name` + `schemas/permission.py`): exactly one colon, components matching `^[a-z0-9_-]+$` or exactly `*`. The originally drafted `taskq.{queue}:{action}` is invalid (dots rejected). Separately, the Diverse taskq scaffold authorizes settlement lanes from **caller-supplied** queue/job_type payload fields — an authorization input the caller controls.

## Decision

**Grammar** (verified against the real validators; queue-name charset `[a-z0-9_]` makes the join injective):

    taskq:{action}            # global — action on any queue
    taskq_{queue}:{action}    # per queue
    taskq_{queue}:*           # all actions on one queue

Actions (closed set): `enqueue`, `run` (claim/heartbeat/complete/fail/release/snooze/handler-cancel), `read`, `control`, `admin`. Authorization is an explicit **any-of** over `taskq_{queue}:{action}`, `taskq:{action}` (plus host-mapped legacy candidates during strangler). "One action on all queues" has no wildcard form — that grant is the global name. The catalog builder validates every generated name through outlabs-auth's **real** validator; taskq maintains no look-alike regex.

**Authoritative lookup:** any route addressing a job by id authorizes from taskq's own metadata — authenticate → `get_authorization_projection(job_id)` (a SECURITY DEFINER read exposing only id, queue, task name, status) → authorize `(action, projection.queue)` → invoke the fenced mutation. Caller-supplied queue/job_type are **assertions**: rejected on mismatch (409/422 per ADR-005), never an authorization source. Bulk commands preflight every distinct queue before mutating anything.

**Credentials:** service tokens are the default worker credential (embedded scopes, bypass API-key grant-policy); system-integration keys where Enterprise is enabled (defaults already allow `read`/`run`/`control` prefixes; `enqueue`/`admin` require host policy extension); personal keys never run workers.

## Consequences

- Authorization doc is the normative detail (already amended); the extraction brief §5 mapping tables stay as strangler candidates.
- Diverse cutover runbook amended: settlement queue/job_type demoted to assertion semantics.
- Harness T6 runs the full grammar/scoping matrix, including the settle-with-lied-queue case.
