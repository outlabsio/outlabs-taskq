# External Design Review Round 7 — Stage-4 Production Completion Gate

## Assignment

Perform an adversarial, source-backed completion audit of the outlabsAPI Stage-4 dogfood. Decide
whether Stage 4 is `ACCEPTED` or `BLOCKED`. Acceptance permits the separately planned legacy-path
retirement and branch reconciliation to be specified later; it does not authorize either change in
this review.

Repository paths:

- library and governing corpus: `/Users/macbookm3/Documents/projects/outlabs-taskq`
- first host: `/Users/macbookm3/Documents/projects/outlabsAPI`

Pinned evidence:

- taskq accepted implementation through `747970a`; the S4-AUDIT evidence/board commit containing
  this request is the review tip;
- host evidence commit `5a8cb7825e2e8d18e44f528985d4f1915c16369f` on
  `codex/s4-03-cycle1`;
- production host revision `3f50b7d46601c407d4c184582618072031a8473a` on Coolify's
  `staging-prep` line;
- immutable taskq release `v0.1.0a2`, source `36db7cf`, wheel SHA-256
  `d3c37b0e30dbc75cbbb279c3e3f64a7df7416bf51ca1acfd016544c03e745f42`; and
- exact OutLabs Auth `0.1.0a24`.

Write the response as new files under `docs/design-review-7/` and modify nothing else. Do not edit
this request, the host, prior review files, Tier-0 contracts, ADRs, the board, or implementation.

## Authority read order

1. `AGENTS.md`
2. `docs/README.md`
3. `TASKS.md`
4. `docs/Task Queue Transport Protocol v1.md`
5. `docs/Task Queue 0.1 Function Manifest.md`
6. ADR-005, ADR-006, ADR-008, ADR-010, ADR-011, ADR-013, ADR-014, and ADR-017
7. `docs/Task Queue Stage 3 FastAPI and Authorization Specification.md`
8. `docs/Task Queue Stage 4 outlabsAPI Dogfood Specification.md`
9. host `docs/taskq-s4-01-preflight.md`, `docs/taskq-s4-02-integration.md`,
   `docs/taskq-s4-03-local.md`, and `docs/taskq-s4-audit.md`
10. both repositories' pinned source and tests

Tier 0 and ADRs win every conflict. Separate contract questions from implementation, operations,
or evidence defects. No SQL, migration, permission-grammar, or wire change is expected in this
audit.

## Required independent reproduction

1. Recompute both repository ranges, commit trailers, changed paths, artifact URL/hash, dependency
   pins, and production-versus-evidence branch identities. Prove no taskq SQL, migration, Tier-0,
   ADR, or earlier Tier-4 file changed after the accepted implementation.
2. Run the full taskq suite on PostgreSQL 18 and 16, Ruff, and the host suite/Ruff/MyPy. State exact
   versions, counts, and skipped tests.
3. Inspect the deployed host source for producer mutual exclusion, the exact allowlist, private-probe
   gating, mounted facade, OutLabs adapter, runtime options, poll-only worker, lifecycle order, health,
   result bounds, and absence of operator capability.
4. Recompute whole-deployment connection arithmetic from source. Do not treat the configured
   `reserve=20` or the six-session idle sample as sufficient by assertion. Decide explicitly whether
   the independently derived maximum and remaining headroom satisfy the frozen requirement.

## Production evidence audit

### A. Normal cycles and canonical path

- Trace both selected-tool canaries from authenticated host 202 through taskq claim, handler,
  settlement, and authorized GET.
- Verify keyed created/existed conservation and external invocation oracles; reject evidence that
  proves only queue state against itself.
- Confirm secrets, caller payload, attempt fence, and upstream bodies are absent from every recorded
  response, durable error, and packet example.
- Prove only `umami` and `aerolineas` changed producer and every external-effect lane stayed on the
  legacy path.

### B. Controlled failure

- Independently decide whether the recorded held-job rolling termination satisfies the frozen
  controlled process-failure drill rather than only a normal drain.
- For job `019f7f21-59e3-7683-8a77-bc875a5c49bf`, derive same-id conservation, attempt/failure/event
  arithmetic, worker transition, lease/reap or budget-free release behavior, timestamps, eventual
  success, and absence of manual DML or duplicate invocation.
- Verify old-container removal took 25.434478 seconds inside the configured 35-second platform grace
  and that the private probe was disabled and absent afterward.
- Block if the production event oracle is missing or self-referential; name the smallest evidence
  remediation without inventing data.

### C. Rollback and re-enable

- Replay the exact sequence: switch producer to legacy, wait for zero taskq active depth, disable the
  taskq runtime, prove the legacy endpoint, preserve taskq schema/history, then re-enable.
- Verify the three successful deployment records and the invalid-mode candidate. Confirm the invalid
  candidate failed settings validation before readiness, production stayed on the old healthy
  container, and the corrected candidate converged without schema repair or manual DML.
- Audit the ephemeral-key creation/revocation/archive trail and the post-re-enable queue-scoped GET.
- Examine the deliberately no-match legacy proof row, recorded pending at attempt 3. Decide whether
  natural retry/terminal handling is acceptable residue or whether acceptance requires a read-only
  terminal-state observation. Do not recommend direct table edits.
- Compare before/after taskq queue, worker, session, health, and probe ledgers.

### D. Provisioning and operational honesty

- Verify actual PostgreSQL 16.14/direct-internal/no-TLS/ceiling-100 facts, capability memberships,
  owner/operator/runtime separation, migration/verifier idempotency, IAM 14-record convergence, and
  exact `tools` profile.
- Verify named-volume/daily-backup evidence while preserving the explicit untested restore/PITR
  caveat.
- Confirm the failed re-enable candidate and the pending legacy proof are disclosed rather than
  edited out of the success narrative.
- Reject any latency, cost, autosuspend, exactly-once, or performance win inferred from this small
  deployment sample.

## Acceptance-oracle and scope audit

Build a matrix for every S4-01, S4-02, S4-03, and S4-AUDIT exit row. Name its independent oracle,
counterexample that would make it fail, durable evidence location, and owner. Specifically challenge:

- artifact and lock immutability;
- authorization allow/deny/hiding and fail-closed 429/503 behavior;
- no dual publish or ambiguous fallback;
- concurrency and settlement-response-loss invocation conservation;
- result and durable-error secrecy;
- database disconnect, stop, cancellation, and resource cleanup;
- two normal deployment cycles;
- controlled same-job process failure;
- zero-DML rollback/re-enable; and
- the gate that forbids legacy retirement before this response is accepted.

## Required response structure

1. **Verdict:** `ACCEPTED` or `BLOCKED` for Stage 4.
2. **Reproduced evidence:** commits, versions, hashes, tests, and production records checked.
3. **Finding registry:** stable ids `R7-01...`, severity, authority, evidence, failure mode, smallest
   remediation, and required oracle.
4. **Contract questions:** state `none` explicitly if none.
5. **Acceptance matrix:** every Stage-4 exit row with independent oracle and result.
6. **Scope/hygiene:** exact ranges and prohibited-surface check.
7. **Preconditions to acceptance:** shortest ordered list; `none` only if fully accepted.
8. **Deferred follow-ups:** only post-acceptance work with an explicit future owner.

Do not name third-party queue projects. Refer to the retained host mechanism as the legacy path.
