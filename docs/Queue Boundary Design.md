# TaskQ Queue Boundary Design

A TaskQ queue is an **operational control boundary**, not a database-table
boundary and not merely a label for a job type. Jobs in different queues may
read and write the same application tables safely when the application uses
transactions, fencing, idempotency, and domain invariants correctly. What a
queue isolates is the control plane around those jobs.

## The ownership rule

Put work in the same queue only when one operational owner can safely make all
of these decisions for every job in that queue:

- pause, resume, drain, purge, and redrive;
- worker placement, concurrency, and rollout timing;
- maximum depth, claim rate, circuit breaker, and recovery ramp;
- credential scope and downstream/provider spend;
- failure classification, recovery procedure, and service objective.

If two workloads need independent answers to any of those questions, give them
different queues even when they use the same database tables or application
domain. A controller that pauses or drains a queue is asserting ownership over
every job in it.

Different task types do not provide operational isolation. Queue-wide controls
do not filter by task type.

## When sharing is correct

Related stages may share one queue when they deliberately have the same owner,
capacity budget, availability target, and recovery lifecycle. For example, a
render pipeline can keep prepare, execute, verify, and upload task types in one
render queue if one controller coordinates the complete pipeline and it is
safe for a render-wide pause to stop every stage.

Do not create one queue per job, handler, workflow step, process, or container.
Use task types for dispatch, workflows for dependencies, worker claim filters
for lane placement, and concurrency/flow keys for real shared resources.

## When to split

Create separate queues when workloads have independent:

- campaign or lifecycle controllers;
- maintenance windows or pause/drain requirements;
- worker fleets, credentials, providers, or cost approvals;
- latency, throughput, retry, or failure objectives;
- depth limits, circuit breakers, rate limits, or concurrency budgets;
- incident recovery and on-call ownership.

A common warning sign is a controller that must wait for unrelated work to
reach zero leases before it can proceed. Another is one campaign accidentally
stopping another because both manipulate the same queue profile.

## Shared infrastructure is still shared

Separate queues are bulkheads for control and admission; they do not create
separate Postgres CPU, storage I/O, network bandwidth, provider quota, or API
capacity. Size those resources explicitly:

1. set a per-queue `max_running`, claim rate, and maximum depth;
2. bound every worker's concurrency and credential request budget;
3. measure planning/enqueue cost as well as handler cost;
4. monitor database saturation and cross-queue latency under concurrent load;
5. use a dedicated installation or database only when physical resource or
   failure-domain isolation is actually required.

## Catalog and deployment behavior

Queue reconciliation must preserve the current state of every existing queue.
Creating or discovering a new queue may leave **that new queue** paused for a
canary, but bootstrap code must not pause, resume, or rewrite unrelated queues
as a side effect of catalog synchronization.

Treat queue names as stable operational API. Provision least-privilege
producer, worker, and controller credentials per queue boundary. Keep one
supervised scheduler per TaskQ installation; the scheduler is a clock, not a
queue owner.

Durable workers require an always-on host and an external service supervisor
with restart policy and observable health. Laptop battery state, lid closure,
sleep-prevention utilities, and an interactive login session are not
production availability controls. A laptop is appropriate only for an
attended, bounded run whose interruption and lease recovery are understood.

## Required integration tests

Consumers with more than one queue should prove the boundary against the real
TaskQ database and mounted authorization layer:

1. pause and drain queue A while queue B remains open and continues settling;
2. reconcile the queue catalog and prove all existing paused/open states are
   unchanged;
3. prove each producer, worker, and controller is denied on queues it does not
   own;
4. run both queues concurrently and verify independent depth, presence,
   counters, flow limits, and circuit-breaker state;
5. terminate a worker and prove supervised restart or lease-expiry recovery
   without duplicate external effects;
6. saturate shared capacity deliberately and verify backpressure without
   correctness or control-plane coupling.

Record the queue-to-workload ownership map beside the consumer's worker
registry and deployment manifest. Review that map whenever a new controller,
provider, worker family, or service objective is introduced.
