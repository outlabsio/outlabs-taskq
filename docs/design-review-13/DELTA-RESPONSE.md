# Round 13 targeted admission remediation delta — Response

> **Scope:** only R13-01..R13-04 and the R13-07(a) provenance confirmation, per the delta request.
> Nothing accepted in substance is reopened; nothing further is authorized.
> **Range verified:** `64a1241..7fb6568` (+ gate `2781a0d`) on `main`, pushed and in sync with
> `origin/main`; all commits trailered with same-commit board updates. The registered round-13
> response is byte-identical (SHA-256
> `7ce717e60e39e0dace15d635c8c5959876cad9433015c051fab002d9cd43ffd7`, recomputed against blob and
> working file) and immutable migration 0007 remains exactly
> `99c76b0e2c787c0f72ace34b864d098cc1977a091ed635af0bda8510f3790696`.

## Verdict

**READY — the durable admission primitive is complete and independently accepted.**

## Check results

1. **R13-01 — PASS.** The new direct-SQL vectors exercise both dark branches without takeover or
   re-reservation: the original handle finishing an expired, unreacquired reservation returns
   exactly TQ409 `{"reason":"reservation_expired"}` and a cancelled, unreacquired one exactly
   `{"reason":"reservation_cancelled"}` — each asserting zero jobs from the raw table. The
   cancel-first race (`test_cancel_wins_over_blocked_finish_without_creating_job`) is genuinely
   concurrent: two producer connections, the cancel held open in an explicit transaction, the
   concurrent finish observed **blocked on the tuple/transaction lock via a `pg_stat_activity`
   wait-event oracle** (no sleeps), then commit → the blocked finish resolves to only
   `reservation_cancelled` with `count(*) FROM taskq.jobs = 0`. Wire-level assertions cover both
   reasons through the mounted facade, and the fake pins cancelled-finish parity without being
   treated as SQL proof.
2. **R13-02 — PASS.** The Tier-3 specification changed in the docs commit (`064dd95`) before the
   vector (`e0da3d7`) and states exactly the required rule: finish identity is the literal JSONB
   `{job, receipt}` content; omitted and explicit-null optional fields are different identities; a
   writer keeps one style across every replay of a handle; the official typed clients canonicalize
   by omitting `None`; cross-writer replay is safe only under the same literal identity; migration
   0007 remains immutable and any future normalization goes through the ordinary docs-first
   process. The cross-writer vector (typed-client omitted-null commit, raw-SQL explicit-null
   replay) returns only `finish_mismatch`.
3. **R13-03 — PASS.** All commits are pushed (`main` ≡ `origin/main` at `2781a0d`) and CI run
   `29920365139` was independently inspected: **success at exactly `2781a0d`** — the previously
   un-executed admission range has now met the full CI matrix.
4. **R13-04 — PASS.** A dedicated `AdmissionCancelWireData` model advertises exactly the three
   optional fields cancel can carry (`job_id`, `receipt`, `receipt_expires_at` — present only for
   `already_admitted`); the reserve projection is no longer reused, and the generated-catalog and
   mounted-OpenAPI oracles assert the exact field set. Runtime behavior unchanged.
5. **R13-07(a) — CLOSED.** The operator's confirmation is recorded on the board and restated in
   the handoff: Round 12 was performed by a separate parallel external-review session, not the
   implementation agent, and its single-commit response+remediation recording was intentional. The
   hash-pinned Round-12 trail was already verified consistent; the acceptance chain's provenance
   is now complete.

Reviewer-reproduced evidence (2026-07-22): full suite **505 passed / 1 opt-in skip** on fresh
PostgreSQL 18.3 **and** exact-minor 16.14 (full 0001→0007 chains, CI-shaped Redis); DB-free
**309 passed**; Ruff and format clean (74 files). Range hygiene: the delta touches only docs,
tests, the cancel wire model, and board files — no SQL, migration, Tier-0, prior-Tier-4, host,
QDarte, production, credential, or provider change; every prior Tier-4 file untouched. **No
Contract questions.**

## Effect

The queue-native durable admission primitive — reserve → plan → atomic finish with a stable
replay handle and immutable receipt, across SQL, HTTP, both official clients, the high-level
`TaskQ` path, and the testing fake — is complete, dual-major-proven, CI-green, and independently
accepted at contract 0.1.5 / Protocol 1.0.8. This acceptance opens **only** the isolated QDarte
package repin and its C6-03 created/existed replay proof in the disposable local environment. It
does not authorize a production migration (0007 keeps its ADR-020 bridge and rollback-floor
sequence), host deployment, existing-queue mutation, direct-queue retirement, any provider call or
side-effecting lane (the hard-kill gate stands), worker expansion, UI work, or Stage 6. The
R13-05/R13-06 low-severity bundles retain their named owners for the next library test slice.
