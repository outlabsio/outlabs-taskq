# outlabs-taskq external design review — round 3 request

> **Tier 4 — immutable review provenance.** Sent 2026-07-18 after the Stage-1 exit gate. This file records the request exactly; findings belong in a separate response artifact, and accepted changes must follow the repository's ADR and contract process.

Andi, please perform an independent, adversarial review of the completed Stage-1 secure SQL kernel. The question is not whether the implementation looks plausible: determine whether migration 0001, its installer/verifier, and the development harness faithfully and completely implement the locked 0.1 SQL contract.

Do not edit the repository during this review. Do not infer intent from the implementation when a higher-authority document answers the question. Do not name external queue projects in the response.

## Authority and read order

Read in this order before evaluating code:

1. `AGENTS.md` — hard implementation rules.
2. `docs/README.md` — tier map and conflict rules.
3. `docs/Task Queue Transport Protocol v1.md` and `docs/Task Queue 0.1 Function Manifest.md` — locked Tier-0 contracts.
4. `docs/adr/README.md` and ADR-001..011 — accepted decisions.
5. `docs/Task Queue Test & Benchmark Harness.md` — normative harness design.
6. `TASKS.md` and `docs/Task Queue Build Plan.md` — claimed completion and exit gates.
7. Only then inspect the implementation and tests listed below.

Tier 0 wins every conflict, followed by accepted ADRs. If the implementation exposes a genuine contract defect or contradiction, label it a **Contract question**; do not recommend an implementation workaround or silently reinterpret the contract.

## Review set

Audit these artifacts as a single system:

- `src/taskq/sql/migrations/0001_initial.sql`
- `src/taskq/sql/__init__.py`
- `src/taskq/cli.py`
- `tests/conftest.py`
- `tests/test_t1_unit.py`
- `tests/test_t2_contract.py`
- `tests/test_t3_races.py`
- `tests/test_t3_stress.py`
- `tests/test_t4_model.py`
- `tests/test_plans.py`
- `tests/test_bench_smoke.py`
- `bench/runner.py`
- `.github/workflows/ci.yml`
- `pyproject.toml` and `uv.lock`

The implementation claims six capability roles, eleven tables, three composite types, thirty-nine hardened SQL functions, migration-ledger verification, and 0.1-only seed state. Independently derive the expected catalog from the Function Manifest rather than trusting those totals.

## Required audit program

### 1. Manifest-to-catalog parity

Build an explicit matrix for every 0.1 function: identity and argument types, return type, defaults, volatility/parallel/security attributes where contracted, owner, pinned `search_path`, PUBLIC revocation, capability grants, registered SQLSTATEs, and normative body invariants. Check tables, constraints, indexes, triggers, composite types, roles, memberships, and seed rows wherever the manifest or ADRs make them contract-visible. Flag both missing and extra surface area.

Pay special attention to fencing, authoritative row lookup, dedup predicates, budget accounting, replay outcomes, verb-aware settlement conflicts, cancellation, redrive, dependency capability gates, lock ordering, and bounded `SKIP LOCKED` convergence. Confirm that no untrusted role can reach table DML or gain behavior through an unintended function grant.

### 2. Migration runner and verifier

Review async and sync paths for identical semantics. Check advisory-lock scope, transaction ownership, discovery and ordering, checksum recording, dirty/partial installation behavior, concurrent installers, already-applied migrations, failure recovery, and CLI exit behavior. Determine whether `verify()` is read-only and whether it detects all promised drift precisely without accepting a permissive false positive.

Challenge the corruption matrix for ownership, signatures/catalog identity, grants, PUBLIC EXECUTE, pinned paths, roles, and checksum state. Identify any hardening property that migration 0001 asserts but the verifier cannot subsequently prove.

### 3. Test validity and concurrency evidence

Map contract clauses and manifest entries to executable coverage; call out untested behavior, not merely untested lines. Inspect fixtures for privilege leakage, shared-state coupling, false isolation, cleanup gaps, or use of superuser access in assertions meant to prove capability behavior.

For T3, verify that barriers, held transactions, and observed waits force the intended interleavings rather than relying on timing. For randomized stress, assess replayability and whether its oracle can miss duplicate claims, attempt-token reuse, budget errors, lost jobs, or wedges. For T4, verify that the model is independent enough to catch SQL defects and that its scratch-only lease rewind helper cannot become product surface or weaken production roles.

### 4. Plans, benchmarks, compatibility, and CI

Check that the million-row plan gate exercises realistic mixed states, stabilizes visibility/statistics legitimately, parses plan structure robustly, proves the named hot paths index-backed, and cannot pass while hiding a full `jobs` scan. Check that B1–B4 measurements are reproducible development evidence rather than a performance claim, and that their JSON captures the documented environment, latency/throughput, WAL, storage/tuple, lock/connection, event-loop, and representative plan evidence.

Audit packaging and CI job commands themselves. Confirm Python 3.12/3.13 import isolation, PostgreSQL 16/18 coverage, migrations, races/model, and benchmark smoke actually collect the intended tests and do not pass through accidental skips. Distinguish locally reproduced evidence from workflow configuration that has not yet run on the hosting service.

## Evidence available, not assumptions

- PostgreSQL 18.3: 58 regular tests green; the opt-in million-row plan test also passed twice.
- PostgreSQL 16.14: the identical 54-test Stage-1 suite at S1-05 was green before the four benchmark smoke tests were added.
- No-DSN collection: 27 passed and 32 PostgreSQL-dependent/opt-in tests skipped.
- Ruff check and format checks are green.
- Stage-1 commits, newest first: `6faf915`, `084b1fd`, `e90c8df`, `ff4efb4`, `e93140e`, `2b75ad3`, `d5d5d55`, `f50fe38`, with opening slice `3e7d55d`.

Re-run tests where useful. The standard scratch DSN is `postgresql://postgres:postgres@localhost:5432/taskq_stage1_test`; migration 0001 creates cluster-wide `taskq_*` roles, so use only an isolated development PostgreSQL cluster.

## Required response shape

Return one self-contained Markdown review with:

1. **Verdict:** `PASS`, `PASS WITH FINDINGS`, or `BLOCKED`, plus whether Stage 2 may begin.
2. **Findings:** ordered by severity (`BLOCKER`, `HIGH`, `MEDIUM`, `LOW`), identified `R3-01`, `R3-02`, and so on. Each finding must include exact file/line evidence, the violated authority or invariant, impact, and the smallest contract-correct remediation and regression test.
3. **Contract questions:** separated from implementation defects. State the conflicting Tier-0/ADR passages exactly enough to locate them; do not propose coding around them.
4. **Manifest coverage matrix:** all contracted functions and hardening axes, with implementation and test evidence or an explicit gap.
5. **Exit-gate assessment:** PG16/18, privileges, verifier corruption, concurrency/model agreement, million-row plans, benchmark evidence, packaging, and CI.
6. **Residual risks and recommended Stage-2 preconditions:** only risks supported by repository evidence.

If there are no findings in a category, say so explicitly. Treat test code and CI configuration as production-critical evidence, and prefer a falsifiable counterexample over a general concern.
