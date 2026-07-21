# taskq — QDarte contact-verify C6-00 compatibility ledger

> **Status:** Tier-3 C6-00 inventory. Captured 2026-07-21 from the named
> QDarte development source revisions and the guarded local development
> database. This is evidence only: it changes no QDarte route, worker,
> credential, queue row, package database, provider, deployment, or production
> setting.
>
> **Authority:** The Stage 5 QDarte Contact Verify Consolidation Specification
> owns the C1–C7 destination. The Stage 5 QDarte Contact Verify Compatibility
> and Cutover Specification owns the C6 work order. This ledger freezes the
> current direct-lane facts C6-01 must preserve or intentionally replace via
> an approved caller-compatible adapter.

## 1. Provenance and environment identity

| Item | Value |
| --- | --- |
| API source | `qdarteAPI` `20a39adc92995c8116b429392036007aedf83709` (`codex/taskq-pilot-p1`) |
| Worker source | `qdarte-workers` `abeaac12e3ba6e657032ae98b995098a8a1276e7` (`codex/taskq-pilot-p1`) |
| Environment health | `development`, database identity verified as `development` |
| Database identity | `9018ce30-f508-42ad-aeaa-9f3bab9e55d5` |
| Database observation | PostgreSQL read-only transaction at `2026-07-21 23:41:48.149234+00` |

The local development process and the durable database identity agreed before
the observation. No secret, DSN, token, payload, candidate phone, or provider
response is recorded in this ledger.

## 2. Current caller contract

All routes below are inside the workers router. The router applies
`require_worker_route_access`; its current path dispatch requires
`require_job_control` for `/ops/jobs`, `/ops/cutover`, and `/ops/taskq`.

| Current route | Input | Current response | Current backend fact |
| --- | --- | --- | --- |
| `POST /ops/jobs/contact-verify-scope` | `ContactVerifyScopeJobCreateRequest` | `WorkerJobDetail` | Incumbent direct `WorkerJobService` only. |
| `POST /ops/cutover/jobs/contact-verify-scope` | Same request | `ContactVerifyCutoverEnqueueResponse` (`route`, exactly one of `legacy_job` / `taskq_job`) | Chooses at request time from `settings.taskq_allows(job_type="contact_verify_scope", queue="comms")`. |
| `POST /ops/taskq/jobs/contact-verify-scope` | Same request | `TaskqEnqueueResponse` | Calls the host-owned direct `TaskqClient` against the incumbent database catalog. |
| `POST /worker/jobs/{job_id}/contact-verify-results` | Worker result request | `WorkerJobContactVerifyResultResponse` | Incumbent direct worker result path. |
| `POST /worker/taskq/jobs/{job_id}/contact-verify-results` | Same result request | `WorkerJobContactVerifyResultResponse` | Host-owned direct-taskq result path; validates the running job and planned entity before applying a result. |

`ContactVerifyScopeJobCreateRequest` forbids unknown fields and carries:
`scope_kind`, `scope_key` (minimum length 2), optional `content_types` and
`place_ids`, bounded optional `limit` (1–500),
`require_unverified_only` (default true), bounded `priority` (0–1000),
`browser`, attribution fields, optional `parent_job_id`, optional idempotency
key, and optional artifact policy. The direct taskq idempotency derivation is
the supplied key or `contact_verify_scope:<scope_kind>:<scope_key>`.

The existing host-owned taskq response includes `job_id`, `created`, `queue`,
`job_type`, `idempotency_key`, and `planned_entities`. This is a current source
fact, not the future package public contract.

## 3. Worker and result-path inventory

The incumbent worker client claims from `/worker/taskq/jobs/claim` with queue,
worker identity, job types, batch, and optional lease. The current worker-loop
path dispatches only `contact_verify_scope` on that taskq loop and otherwise
releases unsupported claims. Its contact handler heartbeats, invokes the
network verifier per planned entity, posts each result to
`/worker/taskq/jobs/{job_id}/contact-verify-results`, then completes/fails or
releases through the same incumbent worker client.

The source proves that the incumbent result payload contains attempt and worker
identities plus the planned entity/place identity and provider result fields.
The current host route validates a running job, requires the same job type,
loads the authoritative planned payload, and rejects an unplanned entity or a
place mismatch before calling the domain apply service.

This incumbent result path is **not** the CV-04 closed package reporter. The
package reporter remains a separate, loopback-only local harness with one
package queue/type and a server-owned stable effect ledger. C6 must not reuse
the copied direct worker model, give a package worker a database credential, or
turn either result path into a generic package endpoint.

## 4. Compatibility delta C6 must resolve

The current `/ops/cutover/...` endpoint is not the frozen C6 closed mode model:
it performs a settings-driven request-time choice between `legacy` and the
host-owned direct `taskq` implementation, and exposes a route discriminator to
the caller. The future C6 adapter must not inherit this as an implicit
compatibility promise.

C6-01 must instead establish the closed startup-validated `legacy`,
`draining`, and `package` modes from the C6 specification, sample the mode
once, and prove there is one publisher/consumer path per request. Before any
adapter change, it must explicitly decide and test the caller-compatible shape
for the existing cutover endpoint (including whether the existing route is
retired, retained as a compatibility facade, or receives a separately approved
revision). It may not retain a dual-publisher choice, fallback after an
ambiguous admission, active-job import, or direct-package row copy.

## 5. Direct-lane high-water baseline

The guarded local `qdarteapi_dev.taskq` observation is deliberately limited to
the direct `contact_verify_scope` lane:

| Oracle | Result |
| --- | --- |
| Direct contact jobs | `0`; `max(created_at)=null`; `max(updated_at)=null` |
| Direct active contact jobs (`queued`, `blocked`, `running`) | `0` |
| Direct contact attempts | `0` |
| Direct contact events | `0` |

This is a local baseline, not a drain attestation for a later cutover. C6-02
must create a fresh, bounded, repeatable drain oracle immediately before any
local package admission and invalidate it on any subsequent direct insertion.

## 6. What C6-00 opens and does not open

C6-00 opens only C6-01’s implementation design and local no-fallback vectors.
It does not authorize a host mode change, package database creation in a
lasting environment, package publish, direct drain, production action, broad
worker, provider invocation, retirement, or any non-contact work.
