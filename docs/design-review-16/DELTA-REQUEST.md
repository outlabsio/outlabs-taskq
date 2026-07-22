# Internal targeted delta review — QDarte C7 environment plan

## Scope and provenance

Review only the docs-only remediation at `5d08f7c` against the immutable
Round-16 response. The owner has authorized this implementation session to
perform the delta because the usual separate review session is unavailable;
any response must disclose that it is internal and non-independent.

Immutable response SHA-256:
`f7c8cef4a68ad44eb345b26b8062d7f7f41befb517c2d4ae79e951344c66ad39`.

READY opens only C7-01 preflight. It authorizes no package cohort, provider
call, production job, direct retirement, non-contact lane, C7-02+, or Stage 6.

## Required checks

1. **R16-01:** the cohort uses a valid shared `ScopeKind`, one exact
   `place_ids` entry, `limit=1`, and an equality oracle on the planned
   `place_id`; no `place` scope survives.
2. **R16-02:** the plan assigns C7-01 a runtime-owned verifier/client that
   payload cannot control, a closed worker with no external network, and a
   dual-homed bounded proxy as its sole egress. Direct/default verification
   and proxy-loss tests fail before provider result or domain effect.
3. **R16-03:** the QDarte API, package facade, worker, and proxy have a reachable
   private Compose graph. The facade has no published port; production accepts
   one exact service origin while development remains loopback-only. Health is
   readiness-bearing for taskq metadata and capped domain/auth storage.
4. **Consistency:** topology/identity, connection, cohort, sequence, stop
   conditions, board, tier map, and Build Plan all describe the same repaired
   design.
5. **Scope:** the delta is docs-only and creates no source, production,
   database, credential, service, queue, job, worker/provider, deployment,
   retirement, non-contact, or Stage-6 change.

## Response

Write only `docs/design-review-16/DELTA-RESPONSE.md`, initially uncommitted.
Return READY or BLOCKED, list each check, Contract questions, provenance, and
the exact scope opened.
