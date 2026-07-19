# External Design Review Round 6 — Response

> **Reviewed:** Stage-4 outlabsAPI Dogfood Specification (frozen by S4-00) + round-6 charter
> **Range:** `outlabs-taskq` `b6e29ca..edf4c33` (docs-only S4-00 range); host `outlabsAPI` at `a0019cd`
> **Method:** three independent source-audit passes (host inventory, library producibility,
> adversarial plan falsification) + reviewer-inline authority reads; every finding-grade claim
> re-verified by the reviewer against repository source before acceptance. Live suites rerun.

## 1. Verdict

**READY.** S4-01 may open.

No Contract questions exist. No BLOCKER or HIGH finding exists. The plan's architecture, contract
boundary, sequencing, and rollback design survived adversarial audit; the registry below is 7
MEDIUM + 8 LOW precision findings, every one remediable by bounded specification wording and
acceptance-vector additions inside the already-frozen slice structure. Preconditions to open
S4-01: **none** (R6-01's remediation is S4-01 work by definition). The specification corrections
for R6-02..R6-15 must land as a docs commit **before S4-02 opens**.

## 2. Reproduced evidence

- Library: HEAD `edf4c33`, worktree clean. Full PostgreSQL suite on 18.3: **448 passed / 1 opt-in
  skip**; Ruff + format clean. Same source previously reproduced by this reviewer on exact
  PostgreSQL 16.14 (448/1, disposable container), DB-free 289/289 on Python 3.12 and 3.13,
  million-row plan gate 2/2, artifact matrix corners through sdist × outlabs × 3.13.
- Host: HEAD `a0019cd`, worktree clean; `outlabs-auth==0.1.0a20` exact pin; `uv.lock` consistent
  (`uv lock --check` exit 0); **zero taskq references** (repo-wide grep). Suite: **47 collected →
  44 passed / 3 gated infrastructure skips** (`RUN_INTEGRATION_TESTS=1`); Ruff clean.
- Overlay: the spec §2 claim reproduces under its stated method (package loaded ahead of a20 —
  same 44/3). A **resolver-level** overlay (`uv run --with outlabs-auth==0.1.0a24`) re-resolves
  and upgrades **fastapi 0.135.1 → 0.139.2** (starlette 0.52.1 unchanged); exactly two tests fail
  (`tests/test_alerts_api.py::test_alert_route_is_exposed` — route set observed empty;
  `tests/test_api_boundary.py::TestApiBoundary::test_api_router_does_not_expose_discovery_routes`
  — `'_IncludedRouter' object has no attribute 'path'`) → 42/3/2. The third
  internals-introspecting test (middleware stack, `tests/test_runtime_config.py:27`) passes under
  0.139.2.
- Installed-package reality: `outlabs_auth-0.1.0a24` dist-info in the library venv; the adapter's
  called API names (seed/require_permission any-of/role-service guards) verified against it during
  the Stage-3 acceptance and unchanged in this range.

### Host source inventory (established independently, not from the spec)

Confirmed accurate as claimed by spec §2: single-uvicorn Dockerfile CMD (no workers flag);
authenticated `POST /tools/{tool_name}/runs/queued` publishing after-response via background task
to the legacy broker with **no durable id** and no publish-failure surface to the caller
(`app/domains/tools/api/routes.py:38-45`, `app/dependencies.py:246-259`); the tools worker
discards successful result data (`app/domains/tools/workers/tool_run_worker.py:33-45`); both
selected tools are async read-only HTTP operations; managed-PostgreSQL DSN normalization exists
incl. `sslmode→ssl` mapping (`app/config.py:26-66`); deploy docs pin the managed-DB/PaaS shape.
**The §3.1 claim that the host mounts an EnterpriseRBAC installation is correct**
(`app/core/outlabs_auth.py:105-120`; schema `outlabs_auth`, `auto_migrate` default false, session
exposed as an async context-manager factory `auth.get_session` — one of the three provider shapes
the taskq adapter owns). Additional host facts material to Stage 4: auth initializes at **module
import** (`asyncio.run` inside `configure_outlabs_auth`, `app/core/outlabs_auth.py:190-194`),
before any lifespan; `GET /health` returns 200 unconditionally today (`app/main.py:184-187`);
CORS currently has no `expose_headers` and allows only Content-Type/Authorization/X-API-Key/
Origin/Accept (`app/main.py:175-181`); lifespan shutdown closes only the auth resources — the
host's own engine/redis clients are never disposed (`app/main.py:127-128`).

## 3. Finding registry

Severity ordering; every entry re-verified by the reviewer in source. "Owner" = the slice whose
acceptance must carry the remediation and regression.

**R6-01 · MEDIUM · Dependency gate: the locked a24 resolution upgrades FastAPI and breaks two
host tests (carried forward from the S4-00 acceptance; remediation assessed).**
Evidence: reviewer's resolver-level overlay (fastapi 0.135.1 → 0.139.2); failing assertions at
`tests/test_alerts_api.py:17` and `tests/test_api_boundary.py:14`, both introspecting
`api_router.routes` internals that 0.139's `_IncludedRouter` changes.
Failure: S4-01's "prove the host suite from the locked environment" fails as written.
Remediation (assessed **adequate**): rewrite both tests router-internals-safe — assert against
the application-level OpenAPI path set or TestClient behavior, never `api_router.routes` shape —
inside S4-01, in the same change as the pin/lock update. Spec §2 and the S4-01 row should name
the known bump so the acceptance is exact.
Oracle: locked-environment host suite green; both rewritten tests must pass under fastapi
0.135.1 **and** 0.139.2 (they encode neither internals shape). Owner: S4-01.

**R6-02 · MEDIUM · §3.5 maps outcomes to a `Fail(...)` type that does not exist; raised
exceptions default to RETRYABLE.**
Evidence: handler result vocabulary is `Complete | Snooze | Cancel | Retry | NonRetryable`
(`src/taskq/execution.py:23-50`); a raised exception classifies `Retry` unless `task.retry is
False` (`src/taskq/worker.py:1098-1110`).
Failure: S4-02 implements "missing tool / invalid params / `ToolResult(success=false)` → terminal
non-retryable Fail" by raising → deterministic failures silently retry (3 attempts, 5–60 s
backoff) before dead-lettering.
Remediation: correct §3.5 to the real vocabulary — terminal cases **return**
`NonRetryable(error=...)`; retryable transport faults may raise or return `Retry`.
Oracle: S4-02 unit vector asserting the exact classification of each §3.5 row (terminal rows
produce one attempt; transport-fault row produces a retry). Owner: S4-02.

**R6-03 · MEDIUM · §3.4's keyed-replay sentence overstates the guarantee: dedup is
active-window only.**
Evidence: the dedup authority is partial unique index `jobs_idem_uq (queue, idempotency_key)
WHERE status IN ('blocked','queued','running')` (`0001_initial.sql:392-393`). After the first job
settles, the same key inserts a NEW job and re-executes the tool.
Failure: an acceptance test written from "repeating a keyed request must return `existed` and the
same job id" (unqualified) fails against correct library behavior post-settlement — or passes by
timing and enshrines a false durability claim toward callers.
Remediation: qualify the sentence to the active-window/concurrent guarantee (matching S4-03's
"keyed **concurrent** producer requests converge"); state that post-settlement replay is a new
execution. Oracle: S4-02 vector proving both halves (active-window converges; post-settlement
creates a new job). Owner: S4-02 wording + vectors.

**R6-04 · MEDIUM · Tool error text can carry secrets into durable taskq state.**
Evidence: `tools/umami/tool.py:62` interpolates the **full authentication response body** into an
error string (`f"Umami auth response missing token: {data}"`) that flows into `ToolResult.error`
(lines 232-238); the aerolineas tool stores upstream response text (truncated 200) in `error`.
Under §3.5 that content becomes the taskq error projection — durable for
`failed_retention_hours=720` and readable via `include_error` by any `taskq_tools:read` holder.
ADR-012 bounds **bytes** (2048), not content.
Failure: an auth-shape change or upstream error leaks token/response material into long-retention
queryable state.
Remediation: make §3.5's "bounded safe reason" mechanical — the S4-02 handler maps failures to a
sanitized classification + tool name, never raw upstream/auth text; fix `umami` line 62 in the
same slice. Oracle: S4-02 vector asserting the stored error projection contains no
credential/response-body content for a forced tool failure. Owner: S4-02.

**R6-05 · MEDIUM · Caller-supplied bearer token becomes durable, readable payload.**
Evidence: `tools/aerolineas/schemas.py:17` accepts `token: Optional[str]`;
`tools/aerolineas/tool.py:35-38,93-96,132-134` injects it into a **module-global cache shared
across all callers**. Under the plan, queued params persist as taskq payload
(`retention_hours=168`) readable via `include_payload` — an exposure duration the transient
legacy publish never created. Aerolineas is the cycle-2 canary.
Failure: a caller's bearer token becomes week-long queryable state; the shared cache lets one
caller's token silently serve another's requests (pre-existing host defect).
Remediation: S4-02 strips or rejects `token` (and any credential-bearing param) on the queued
path before enqueue. Oracle: S4-02 vector — queued request with `token` → typed rejection, or
stored payload proven token-free. The cache poisoning itself is recorded as a pre-existing host
defect (deferred follow-ups). Owner: S4-02.

**R6-06 · MEDIUM · §3.3 prescribes the cross-path replay its own rule forbids, and the
read-only-benign residual is unstated.**
Evidence: §3.3 forbids dual publishing/fallback "because each can execute a tool twice," then
directs: "The operator may change the feature flag and retry with an idempotency key." Sequence:
enqueue commits, response lost (typed ambiguous error) → operator flips mode to legacy → caller
retries with the same key → the legacy path (no idempotency concept) executes, while §7.3 keeps
the embedded worker draining the committed job. One logical request, two executions. The key
spans only the taskq path; the plan never says so, and the residual is acceptable **only**
because the allowlisted tools are read-only — a dependency the plan leaves implicit.
Remediation: two sentences in §3.3: the idempotency key is meaningless to the legacy path, so
flip-then-retry can execute a read-only tool twice; this residual is accepted only for read-only
lanes and is among the reasons side-effecting lanes are excluded (cross-reference the §9 stop
condition). Oracle: none required beyond the wording (the S4-02 no-fallback vector already covers
the application half). Owner: spec wording (before S4-02).

**R6-07 · MEDIUM · The deployment platform's kill grace is never pinned; container-stop defaults
SIGKILL before the 20 s soft stop.**
Evidence: §5 pins `soft_stop_timeout=20s` / `asgi_graceful_timeout=30s` (internally consistent),
§7.2 claims "old process drains within 20 seconds," S4-03 requires "shutdown releases/drains
inside platform grace" — but no requirement anywhere records or configures the platform's stop
timeout, and common container-stop defaults (10 s) are below both values.
Failure: every normal deploy SIGKILLs mid-drain; §7.2's evidence is unproducible and the S4-03
row has no recorded platform value to compare against. Correctness survives via lease recovery,
but two frozen acceptance rows silently degrade into the forced-failure path.
Remediation: one line in §5 + the S4-01 preflight: the platform stop grace must be configured
≥ `asgi_graceful_timeout` and the configured value recorded in the S4-AUDIT packet.
Oracle: S4-AUDIT packet contains the platform grace value and a normal-deploy drain transcript
inside it. Owner: S4-01 preflight + S4-AUDIT evidence.

**R6-08 · LOW · §5's health-readiness requirement has no owning acceptance row.**
`GET /health` is unconditionally 200 today (`app/main.py:184-187`); §5 requires 503 when taskq is
enabled-but-not-ready; no §8 row proves it (S4-AUDIT only "records observations").
Remediation: add to S4-02's row set: enabled + not-ready → 503; disabled → 200; backlog alone
does not fail health. Owner: S4-02.

**R6-09 · LOW · §7.3's drain condition names no observable; no stats field is literally
"queued".**
The stats surface buckets are `ready / scheduled / blocked / running / expired_running / dead`
(`0001_initial.sql:1867-1875`); retry-backoff jobs sit in `scheduled` (job status remains
`queued` with future `scheduled_at` — `0001_initial.sql:1523-1534` — so the state model itself
has **no stranding hole**; verified). An operator equating "queued" with `ready` can observe
0/0 while a retry waits in `scheduled` and stop the runtime early.
Remediation: one clause in §7.3: drain-zero means `ready + scheduled + blocked + running = 0`
for queue `tools`. Owner: spec wording (before S4-02); S4-AUDIT rollback transcript uses it.

**R6-10 · LOW · §3.2 "the task registry exposes one canonical task" contradicts §6's probe
task.** Remediation: qualify §3.2 ("plus the §6 probe task only while its flag is enabled") so an
exactly-one-task assertion doesn't block the drill. Owner: spec wording; S4-02 registry vector.

**R6-11 · LOW · The controlled-failure drill is executed in §7.2 (cycle 2, step 5) but owned by
S4-AUDIT in §9.** Ambiguous board ownership invites closing S4-03 without the drill or running it
with no task hosting its evidence. Remediation: one line assigning §7.2 step 5 to S4-AUDIT.
Owner: spec wording + board.

**R6-12 · LOW · The two S4-03 invocation-count rows name no independent oracle.**
"One tool invocation" asserted from handler logging is the implementation testing itself.
Remediation: pin the oracle — attempt/event ledger arithmetic in the taskq tables plus an
external invocation counter (target-service access log or a local counting endpoint).
Owner: S4-03 vectors.

**R6-13 · LOW · S4-03 "host requests remain responsive" is unfalsifiable as written.**
No threshold exists and §10 (correctly) forbids inventing a baseline. Remediation: a crude,
explicitly non-baseline bound (e.g., health and sync-tool endpoints answer within N seconds while
a job runs) or an event-loop-stall check. Owner: S4-03 vector.

**R6-14 · LOW · The depth-refusal row's live method is unspecified.**
The refusal is real and typed (advisory existence probe raising the depth-refusal error,
`0001_initial.sql:824-833`), but producing it live requires ~1000 queued jobs against a
concurrency-1 canary. Remediation: name the method — a scratch queue with a tiny `max_depth`
ensured via the operator pre-deploy credential, or an accepted harness-level proof for this one
row. Owner: S4-03 vector definition.

**R6-15 · LOW · §5's production posture omits the two enablement keys the runtime validator
requires.** `expected_environment="production"` + `allow_production=True` are mandatory for a
production start (`src/taskq/http/runtime.py:100-102`); the §5 block omits both ("equivalent to"
softens this, but S4-02's settings-reject row should enumerate them). Remediation: add both keys
to §5's posture block and the S4-02 settings vectors. Owner: S4-02.

## 4. Contract questions

**None.** No plan item requires a new wire field, SQL function, outcome, or permission-grammar
change. Deliberately re-verified: the §4.2 profile is exactly the contract's ten-key whitelist
with in-bounds values (`0001_initial.sql:1927-2018`); the §3.4 202 body and status URL are
producible from `EnqueueResult` + the mounted facade's exact GET-job query allowlist
(`facade.py:313-321`) with no request echo standing in for durable state; the probe needs only
generic registry/Retry/async-hold surface (no library change).

## 5. Acceptance audit matrix

Per-row oracle assessment (independent oracle named; weak rows are the LOW findings above):

- **S4-01:** lock/resolve rows — resolver + lock content, independent, durable: sound. Host
  47-test suite/Ruff/type/Docker — pre-existing regression suite, independent of new code: sound
  **after R6-01's rewrite** (currently fails honestly under the locked resolution). Preview
  migrate/verify/provision — external managed-DB behavior with transcripts: sound. "Facts
  recorded without credentials" — evidence-existence row; recommend cross-checking recorded
  server facts against the live `pg_settings`/provider console during acceptance. Idempotent
  pre-deploy + auto-migrate off — run-twice oracle: sound.
- **S4-02:** settings rejections — enumerated-list oracle (add R6-08/R6-15 rows): sound.
  Mutually-exclusive producer / no-fallback-after-ambiguous — needs a fault-injection seam and a
  legacy-publisher spy; the oracle exists but should be named in the vector; the per-request
  mode/allowlist snapshot should be pinned (one settings read per request). Exact 202/keyed
  replay — frozen JSON + typed dispositions (scope per R6-03): sound. Lifespan-to-baseline —
  resource-census comparison: sound. CORS/OpenAPI/projection exactness — the generated catalog is
  a genuinely independent oracle: sound. No-contract-change diff — git: sound.
- **S4-03:** live end-to-end and hiding/job-untouched rows — real DB rows, canonical reads,
  `updated_at` invariance: sound. Invocation-count rows — R6-12. Depth refusal — R6-14.
  Responsiveness — R6-13. Shutdown-inside-grace — R6-07. Poll-only disconnect recovery —
  fault-injectable, plus source inspection for the no-sleeps property: sound.
- **S4-AUDIT:** hashes, redacted config manifest, IAM report, attempt/event conservation
  arithmetic (strong ledger oracle), resource ledger, 202→GET transcripts, rollback/re-enable
  transcript, producer-scope confirmation — durable artifacts reviewed by the independent
  acceptor: sound. The "honest latency/cost note" is a judgment row correctly bounded by §10's
  no-baseline rule.

**No row is unsalvageably self-asserting.** The weakest rows are exactly R6-12/R6-13/R6-14 plus
the platform-grace gap (R6-07).

## 6. Scope and hygiene result

The S4-00 range `b6e29ca..edf4c33` contains exactly two commits, both trailered, worktree clean:
the Stage-3 acceptance record (`8a13262`: TASKS.md, docs/README.md, Build Plan, README, AGENTS —
status/registration lines only) and the freeze (`edf4c33`: the 401-line Tier-3 specification, the
165-line round-6 request, and board/index/status updates). The Build Plan edits are status-record
paragraphs, not stage-strategy or exit-gate changes; under the documentation constitution the
Build Plan is Tier-2 operating plan, so the range stays within the charter's allowed set — the
four front-door status files are explicitly **accepted** as registration text. No SQL, migration,
Tier-0, Tier-1 decision, Tier-4, host-source, dependency, or Stage-4 implementation change
exists anywhere in the range. `docs/design-review-6/` contained only `REQUEST.md` before this
response. Zero third-party queue-project names appear in the new documents; this response refers
to the existing system only as the legacy broker/path.

## 7. Preconditions to open S4-01

**None.** R6-01's remediation is S4-01 work by definition and is now pre-known with its failure
signature. All other findings are specification-wording and acceptance-vector corrections that
must land as a docs commit **before S4-02 opens** (R6-02, R6-03, R6-06, R6-09, R6-10, R6-11,
R6-15 wording; R6-04, R6-05, R6-08, R6-12, R6-13, R6-14 as owned vectors; R6-07 into the S4-01
preflight list and S4-AUDIT packet).

## 8. Deferred follow-ups (non-blocking, named owners)

- Aerolineas module-global token cache is cross-caller state (any `tools:run` holder can poison
  the token used by other callers) — pre-existing host defect independent of Stage 4; host
  backlog.
- `umami` auth-response interpolation (`tools/umami/tool.py:62`) — fix recommended inside S4-02
  while the lane is touched (listed under R6-04).
- `requirements.txt` is an unpinned parallel dependency list beside `uv.lock`; production builds
  use `uv sync --frozen`, so provenance is unaffected — host hygiene cleanup.
- Legacy worker/API queue-declaration argument mismatch (dead-letter arguments declared on one
  side only) — legacy-broker context noted during inventory; irrelevant to Stage 4; host backlog.
- Host lifespan shutdown never disposes the host's own engine/redis clients — pre-existing;
  S4-02's lifespan-to-baseline vectors will make this visible and it should be fixed there or
  explicitly accepted.
