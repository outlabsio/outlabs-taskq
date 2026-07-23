# ADR-026 — Sealed workflow lifecycle and native dependency activation

**Status:** Accepted 2026-07-23
**Resolves:** S5-QD-FR-CQ-03; activates S5-QD-FR-02B

## Context

FR-02B creates a workflow and admits its member jobs through separate producer
calls. The accepted design also derives terminal workflow status from the
current member set. Without a durable graph-closure point, an empty workflow or
one whose currently admitted members finish before a later HTTP enqueue is
indistinguishable from a complete graph. A finalizer could close legitimate
work early, while allowing membership after terminalization would make workflow
status reopen and drift.

QDarte currently avoids part of this race through one application-database
transaction, but taskq's SQL and HTTP transports must share one general,
response-loss-safe contract. Retaining a host transaction or orchestration
wrapper would violate the full-replacement destination.

## Decision

1. Protocol document revision **1.0.10**, SQL contract **0.2.1**, and immutable
   migration **`0009_workflows.sql`** activate capability
   `dependencies_workflows`. Wire major remains `1`.
2. A workflow is created **open** through producer-granted
   `create_workflow(workflow_key, kind, params, declared_queues, actor)`.
   Workflow key, finite kind, canonical params and the sorted distinct declared
   queue set form immutable creation identity. Exact replay returns `existed`;
   changed identity is non-retryable `TQ409 workflow_mismatch`. The first
   authenticated actor is attribution, not replay identity.
3. Producer-granted `seal_workflow(workflow_id, actor)` is the graph-closure
   linearization point. Workflow-row locking serializes seal against member
   admission and stamps the first sealing actor. Only sealed workflows can
   finalize. A sealed empty workflow succeeds. Exact replay of an
   already-admitted step remains `existed` after sealing; a new step is
   non-retryable `TQ409 workflow_sealed`.
4. Workflow members use the existing enqueue identity's reserved
   `workflow_id`, `step_key`, and `depends_on` arguments. Workflow id requires
   one unique bounded step; dependencies require that workflow and contain
   1–100 distinct existing parents from the same workflow. Newly inserted
   dependents may reference only already-existing parents, so cycles are
   structurally impossible. Parent rows lock in ascending id order after the
   workflow row.
5. Each workflow member stores a database-computed canonical intent hash over
   its queue/job command and sorted dependency set. The workflow-step unique
   key is permanent while the member remains hot. Same-step/same-intent replay
   returns `existed` even after its edges are satisfied and deleted;
   same-step/different-intent is `TQ409 workflow_step_mismatch`.
6. Already-succeeded parents are satisfied at admission and create no edge.
   Failed or cancelled parents reject the whole enqueue with non-retryable
   `TQ409 dependency_terminal`; unknown workflow or parent is `TQ001`. Every
   validation and parent-state check occurs before insertion.
7. Parent success removes only its direct surviving edges, decrements exact
   counters, and promotes zero-pending members in the same transaction.
   Failure or cancellation advances a bounded direct-descendant cancellation
   frontier; skipped or deeper descendants remain unclaimable and converge
   through a bounded housekeeper straggler pass. All graph mutations follow
   workflow-row, then ancestor/frontier, then ascending-id tie-break order.
8. `cancel_workflow` is operator/control-only and records an asynchronous,
   database-stamped cancellation intent. It implicitly seals an open workflow,
   runs one bounded cancellation batch, and the housekeeper completes the
   remaining frontier. It never forges worker settlement. Replays return
   `already_requested` or `already_terminal`.
9. Terminal workflow status is a monotonic materialization after sealing:
   requested workflow cancellation wins as `cancelled`; otherwise any failed
   member yields `failed`, else any cancelled member yields `cancelled`, else
   all-success or an empty graph yields `succeeded`. Individual workflow-member
   redrive is rejected in 0.2.1; a corrected run uses a new workflow key. A
   future graph-level redrive requires its own contract.
10. HTTP creation authenticates, strictly decodes, and authorizes `enqueue` for
    every declared queue before SQL. Member enqueue first authorizes its path
    queue, then strictly decodes, projects the authoritative workflow queue set,
    authorizes `enqueue` for every declared queue, and only then performs any
    dependency lookup. Seal repeats that producer authorization; cancellation
    uses `control` for every declared queue through the separate operator
    transport. No dependency or workflow state is disclosed before all required
    authorization succeeds.
11. ADR-020's bridge set becomes
    `{0.1.2, 0.1.3, 0.1.4, 0.1.5, 0.2.0, 0.2.1}` before migration 0009.
    Workflow routes and fields remain inactive unless exact metadata includes
    `dependencies_workflows`. Applying 0009 raises the database rollback floor
    to that bridge; production application remains a separate host decision.
12. Migration 0009 preserves existing active capabilities and replaces
    metadata by exact equality with `admission_reservations`,
    `dependencies_workflows`, `followups`, and `read_model_list_ready`.
    Deactivation requires a later immutable metadata migration.

## Consequences

- A planner can construct a graph over SQL or HTTP without an application
  transaction and knows exactly when membership becomes immutable.
- Workflow finalization can be bounded and deterministic without guessing from
  a temporarily empty member set.
- QDarte can delete its workflow/dependency mutation services rather than
  retaining them as a taskq wrapper.
- Fresh/full 0001→0009 chains, dual-major catalog and metadata equality,
  fan-in/fan-out/diamond races, enqueue-versus-settle/seal/cancel races,
  promotion/cascade convergence, privilege equality, SQL/HTTP/fake parity,
  bounded plans, resources and installed artifacts gate acceptance.
