# ADR-011 — Housekeeper role, deployment credentials, and version-aware maintenance

**Status:** Accepted 2026-07-18
**Amends:** ADR-010 (adds a sixth capability role + the credential matrix + the function manifest gate)
**Resolves:** R2-04, R2-05 (design-review-2); detail in `../design-review-2/04-sql-and-role-audit.md`

## Context

ADR-010's five roles cannot express a verified, required flow: the HTTP facade process must run the housekeeper (`tick`, and in 0.1 the due-gated janitor pass — spec §11.4/§13.5; the Diverse reference facade already calls `taskq.tick()`), but its documented login holds only producer+runner+observer, and `tick`/`janitor` are operator-tier. Granting operator to the web process would erase the capability split. Separately, the hardening contract lived as prose beside DDL that doesn't carry it (R2-04), and "admin credentials" for external maintenance ignored a real version split: PostgreSQL 17–18 offer a narrow `MAINTAIN` privilege; PostgreSQL 16 requires relation *ownership* for REINDEX.

## Decision

1. **Sixth capability role, `taskq_housekeeper` (`NOLOGIN`):** EXECUTE on `tick`, the 0.1 due-gated janitor entry, and (0.2) the schedule claim/fire/error trio. It gets no operator controls, no queue/profile mutation, no DML, no external maintenance. `tick`/`janitor` are additionally EXECUTE-granted to `taskq_operator` as manual escape hatches. All package roles are `NOLOGIN`; deployment logins receive memberships.
2. **Standard deployment credentials** (full matrix in review doc 04 §3, adopted): facade runtime login = producer+runner+observer+**housekeeper**, never operator; operator HTTP routes are opt-in and require a **separate** operator pool/login (observer+operator) — the ordinary facade pool must never hold operator; co-resident host request login = producer (+observer as needed); direct DB workers = runner+observer (+producer only if handlers explicitly enqueue); the designated `_system` runtime (0.2) = runner+observer+housekeeper; migration login uses `SET ROLE taskq_owner`; verify login is read-only.
3. **Version-aware external maintenance credential:** on PostgreSQL 17–18, a dedicated login with schema USAGE + `MAINTAIN` on the selected taskq tables, autocommit, advisory-locked, plan-logging, dry-run-capable. On PostgreSQL 16 there is no narrow grant: the CLI defaults to emitting a DBA/owner-managed plan and refuses automated reindex unless an explicitly provisioned, isolated owner-authorized credential is supplied — named as broader authority, never a silent fallback. The CLI detects server version and refuses under- or over-privileged credentials.
4. **Function manifest gate (mechanizes ADR-010's hardening — R2-04):** migration 0001 ships a manifest recording, per function: signature + return composite, owner, security mode, pinned `search_path`, volatility, PUBLIC revoke, exact grants, release capability, protocol command, documented TQ SQLSTATEs, time budget, replay rule, and covering test ids. `verify()` compares the live catalog (`proowner`, `prosecdef`, `proconfig`, ACLs) against the manifest. Internal helpers (`uuid7`, `backoff_seconds`, `emit_event`, `reap_job`, cascade/finalizer/stats helpers, the 0.2 followup inserter) are owner-only — no application-role EXECUTE; nested calls work because outer functions run as owner.
5. **No public HTTP tick route.** The housekeeper is an internal runtime concern; the operator CLI is the manual surface.

## Consequences

- Spec §4 role model, §11.4/§13.5, and §20.2 amended; Authorization doc drops its `tick` route and gains the IAM-roles-vs-DB-roles clarification; extraction brief credential text updated; harness fixtures dispatch each operation through its exact capability role.
- The observer surface is safe views/functions only — spec §11.5's raw-table runbook queries are replaced by function/view equivalents; base-table forensics is an audited owner break-glass path.
- outlabsAPI's simple profile is one login: producer+runner+observer+housekeeper — the smallest host stays one credential without touching operator.
