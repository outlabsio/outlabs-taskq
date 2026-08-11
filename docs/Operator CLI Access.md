# Operator CLI Access — Direct SQL vs a Mounted Facade

For package/schema upgrades and consumer deployment ordering, first apply
[Consumer Rollout Safety](Consumer%20Rollout%20Safety.md).

The packaged `taskq` CLI is a complete operator and read surface, but it speaks
**one specific wire contract per transport**. A consumer that exposes TaskQ over
its own HTTP routes instead of the packaged facade will find the CLI cannot talk
to it. This page explains why, and gives the two supported ways to give
operators the CLI against a real deployment.

## The failure: `TQ500: missing_or_invalid_protocol_header`

The packaged CLI's HTTP transport requires the packaged **facade's** wire
contract:

- Its routes live under `/taskq/v1/*` (the mounted `create_taskq_app` app,
  whose OpenAPI is published at `/taskq/openapi.json`).
- Every response carries a `Taskq-Protocol-Version: <major>` header, set by the
  facade's global `protocol_boundary` middleware. The client rejects any
  response whose header is missing or a different major with
  `TQ500: missing_or_invalid_protocol_header`.

If you point the CLI's `transport = "http"` context at an application that
serves TaskQ through its **own** routes (e.g. `/api/v1/taskq/*`) rather than the
mounted packaged facade, those responses do not carry the header and do not
match the route shapes, so **every** operator command fails with that TQ500.
This is not a bug in either component — it is two different HTTP contracts. The
CLI is not a generic HTTP client for arbitrary TaskQ-flavored APIs; it is the
client for the packaged facade.

> Adding only the header to your custom routes does **not** fix it: the CLI also
> requires the packaged `/taskq/v1/*` route shapes and envelope. Header-only
> work moves the failure, it does not remove it.

## Recommended: a direct-SQL operator context

For operators and boxes that can reach the queue database — which every worker
host already can — **direct SQL is the simplest and most robust operator
transport.** It needs no HTTP surface, no authorizer wiring, and no
facade/CLI version alignment, and it exposes the full operator/read surface:
`db verify`, `target show`, `scheduler doctor`, `job list`, `queue health`,
`queue list`, pause/resume, redrive, reprioritize, and the rest.

Contexts never store secrets — only the **name** of the environment variable
that supplies the DSN. Give the operator a login mapped to the read/operator
capability roles (never the owner/migration role):

```toml
# ~/.config/taskq/config.toml   (or point --config / TASKQ_CONFIG at it)
version = 1

[contexts.diverse-prod]
transport = "sql"
dsn_env = "TASKQ_DIVERSE_DSN"          # the CLI reads the DSN from this env var
expected_environment = "production"     # must match the bound target environment
expected_installation_id = "…"          # from `target show` once bound
actor = "operator:<your-name>"          # required for SQL mutations
```

```bash
# The DSN stays out of shell history / process args — supplied via the env var.
export TASKQ_DIVERSE_DSN='postgresql://taskq_operator:…@host:5432/diverse'

taskq --context diverse-prod db verify -o json
taskq --context diverse-prod target show -o json
taskq --context diverse-prod scheduler doctor -o json
taskq --context diverse-prod job list <queue> --view running -o json
taskq --context diverse-prod queue health -o json
```

Notes:
- Read/diagnostic commands only need the `taskq_observer` role. Operator
  mutations (pause/resume, redrive, reprioritize) need `taskq_operator` and an
  `--actor`. Never hand an operator the owner/migration credential.
- `expected_environment` and `expected_installation_id` are safety interlocks:
  the CLI refuses to act against a target whose bound identity does not match,
  so a context cannot silently point at the wrong database. Read them once with
  `target show` after the target is bound.
- No migration, schema change, or deploy is involved — this is a client-side
  config file. It is the fastest way to restore operator/stats capability when
  the HTTP CLI path is unavailable.

## Alternative: mount the packaged facade

Mount the packaged facade only when operators must reach TaskQ over **HTTP**
without database access (remote operators, a DB-less bastion, an auth boundary
you want in front of TaskQ). It is a larger surface than direct SQL; prefer SQL
unless HTTP is a hard requirement.

`create_taskq_app` returns a self-contained FastAPI app. Mount it as a
**sub-application at `/taskq`** — additive, alongside your existing routes,
touching none of them:

```python
from taskq.http import create_taskq_app

taskq_app = create_taskq_app(
    resources=…,          # the TaskqRuntime resources (DB pool, etc.)
    authorizer=…,         # maps the incoming credential -> TaskQ capabilities;
                          # use the outlabs-auth adapter or a host-supplied one
)
app.mount("/taskq", taskq_app)   # CLI context: base_url = "https://…/taskq"
```

Then point an `transport = "http"` context's `base_url` at that mount.

Implementation and operational considerations — document these when you do it:

- **Protocol-major alignment.** The mounted facade and the operator's CLI must
  share the same protocol **major** (the `Taskq-Protocol-Version` value). Pin
  the facade's TaskQ version and the operator's CLI version to the same
  supported range; a major mismatch reproduces the same TQ500 by design.
- **Authorization is yours to wire.** The facade delegates every request to the
  `authorizer` you pass. Map your credentials to the per-queue TaskQ
  capabilities (`taskq_{queue}:{action}`, `taskq:read`, …). The optional
  outlabs-auth adapter does this for outlabs-auth-minted keys.
- **It is additive and non-breaking.** Mounting a new sub-app adds routes; it
  does not change your existing application routes or the database. There is no
  schema migration. The only rollout effect is a redeploy of the API process.
- **Do not also expose the owner role.** The facade runs with runtime
  capability roles, never the owner/migration credential.

## Which to choose

| Situation | Use |
|---|---|
| Operator/box can reach the queue database | **Direct-SQL context** (recommended) |
| Operator is remote / DB-less, or you want an HTTP auth boundary in front of TaskQ | **Mounted packaged facade** |
| You already serve TaskQ over custom application routes (`/api/v1/taskq/*`) for your app's own workers | Keep them for the app; add **one** of the above for the CLI. The custom routes and the CLI are different contracts and do not converge. |
