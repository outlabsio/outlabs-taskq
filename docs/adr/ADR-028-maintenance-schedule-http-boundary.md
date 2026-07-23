# ADR-028 — Package maintenance schedules are not HTTP resources

**Status:** Accepted 2026-07-23
**Resolves:** S5-QD-FR-CQ-05

## Context

ADR-027 and SQL contract 0.2.2 define ordinary schedules as operator-managed,
queue-bound job definitions. Their HTTP authorization is therefore
`control` on the authoritative queue and their public profile has one exact
job-target shape.

Migration 0010 also seeds the sole package-owned
`taskq-janitor-daily` definition. It is intentionally caller-immutable, has a
finite maintenance target rather than a queue, and is executed only by the
housekeeper. It consequently has neither an authoritative queue for HTTP
authorization nor the public job-target profile shape. Protocol 1.0.11 said
that it had no HTTP mutation route but did not settle GET or create semantics.

## Decision

1. Protocol document revision **1.0.12**, amendment **19**, changes no wire
   major, SQL contract, function identity, grant, migration, capability or
   stored row.
2. The exact package identity `taskq-janitor-daily` is excluded from the HTTP
   schedule-name grammar. After authentication and request-id validation, GET,
   PUT and DELETE all reject it as `TQ422` with the fixed safe details
   `{"field":"name"}`. This happens before schedule lookup, authorization
   projection, `If-Match` parsing, request-body decoding or SQL.
3. The rejection is grammatical and uniform, not a row-existence hiding rule.
   It therefore creates no absent-PUT special case and discloses no mutable
   maintenance profile. Every route has an executable vector asserting the
   same response and zero schedule/job/event mutation.
4. Ordinary names continue to match `[a-z0-9][a-z0-9_.-]*`, use the existing
   1–120 UTF-8-byte bound, and retain Protocol 1.0.11 authorization and ETag
   behavior unchanged. Direct SQL remains capable of reading the finite seeded
   definition under the operator credential.
5. Legitimate operational observation is through the runtime's
   housekeeper-health snapshot and bounded failure telemetry. Privileged
   definition inspection uses the manifest-backed direct-SQL operator
   `get_schedule` command; clients must not reinvent an HTTP probe.
6. No schedule enumeration route exists in 1.0.12. Any future enumeration,
   search, export or list contract must exclude every package-owned maintenance
   definition, including `taskq-janitor-daily`, and must carry an explicit
   negative vector before activation.
7. The hand-derived route catalog, mounted OpenAPI and SQL/HTTP parity program
   pin the three-route rejection. Housekeeper direct-SQL behavior and migration
   0010 remain byte-identical.

## Consequences

- The public schedule resource family has one authorization model and one
  profile shape: queue-bound ordinary jobs.
- Package maintenance remains inspectable and observable without becoming
  operator-managed application content.
- The facade needs no global-admin exception, maintenance wire union or
  create-on-reserved-name special case.
- A future maintenance target or list surface requires a new docs-first
  decision; it cannot inherit HTTP visibility from the SQL row.
