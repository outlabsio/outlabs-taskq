# Round 9 targeted delta acceptance request

## Assignment

Perform only the targeted delta check authorized by the round-9 response. Decide whether
R9-01 through R9-05 are **ACCEPTED** or remain **BLOCKED**. Do not reopen the read-model
architecture, contracts, migrations, or B9 conclusion already accepted in substance.

This review authorizes neither host adoption, production migration, a capability change, UI work,
retirement work, nor a Stage-5 pilot. `running` and `finished` must remain inactive regardless of
the verdict.

## Pinned identity

- Repository: `~/Documents/projects/outlabs-taskq`
- Baseline: `8b1547a92e3a7d766eb8708db1639438fb347d67` (Round-9 response recorded)
- Delta tip: `1610b5a97488a044221028f13568f80db0176f38` on `main`
- Immutable response SHA-256:
  `4087e6524ff45d2ae0e38400dcc41d575a37891a1a609a631d37da23ad0846d9`

Derive the range, changed paths, commit trailers, and remote publication directly from Git before
trusting this request. Confirm the user-owned ADR-018/UI documentation batch remains outside the
reviewed committed range and is neither staged nor adjudicated.

Write only `docs/design-review-9/DELTA-RESPONSE.md`, leave it uncommitted, and modify nothing
else.

## Required checks

1. **R9-01 — missing queue PUT:** Under a valid operator authorization, exercise conditional
   `PUT /taskq/v1/queues/{unknown}`. It must return the existing typed `TQ001`/404 outcome rather
   than an opaque TQ500; inspect the SQL NULL-composite normalization and the generated command
   error ledger to ensure this is conformance to the frozen contract, not a new wire behavior.
2. **R9-05 — inactive view details:** Exercise an existing queue with `running` and `finished`.
   Each response must remain typed `TQ501` and expose exactly the safe
   `reason=read_model_view_inactive` plus requested `view` details, with no SQL text or other
   diagnostic leakage.
3. **R9-02 — B9 post-state:** On PostgreSQL 16.14 and 18.x run the opt-in million-row plan gate.
   It must assert the exact 0006 capability state (`read_model_list_ready` only) and preserve the
   established bounded `ready` plan and rejected `running`/`finished` evidence.
4. **R9-04 — wire vectors:** Inspect and run the mounted-facade and official-client regressions:
   list success plus keyset pagination; malformed, foreign-queue, foreign-view, oversized, and
   duplicate query/cursor rejection; stale exact `If-Match` as TQ409 with current-version-only
   details; weak-tag rejection; and published canonical `{"profile": {...}}` PUT decoding with
   `profile_version` intact. Ensure these use the protocol surface, rather than testing helpers
   that bypass the facade.
5. **R9-03 — hygiene, artifacts, and CI:** Confirm the delta is pushed to `origin/main`; the
   range-owned facade/tests are clean under the pinned Ruff formatter; and the installed wheel and
   sdist smoke ledger now checks immutable migrations 0001–0006 plus the 43-function catalog.
   Independently inspect the successful CI run `29830113693`, including both PostgreSQL SQL-contract
   lanes, artifact jobs, lint, races, and import isolation. The skipped CI million-row job is
   acceptable only because item 3 was independently run on both supported majors.

## Required evidence and disposition

Report the exact commands, PostgreSQL versions, counts, artifact/CI result, and any environmental
limit. Confirm the unchanged Tier-0/ADR/migration chain and the response hash. State whether all
five findings are accepted and whether any Contract question exists.

If all checks pass, state **ACCEPTED — the read-model library slice is complete**. That acceptance
opens only a future, separately specified host-adoption decision for the already-active `ready`
view. It does not authorize a production 0004–0006 migration, further view activation, UI work,
retirement, or a Stage-5 pilot.
