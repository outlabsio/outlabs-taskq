# TaskQ Consumer Rollout Safety

Use this gate whenever a service upgrades `outlabs-taskq`, migrates its TaskQ
schema, replaces an API/scheduler image, changes queue ownership, or restarts a
worker lane. A package upgrade is not one state change: it crosses several
independent contracts that must each be proven.

## Independent contracts

| Contract | Required proof |
|---|---|
| Consumer package | The manifest and lock select the intended exact release, and the installed package inside the built artifact reports that version. |
| TaskQ database | `taskq db verify` succeeds against the exact bound target; package protocol/SQL contract is compatible with the installed migration head. |
| Authorization | The caller has only the TaskQ capability or host-domain scope needed for its operation. Queue capability roles and host API permissions are separate. |
| Runtime artifact | Source commit, image/bundle digest, platform, TaskQ version, and target identity match the accepted release manifest. |
| Operational ownership | Scheduler singleton, queue state, worker placement, concurrency, and downstream-spend policy are explicitly accepted. A library bump does not authorize opening a queue. |

## Mandatory rollout sequence

1. Start from clean, merged source. Select an exact package version and review
   its release notes, protocol revision, SQL contract, and migrations.
2. Build immutable, platform-correct artifacts once. Record source SHA, artifact
   digest, installed TaskQ version, and previous rollback artifact. Do not use a
   mutable `latest` tag as release identity.
3. Keep queues paused. Against the exact target, run `taskq db plan`, review the
   digest, migrate with the owner credential, bind/verify the expected target,
   then require `taskq db verify` to pass. Runtime services never receive the
   owner/migration credential.
4. Prove authorization boundaries. A generic producer/worker should fail with
   the expected `403` on a host application's domain planner route; a dedicated
   least-privilege integration should succeed only on the required route. Do
   not broaden a generic TaskQ key to solve a domain authorization failure.
5. Under a serialized release lock, start the API first, exactly one scheduler,
   and worker lanes while all queues remain paused. Attest the running package,
   source, artifact digest/platform, target installation, migration head, and
   scheduler identity before traffic.
6. Open one queue and one bounded canary at a time. Record workflow keys/IDs,
   terminal counts, queue state, provider side effects, and committed writes.
7. Roll back by pausing the affected queue, draining or allowing leases to
   expire according to the handler contract, restoring the retained immutable
   artifact, and re-running all package/database/identity checks.

## Known footguns

### Package version is not database compatibility

An old package can start against a newer TaskQ database and fail with an
unsupported-contract error. Prove both values before recreate. Never monkey
patch the consumer to suppress a TaskQ contract check; fix and publish TaskQ,
then update every consumer deliberately.

### The packaged CLI is not a generic domain API client

The HTTP CLI speaks the mounted `/taskq/v1/*` facade and requires its protocol
header and envelopes. A consumer's custom routes need not implement that wire
contract. Use a direct-SQL operator context where the database is reachable, or
mount the packaged facade. See [Operator CLI Access](Operator%20CLI%20Access.md).

Host-domain routes also have their own permissions. A TaskQ producer capability
does not imply `agent:read`, `agent:write`, or any other application scope.

### A timed-out admission may already have committed

A proxy or edge timeout does not prove that workflow creation failed. Every
operator-triggered batch must use a deterministic workflow key. After any
timeout, query that key before retrying; retry only if no workflow exists. For
long planning requests, prefer a host-local API path while retaining the same
idempotency key.

Candidate-selection pagination and TaskQ member batch size are separate knobs.
A one-item member size should create independently retryable jobs, not force
one database query per candidate.

### Blocking handlers and worker restarts

When a synchronous blocking handler may still be running underneath an async
worker, shutdown intentionally does not release its lease immediately. The
lease-expiry/reaper path is the safe reclaim mechanism; immediate release could
run the same paid or externally mutating operation twice.

Choose concurrency before opening the queue. To change it, pause the queue,
wait for zero running leases, restart the lane, prove its identity, then resume.
If emergency shutdown leaves leases behind, observe them through expiry and
bound replay risk with idempotent, small members.

### Queue ownership is a separate release decision

Everything runs concurrently only when separate queues have accepted worker
owners and explicit resource fences. Keep one scheduler. Start queues closed,
then open upload/render/agent/property lanes independently. Package deployment
must not silently place workers, resume queues, raise concurrency, or enable
provider spend.

## Evidence packet

Keep these values outside terminal scrollback and exclude credentials:

- source SHAs, package versions, SQL contract/migration heads, target
  environment and installation ID;
- image/bundle digests and target platforms;
- negative and positive authorization checks;
- scheduler singleton and worker identity/concurrency;
- queue states before and after, workflow key/ID, terminal counts, committed
  side effects, and rollback artifact/command.

