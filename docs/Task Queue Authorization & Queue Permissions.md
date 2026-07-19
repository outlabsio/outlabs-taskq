# taskq — Authorization & Queue Permissions

> **Status:** Normative design — 2026-07-18; grammar corrected per D-01 (outlabs-auth's creation-time validators reject dots and require exactly one colon; verified in `outlabs_auth/utils/validation.py::validate_permission_name` and `schemas/permission.py`); **ADR fold-in applied same day** — this doc is the detail behind [ADR-006](./adr/ADR-006-permission-grammar-authoritative-lookup.md) (grammar + authoritative lookup) and defers to [ADR-010](./adr/ADR-010-db-roles-security-definer-maintenance.md) for database roles.
> **Extends:** [`Task Queue Library Extraction Design Brief.md`](./Task%20Queue%20Library%20Extraction%20Design%20Brief.md) §5 (the three trust layers, `TaskqAuth`, adapters). That section remains authoritative for layering; this doc adds **queue-scoped authorization** and the **provisioning DX**.
> **Grounded in:** outlabs-auth `0.1.0a24` source survey (permission parser, `PermissionSeed`/`seed_system_records`, service tokens, API-key grant-policy prefixes, entity/tree deps). Verify against the pinned version at implementation time — outlabs-auth is alpha and may move.
> **Audience:** implementers of `taskq.http` and `taskq.http.outlabs`; host bootstrap authors.

---

## 0. Verdict

Per-queue permissions are a **facade-tier feature**. The SQL contract stays IAM-agnostic (L1: the capability roles are queue-global — ADR-010/011; stated honestly in §7 below). The facade grows one concept — every route is annotated with an **action** and a **queue extractor** — and adapters decide what a `(action, queue)` pair requires. The outlabs-auth adapter maps it onto a two-candidate permission check with a naming convention that survives outlabs-auth's wildcard parser, plus an **opt-in provisioning helper** that makes "new queue + permissions + roles" a one-liner in host bootstrap.

Design goal, in the owner's words: *"super simple just to set up queues with different permissions."* Concretely:

    await provision_taskq_auth(auth, session, queues=["emails", "tools"], roles="standard")
    # → five global + five exact per-queue permission rows seeded for each queue
    # → roles taskq-producer / taskq-worker / taskq-operator / taskq-admin created
    # then mint a service token or API key holding taskq_emails:run — that worker can ONLY work "emails"

---

## 1. Actions (closed set)

Five verbs cover the whole facade. Every route maps to exactly one.

| Action | Covers (facade routes) | Typical principal |
|---|---|---|
| `enqueue` | enqueue, enqueue-bulk | producer services, API endpoints |
| `run` | claim, heartbeat, complete, fail, release, snooze | workers |
| `read` | job detail, queue stats, cutover/schema status | dashboards, workers, operators |
| `control` | pause, resume, cancel, redrive, expire-worker | operators, ops tooling |
| `admin` | ensure/alter queue, concurrency limits | deploy/bootstrap, admins |

Rules:

1. `run` deliberately bundles claim **and** settle — splitting them creates principals that can claim but never settle (a wedge machine, never a real need).
2. `tick`/`janitor` are database/CLI-only through the housekeeper capability and have no facade
   action or HTTP route. Workflow, schedule, and general-list routes are inactive in 0.1.
3. Verb names are part of the contract — adapters, docs, and the provisioning helper all use these five strings.

---

## 2. The facade contract: action + queue extractor

### 2.1 Route annotation (normative)

The generated Taskq sub-application declares, per active route: `(action, queue_source)` where
`queue_source ∈ {path_param, declared_queues, job_lookup, none}`.

| Route | Action | Queue source |
|---|---|---|
| `POST /taskq/v1/queues/{queue}/claims` | `run` | path |
| `POST /taskq/v1/queues/{queue}/jobs` | `enqueue` | path |
| `POST /taskq/v1/queues/{queue}/jobs/batch` | `enqueue` | path — one queue and one authorization check per atomic batch |
| `POST /taskq/v1/jobs/{id}/heartbeat·complete·fail·release·snooze·cancel-running` | `run` | job lookup |
| `POST /taskq/v1/workers/heartbeat` | `run` | every distinct declared queue, all-or-nothing preflight |
| `POST /taskq/v1/jobs/{id}/cancel·redrive·expire` | `control` | job lookup |
| `GET /taskq/v1/jobs/{id}`, queue stats | `read` | job lookup / path; global stats uses no queue |
| `POST /taskq/v1/queues/{queue}/pause·resume` | `control` | path |
| (no HTTP tick route — the housekeeper is runtime-internal, ADR-011; manual surface is the operator CLI) | — | — |
| queue ensure / concurrency-limit commands | `admin` | path / none; operator pool + authorizer only |

**Job-lookup order (normative, ADR-006):** authenticate → **`taskq.get_authorization_projection(job_id)`** (a `SECURITY DEFINER` read granted to the facade's observer role, exposing only id, queue, job_type, status — never payloads or attempt fences) → authorize `(action, projection.queue)` → invoke the fenced mutation (which re-validates ownership atomically). Caller-supplied queue/job_type in payloads are **assertions**: rejected on mismatch (409/422 per the ADR-005 protocol), never an authorization input. On authorization failure the default is **403 naming the queue** (single-tenant blast-radius scoping, not tenant isolation); hosts that want existence-hiding set `not_found_on_forbidden=True` to get 404.

### 2.2 `QueueAuthorizer` protocol (supersedes `TaskqAuth` read/write/operator)

Defined in `taskq.http.deps`, zero outlabs imports:

    class QueueAuthorizer(Protocol):
        async def authorize(
            self, request: Request, action: TaskqAction, queue: str | None,
        ) -> AuthContext:
            """Raise HTTPException(401/403) or return the principal context.
            queue=None means a global (non-queue-scoped) route."""

- `AuthContext` (actor string + opaque principal) is unchanged from the extraction brief §5.2.
- **Back-compat shim:** a v1 `TaskqAuth` (read/write/operator) wraps into a `QueueAuthorizer` that ignores `queue` — `read → read`, `enqueue/run → write`, `control/admin → operator`. The bundled `static_api_key_auth` / `bearer_token_auth` / `no_auth_for_tests` adapters stay queue-blind exactly this way: one credential, all queues. Queue scoping is opt-in sophistication, never a toll on the simple path.
- Canonical bulk enqueue authorizes its one path queue exactly once. Historical body-queue enqueue
  shapes may exist only as host-owned legacy aliases; no multi-queue bulk alias is permitted because
  splitting it would destroy the canonical command's one-transaction atomicity.

---

## 3. Permission naming under outlabs-auth (the load-bearing convention)

outlabs-auth validates permission names **at creation** (`validate_permission_name` + the router schema, both in `0.1.0a24`): exactly one colon, each component matching `^[a-z0-9_-]+$` or being exactly `*`. **Dots are invalid** — a dotted convention could never exist as catalog rows. Queue names match `^[a-z0-9_]{1,57}$` (57-byte cap per spec §4 v1.6) and the `taskq_` prefix is fixed, so the underscore join is injective (`taskq_X = taskq_Y` iff `X = Y`):

| Permission | Meaning |
|---|---|
| `taskq_{queue}:{action}` | action on ONE queue — e.g. `taskq_emails:run` |
| `taskq_{queue}:*` | all actions on one queue (outlabs-auth native wildcard) |
| `taskq:{action}` | **global fallback** — action on ANY queue |
| `taskq:*` | full taskq surface (operator/admin bundles) |

**Two-candidate check (normative):** the adapter authorizes `(action, queue)` by requiring ANY of `taskq_{queue}:{action}`, `taskq:{action}` (outlabs-auth `require_permission(a, b)` is any-of by default — the adapter passes that intent explicitly, never relying on the default). Global routes (`queue=None`) check `taskq:{action}` only.

**Validator rule (normative):** the catalog builder runs every generated name through outlabs-auth's **real** validator (importable under `[outlabs]`); taskq never maintains a look-alike regex that can drift.

Explicitly impossible (validator reality, documented, not fought): **"one action on ALL queues" via wildcard** — `taskq_*:run` fails the component charset (`*` is only valid as the whole component). That grant is spelled `taskq:run` (the global fallback). Per-queue-set grants enumerate queues (the provisioning helper makes that cheap) or use roles.

### 3.1 Dynamic dependency construction

outlabs-auth builds permission checkers per name-set. The adapter builds them **lazily per (action, queue) and caches** — the exact pattern qdarteAPI and diverse-data-api already use in `_permission_dependency` (construct `get_auth().deps.require_permission(...)`, then `await checker(request=request, session=session)`):

    # taskq/http/outlabs.py (sketch)
    class OutlabsQueueAuthorizer:
        def __init__(self, *, auth, session_dependency,
                     actor_from_principal=None, not_found_on_forbidden=False): ...
        # no resource_prefix: the taskq namespace is fixed (ADR-006; R2-17)

        async def authorize(self, request, action, queue):
            names = ([f"{self._prefix}_{queue}:{action}"] if queue else []) + [f"{self._prefix}:{action}"]
            checker = self._checkers.get_or_build(tuple(names))   # cached makefun dep
            result = await checker(request=request, session=await self._session(request))
            return _to_auth_context(result)                        # actor = email | key name | service id

There is **no** `resource_prefix`/namespace option (v1.6, R2-17): ADR-006 fixes the grammar and ADR-002 fixes the schema — isolated installations use separate databases. A configurable prefix would fork the permission namespace the provisioning helper, adapters, and catalogs all assume.

### 3.2 Legacy host mappings (strangler-compatible)

During cutover, hosts keep their existing catalogs; the adapter accepts **extra candidate names** per action so old grants keep working:

    OutlabsQueueAuthorizer(
        auth=auth, session_dependency=get_async_session,
        extra_candidates={
            "read":  [DiversePermission.JOB_READ],        # Diverse today
            "run":   [DiversePermission.JOB_WRITE],
            "enqueue": [DiversePermission.JOB_WRITE],
            # QDarte: "run"/"enqueue" → QdartePermission.WORKER_RUN, "control" → JOB_CONTROL
        },
    )

This replaces the brief's static read/write/operator mapping tables — same hosts, now with a path to per-queue tightening lane by lane (tighten = mint new tokens with `taskq_{queue}:run`, drop the legacy candidate when the lane is done).

---

## 4. Provisioning DX (the "one-liner" — opt-in, host-initiated)

The extraction brief §5.3 said "the package never seeds IAM rows." **Amended, precisely:** the package never seeds at import/mount time and never runs implicitly — but `taskq.http.outlabs` SHIPS an explicit provisioning helper the host calls from its own bootstrap (the same place it already calls `seed_system_records`). Seeding remains host-initiated; taskq just kills the boilerplate.

### 4.1 Catalog builder + seeder

    from taskq.http.outlabs import taskq_permission_catalog, provision_taskq_auth

    # Pure function → tuple[PermissionSeed, ...] (feed to seed_system_records yourself)
    catalog = taskq_permission_catalog(queues=["emails", "tools"])
    # 5 global (taskq:enqueue…admin) + 5 per queue (taskq_emails:enqueue…)
    # every name is passed through outlabs-auth's real validator before returning

    # Or the full helper: idempotent seed + optional standard roles.
    # Permissions go through seed_system_records(permission_catalog=...); ROLES go
    # through outlabs-auth's public RoleService (seed_system_records does not create
    # roles — the helper owns that part) in two modes:
    #   mode="report" (default) → typed report of created/existing/changed/conflicting
    #   mode="apply"            → applies; NEVER silently mutates a manually edited
    #                             role unless reconcile=True is passed explicitly
    report = await provision_taskq_auth(
        auth, session,
        queues=["emails", "tools"],
        roles="standard",              # None → permissions only
        role_prefix="taskq-",
        mode="report",
    )

**Standard roles** (created only when `roles="standard"`, idempotent,
`is_system_role=False`) — these are **outlabs-auth IAM roles, not PostgreSQL roles** (the DB
capability roles are ADR-010/011's, a different trust layer). Non-system is deliberate: the supported
public role service refuses every mutation of a system role, while explicit `reconcile=True` must be
able to converge drift through public APIs.

| Role | Grants |
|---|---|
| `taskq-producer` | `taskq:enqueue`, `taskq:read` |
| `taskq-worker` | `taskq:run`, `taskq:read` |
| `taskq-operator` | `taskq:read`, `taskq:control` |
| `taskq-admin` | `taskq:enqueue`, `taskq:run`, `taskq:read`, `taskq:control`, `taskq:admin` |
| `taskq-worker-{queue}` (per queue, opt-in `per_queue_roles=True`) | `taskq_{queue}:run`, `taskq_{queue}:read` |

### 4.2 CLI

    taskq auth sync-permissions --queues emails,tools [--roles standard]

Requires `taskq[outlabs]` + host DSN/schema settings; calls the same helper; prints a diff (created / already-present) and **prints any host-config change it cannot make** (next section). API keys cannot hold wildcard scopes; key grants enumerate exact actions even though wildcard permissions remain meaningful for roles and service-token matching. Also: `ensure_queue(..., provision_auth=True)` forwards to the helper when an outlabs adapter is configured — declare a queue and its permissions in one call.

### 4.3 API-key grant-policy honesty (document, don't hide)

outlabs-auth gates which scopes an API key may HOLD by action-prefix allowlists. taskq's verbs vs the defaults:

| Verb | Personal keys (default) | System keys (default) |
|---|---|---|
| `read` | allowed (`read` prefix) | allowed (`read` prefix) |
| `run` / `control` | denied under EnterpriseRBAC policy; not package-enforced under SimpleRBAC | allowed (`run` / `control` prefixes) |
| `enqueue` / `admin` | denied | **not in default list** |

(Verified against `core/config.py` defaults in `0.1.0a24` — corrected 2026-07-18; an earlier revision of this table wrongly claimed system keys lack `read`.) So: hosts minting **API keys** carrying `enqueue`/`admin` taskq scopes add those verbs to `api_key_system_allowed_action_prefixes` (one constructor kwarg; the CLI prints the exact line) — worker keys (`run`/`read`) need no policy change. EnterpriseRBAC personal keys stay locked down. Under SimpleRBAC the package's personal-key policy returns before prefix filtering, so worker denial is instead a documented host grant invariant: never grant `run` to human roles and use service tokens for fleets. **Service tokens bypass grant-policy entirely** (permissions embedded at mint) — which is one reason they're the default worker credential below.

### 4.4 Worker credential guidance (normative table)

| Credential | When | Notes |
|---|---|---|
| **Service token** (qdarte pattern) | Default for fleets on SimpleRBAC hosts | Embedded scopes (`taskq_{queue}:run`, `taskq_{queue}:read`); ≤30-day lifetime → rotation is an ops rhythm, document alongside deploy |
| **System-integration API key** | Enterprise hosts wanting durable, rotatable, IP-allowlisted identities | Principal's `allowed_scopes` caps key scopes; RBAC-only (no ABAC conditions) |
| **Static key / bearer adapters** | No-IAM hosts (labs, outlabsAPI pre-auth) | Queue-blind by design |
| Personal API key | Never recommended for workers | EnterpriseRBAC prefix policy blocks `run`; SimpleRBAC must enforce this through human-role grants |

One credential per fleet remains acceptable single-tenant posture (Unified Spec §14); per-queue tokens are the upgrade path, not a mandate.

---

## 5. Enterprise path (queues as entities) — later, if hierarchy is real

outlabs-auth EnterpriseRBAC models resources as an entity tree with `_tree` permission variants and integration-principal anchoring. Mapping: one entity per queue under a `taskq` root entity → `taskq:run_tree` granted at the root = "run on all queues, including future ones"; per-subtree grants give team-level scoping; system keys anchor to a queue entity.

**Not v1:** both hosts run SimpleRBAC; the string convention above covers every current need without entity migration. Keep the door open by never encoding SimpleRBAC assumptions into `QueueAuthorizer` (it already doesn't — an Enterprise adapter is just another implementation). Revisit when a host actually runs EnterpriseRBAC.

---

## 6. Actor propagation (unchanged, restated)

`AuthContext.actor` flows into `taskq` events (`operator:{email}` for humans, key/service name for machines, worker_id for workers) exactly as the extraction brief defines. Per-queue authorization adds nothing to the SQL audit surface — it only changes who gets through the door.

---

## 7. Trust boundary, stated honestly

Queue scoping is enforced **at the HTTP facade only**. A principal holding raw database capability-role credentials (ADR-010: producer/runner/observer/operator) can drive any queue its functions reach — L1's job is function-level least privilege and state-machine integrity (CAS, fencing, budget), not tenancy. This is the documented single-tenant posture (Unified Spec §18 "coarse trust model"), now with a finer front door. Consequences:

1. Fleets that must be queue-scoped go through the facade (both fleets already do — no-DB-credential workers).
2. DB-direct clients (CLI, in-process API code, embedded workers) are trusted code by definition.
3. Per-queue **database** roles are rejected: row-level security or per-queue GRANTs on SECURITY DEFINER functions would fork the SQL contract per deployment for a tenancy model nobody needs. If real multi-tenancy ever arrives, that's a separate design (and probably separate databases).

---

## 8. Acceptance tests

1. Adapter matrix: token holding `taskq_emails:run` → can claim/settle on `emails`; 403 on `tools`; global `taskq:run` → both.
2. Two-candidate check: `taskq:enqueue` alone authorizes enqueue on any queue; `taskq_emails:enqueue` alone authorizes only `emails`.
3. Canonical batch on the `tools` path while holding only `taskq_emails:enqueue` → 403 naming
   `tools`; nothing enqueued (authorize once before executing the atomic one-queue batch).
4. Job-lookup routes: settle on a job in a forbidden queue → 403 (404 when `not_found_on_forbidden=True`); job stays untouched.
5. Legacy candidates: Diverse `job:write` token still claims during strangler; removing the extra candidate breaks it (proving the tightening path).
6. `provision_taskq_auth` idempotent: second run creates nothing, reports already-present; catalog matches queue list.
7. v1 `TaskqAuth` shim: existing static-key mounts work unchanged, queue-blind.
8. Core import rule intact: `import taskq`, `import taskq.http` succeed without outlabs-auth installed (adapter module only under `[outlabs]`).
9. Service token with embedded `taskq_emails:run` authorizes without any permission rows existing (embedded-scope path).
10. First `mode="apply"` on empty IAM creates the catalog and all four standard roles; a drifted
    non-system standard role conflicts by default and converges only with `reconcile=True`.
11. EnterpriseRBAC rejects a personal key carrying `run`; SimpleRBAC demonstrates that a personal
    key follows its owner's grants and is denied only when the documented human-role policy holds.

---

## 9. Explicit non-goals

- Per-queue SQL roles / RLS on `taskq.jobs` (§7.3)
- ABAC conditions on taskq permissions (principal-backed keys are RBAC-only in outlabs-auth; keep taskq checks condition-free)
- taskq-owned users/roles UI (OutlabsAuthUI / host admin territory)
- Namespace-level RBAC on Blueprint task names (feature 08 non-goal stands — authorization scopes queues, not job types)
- Multi-tenant fairness or isolation guarantees (Unified Spec §1 non-goal)
