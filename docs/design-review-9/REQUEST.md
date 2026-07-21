# External targeted review — Read-model activation

## Assignment

Perform an adversarial, source-backed audit of the completed H-08/H-11 read-model slice. Return
**READY** only if the contract, immutable migration chain, generated SQL/HTTP surfaces, and B9
evidence support a later, separately approved host adoption of the `ready` view. Otherwise return
**BLOCKED** with the smallest explicit preconditions.

This review authorizes no host dependency update, production migration, capability change, queue
profile change, UI work, deployment, database/IAM mutation, retirement action, or Stage-5 pilot.
`running` and `finished` must remain inactive regardless of the verdict.

## Repository identity and range

- Repository: `~/Documents/projects/outlabs-taskq`
- Review the complete read-model range `7826cbc..c1fac41`.
- The range begins with the Tier-3 proposal and ends with immutable migration
  `0006_activate_ready_read_model.sql`.
- A separate user-owned ADR-018/UI documentation batch may be present in the working tree. Confirm
  the reviewed range does not absorb or alter it. Do not modify, stage, or adjudicate it.

Derive commit identities, trees, and changed paths directly from Git before trusting this request.
The final implementation commits are `cc15b22` (0004), `40aa9b5` (0005), `cc35446`/`c3575d7`
(generated HTTP/client/parity), and `c1fac41` (0006). They are audit leads, not evidence.

## Authority order

Read in this order:

1. `AGENTS.md`, then `docs/README.md`;
2. Transport Protocol v1 document revision 1.0.7 and Function Manifest 0.1.4;
3. ADR-005, ADR-006, ADR-010, ADR-011, ADR-019, ADR-020, and ADR-021;
4. `TASKS.md`, the Build Plan, and the Read Model Specification;
5. source, migrations, generated protocol metadata, clients, facade, tests, and benchmark code;
6. commit `7fe2c6b` B9 evidence and actual PostgreSQL 16/18 execution.

Tier 0 and accepted ADRs win every conflict. Report a **Contract question** rather than proposing
a Tier-3 workaround. Never name third-party queue projects in the repository or response.

## Audit A — governance, scope, and immutability

Independently verify docs-first ordering for the three accepted ADRs; trailers and same-commit board
updates; and that migrations 0004, 0005, and 0006 are append-only and cannot be changed or skipped
under their recorded checksums. Confirm 0006 is metadata-only, preserves SQL contract 0.1.4 and the
wire contract, and cites committed B9 evidence rather than a manual configuration switch.

Try to falsify the claimed range and the distinction between design, contract, implementation, and
activation. Reject any unreviewed SQL function, grant, index, direct table projection, protocol
alias, broad job browser, host/deployment/production/UI/retirement change, or Tier-4 edit.

## Audit B — SQL contract, ordering, and verification

Derive the live catalog from the Function Manifest instead of trusting claimed counts. On both
PostgreSQL 16 and 18, exercise fresh installation and the full `0001 → 0002 → 0003 → 0004 →
0005 → 0006` chain. Confirm `verify()` and catalog parity independently reject at least one
mutated capability value or migration/catalog drift.

Prove these ordered dispositions under the observer role:

| Queue/view state | Required disposition |
|---|---|
| Authorized unknown queue, any view | `TQ001` before capability evaluation |
| Existing queue, `ready`, at 0005 | `TQ501 read_model_view_inactive` |
| Existing queue, `ready`, after 0006 | bounded page, including a 200 empty page |
| Existing queue, `running` or `finished`, after 0006 | `TQ501 read_model_view_inactive` |

Audit 0006's precondition and exact post-state: metadata must be 0.1.4 and capabilities must equal
`{"active":["read_model_list_ready"]}`, not merely contain that value. Determine whether any
unauthorized role can alter capability metadata, execute a read-model function, or obtain a base
table projection. Any such bypass blocks READY.

## Audit C — profile update and wire conformance

Audit `get_queue_profile`, `update_queue_profile`, and bootstrap `ensure_queue` without treating a
generated client model as authority. Confirm the observer projection is exact and safe; version
changes only on canonical profile changes; and stale updates return TQ409 / `profile_version_conflict`
with current-version-only details.

Confirm HTTP GET remains flat while successful PUT has Protocol-1.0.7's canonical
`{"profile": {...}}` envelope and ETag. Exercise exact, absent, malformed, weak, wildcard, and
stale `If-Match`. No request echo may become row truth. Direct SQL and HTTP must share safe fields
and outcome semantics.

Independently derive OpenAPI/command catalog from Protocol 1.0.7. Check both official clients have
exactly the active methods, reject deferred/gated escape hatches, and expose no hidden success path
for `running`, `finished`, queue detail, or worker list.

## Audit D — authorization, cursors, redaction, and parity

Attack the mounted facade and both transports with authentication before request-ID validation and
cursor parsing; queue-scoped permit/wrong-queue denial/global-read/hiding equality; malformed,
foreign-queue, foreign-view, oversized, invalid-UUID, and multibyte cursors; and direct SQL versus
generated client → mounted ASGI → SQL comparison against raw owner-only `taskq.jobs` rows.

Validate request-ID, ETag, bounds, retryability, and error-code registry conformance. The fence is
write-only and must never appear in a response, representation, OpenAPI, or diagnostic. The queue
must be authorized before cursor decoding, with no facade preflight that changes SQL `TQ001`/`TQ501`
ordering.

## Audit E — B9 evidence and activation honesty

Reproduce or rigorously inspect B9 on both PostgreSQL majors with the contracted million-row
fixture. Inspect actual plans—not timing alone—and require any accepted view to use its named index
family, avoid sort/sequential scan, and inspect at most `limit + 1` candidates after queue/view
filtering.

The expected asymmetric conclusion is: `ready` uses `jobs_claim_idx` with the bounded shape
recorded at `7fe2c6b`; `running` and `finished` remain inactive because their evidence fails the
structural gate; and neither benchmark timing nor configuration can activate another view. Try to
regress ready through pagination, queue skew, cancellation state, or index/projection mutation.
If the plan fails on either major, require a future immutable migration to deactivate it—never
manual DML.

## Audit F — compatibility, packaging, and gates

Confirm ADR-020's runtime membership is a closed set, preserves the historical exact-pin negative,
and does not loosen negotiation or add wire behavior. A database at 0.1.2, 0.1.3, or 0.1.4 must
have the documented runtime posture; application of 0006 must not make a pre-0006 runtime valid by
accident. Production application of 0004/0005/0006 remains out of scope and retains its separate
rollback-floor decision.

Run or substantiate the taskq SQL suite plus Ruff; PostgreSQL 16.14 and 18.x fresh/full-chain
evidence; the opt-in million-row plan gate; DB-free HTTP/client conformance; and wheel/sdist
optional-extra boundaries where this slice crosses a package boundary. State limits honestly; do
not touch a host or production database to compensate for a missing local gate.

## Required response

Create only `docs/design-review-9/RESPONSE.md`; modify nothing else and leave it uncommitted.
Include:

1. **READY** or **BLOCKED**;
2. independently derived range, catalog, migration, and capability identities;
3. findings numbered `R9-01...` with severity, authority, evidence, impact, remediation, and owner;
4. a separate Contract-questions section, even if none;
5. dispositions for the four Audit-B states, all three views, profile/ETag matrix, SQL/HTTP parity,
   and B9 on both majors;
6. exact commands/gates and honest environmental limits; and
7. confirmation that READY authorizes only a future separately specified host-adoption decision,
   not production migration, UI work, further activation, retirement, or a Stage-5 pilot.
