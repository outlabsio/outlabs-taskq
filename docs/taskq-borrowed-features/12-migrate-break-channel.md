# 12 — Migrate Break Channel

> **Priority:** MUST for 0.1 (promoted from NICE by ADR-009 — operational recovery is first-release scope)
> **Provenance:** the Node/Postgres SQL-first worker migrate NOTIFY / worker exit on breaking schema
> **Depends on:** [06 Notify Nudge](./06-notify-nudge-and-poll.md), [13 SQL Packaging](./13-sql-packaging-conventions.md)

---

## 1. Intent

When a **breaking** schema migration is applied, running workers should stop cleanly instead of calling obsolete function signatures and corrupting state.

---

## 2. Mechanism

1. Installer writes `taskq.meta` keys:
   - `schema_version` (int / semver string)
   - `min_client_version` (semver)
2. Breaking migrations call `PERFORM pg_notify('taskq_migrate', json_build_object('version', ...)::text);`
3. Workers LISTENing on `taskq_migrate` (or polling `taskq.meta` every N ticks) compare versions.
4. On skew: enter soft stop (feature 11) with reason `schema_mismatch`, exit non-zero.

### 2.1 Compatibility matrix

| DB schema vs client | Behavior |
|---|---|
| Equal | Run |
| DB newer, client supports (`min_client_version` ok) | Run |
| DB newer, client too old | Stop |
| Client newer than DB | Stop or refuse start (prefer fail-fast at `run()`) |

---

## 3. HTTP workers

Facade host checks schema on startup and periodically; returns `503 schema_mismatch` on taskq routes when unsafe. Workers see HTTP errors and back off.

---

## 4. Acceptance tests

1. Bump breaking version + notify → worker stops without completing half-settled work incorrectly (release path).
2. Non-breaking migration (add view) → no restart required.
3. Start worker against empty/missing schema → fail-fast with clear error.

---

## 5. Explicit non-goals

- Automatic in-process schema migration by workers
- Blue/green traffic shifting beyond exit + redeploy
- Notifying on every no-op migrate
