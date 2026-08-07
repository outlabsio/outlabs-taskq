# TaskQ CLI operator and agent reference

TaskQ's CLI is a non-interactive control surface for human operators and coding
agents. It uses the same typed SQL and HTTP transports as applications; it does
not issue privileged fallback queries, guess output from TTY state, prompt,
open editors or browsers, create queues implicitly, or migrate implicitly.

The executable is `taskq`. Global connection, safety, and output options may be
placed before or after a leaf command. Examples in this guide put them first.

## Discover the surface

The command registry is the source of truth:

```bash
taskq commands -o json
taskq schema job.enqueue -o json
taskq capabilities --context staging -o json
taskq completion zsh
```

`commands` returns every leaf with transport support, capability/role,
mutability, danger level, input/output JSON Schema, examples, and exit behavior.
`schema` returns the same metadata for one command. Completion text is printed
to stdout; the CLI never edits shell startup files.

## Contexts

The default path is `$XDG_CONFIG_HOME/taskq/config.toml` or
`~/.config/taskq/config.toml`. Override it with `--config` or `TASKQ_CONFIG`.
There is deliberately no implicit current context.

```toml
version = 1

[contexts.staging]
transport = "sql"
dsn_env = "TASKQ_STAGING_DSN"
expected_environment = "staging"
expected_installation_id = "018f0000-0000-7000-8000-000000000000"
actor = "operator:release-agent"

[contexts.remote]
transport = "http"
base_url = "https://api.example.com"
bearer_token_env = "TASKQ_STAGING_TOKEN"
expected_environment = "staging"
expected_installation_id = "018f0000-0000-7000-8000-000000000000"
```

Contexts may store endpoint URLs, secret environment-variable names, target
expectations, and a direct-SQL actor. They cannot contain literal DSNs, tokens,
passwords, or header values.

> **Talking to a real deployment.** The CLI's `http` transport speaks the
> packaged facade's contract (`/taskq/v1/*` routes + a `Taskq-Protocol-Version`
> response header). It cannot drive an application that exposes TaskQ over its
> own custom routes — that yields `TQ500: missing_or_invalid_protocol_header`.
> For operators with database reach, a `sql` context is the simplest and most
> robust path and needs no HTTP surface. See
> [`Operator CLI Access.md`](Operator%20CLI%20Access.md) for both options and
> their trade-offs.

```bash
taskq context validate
taskq context list
taskq context show staging -o yaml
taskq --context staging doctor
```

Every connected command requires `--context NAME` or complete explicit
connection flags. Explicit identity values that conflict with a context are
rejected. SQL mutations require `--actor`, `TASKQ_ACTOR`, or the context actor.
HTTP mutations always use the authenticated server principal and reject actor
spoofing. An explicit mutation also requires literal `--expected-environment`;
ambient `TASKQ_EXPECTED_*` runtime settings are never treated as operator target
constraints.

For supervised processes and one-off deployment wrappers that already receive
secrets from their environment, `--dsn-env NAME` explicitly selects the named
variable without placing its credential-bearing value in process arguments:

```bash
taskq worker run \
  --dsn-env TASKQ_STAGING_DSN \
  --expected-environment staging \
  --actor service:auth-maintenance \
  --registry app.tasks:registry \
  --queue auth_maintenance \
  --environment staging
```

`--dsn`, `--dsn-env`, and `--http-base-url` are mutually exclusive. Ambient
`TASKQ_DSN` is deliberately not an implicit endpoint selection for this CLI.

## Output contract

`-o table|json|yaml|jsonl|name` is supported. `table` is always the default.
JSON and YAML use the stable `taskq.cli/v1` envelope:

```json
{
  "api_version": "taskq.cli/v1",
  "kind": "JobList",
  "command": "job.list",
  "ok": true,
  "data": {},
  "meta": {
    "context": "staging",
    "transport": "sql",
    "target": {},
    "next_cursor": null,
    "sensitive_fields_included": false
  },
  "warnings": []
}
```

Structured errors contain stable code/category/retryability, safe bounded
details, request ID, and a remediation hint. They never expose an exception,
SQL text, credential, payload, attempt fence, or secret. JSON/YAML errors go
only to stderr and leave stdout empty. JSONL watches emit versioned
`initial`, `modified`, and `terminal` records; a stream failure emits an
in-band terminal error before the nonzero exit.

Exit codes are stable:

| Code | Meaning |
|---:|---|
| 0 | Success |
| 1 | Non-retryable operation failure |
| 2 | Usage, configuration, or safety refusal |
| 3 | Retryable, unavailable, or runtime failure |
| 4 | Wait timeout |
| 5 | Partial or degraded result |
| 130 | Watch interrupted |

## Safety model

Before every mutation, the CLI reads the target identity and compares its
environment and installation UUID with the selected context/flags. A one-shot
production mutation requires all of:

- an exact expected installation UUID;
- literal `--allow-production`;
- literal `--yes`.

Configuration or environment acknowledgements do not satisfy these gates.
Destructive/bulk operations require `--yes` in every environment. These include
schema migration, target bind/rotation, purge, bulk redrive, workflow cancel,
schedule retirement, lease expiry, janitor, audit-log prune, and auth reconciliation.

Migration, schedule-manifest, and auth apply commands accept the digest from a
reviewed plan. Apply recomputes the target-bound plan and rejects drift. Queue
and individual schedule updates require the current version for CAS.

## Command catalog

This summary is generated from the same command registry tested by the CLI.
Use `taskq commands -o json` for complete machine metadata.

| Group | Commands |
|---|---|
| Discovery | `version`, `doctor`, `capabilities`, `commands`, `schema`, `completion` |
| Context | `context list`, `context show`, `context validate` |
| Database | `db plan`, `db migrate`, `db verify` |
| Target | `target show`, `target bind` |
| Queues | `queue list`, `health`, `audit`, `show`, `ensure`, `update`, `pause`, `resume`, `purge`, `redrive-failed`; flow control: `set-breaker`, `set-breaker-rate`, `set-breaker-latency`, `trip-breaker`, `close-breaker`, `set-aging`, `set-flow-limit` |
| Jobs | `job list`, `show`, `events`, `enqueue`, `enqueue-many`, `cancel`, `redrive`, `run-now`, `reprioritize`, `expire`, `watch`, `wait` |
| Workers | `worker run`, `list`, `shutdown`, `expire-leases` |
| Workflows | `workflow list`, `show`, `create`, `seal`, `cancel`, `watch`, `wait` |
| Schedules | `schedule list`, `show`, `pause`, `resume`, `retire`, `set-smear`, `watch`, `wait`; `schedule manifest plan|apply|retire` |
| Runtime/admin | `scheduler run|once|doctor`, `maintenance tick|janitor|prune-audit`, `auth plan|apply`, `metrics` |

## Bounded reads, cursors, watch, and wait

List commands request bounded server pages. `--limit` bounds the total CLI
result, `--all` explicitly exhausts pages, and the returned opaque cursor can
resume a traversal. A cursor is bound to the command, filters, transport, and
target installation; reuse elsewhere fails safely.

```bash
taskq --context staging job list --queue mail --view failed --limit 50 -o json
taskq --context staging workflow list --view running --all -o jsonl
taskq --context staging schedule list --view paused --limit 100
```

`watch` polls and emits changes only. `wait` accepts finite terminal
conditions and always has an explicit timeout and polling interval:

```bash
taskq --context staging job watch "$JOB_ID" -o jsonl
taskq --context staging job wait "$JOB_ID" \
  --for succeeded --timeout 300 --poll-interval 2 -o json
```

Job inspection is summary-only by default. Payload, result, progress, error,
and event details require explicit include flags and set
`meta.sensitive_fields_included=true`.

## Input workflows

JSON or YAML requests can come from a file or stdin through `--input FILE|-`.
Mixing a full input document with field flags is rejected.

```bash
taskq --context staging job enqueue \
  --queue mail --type mail.send \
  --payload '{"message_id":"m-42"}' \
  --idempotency-key 'mail:m-42' -o json

taskq --context staging job enqueue --input - -o json <<'JSON'
{
  "queue": "mail",
  "job_type": "mail.send",
  "payload": {"message_id": "m-43"},
  "idempotency_key": "mail:m-43"
}
JSON
```

CLI enqueue requires an idempotency key or workflow identity unless the caller
explicitly supplies `--allow-unkeyed`.

## Plan and apply

```bash
taskq --context staging db plan -o json
taskq --context staging --yes db migrate --plan-digest "$PLAN_DIGEST" -o json

taskq --context staging schedule manifest plan schedules.yaml -o json
taskq --context staging schedule manifest apply schedules.yaml \
  --plan-digest "$PLAN_DIGEST" -o json

taskq --context staging auth plan --queues mail,exports -o json
taskq --context staging --yes auth apply --queues mail,exports \
  --plan-digest "$PLAN_DIGEST" -o json
```

The CLI never retries an unsafe mutation. Retry only after inspecting the
structured error and, for plan/apply operations, producing a fresh plan.

## Transport and capability parity

Each command declares `sql`, `http`, both, or no transport. Unsupported
operations return a structured capability error; they never fall back to raw
queries. SQL contract 0.3.1 activates:

- `read_model_job_views_v2` for scheduled, blocked, cancel-requested, and
  failed job views;
- `read_model_job_events`;
- `read_model_workflow_list`;
- `operator_schedule_list`.

The unreleased a24 candidate introduced the dormant models. Release 0.1.0a25
shipped both that CLI foundation and the activation migration
`0021_cli_read_model.sql`; it remains compatible with contract 0.3.0 before
the migration and contract 0.3.1 afterward.
