# ADR-025 — Follow-up helper return shape uses the existing enqueue projection

**Status:** Accepted 2026-07-22
**Resolves:** S5-QD-FR-CQ-01; corrects ADR-024 Manifest drafting

## Context

The SQL 0.2.0 Manifest named the new owner-private helper as returning
`taskq.enqueue_result`. No such composite exists in the accepted catalog, and
ADR-024 did not authorize adding one. Implementing that text would create an
otherwise unnecessary type solely for a private function.

## Decision

`taskq._enqueue_followup(uuid,text,jsonb,integer)` returns the same anonymous
table projection as ordinary enqueue:

```text
TABLE(job_id uuid, created boolean)
```

The function remains owner-only and is not a protocol command. This correction
does not change the complete request/response, add a composite, expose child ids
on the wire, or alter `created | existed` semantics. `complete_job` may consume
or ignore the private row; tests assert truthful identity and outcome directly
under owner execution.

## Consequences

- Migration 0008 adds one private function and no type.
- Manifest/catalog parity can reuse the existing anonymous enqueue result
  vocabulary without inventing a public model.
- ADR-024's transaction, authorization, validation, key and evidence decisions
  are unchanged.
