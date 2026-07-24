# outlabs-taskq — Execution Tracker

> **Tier 2 (live).** Task-level truth for the implementation: what is in flight, what is next, what is done. The [Build Plan](docs/Task%20Queue%20Build%20Plan.md) owns stage strategy and exit gates; this file owns the granular work. **Update this file in the same commit as the work it describes** — a task not updated here didn't happen.

## Cold start (any agent, from zero)

1. Read `AGENTS.md` (hard rules) → `docs/README.md` (tier map — Tier-0 contracts beat everything) → this file.
2. Environment: Python 3.12+, `uv`, and a local PostgreSQL. A dev Postgres 18 usually runs via docker (`docker ps` → container from localDevServices, `postgres/postgres@localhost:5432`). Create/reuse the scratch DB:
   `psql postgresql://postgres:postgres@localhost:5432/postgres -c "CREATE DATABASE taskq_stage1_test"` (ignore exists-error).
   Caveat: migration 0001 creates six cluster-wide `taskq_*` roles on that server — expected on a dev cluster; never point tests at a shared/production server.
3. Run everything:
   ```bash
   uv sync --extra dev --extra http --extra outlabs
   uv run pytest tests/ -q                                   # T1 only (no DSN)
   TASKQ_TEST_DSN="postgresql://postgres:postgres@localhost:5432/taskq_stage1_test" \
     uv run pytest tests/ -q                                 # T1 + T2 (must be 42/42 before you start)
   uv run ruff check .
   ```
4. Pick the topmost unchecked task in **Now**, or the next in **Next**. Work it to its acceptance criteria.
5. Definition of done, every task: suite green (no skips you introduced), `ruff check` clean, docs amended if the task's row says so, this file updated (move the task, one-line result note), one commit ending with the repo's `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` trailer convention.

**Standing rules (non-negotiable):** Tier-0 contracts and ADRs win every conflict — if implementation reveals a contract bug, STOP, record it under **Contract questions** below, and fix docs-first (errata/ADR), never code-around. Never name third-party queue projects. Never edit Tier-4 historical docs. New SQL functions require a Function Manifest entry first.

## Status snapshot

| | |
|---|---|
| Stage | **Stage 5 QDarte full replacement** — the owner has retired the contact-only strangler direction as the destination. The only active goal is one native taskq system for every QDarte lane, followed by deletion of both old queue implementations, every compatibility mode/wrapper, and their execution data. Business content remains; queue history is not migrated. FR-00/01/02 are complete and FR-03 is active. The native contact, website, classification, photo verification, editorial, listing-research, content-synthesis, translation, review, buzz, region-rescue, photo-find and discovery-import effect families are complete. Production remains untouched |
| Suite | taskq 584/584 regular with 1 opt-in skip on local PostgreSQL 18.3 using the CI-shaped Redis; the previously accepted exact-16.14 lane is unchanged and Ruff is clean. Published `v0.1.0a7` remains the exact QDarte pin. Runtime is 1184/1184 with one unrelated dependency warning and clean Ruff/196-file MyPy; workers are 736/736 with clean Ruff and MyPy; the API is 1812 passed/11 known unrelated route/environment/stale-fixture failures, while the focused discovery-import/reporter/HTTP/inventory set is 38/38 with clean static gates. Runtime `491c0b9`/`10fdf3f`/`9c750ee`, API `f24642c`/`b0e96cb` and workers `e150e1a` close the immutable discovery-shard authority, real database import, no-resharding regression, reporter replay and handler boundary. No external provider, persistent database or production state changed |
| Contracts | Protocol v1 document revision 1.0.13 + Function Manifest/installed SQL 0.2.3 through immutable migration 0013 (+ ADR-012..031). ADR-029 freezes only finite running/finished queue pages and one exact workflow projection; ADR-030 preserves cancellation lock order through no-FK private counters; ADR-031 adds only QDarte's private closed provider-control reporter member. B9-backed migration 0012 activates all three finite projections, and 0013 repairs only the committed workflow-page composite assignment without changing its identity or capability state |
| Next review | Bind the next source-derived family only after its old handler and completion-hook behavior are re-derived; FR-03E remains the later whole-registry disposable SQL/HTTP completion gate |

## Now

- [x] **S5-QD-FR-00 · Full QDarte replacement destination frozen** — the new Tier-3 full-replacement specification supersedes the contact-only compatibility/strangler destination. It pins the five-repository source floor, 23 declared types and 21 executable handlers, the preserve-business-content/delete-queue-history boundary, one native registry and `WorkerService`, a general trusted domain-effect protocol, initial resource-isolated queues, the missing 0.2 capability program, all-lane migration waves, complete executable-code deletion, existing-install contraction plus a clean fresh baseline, a one-time production cutover, and a machine-generated zero-legacy audit. No source, SQL, migration, database, worker, provider, service, credential, deployment, or production state changed. FR-01 alone is next.

- [x] **S5-QD-FR-01 · Executable inventory and deletion manifest** — QDarte commits runtime `bd761a7`, API `cbe7949`, workers `9baec77` and admin `5a169e9` add a source-derived aggregate manifest plus repository-local closed file manifests and drift gates. The executable oracle covers 266 queue-sensitive files, 23 registered types, 21 handlers, every allowed-child edge and worker default, 30 API worker-domain relations and 130 worker routes; every file has a replacement disposition, owner and proof, every relation is classified, and the five-queue cohort map plus eight missing native capabilities are explicit. Adding an unclassified symbol or changing the registry/handler/default/route/relation graph now fails. Runtime 1151/1151, workers 629/629, admin 114/114 plus build/typecheck, and all four focused inventory gates pass; the API's unrelated full-suite order/environment debt is recorded in the status snapshot for closure before FR-AUDIT. All four branches are pushed. No production, database, queue or legacy execution state changed.

- [x] **S5-QD-FR-02 · Native 0.2 prerequisite program** — from FR-01 evidence, delivered lossless follow-ups, sealed dependencies/workflows, database-time schedules with janitor takeover, and only the three independently bounded finite projections QDarte needs. Every family followed docs-first contract, bridge, immutable migration, typed client/fake/runtime, parity/race/plan/artifact and dual-major gates; no QDarte emulation or generic reporting surface was introduced.

  - [x] **FR-02-SPEC · Native orchestration program frozen** — the Tier-3 0.2 specification narrows FR-01's eight capability gaps to four independently activated product families and fixes the bridge/migration order `0008` follow-ups → `0009` workflows/dependencies → `0010` schedules → proof-backed finite projections. It freezes lossless follow-up validation/atomicity, producer-safe workflow creation across declared queues, lock-ordered graph admission and propagation, database-clock schedule claiming/firing and janitor takeover, exact redacted projection limits, client/fake/runtime obligations, dual-PG evidence and stop conditions. No Tier-0 contract, ADR, SQL, migration, source, package, QDarte database, queue or production state changed. FR-02A docs-first contract work is next.
  - [x] **FR-02A · Lossless native follow-ups** — freeze ADR/Protocol/Manifest identities and bridge support, then ship immutable migration 0008, typed child targets, runtime/client/fake support and complete atomicity/race/parity/artifact evidence.
    - [x] **FR-02A-SPEC · Follow-up contract frozen docs-first** — ADR-024, Protocol document revision 1.0.9 and Function Manifest target 0.2.0 activate ADR-007 without changing the complete route/signature, settle outcomes, IAM grammar or wire major. They freeze the closed child object and derived step key, validate-all/commit-all transaction, child-queue `run` authorization, owner-only depth-exempt helper, runner-without-producer authority, exact post-0008 capability set, ADR-020 bridge/rollback floor and complete dual-PG evidence matrix. No SQL, migration, Python source, package, QDarte database, queue or production state changed; bridge implementation is next.
    - [x] **FR-02A-BRIDGE · 0.2.0 runtime floor prepared** — the closed ADR-020 runtime set now accepts `{0.1.2,0.1.3,0.1.4,0.1.5,0.2.0}` without inferring capabilities from version. Admission remains separately restricted to its safe `{0.1.5,0.2.0}` set plus exact capability presence, with a 0.2.0 regression proving the existing admission surface survives the additive revision. The historical exact-0.1.2 negative remains. No migration, SQL catalog, facade route, generated model, QDarte database or production state changed; 0008 may now be implemented without stranding this runtime.
    - [x] **FR-02A-SQL · Immutable migration 0008 and atomic kernel complete** — migration `0008_followups.sql` installs the owner-only depth-exempt helper, replaces `complete_job` without changing its identity, and activates the exact 0.2.0 capability set only after the 0007 metadata precondition. Executable vectors prove all-before-parent validation, the 20-child boundary, same/cross-queue insertion, depth exemption, response replay, second-child collision rollback, a real tuple-lock concurrent completion, exact private grants, the pre-0008 `TQ501` transition, fresh/full 0001→0008 chains, catalog parity and installer concurrency. The identical full suite is 513 passed with one opt-in skip on PostgreSQL 18.3 and exact 16.14; both million-row plan lanes are 2/2 and Ruff/format are clean. No HTTP, registry, client, fake, QDarte database or production state changed; the typed surface is next.
    - [x] **FR-02A-SURFACE · Finite typed graph and transport parity complete** — `Followup` is the single closed Protocol model used by handler results, SQL transport, both generated HTTP clients and the testing fake; `FollowupTarget` makes each parent task's allowed `(queue, job_type)` graph explicit. Worker construction rejects missing or queue-mismatched declarations, handler normalization validates child payloads against their registered model, and an invalid declaration terminal-fails without acquiring producer authority. The facade now authorizes the authoritative parent queue before body decode and every distinct resolved child queue before SQL. Fake/SQL/HTTP/raw-row parity pins the same derived child graph, the fake inserts native children rather than simulating a second producer call, both HTTP clients serialize the exact shape, and OpenAPI exposes no extra child field. PostgreSQL 18.3 is 521 passed with one opt-in skip; focused typed/authorization/parity evidence is 197/197 and Ruff is clean. Dual-major repetition, packaging isolation and final resource evidence remain under FR-02A-AUDIT.
    - [x] **FR-02A-CI · Installed-artifact ledger corrected** — the Python 3.12/3.13 artifact jobs exposed stale packaging oracles: the installed migration ledger stopped at `0007` and the function catalog at 46 even though the built distribution correctly shipped `0008_followups` and the 47-function 0.2.0 manifest. The permanent smoke now asserts the complete 0001→0008 chain, exact 47-function catalog and public closed `Followup` construction from each installed wheel and sdist; no SQL, migration, contract, runtime or QDarte source changed.
    - [x] **FR-02A-AUDIT · Lossless follow-ups complete** — exact-tip CI run `29982347978` reproduces 521 passed with one opt-in skip on PostgreSQL 18 and 521/1 on PostgreSQL 16, both Python unit/import lanes, choreographed races, Stage-3 security/resource parity and both installed-artifact matrices. Local evidence additionally repeats 521/1 with authenticated Redis, Ruff/format and wheel/sdist × core/HTTP/OutLabs isolation. The 0001→0008 chain, exact 47-function catalog and public `Followup` type are permanent artifact oracles. No QDarte package pin, database, queue, worker, provider or production state changed; FR-02B is next.
  - [x] **FR-02B · Dependencies and workflows** — freeze and ship immutable migration 0009 plus producer-safe workflow identity, atomic edge admission, promotion/cascade/finalization, bounded reads and concurrency evidence.
    - [x] **FR-02B-SPEC · Sealed workflow contract frozen docs-first** — owner-approved ADR-026 resolves S5-QD-FR-CQ-03 through a producer-safe seal linearization point. Protocol 1.0.10 and Manifest target 0.2.1 freeze create/seal/cancel identities, immutable declared queues, same-step canonical-intent replay, same-workflow existing-parent edges, sealed-only monotonic finalization, bounded cancellation/cascade/straggler passes, member-redrive refusal, all-declared-queue authorization, exact metadata and migration `0009_workflows.sql`. No SQL, migration, Python source, package, QDarte database, queue, worker or production state changed; bridge support is next.
    - [x] **FR-02B-BRIDGE · 0.2.1 runtime floor prepared** — ADR-020's closed runtime set now accepts `{0.1.2,0.1.3,0.1.4,0.1.5,0.2.0,0.2.1}` while the admission-enabled runtime correctly preserves its active surface on 0.2.1. The bridge adds no workflow command, model, route or transport method; exact capability metadata remains the later activation gate. A preserved pre-0.2.1 set rejects 0.2.1 with the typed version error. No migration, SQL catalog, machine manifest, QDarte database, queue, worker or production state changed; immutable migration 0009 is next.
    - [x] **FR-02B-SQL · Immutable workflow/dependency kernel and direct transport complete** — migration `0009_workflows.sql` activates exact SQL contract 0.2.1 with producer-safe create/seal, operator cancellation intent, workflow-safe enqueue identity, atomic ordered parent validation, direct-edge promotion/cascade, starvation-free bounded straggler/finalizer passes, cancellation-aware claiming and member-redrive refusal. The machine verifier and independent catalog/privilege matrices assert 55 exact functions, appended composites/table shapes, constraints, indexes, metadata and raw-relation walls; dormant-state refusal is atomic. Choreographed tuple-lock races cover enqueue-versus-seal, enqueue-versus-parent-terminal, cancellation intent versus claim/settlement, plus replay, idempotency-domain collision, fan-out/fan-in/diamond/sibling and deep-cascade convergence. Fresh/full 0001→0009 chains pass at 538/538 with one opt-in skip on PostgreSQL 18.3 and exact 16.14; the million-row claim/direct-edge/finalizer gate is 1/1 on each, DB-free is 318/318, Ruff/format and wheel/sdist isolation are green. No workflow HTTP route, generated HTTP client, fake, QDarte package/database, provider or production state changed; FR-02B-SURFACE is next.
    - [x] **FR-02B-SURFACE · Generated workflow surface and parity complete** — Protocol 1.0.10's three workflow routes now come solely from the hand-audited HTTP catalog and remain absent unless a runtime explicitly enables exact 0.2.1 plus `dependencies_workflows`; cancellation additionally requires the separate operator transport. Create/member/seal/cancel derive actors from authentication, expose only the strict two-field workflow projection, and authorize the path then every authoritative declared queue before dependency access. Both HTTP clients, the high-level `TaskQ` facade and the native testing fake implement the same workflow producer contracts; workflow step keys safely drive response-loss retry without requiring a competing idempotency domain. Live vectors prove byte-identical create/member/seal replay, raw SQL/HTTP/fake state parity, dependency promotion, exact OpenAPI shapes, all-queue denial with zero writes, authenticate/path-authorize-before-decode, sync/async actor non-serialization, runtime/route absence below the capability gate and fake cancellation convergence. Local evidence is 548/548 plus one opt-in skip on PG18.3 and exact PG16.14, 323 DB-free, 2/2 plan gates on both majors, Ruff/format clean and a full Python 3.12/3.13 wheel/sdist × core/HTTP/OutLabs smoke. FR-02B-AUDIT owns publication and the final exact-tip repeat; no QDarte or production state changed.
    - [x] **FR-02B-AUDIT · Workflow completion evidence** — the independent source/Tier-0 pass confirms route identities, 202/200 cancellation outcomes, strict request/projection shapes, capability/contract gates, authentication and queue-authorization ordering, actor non-serialization, separate operator authority and no generic client escape around disabled routes. It found and closed two fake-only parity defects before acceptance: dependency sets are now order-canonical and an outside-declaration queue uses the contracted validation class; reversed fan-in, outside-queue, cancellation denial and cancel-bound vectors pin the corrections. Exact-tip CI run `29988702521` is green across SQL contract PostgreSQL 16/18, races/resources, Stage-3 security parity, Python 3.12/3.13 unit/import isolation and both complete installed-artifact matrices. Local evidence independently records 548/548 plus one opt-in skip on PG18.3 and exact PG16.14, 2/2 million-row plans on both, 323 DB-free, Ruff/format and all 12 Python 3.12/3.13 wheel/sdist × core/HTTP/OutLabs combinations. No Contract questions, QDarte state or production state changed; FR-02C-SPEC alone may open.
  - [x] **FR-02C · Schedules** — freeze and ship immutable migration 0010 plus operator definitions, housekeeper claims/fires/errors, database-clock catch-up, seeded janitor takeover and race/plan evidence.
    - [x] **FR-02C-SPEC · Native schedule contract frozen docs-first** — owner-approved ADR-027, Protocol 1.0.11 and Function Manifest target 0.2.2 freeze compile-first definitions, a closed five-field cron/interval evaluator fed only database instants, explicit DST and catch-up semantics, operator GET/PUT/retire with authoritative queue authorization, fenced direct-SQL housekeeper claim/fire/error, response-loss replay, permanent occurrence identity and the sole caller-immutable `maintenance:janitor` exception. Retired definitions are permanent: mutation returns typed `schedule_retired` with current version while exact DELETE replay is `already_retired`. Migration 0010 must seed the daily UTC janitor and disable the hardwired tick branch atomically. No SQL, migration, Python source, package, QDarte database, queue, worker or production state changed; bridge support is next.
    - [x] **FR-02C-BRIDGE · 0.2.2 runtime floor prepared** — ADR-020's closed runtime set now accepts `{0.1.2,0.1.3,0.1.4,0.1.5,0.2.0,0.2.1,0.2.2}`; admission and workflow surfaces remain valid on the additive revision only with their exact capabilities. A preserved 0.2.1 bridge rejects 0.2.2 with the typed version error. The bridge adds no schedule option, transport, route, model or loop and a regression asserts that absence. No migration, SQL catalog, QDarte database, queue, worker or production state changed; immutable migration 0010 is next.
    - [x] **FR-02C-SQL · Immutable schedule kernel** — migration `0010_schedules.sql` activates SQL contract 0.2.2 with private schedule/occurrence ledgers, strict operator definitions and permanent retirement, SKIP-LOCKED housekeeper claims, database-fenced atomic fire/error replay, compile-first catch-up, permanent occurrence identity, the seeded finite janitor target and removal of tick's hardwired janitor trigger. Typed direct transports cover all 47 public identities in the exact 62-function catalog without giving housekeeper functions HTTP identities. Exact catalog/grant/composite/table/index/metadata equality, create/replay/CAS/retire, interval/cron validation, all catch-up policies, error and response-loss replay, definition fencing, two-housekeeper races, janitor one-only takeover, fresh/full 0001→0010 chains and the bounded 100k-schedule due-index plan pass identically: 560 passed with one opt-in skip and 2/2 plan gates on PostgreSQL 18.3 and exact 16.14; Ruff/format are clean. No schedule facade route, generated HTTP client, runtime loop, fake, QDarte database, package release or production state changed; FR-02C-SURFACE is next.
    - [x] **FR-02C-CALENDAR · Deterministic recurrence evaluator complete** — the package evaluator accepts only the closed numeric five-field cron grammar or bounded elapsed interval and consumes only claim-projected aware `next_fire_at`/database `as_of` instants. Compile-first and skip emit nothing; interval `fire_once` computes the latest due instant without replaying backlog; `fire_all` returns only the bounded oldest-first prefix. IANA-zone cron uses traditional day-of-month/day-of-week OR, skips spring gaps, chooses only the earlier fall fold and never consults a local wall clock. Ten focused vectors pin long-downtime bounds, grammar rejection, UTC interval arithmetic, both DST transitions and invalid/not-due claims. HTTP schedule work remains stopped at open CQ-05; this independent calendar component defines no route or authorization behavior.
    - [x] **FR-02C-SURFACE · Calendar/runtime/operator parity** — Protocol 1.0.12's construction-gated GET/PUT/DELETE schedule routes, async/sync clients and exact OpenAPI catalog now expose only ordinary queue-bound schedules with strong ETags and old-then-new queue authorization. ADR-028's reserved janitor identity fails uniformly as TQ422 before lookup, body/header decode or SQL on all three routes while direct SQL retains observability. `TaskqRuntime` requires exact SQL 0.2.2 plus the `schedules` capability, evaluates database-stamped claims, isolates calendar failures through fenced `schedule_error`, and is disabled by default. The native fake mirrors profile/CAS/retirement, claim/fire/replay/error semantics with an injectable aware clock. SQL/HTTP/fake lifecycle, raw-state, authorization, catalog and runtime vectors pass in the full PostgreSQL 18.3 suite: 577 passed with one opt-in skip; Ruff/format are clean. No migration, QDarte database, package release or production state changed; FR-02C-AUDIT is next.
    - [x] **FR-02C-AUDIT · Schedule completion evidence** — the identical fresh/full-chain suite passes 577/577 with one opt-in skip on PostgreSQL 18.3 and exact 16.14; the exact schedule contract/evaluator/facade/fake subset passes 25/25 under warnings-as-errors on each. Both million-row gates are 2/2, DB-free is 338/338, and Ruff/format are clean. The locally built a6 wheel and sdist each pass core/HTTP/OutLabs isolation on Python 3.12 and 3.13 (12/12); the installed-artifact oracle now executes create/read/retire through the public schedule fake while retaining exact 0001–0010 and 62-function checks. Source review confirms the seeded maintenance identity has no HTTP catalog/client escape, runtime schedule work requires exact 0.2.2 plus capability, SQL remains the only conformance oracle, and no migration or Tier-0/Tier-4 file changed after ADR-028. No QDarte package/database, release publication, production or old-ledger state changed; FR-02D is next.
  - [ ] **FR-02D · Finite operator projections** — independently contract, plan-test and activate only running/finished/workflow projections that meet their exact B9 and redaction gates; no timeline or generic reporting surface.
    - [x] **FR-02D-SPEC · Finite projections frozen docs-first** — ADR-029, Protocol 1.0.13 and Manifest target 0.2.3 freeze the existing running/finished queue pages plus one exact workflow profile/count/member page, all independently capability-gated. Exact workflow counts use an owner-private materialized counter and member pages use bounded UUID keysets; SQL/HTTP projections are identical. The closed QDarte inventory proves its former attempt/event timeline is a deletion target, so FR-02D explicitly rejects that surface and every generic reporting escape. Immutable 0011 adds backing while preserving capabilities; proof-backed metadata-only 0012 activates only winners. No SQL, migration, Python source, package, QDarte database, queue or production state changed; bridge support is next.
    - [x] **FR-02D-BRIDGE · 0.2.3 runtime floor** — ADR-020's closed runtime set now accepts `{0.1.2,0.1.3,0.1.4,0.1.5,0.2.0,0.2.1,0.2.2,0.2.3}` while admission, workflow and schedule surfaces remain available on the additive revision only with their existing exact capabilities. A preserved 0.2.2 bridge rejects 0.2.3 with the typed version error. The bridge adds no finite-projection option, route, model or transport method. No migration, SQL catalog, QDarte database, queue, worker or production state changed; immutable 0011 is next.
    - [x] **FR-02D-SQL · Immutable finite-projection backing** — migration `0011_finite_projections.sql` installs SQL contract 0.2.3 with three proof indexes, four exact redacted workflow composites, an observer-only bounded workflow page and owner-private materialized state counts while preserving the exact 0010 capability set. ADR-030's no-FK lifecycle and UPDATE-only job trigger retain cancellation lock order; the held-open settlement race, exact count transitions, backfill, missing-invariant atomic TQ500, unknown-before-capability ordering, fresh/full chain, exact 65-function/trigger/table/index/grant catalog and direct transport all pass. PostgreSQL 18.3 is 581/581 with one opt-in skip using an isolated authenticated Redis lane; Ruff/format are clean. No HTTP workflow-page route, metadata activation, package release, QDarte database or production state changed; independent B9 is next.
    - [x] **FR-02D-B9 · Independent per-projection plans** — the million-row gate independently proves all three frozen projection candidates on PostgreSQL 18.3 and exact 16.14. `running` is bounded to 101 rows on `taskq_jobs_running_page_idx`, `finished` on `taskq_jobs_finished_page_idx`, workflow members on `taskq_jobs_workflow_page_idx`, and exact workflow counts are a one-row `workflow_member_counts_pkey` lookup; none sorts or scans the jobs heap. Existing ready and every prior hot-path/schedule/graph gate remain green. Capability metadata is asserted byte-for-byte unchanged, so evidence alone activates nothing. All three winners may now be named docs-first for immutable 0012.
    - [x] **FR-02D-ACTIVATE · Metadata-only winner activation** — immutable 0012 activates exactly `read_model_list_running`, `read_model_list_finished` and `read_model_workflow` on top of the five existing capabilities, citing dual-major B9 commit `988309c` and changing no contract version. The first activated workflow-page vector exposed 0011's committed composite-assignment defect; docs-first immutable 0013 replaces only that function body, preserves its identity/grant/capability state, and explicitly raises ADR-030's TQ500 counter invariant. The 0011-inactive → 0012-active → 0013-repaired transition, active/missing workflow pages, exact migration/catalog/capability parity and `verify()` pass on PostgreSQL 18.3. No HTTP route, client method, QDarte package/database or production state changed; the generated surface is next.
    - [x] **FR-02D-SURFACE · Generated clients/facade/parity** — Protocol metadata, both official HTTP clients and the lifespan-free facade now expose the exact workflow page behind an explicit default-off `workflow_read_enabled` runtime gate. Startup requires SQL 0.2.3 plus both workflow capabilities before mounting the route. The facade obtains the safe workflow authorization projection, authorizes `read` on every declared queue, and only then validates a workflow-bound opaque UUID cursor; denial and absence are the same hidden 404. Exact profile/count/member redaction, two-page cursor binding, OpenAPI/catalog identity, SQL/HTTP/raw-row parity, and active running/finished view parity are executable. PostgreSQL 18.3 passes 584/584 with one opt-in skip; 50 focused catalog/runtime/facade/parity tests and Ruff/format are clean. No timeline, generic reporting route, QDarte package/database or production state changed; dual-major and artifact completion evidence is next.
    - [x] **FR-02D-AUDIT · Dual-major and artifact evidence** — the identical fresh/full-chain suite passes 584/584 with one opt-in skip on PostgreSQL 18.3 and exact 16.14. The exact finite kernel/runtime/catalog/facade/parity subset is 53/53 under warnings-as-errors; the million-row gate is 2/2 on each major with all three finite winners bounded to their proof indexes; DB-free is 340/340; Ruff/format and package build are clean. The locally built a6 wheel and sdist pass all 12 Python 3.12/3.13 × core/HTTP/OutLabs isolation corners, with executable migration 0001–0013, 65-function and public workflow-type assertions. No Tier-0/ADR/migration changed after activation, no rejected timeline or generic report surface exists, and no QDarte database, package repin or production state changed. FR-02-AUDIT is next.
  - [x] **FR-02-AUDIT · Native orchestration completion** — owner-authorized internal acceptance (explicitly non-independent while the external reviewer is unavailable) re-derived the exact eight-capability final state, complete 0001–0013 chain and absence of any timeline/generic-report command. The identical 584/1 full suite passes on PostgreSQL 18.3 and exact 16.14; the combined follow-up/workflow/schedule/projection contract, fake, evaluator, facade and SQL/HTTP/raw parity set is 63/63 under warnings-as-errors. Both million-row gates are 2/2, DB-free is 340/340, Ruff/format/build are clean, and all 12 installed-artifact corners pass with the 65-function catalog. FR-03 may consume native 0.2 locally; no package release, QDarte repin/database, old worker, provider or production state changed.

- [ ] **S5-QD-FR-03 · Native QDarte registry and domain-effect layer** — relocate payload/result contracts out of the old queue API, register every active task once, generalize the ADR-022 reporter into a closed idempotent QDarte domain-effect protocol, and prove every handler under `taskq.testing` plus disposable real SQL/HTTP without starting the old worker.
  - [x] **FR-03-RELEASE · Immutable native-orchestration alpha** — annotated tag and GitHub prerelease `v0.1.0a7` resolve to source `1be3b65`, after exact-tip CI run `30015623745` completed green. Independently downloaded release artifacts match the prepublication bytes: wheel SHA-256 `6ec807ec29b30047a38e11916ba2ff3f29149e208cd48c490943f12a223561bf` and sdist SHA-256 `8ebd8592376a9dab18636b7d1b0721b30d8a4059ad03bc26c7d918f3a8cd3b60`. The installed-artifact matrix proves Python 3.12/3.13 × core/HTTP/OutLabs isolation, migrations 0001–0013, the exact 65-function catalog, Protocol 1.0.13 surface and public finite-workflow types. No QDarte package pin, database, queue, old worker, provider or production state changed; FR-03 source work may begin.
  - [x] **FR-03A · Immutable floor and canonical contract relocation** — API commit `2c73c7c` and workers commit `958ff7a` exact-pin the published a7 artifact. Runtime commit `035edd8` moves the actual model definitions from the retiring `core.worker_api.models` module into `core.tasking.contracts` without a compatibility module; API `3f12e5a` and workers `1e99002` consume that canonical path directly. The regenerated machine oracle remains exactly 266 queue-sensitive files, 23 declarations, 21 executable handlers, 30 relations and 130 routes; ignored local scratch state can no longer create false inventory drift. Runtime is 1151/1151 with clean Ruff/MyPy, workers 629/629 with clean Ruff, and 34 focused API boundary/inventory tests pass. No old worker, service, database, queue, provider or production state changed.
  - [x] **FR-03B · Exact native definitions and strict outputs** — QDarte runtime `3cedb6e` defines 21 distinct frozen/extra-forbid inputs, 21 distinct bounded outputs, and one immutable catalog with exact queue, priority, lease, closed retry, follow-up, effect-family and resource metadata. The canonical metadata serialization is pinned by SHA-256 `931cd2c0687dc517b292ff48929497cc5f87f0afc9ab4a78d5b37a63ac0bd8fa`. Workers `47d91d3` builds the handler-free a7 `TaskRegistry`, validates the complete follow-up graph, and proves machine handler inventory = native definitions = taskq registry = current handler map, exactly 21. Both non-executable declarations, historical task literals and aliases are absent. The replacement oracle is 272 files/23 declarations/21 handlers/30 relations/130 routes. Runtime passes 1162/1162 with clean Ruff and 195-file MyPy; workers pass 632/632 with clean Ruff and 54-file MyPy. No handler was adapted, no old job/client constructed, and no worker, database, queue, provider, service or production state changed.
    - [x] **FR-03B-BOUNDARY · Whole-model byte bounds made executable** — the first follow-up-handler design pass caught that frozen/extra-forbid inherited models did not by themselves bound aggregate JSON size. Runtime `874b3e1` adds one canonical 64KB ceiling to every native input and output plus `changed_count <= processed_count`; regressions use individually valid fields to exceed the aggregate boundary and fail typed validation. This corrects implementation to the already-frozen “bounded input/output” requirement without changing the task catalog digest or any taskq contract. Runtime passes 1163/1163 with clean Ruff and 196-file MyPy; workers remain 635/635 with clean Ruff and 55-file MyPy.
    - [x] **FR-03B-EFFECT-INVENTORY · Closed operation ownership oracle** — QDarte runtime commit `6f18c71` adds the checked-in source-backed operation manifest and an independent verifier over all 21 executable handlers. Exact task and queue sets derive from the replacement manifest; every pure/read/follow-up/domain/filesystem/deployment/session operation cites a live source marker; every mutation names an idempotent owner and stable `job/family/entity/operation` identity; operation-family equality and settlement-domain-mutation prohibition fail closed. The replacement oracle is now 268 files/23 declarations/21 handlers/30 relations/130 routes. Runtime passes 1155/1155, Ruff is clean, changed files are format-clean and MyPy passes 194 source files. Repository-wide format and tests-MyPy retain unrelated baseline drift and were not rewritten. No worker, database, queue, provider, service or production state changed; strict native definitions remain open.
  - [ ] **FR-03C · Native handler bindings** — refactor pure/read/effect cohorts to the taskq handler signature without constructing an old job/attempt/client, then prove every binding and follow-up graph through `taskq.testing`.
    - [x] **FR-03C-PURE · Pure cluster-research binding** — runtime `e0acb75` owns the single deterministic full projection and bounded native digest projection. Workers `a032e8d` binds `native_cluster_research(JobContext, NativeClusterResearchInput) -> NativeClusterResearchOutput`; it constructs no old job, attempt or client and invokes no lifecycle method. Production `taskq.testing.work()` proves claim, handler normalization and terminal completion with no follow-up/effect; the result digest covers the complete inherited projection and its ordered candidate/proposal identities are pinned. The temporary incumbent handler calls the same pure function while it awaits deletion. The replacement oracle is 274 files/23 declarations/21 handlers/30 relations/130 routes. Runtime passes 1162/1162 with clean Ruff and 196-file MyPy; workers pass 635/635 with clean Ruff and 55-file MyPy. No worker process, database, queue, provider, service or production state changed.
    - [x] **FR-03C-FOLLOWUPS · Queue-native content coordinator** — CQ-09/10 were resolved docs-first before implementation. Runtime `55668a4` makes canonical QDarte scope explicit on all 21 native inputs and replaces the insufficient content selector payload with at most 20 discriminated, fully planned, scope-equal photo/editorial children. Workers `29cb1e9` binds the coordinator without an old job/client/planner and returns one taskq `Complete` carrying exact child policies; the real testing fake proves parent settlement and all child inserts are atomic, while an invalid second child produces zero enqueues and an empty plan is typed `no_change`. The replacement oracle is 275 files/23 declarations/21 handlers/30 relations/130 routes. Runtime passes 1165/1165 with clean Ruff and 196-file MyPy; workers pass 639/639 with clean Ruff and 55-file MyPy. No worker process, database, queue, provider, service or production state changed.
    - [x] **FR-03C-CONTACT · Native trusted-effect handler** — workers `7261873` binds strict `contact_verify_scope` directly to `JobContext.report_effect()`: inspect precedes each provider call, committed inspection skips the provider, apply accepts only the canonical contact result and output contains bounded effect receipts. A production `WorkerSupervisor` vector proves the trusted reporter alone receives active attempt identity while the handler sees none; exact queue/type alias refusal and no old job/client/contact-package imports are pinned. Workers pass 644/644 with clean Ruff and 55-file MyPy. Runtime `fbc4458` records the 281-file exact inventory. No HTTP reporter transport, old worker, provider, database or production state changed.
    - [x] **FR-03C-PROVIDER-CONTROL · Queue-independent metered provider boundary** — taskq `99f3b5a`/`892d056`, runtime `b5a56ab`/`d30527c`, API `7dc69fd`/`97be822` and workers `4c1e817`/`2229cfe` implement ADR-031 plus CQ-13's generation rollover. The private reserve/settle member authenticates and authorizes the authoritative queue before decode, binds reservations to reporter-owned attempts, makes same-attempt replay exact, returns retryable `reservation_pending` across live attempts, records database-expired reservations permanently as `expired_unsettled`, and opens a later numbered generation without exposing old queue models or provider proxying. Reserve, failover, settlement, concurrency and unknown-cost vectors are green; no taskq Protocol/Manifest/SQL change was required.
    - [x] **FR-03C-PHOTO · Native photo-verification binding and hard-kill conservation complete** — CQ-11 is resolved across taskq `f9846a2`/`137a81b`, runtime `c21960e`/`e53b12b`/`1e817a9`, API `d391e1a`/`7711df5`, and workers `db54852`/`800445c`/`b6a021e`. Inputs carry exact per-entity retry/review plans with structural exhaustion and reject duplicate entity identities; the handler uses shared ADR-031 provider control, commits one mutually exclusive photo verdict, returns only preplanned children and reuses one job-scoped immutable artifact. The guarded local rehearsal exposed and fixed the reporter's missing exact media/photo registration, then killed worker 1 with `SIGKILL` after provider return and artifact publication. The same job reclaimed as `expired/lease_expired`; attempt 2 recorded generation 1 as `expired_unsettled` without egress; attempt 3 used generation 2 and succeeded. Raw oracles prove 2 external calls = 1 known provider event + 1 unknown-cost generation, one `verified` effect, `photo_verified` domain truth, one artifact, an exact empty child set, zero releases and zero old job/event rows. Runtime passes 1172, workers 684, taskq remains 584/1 and the API baseline remains 1764 pass/10 unrelated failures with clean static gates. No persistent database, external provider, old worker or production state changed.
    - [x] **FR-03C-DISCOVERY-IMPORT-SPEC · Native discovery import boundary frozen** — source re-derivation replaces the old worker-owned direct database runtime with one job-bound immutable manifest/shard plan and the closed `discovery_import/apply` reporter effect. The API alone resolves both relative artifacts beneath its import root, verifies size/digest/manifest selection, and commits import plus optional normalization, bounded receipts and effect truth in one transaction; PostgreSQL time owns effect application while existing discovery-record timestamps remain queue-independent domain semantics. Inspect/replay never rereads artifacts, no child exists, and old job/attempt/client/session/lifecycle imports are forbidden. No Tier-0 contract, SQL, migration, QDarte source, database, provider or production state changed.
    - [x] **FR-03C-DISCOVERY-IMPORT · Native discovery import family complete** — runtime `491c0b9`/`10fdf3f` freezes the strict relative manifest/shard plan and closed response; API `f24642c`/`b0e96cb` authorizes the stored running task, verifies root containment, bytes, digests and exact manifest selection, and applies import plus optional normalization through one authoritative session/effect transaction; workers `e150e1a` binds only `discovery.import_batch` and performs no filesystem or database mutation. A real disposable-DB vector imports the already-materialized shard exactly once and pins the no-double-sharding correction; effect replay returns the same counts, digest and artifact truth without a second apply. Runtime inventory `9c750ee` removes the old worker import markers. Runtime passes 1184, workers 736 and API 1812 pass/11 unchanged unrelated failures; focused API evidence is 38/38 with static gates clean. No old job/attempt/client/session/lifecycle method, external provider, persistent database or production state changed.
  - [ ] **FR-03D · General idempotent domain effects** — replace the 12 old result-route families with one closed inspect/apply reporter union, authoritative plan validation and stable job/family/entity/operation idempotency.
    - [x] **FR-03D-KERNEL · Closed transaction and replay kernel** — API `ffb9b57` adds existing-install migration 0077 plus a route-free internal kernel for the exact 17 source-derived domain-effect families. Stable `(taskq job id, family, entity, operation)` identity, canonical request hashing, mismatch refusal, and domain callback plus bounded receipt share one transaction; a failed mutation rolls the reservation back. Fresh-session response-loss replay and a real two-session concurrent same-intent race prove the callback executes once. Runtime `a87804f` extends the closed scanner/oracle to all 278 queue-sensitive files; `88b3fd6` independently derives the domain families from the effect manifest and equality-checks both the API service set and migration constraint. Ten focused API migration/kernel/inventory tests and the runtime inventory/effect set pass with clean Ruff/format and focused MyPy. No generic effect route, family adapter, old worker, provider, production database or production state changed.
    - [x] **FR-03D-CONTACT-AUTHORITY · First authoritative family adapter** — runtime `d70d8b7` freezes the bounded discriminated contact inspect/apply request and receipt response without attempt/worker identity or request echoes. API `10d0533` separates queue-independent contact domain mutation from the old ledger, then authorizes the native adapter against the current taskq attempt, exact verification queue/type and stored strict entity before ledger access. Place, phone, source and provider-plan metadata come only from the stored payload; PostgreSQL supplies mutation time. Unplanned/wrong/stale tasks fail before effect access. Runtime passes 1167/1167 with clean Ruff and 196-file MyPy; 19 focused API authority/domain/kernel/migration/inventory tests pass with clean Ruff/format and focused MyPy. The replacement oracle is 280 files/23 declarations/21 handlers/30 relations/130 routes. No route, worker handler, generic reporter, provider, production database or production state changed.
    - [x] **FR-03D-REPORTER-SPEC · Private native reporter wire frozen** — the Tier-3 specification fixes one non-Protocol internal POST, the shared `QueueAuthorizer`, authenticate and queue-scoped `run` authorization before an 8KB streamed body decode, reporter-owned attempt identity, fixed non-echoing validation/conflict errors and the contact-only initial union. Future families extend the closed union docs-first rather than adding paths or a generic method selector. No source, SQL, migration, package, service, database, credential or production state changed.
    - [x] **FR-03D-REPORTER · Private reporter transport implemented** — runtime `201fa05` adds the frozen reporter-owned attempt envelope and closed contact union; API `b2c4a2e` adds the bounded private route with shared authenticate-then-queue-run-authorize ordering before streamed body decode, fixed non-echoing failures and transaction commit; workers `552fc6a` adds a secret-safe HTTP reporter that binds the active taskq attempt and refuses wrong queue/type or non-contact effects before transport. Runtime passes 1167/1167 with clean Ruff/196-file MyPy; workers pass 650/650 with clean Ruff/56-file MyPy; 15 focused API authority/body/receipt vectors pass with clean Ruff/MyPy; the aggregate inventory equality-checks 285 files. All three commits are pushed on their non-deploying replacement branches. No generic effect route, provider, production database, credential or production state changed.
    - [x] **FR-03D-CONTACT-E2E · Native contact effect conserved through real SQL/HTTP** — API `6493b07`/`798743a` compose one native facade with an exact five-queue service-token authorizer and global-read metadata bootstrap; workers `f99efdf` compose the official HTTP client, native registry, reporter and `WorkerService`. The integration drill exposed and fixed retryable transport-loss normalization (`55f0712`), native lifespan composition and injected-client origin handling (`add27e3`/`5cc68ce`). Runtime `7e31622` adds a self-cleaning PostGIS rehearsal that applies both real migration chains, provisions separate non-superuser facade/domain logins, runs the authoritative contact job, discards the first successful apply response after commit and proves exactly one provider call, native ledger row, contact mutation, taskq attempt and job, with zero failures/releases and zero old job/event/contact-effect rows. Workers pass 656/656 with clean Ruff/57-file MyPy; runtime passes 1167/1167 with clean Ruff/196-file MyPy; 18 focused API vectors pass; the 294-file inventory is exact. All commits are pushed on non-deploying replacement branches; no production state changed.
    - [x] **FR-03D-WEBSITE-SPEC · Bounded website-verification effect frozen** — the Tier-3 reporter union now includes `website_verification` on the existing private route and verification queue. Stored native input remains authoritative for contact/content identity and submitted website; the handler may report only a bounded network/extraction/judgment result with no arbitrary evidence, body, prompt, credential, provider error text, caller timestamp or duplicated row identity. The exact verdict decision table, database-clock mutation, missing-row rollback, inspect-before-provider and ambiguous-response replay obligations are frozen before source work. No Tier-0/1 contract, SQL, migration, package, QDarte source, database, provider or production state changed.
    - [x] **FR-03D-WEBSITE · Native website-verification family complete** — runtime `3926ec8` replaces the contact-only envelope names with one closed two-family request union and adds strict bounded network/extraction/judgment models whose validator enforces the frozen verdict table. API `476ef36` dispatches from current attempt plus stored strict payload, applies only the authoritative website contact point at database time, rejects missing rows transactionally, and exposes no old job state; `0193665` keeps the source oracle exact. Workers `9b2457f` replaces the contact-only process composition with one exact two-type verification registry/`WorkerService`, a bounded native fetch/extract/judge provider and a reporter that rejects task/family mismatch; `b50e30f` records the closed surface. Runtime `656194a` renames and extends the disposable rehearsal: both contact and website lose their first committed apply response yet each provider runs once, both jobs succeed in one attempt, exactly two native effects and both domain mutations exist, and old jobs/events/contact effects remain zero. Runtime 1168/1168, workers 661/661, 17 focused API vectors, clean Ruff/MyPy and the exact 295-file inventory pass. No old worker, external provider, persistent database or production state changed.
    - [x] **FR-03D-CLASSIFICATION-SPEC · Native TripAdvisor classification effect frozen** — the Tier-3 reporter union now spans `qdarte_discovery` through a two-phase authenticate → authoritative path-job queue lookup → queue-run authorize sequence before body decode. The stored strict target is the complete bounded provider plan; inspect precedes deterministic or provider work; Google-type, provider, existing and unavailable outcomes are closed; apply revalidates the live source/place relationship and owns classification plus alignment in the effect transaction; provider failures remain retryable without an effect; output is bounded counters/receipts with no follow-up. No Tier-0/1 contract, SQL, migration, package, QDarte source, database, provider or production state changed.
    - [x] **FR-03D-CLASSIFICATION-REPLAY · Reclaim-stable output corrected docs-first** — implementation design proved that a generic replay receipt exposes the committed result digest but not the provider failover winner. The classification output therefore retains exact outcome/replay counters, receipts and warnings but omits provider/model/endpoint labels instead of guessing them after reclaim or widening every family's receipt. Provider identity remains in the hashed effect intent and domain metadata. No source, contract, SQL, migration, database, provider or production state changed.
    - [x] **FR-03D-CLASSIFICATION · Native classification family and hard-kill conservation complete** — API `7dc69fd`/`08aff8b` adds the source-backed classification adapter, durable provider-control transactions and loop-isolated tests; workers `4c1e817`/`5dce354` bind the exact queue/type and compose the classification-only `WorkerService`. CQ-13 remediations `97be822`/`2229cfe` prevent cross-attempt provider authority while a live reservation exists. Workers `8ec7b37` and API `1f6ffbe` add a guarded local process-kill harness and immutable evidence: a real first worker exits `-9` after provider return, the same taskq job reclaims through `lease_expired`, attempt 2 records generation 1 as `expired_unsettled` without egress, attempt 3 spends generation 2 and succeeds, and the raw oracle proves 2 external calls = 1 provider event + 1 unknown-cost generation, one native effect, zero releases and zero old job/event rows. Runtime 1170/1170, workers 676/676, taskq 584/584 plus one opt-in skip, and the API's recorded 1761-pass/10-failure unrelated baseline all reproduce with clean static gates. No persistent database, external provider, old worker or production state changed.
    - [x] **FR-03D-EDITORIAL-SPEC · Bounded editorial-enrichment effect frozen** — CQ-14 resolves the mismatch between a content-carrying editorial result and the private reporter's former 8KB ceiling without restoring an old result route or adding artifact-location coupling. The existing private endpoint remains authenticate → authoritative queue-run authorize → bounded decode, but its aggregate ceiling becomes the native model ceiling of 64KB. The closed `editorial_enrichment` member carries one strict draft/status result, uses stored input for row identity, applies at database time, and returns only a bounded receipt. Exact per-entity optional review branches are producer-planned, scope-equal, capped at 20 and selected only after the effect commits; response-loss replay returns the identical receipt and child. Metered model calls use ADR-031 provider control. No Tier-0/1, SQL, migration, package, QDarte source, database, provider or production state changed.
    - [x] **FR-03D-EDITORIAL · Native editorial-enrichment family complete** — runtime `ebf31d1` adds the strict draft/status effect, exact per-entity review plans and the editorial ADR-031 provider member under the 64KB aggregate boundary; the machine effect inventory now cites only the native handler and authoritative domain owner. API `eb0c8e7` authorizes the current content task from stored SQL authority, raises the private streamed ceiling only after authentication/queue-run authorization, applies bounded locales and review state at database time, and records the effect receipt atomically. Workers `d0dd596` inspects before egress, reserves/settles provider usage, applies once, and returns only the scope-equal preplanned review child; replay skips the provider and returns byte-equivalent result/child intent. Runtime passes 1173 with one unrelated dependency warning, workers 687, and the API baseline is 1766 pass/10 unchanged unrelated failures; focused warning-as-error, Ruff and configured MyPy gates are clean. No old job/attempt/client enters the native graph, no service/provider/database/queue/worker started, and production remains untouched.
    - [x] **FR-03D-LISTING-SPEC · Native listing-research artifact and branch contract frozen** — CQ-15 resolves the completion-time artifact identity and branch-selection gap without putting application blobs in taskq or restoring an old result route. Producers mint one stable bundle UUID and fully materialize an optional scope-equal synthesis child before parent enqueue. The closed `listing_research` effect carries only a bounded writer-firewalled bundle or bounded underfill result; the API persists it under that identity and atomically returns the stable receipt plus `synthesis_ready | curated_hold | blocked_exhausted` from authoritative current state. The worker only selects the preplanned child after that disposition commits; inspect and response-loss replay return the identical disposition, receipt and child. No Tier-0/1, SQL, migration, package, QDarte source, database, provider or production state changed.
    - [x] **FR-03D-LISTING · Native listing-research family complete** — taskq `1cb0ec1`/`a6bee9a` freezes producer-minted stable bundle identity, synthesis-ready exact-place evidence and an authoritative typed disposition before source. Runtime `4c75804`/`58f15df` adds strict branch/effect/response models, one shared canonical-plus-readiness firewall and exact native effect inventory. API `ef015aa` validates current SQL task authority and the planned bundle identity, persists the domain artifact and current pipeline decision atomically with the stable effect receipt, preserves the family-specific outcome over HTTP and removes no old data. Workers `173557a` inspects before research, applies once, selects the synthesis child only from `synthesis_ready`, preserves typed holds, and makes response-loss replay skip research and return the identical child. Runtime 1174, workers 692 and API 1768 pass/10 unchanged unrelated failures with clean Ruff and configured MyPy. No old job/client/completion hook enters the native graph; no service, provider, persistent database or production state changed.
    - [x] **FR-03D-SYNTHESIS-SPEC · Native content-synthesis preparation, effect and branch contract frozen** — CQ-16 resolves the artifact-read and completion-hook boundary without copying application bundles into taskq or restoring an old client. Each entity has one exact stable bundle id and producer-selected `review | repair_review` branch. The closed family prepares only that authorized synthesis-ready artifact, then commits exactly one `synthesized | bundle_blocked | geo_blocked | writer_blocked` effect; only synthesized selects the preplanned child. The API owns database-time draft/blocker mutation and usage refresh, metered model/repair calls use ADR-031's new `content_synthesis` lane, and all responses remain bounded and replay-stable. No Tier-0/1, SQL, migration, package, QDarte source, database, provider or production state changed.
    - [x] **FR-03D-SYNTHESIS · Native content-synthesis family complete** — runtime `c75be6b` adds exact bundle/review plans, the closed prepare/apply union, bounded terminal response and the ADR-031 `content_synthesis/synthesize` control member while narrowing the native child graph to review only. API `2e0504a` authorizes the current content task and exact planned bundle before returning a synthesis-ready artifact, uses database time, mutually excludes all four outcomes, applies draft or blocker truth plus artifact usage in the effect transaction, and validates provider reservation identity from the stored plan. Workers `e9d2685` prepare before egress, reserve/settle provider usage, commit one terminal outcome, select only the preplanned review child after `synthesized`, and make replay skip the provider. Runtime passes 1175, workers 697, taskq passes 584/1, and API passes 1770 with the same 10 unrelated baseline failures; Ruff and configured MyPy are clean throughout. No old queue client, attempt model or result route enters the native path; no service, provider, persistent database or production state changed.
    - [x] **FR-03D-TRANSLATION-SPEC · Native translation revision, effect and review contract frozen** — CQ-17 replaces the old provider-admission client and completion-time handoff with a current-source revision prepare, the shared ADR-031 `translation/translate` lane, a closed `translated | source_stale` effect and at most one producer-planned `review | repair_review` child. Only translated mutates the target locale and selects the child; stale source performs no egress, mutation or follow-up. The CQ-11 completion-hook sweep is now recorded for every remaining unbound family. No Tier-0 contract, SQL, migration, public route, QDarte source, database, provider or production state changed.
    - [x] **FR-03D-TRANSLATION · Native translation family complete** — runtime `0a019fb` replaces the filesystem/read-result shape with a strict source-revision plan, closed prepare/apply response and exact optional review branch while adding the private ADR-031 `translation/translate` member. API `a057ceb` authorizes the current content task and stored revision before egress, records stale source as durable terminal truth, reuses the queue-independent locale mutation kernel at database time and refuses unplanned provider/model pairs. Workers `a962fe9` prepares before provider use, reserves/settles metered usage, commits once, selects only the producer-planned review child after `translated`, and makes replay or `source_stale` perform zero provider calls. Runtime passes 1176, workers 702 and API 1773 pass/10 unchanged unrelated failures with clean Ruff and configured MyPy. No old queue client, attempt model, result route, result file or completion-time planner enters the native path; no service, provider, persistent database or production state changed.
    - [x] **FR-03D-REVIEW-SPEC · Native review packet, effect and branch matrix frozen** — CQ-18 replaces the old review-entity read, result route and completion-time publish/repair/stale-translation planning with one producer-materialized revision-bound packet, the shared ADR-031 `review/review` member, a closed authoritative review effect and exact preplanned publish/copy/photo/translation alternatives. Database-time review/media/pipeline/repair mutation and branch selection share the effect transaction; only its typed outcome may select one matching child. Replay never reruns the provider or changes branch, incomplete matrices fail before egress, and stale input performs no review mutation or follow-up. No Tier-0 contract, SQL, migration, public route, QDarte source, database, provider or production state changed.
    - [x] **FR-03D-REVIEW · Native review family complete** — runtime `b654f86` adds the bounded producer packet, exact validated branch plans, closed inspect/apply response, native output and ADR-031 `review/review` member while moving the machine inventory to the native handler and authoritative domain owner. API `af60d17` authenticates the live task and stored packet, rejects revision drift before egress, reuses the queue-independent review/media mutation, preserves the old repair classification through a shared domain policy, records database-time effect truth, refuses unplanned provider/model pairs and returns only the closed publish/repair/translation/human/block/reject/stale outcome. Workers `19a9dae` enforces exact task-to-provider-lane authority, inspects before provider use, reserves/settles metered usage, commits one effect, selects only its matching preplanned child and makes replay or `input_stale` perform zero provider calls. Runtime passes 1177, workers 706 and API 1776 pass/10 unchanged unrelated failures with clean Ruff and configured MyPy; 75 focused API/domain vectors pass. No old queue client, attempt model, result route, result file or completion-time planner enters the native path; no service, provider, persistent database or production state changed.
    - [x] **FR-03D-BUZZ · Native buzz-discovery family complete** — CQ-19 corrects the completion-hook inventory before source. Runtime `23f2a69` adds the strict scope-equal rescue branch, closed cluster/geo result and inspect/apply/artifact response while moving the machine effect inventory to the native worker and authoritative domain owner. API `a429c21` authorizes the live discovery task and stored scope before decode, persists exactly one database-timed `buzz_report` or `region_buzz` artifact without constructing an old queue object, and returns replay-stable effect/artifact receipts; changed canonical content fails closed. Workers `e73f13e` inspects before discovery, applies once, selects only the producer-planned rescue child, and makes committed replay perform zero provider calls. Runtime passes 1178, workers 710 and API 1779 pass/10 unchanged unrelated failures with clean Ruff, changed-file formatting and configured MyPy. No old result route, job/event model, completion hook or planner enters the native graph; no service, provider, persistent database or production state changed.
    - [x] **FR-03D-RESCUE-SPEC · Finite native region-rescue workflow and controls frozen** — source tracing resolves CQ-20/21 before binding. ADR-032 extracts the old search-quota and proxy-lease guarantees into closed attempt-bound private controls and extends ADR-031 only for grounded rescue search. The recursive completion-time rescue loop becomes a producer-built finite sealed workflow of one-unit media/promotion/buzz jobs; producers mint create-path identities, reserve artifacts against the native job id and fully plan the only permitted photo child before egress. Replay, expiry, hard-kill, extraction-parity and zero-old-client evidence are binding. No Tier-0 contract, taskq SQL/migration, QDarte source, database, provider or production state changed.
    - [x] **FR-03D-RESCUE · Native region-rescue family** — revise buzz to artifact-only completion, implement the stable artifact-revision producer/workflow planner, strict one-unit definitions, artifact reservation and authoritative rescue/media effect; add ADR-031/032 provider, search and proxy controls; bind the exact native handler and prove replay, response loss, concurrent settlement, external-control parity and hard-kill conservation without an old queue/client/completion path.
      - **Progress 2026-07-23:** runtime `c261749`/`712d4f8` implement the closed grounded/search/proxy request and receipt catalogs, strict one-unit media/promotion/buzz inputs, producer-minted buzz target identities, exact optional photo branch, artifact-only buzz graph and exact machine-inventory/digest oracles. Full runtime is 1180/1180 with one unrelated dependency warning; Ruff, changed-file format and 196-file MyPy are clean. API control/effect/planner, worker binding and hard-kill evidence remain open.
      - **Progress 2026-07-23:** API `7684e36`/`56b6fc9` add the shared database-time external-control ledger, authoritative region-rescue queue/attempt validation, grounded-provider and search-quota control, and host-selected browser-proxy claims through the private reporter route. Proxy credentials never enter task input or caller claims, typed response models hide them from representations, expiry retains unknown-health posture, and focused warning-as-error, Ruff and MyPy gates are clean. Stable artifact-revision planning, authoritative rescue/media effects, worker binding and the hard-kill rehearsal remain open.
      - **Progress 2026-07-23:** API `458460c` implements the post-artifact producer command as an expand-bind-seal choreography: deterministic create-path identities, unsealed/unclaimable idempotent workflow members, PostgreSQL-time artifact reservations bound to returned native job ids, application commit before seal and exact crash-boundary replay. Runtime `e2e4fc0` adds the closed `media_application | region_rescue` inspect/apply evidence and authoritative response union; 1181 tests, Ruff and 196-file MyPy pass. The implementation deliberately does not route this surface through the old media-result or completion request models; extracting queue-independent media/place mutation kernels is the next open boundary before reporter and worker binding.
      - **Progress 2026-07-23:** API `e993c19` extracts media application into a queue-independent content-domain mutation kernel and makes the old completion surface delegate inward; API `24ec803` binds the native reporter directly to that kernel with stored-plan authority, database-timed application and replay-stable receipts. Promotion/buzz mutation extraction, worker binding and the hard-kill conservation rehearsal remain open; the native path still cannot fall back to an old completion helper.
      - **Progress 2026-07-23:** API `b6d3853` makes the shared content creation primitive accept the producer-planned content identity only on a genuine create while preserving any authoritative existing identity. This removes the old completion-time identity-minting assumption needed by the promotion kernel; focused warnings-as-errors, Ruff and MyPy gates pass.
      - **Progress 2026-07-23:** API `d1bb91f` replaces the legacy-job/result-coupled artifact helper at the native boundary with a queue-independent, row-locked settlement kernel. It validates the exact stored artifact revision and taskq job reservation, consumes one lead at database time, returns a bounded digest receipt and fails closed on revision or reservation drift. Promotion binding remains stopped at CQ-24; buzz domain mutation extraction can proceed independently.
      - **Progress 2026-07-23:** API `32f9cdc` implements and binds the queue-independent buzz-lead mutation. Stored lead authority now drives duplicate hold, matched-place website recovery, geo-fenced producer-ID create/promote or manual review; its domain mutation and exact artifact-reservation consumption run inside one replayable native effect callback. Response loss replays the stored outcome and artifact digest without repeating either mutation. The focused rescue regression set is 111/111 with Ruff and configured MyPy clean. CQ-24 still gates the separate promotion discriminator.
      - **Progress 2026-07-23:** workers `ec2efa9`/`f4df59e`/`a818946` authorize the closed rescue/control families, decode the typed rescue and artifact receipts, bind the finite media/buzz handler and exact registry, and remove the stale worker-side buzz-to-rescue child now owned solely by the post-artifact producer command. Inspection precedes egress, committed replay performs zero rescuer calls, and only the stored photo branch can be selected. The full workers gate is 714/714 with Ruff and 64-file MyPy clean; the closed source inventory includes the new evidence. Promotion and the concrete external rescuer remain open.
      - **Progress 2026-07-23:** workers `de04552`/`c8e17c2` add the first concrete queue-native external rescuer and an exact rescue-only `WorkerService`. Stored official candidates cause no metered egress; Serper recovery reserves through `search_api_control` before the call, settles every known outcome, keeps the credential worker-local and returns only bounded evidence. The worker owns its HTTP taskq client, reporter, control executor and lifecycle without the old worker loop or queue client. The full workers gate is 722/722 with Ruff and 66-file MyPy clean. Browser/proxy and grounded-provider fallback parity, promotion CQ-24 and hard-kill evidence remain open.
      - **Progress 2026-07-23:** workers `6337083`/`54e4ec5` complete the native external-control chain. Search denial/empty/transport falls through to a host-selected `browser_proxy_control` lease whose credentials exist only in the trusted response; bounded probes settle known outcome and health. Unresolved proxy recovery falls through to injected grounded search, with every provider/model attempt reserved and settled through ADR-031 before the next option. Unknown usage/health fails closed. The full workers gate is 724/724 with Ruff and 66-file MyPy clean. Promotion CQ-24, live response-loss/concurrency and hard-kill evidence remain open.
      - **Progress 2026-07-23:** CQ-24 is closed docs-first by taskq `836ff15`; runtime `b34a7cb` requires the producer-planned content identity on every promotion unit; API `7244922` applies the row-locked, scope-checked, geo-fenced promotion through the queue-independent kernel; workers `668b9f9` binds the discriminator without external rescue calls and can select only its stored photo branch. API `9def6e0` adds a live disposable-PG18 two-transaction rescue-effect proof: a concurrent same-intent settlement serializes to one domain mutation, both returned responses may be lost, and a third transaction replays the byte-identical committed receipt. Runtime is 1181/1181 with one unchanged third-party RDFa deprecation warning, clean Ruff and 196-file MyPy; workers are 725/725 with clean Ruff and 66-file MyPy; the focused API rescue set is 113/113 with clean Ruff and six-file MyPy. A true rescue-lane hard-kill rehearsal remains before FR-03D-RESCUE can close.
      - **Result 2026-07-23:** workers `5f96edf` adds the confirmation-gated local kill worker; the first real rehearsal exposed a worker/API canonical-fingerprint mismatch before egress, and workers `fb9f78a` replaces both search and proxy fingerprints with the shared authoritative algorithm plus a cross-side oracle. API `bf4c752` then proves the complete process boundary on disposable databases: worker 1 receives one loopback search response and is killed by `SIGKILL` before usage settlement; the same job is reclaimed after lease expiry; attempt 2 records generation 1 as expired/unsettled with no egress; attempt 3 opens generation 2 and succeeds. Raw oracles prove two external calls equal one known provider event plus one unknown-cost generation, one `region_rescue` domain effect, one `website_unlocked` artifact consumption, zero releases and zero old job/event rows. API `0f51072` repairs the CQ-24 control fixtures and workers `291a196` keeps the closed source inventory exact. Final gates: taskq 584/1 with CI-shaped Redis and Ruff clean; runtime 1181 with the unchanged third-party RDFa warning and Ruff/MyPy clean; workers 727 under warnings-as-errors with Ruff and 67-file MyPy clean; the focused API rescue/control/effect set is 41/41 under warnings-as-errors with Ruff/format clean. No persistent database, external provider, old worker, old queue client, production service or production state changed.
    - [x] **FR-03D-PHOTO-FIND-SPEC · Native photo-find artifact/effect/branch contract frozen** — the source-derived completion sweep is made executable before binding. Each strict entity plan carries at most one complete scope-equal verify or repair-review child; only a committed usable result may select it. Candidate files and the bounded selection manifest use job/family/entity identity, never attempt paths; relative keys and SHA-256 digests are verified beneath the host artifact root. One closed `photo_application/apply` effect owns media, hold, blocker and optional Wikidata-fill mutation at PostgreSQL time, atomically with its stable receipt. Replay skips provider/filesystem work and returns identical artifacts, effect truth and child. The old photo-result route, event writes and completion planner are forbidden. No Tier-0 contract, SQL, migration, QDarte source, database, provider or production state changed.
    - [x] **FR-03D-PHOTO-FIND · Native photo-find family complete** — runtime `de3720c` freezes exact entity-covering mutually exclusive verify/review plans, immutable relative artifacts, bounded manifests and the closed effect; API `a91ab9b`/`683021b` adds stored-input authority, server-root containment, manifest/result equality, SHA-256 verification, PostgreSQL-time queue-independent mutation and a real two-transaction response-loss/concurrency proof with one committed artifact set; workers `0f4fd45` binds the exact type, trusted reporter, provider-injected handler and create-once job/family/entity artifact store. Apply and inspect return the same stable effect/artifacts; replay skips the provider and returns the identical preplanned child; conflicting artifact bytes fail closed; no old job, attempt, client, result route or completion hook is constructed or called. Runtime passes 1183 and its 57 inventory/definition oracles pass after inventory commit `644720c`; workers pass 732 with Ruff and MyPy clean; focused API reporter/HTTP/artifact/effect suites pass 39 with static gates clean, while the full branch reaches 1805 passes with the same 11 unrelated route/environment/stale-fixture failures after source-inventory repair `7895fda`. FR-04's real side-effecting hard-kill remains explicitly unwaived.
  - [ ] **FR-03E · Disposable SQL/HTTP completion** — provision all five queues and run all 21 handlers through real a7 SQL/HTTP against fresh and sanitized production-shaped local databases, proving no old worker or queue row changes.

- [ ] **S5-QD-FR-04 · All-lane local migration** — migrate pure, leaf verification/classification, media/content effect, chained content/publish, and discovery/import/scheduled waves into the one native worker. Each wave proves replay, cancellation, retry exhaustion, response loss, hard-kill reclaim, bounded concurrency, effect conservation and exact follow-up/dependency graphs with no dual publisher or consumer.

- [ ] **S5-QD-FR-05 · QDarte API/admin/runtime replacement** — move domain submissions and bounded status to generated taskq surfaces, remove old queue lifecycle routes/clients/process controls as their last callers disappear, and retain only true provider/resource/domain controls. No native job may be translated into an old job model.

- [ ] **S5-QD-FR-06 · Executable legacy deletion** — delete the old queue models/services/routes/client/loop/settings and all taskq pilot/contact/C6/C7/C8 adapters, modes, controllers and product harnesses. Historical evidence stays immutable; active verification is rewritten against the final architecture.

- [ ] **S5-QD-FR-07 · Database contraction and clean baseline** — add a content-safe existing-install migration that drops all queue-delete relations without importing rows, plus a clean fresh-install QDarte baseline that never creates the old queue or embedded taskq clone. Prove retained-domain foreign keys/counts/digests and independent business/taskq backup-restores.

- [ ] **S5-QD-FR-08 · Production cutover package** — prepare the explicit backup/restore, old-process stop, final queue digest, contraction, native deployment, least-privilege enablement, bounded validation and whole-database rollback choreography. Preparation is local-only until the owner separately authorizes production testing.

- [ ] **S5-QD-FR-AUDIT · Full local production-readiness gate** — pass dual-PG taskq evidence, every QDarte suite/static gate, fresh and production-shaped database paths, all handlers and representative queue families, graphs/schedules/recovery/effects/auth/resource tests, two clean restarts, backup/restore, and the exact zero-legacy oracle. Only this gate can declare QDarte ready for the production cutover test.

The FR sequence above is the sole active goal. Every other unchecked row in
this file is parked and must not preempt it unless the owner explicitly changes
direction.

- [x] **S5-AR-SPEC · Durable two-phase admission frozen** — ADR-023 accepts a reusable queue-native `(queue, idempotency_key)` reservation ledger rather than a QDarte mapping wrapper. Protocol 1.0.8 and Manifest 0.1.5 freeze client-generated retry-stable handles, SHA-256 intent binding, `reserved | pending | admitted`, atomic finish with immutable bounded receipt, reservation-only cancellation, database-clock expiry/retention, producer-scoped authorization, bridge/rollback-floor ordering, and immutable migration 0007. QDarte is the first integration but owns no durable mapping. No SQL, migration, source, package release, host database, worker, provider, deployment, production, or direct-queue change occurs in this docs-first task.

- [x] **S5-AR-01 · Admission SQL kernel and bridge proof** — immutable `0007_admission_reservations.sql` adds the private durable reservation ledger, one-job linkage, database-clock expiry/retention, bounded janitor cleanup, and producer-only reserve/finish/cancel functions under exact 0.1.5 catalog/grant verification. The bridge accepts the closed `{0.1.2,0.1.3,0.1.4,0.1.5}` set while exposing no admission transport yet. Fresh/full chains, canonical finish replay, intent/handle mismatch, cancellation, expiry takeover, retention, privilege walls, and choreographed reserve/finish/cancel races pass identically on PG18.3 and exact PG16.14: 489 passed with 1 opt-in skip each; the PG18 million-row plan gate remains 2/2. No HTTP, QDarte, host database, or production action occurred.

- [x] **S5-AR-02 · Generated admission transports and parity** — strict SQL, async HTTP, sync HTTP, high-level `TaskQ`, and substitutable fake transports now expose reserve/finish/cancel from the shared 36-command catalog. Admission routes are absent unless explicitly enabled, and enabled runtime startup requires exact 0.1.5 metadata plus `admission_reservations`. Request models exclude competing authority, hide handles in repr/OpenAPI, and bound receipts; queue authorization precedes body decoding and SQL. Live SQL/HTTP parity, denied/auth-dependency paths, rollback under backpressure, committed-response-loss replay with a stable handle/fresh request IDs, sync-client retry, and one-job/one-receipt raw-state oracles pass on PG18.3: 501 passed with 1 opt-in skip; DB-free is 308 passed, package build and exact `verify()` are green. No QDarte, host database, production, worker, provider, or existing queue changed.

- [x] **S5-AR-AUDIT-EVIDENCE · Admission completion evidence regenerated** — the identical fresh/full-chain suite is 501 passed with 1 opt-in skip on PG18.3 and an exact disposable PG16.14 container; the 21 admission kernel/surface tests pass under warnings-as-errors on each, and the million-row structural plan gate is 2/2 on each. The installed-artifact ledger now asserts the 0001–0007 migration chain, exact 46-function internal catalog, and an executable fake admission lifecycle; current wheel and sdist pass core/HTTP/OutLabs isolation on Python 3.12 and 3.13 (12/12). Ruff/format, package build, and exact `verify()` are clean. No host, QDarte, production, external queue, credential, provider, or database outside disposable/local test targets changed.

- [x] **S5-AR-AUDIT-ORACLES · Admission wire disposition matrix completed** — a mounted real-SQL vector now covers unknown queue/admission, competing-handle `pending`, wrong-handle conflict, cancelled/idempotent-cancel, reacquisition, created, finish mismatch, intent mismatch, already-admitted cancellation with stored receipt, and database-time expiry. Every conflict detail is asserted as its exact one-key safe reason, and all reserve/cancel outcome variants cross the generated client/facade boundary. No source, SQL, migration, contract, host, QDarte, production, provider, credential, or existing queue changed.

- [x] **S5-AR-REVIEW-REQUEST · Round-13 admission completion gate assembled** — the targeted request pins `8d520d2..7f6f662` and requires independent catalog derivation, migration/checksum immutability, SQL linearization and raw-row oracles, response-loss replay, cancellation/finish races, expiry/retention, privilege walls, authenticate/authorize-before-decode ordering, SQL/HTTP/client/fake parity, exact runtime mount gating, resource closure, dual-PG plans, and the 12-way artifact boundary. Final tip evidence is 502 passed with 1 opt-in skip on each PostgreSQL major and 22 admission tests under warnings-as-errors on each. Its response alone decides S5-AR-AUDIT; READY can open only the isolated QDarte repin and C6-03 proof, never production migration, retirement, existing-queue mutation, deployment, provider work, or Stage 6.

- [x] **S5-AR-R13-RESPONSE · Round-13 BLOCKED response recorded docs-first** — registered the immutable response byte-for-byte (SHA-256 `7ce717e60e39e0dace15d635c8c5959876cad9433015c051fab002d9cd43ffd7`). The reviewer independently reproduced the full dual-PG, race, privilege, plan, DB-free, and artifact evidence and found the primitive sound, with zero Contract questions. R13-02 is clarified without SQL or wire change: finish identity is literal JSONB, omitted and explicit-null fields differ, each writer must preserve one style, and official clients omit `None`. Owner confirmation for R13-07(a): Round 12 was performed by a separate parallel external-review session, not the implementation agent; its response and remediation were intentionally recorded together in `b854f46`. R13-01/03/04 remain the targeted delta preconditions.

- [x] **S5-AR-R13-01-02 · Conflict branches and cross-writer identity pinned** — direct SQL and mounted-wire vectors now exercise unreacquired `reservation_expired` and `reservation_cancelled` finish failures, and the missing cancel-wins race proves the blocked finish creates no job. The fake pins cancelled-finish parity. A client-omitted versus raw-SQL-explicit-null replay demonstrates the documented literal-JSONB identity and returns only `finish_mismatch`; migration 0007 remains byte-identical. R13-01 and R13-02 are closed without source, SQL, migration, wire, host, QDarte, production, provider, credential, or existing-queue change.

- [x] **S5-AR-R13-04 · Cancel OpenAPI projection narrowed** — a dedicated cancel wire-data model now advertises exactly optional `job_id`, `receipt`, and `receipt_expires_at`, matching the only data-bearing `already_admitted` outcome; it no longer reuses reserve's handle/retry/expiry projection. The generated-catalog and mounted-OpenAPI oracles assert exact field equality, while runtime response behavior remains byte-identical. No SQL, migration, contract, host, QDarte, production, provider, credential, or existing-queue change occurred.

- [x] **S5-AR-R13-DELTA-REQUEST · Targeted Round-13 recheck assembled** — pins `64a1241..7fb6568`, the immutable response and migration hashes, the missing finish-state/race vectors, literal-JSONB cross-writer behavior, exact cancel OpenAPI projection, Round-12 provenance, residual ownership, and final 505/1 dual-PG plus 309 DB-free gates. R13-03 additionally requires the complete range pushed and every CI job green at the delta-request tip. READY may open only the isolated QDarte repin/C6-03 proof; no production migration, retirement, existing-queue mutation, deployment, provider work, or Stage 6 is authorized.

- [ ] **S5-AR-R13-FOLLOWUPS · Nonblocking admission hardening** — own R13-05/06/07(c,d): add live unmounted-route, facade-backpressure, recycle, janitor-class/bound, and async mint-once vectors; extend safe-detail defense-in-depth for handle/receipt/intent hash; and label fake hashes as non-comparable to SQL hashes while keeping admission-specific replay prose authoritative. These do not open QDarte or production scope and must not be represented as Round-13 preconditions.

- [x] **S5-AR-R13-DELTA-RESPONSE · Durable admission independently accepted** — registered the immutable response byte-for-byte (SHA-256 `6fac5706b624fe3fb92f10c2807e71d7aff6684371c3f0cc03cadd42f7ac1a66`). The reviewer independently re-ran 505/1 on PG18.3 and exact PG16.14, 309 DB-free, Ruff/format, full chains, hashes, race waits, cross-writer identity, exact cancel projection, published CI `29920365139`, and Round-12 provenance; all five checks pass with zero Contract questions. READY opens only an isolated immutable QDarte repin and C6-03 created/existed proof in the disposable local stack.

- [x] **S5-AR-AUDIT · Admission primitive completion gate** — Round-13 targeted acceptance closes the dual-PG race/resource/packaging/plan gate at Protocol 1.0.8 / SQL 0.1.5 / migrations 0001–0007. `admission_reservations` and `read_model_list_ready` are the exact active capabilities. No production migration, host deployment, existing-queue mutation, retirement, provider call, side-effecting lane, worker expansion, UI work, or Stage 6 is authorized.

- [x] **S5-QD-C6-03B · Immutable admission repin and isolated created/existed proof** — QDarte API commits `84e23ea`/`96fe5f0` and worker commit `21bd880` exact-hash pin immutable `outlabs-taskq==0.1.0a6`. Only disposable local `qdarte_contact_verify_dev` advanced through 0006/0007 to SQL 0.1.5; repeated migrate and two `verify()` runs passed under the owner while the facade retained its dedicated non-superuser four-capability login. The real adapter reserved before one real plan, atomically finished job `019f89f4-b4d0-760c-a513-1c76ed6fbf9a`, then returned `existed` with the same one-field receipt and zero planner calls on replay. Raw state is one queued job with zero attempts/failures/releases; direct-ledger and effect/provider full-row oracles remained equal. A lowercase no-candidate diagnostic remains auditable as an unlinked reservation with no manual cleanup. Focused API gates are 62/62 with Ruff/format/MyPy clean; the complete worker taskq/contact/config set is 73/73 with Ruff clean and imports a6. Both isolated branches are pushed. Evidence: `qdarteAPI/docs/taskq-contact-c6-03b-admission-evidence.md`. Stop before C6-04, any worker/provider action, production, existing-queue mutation, retirement, or Stage 6.

- [x] **S5-QD-C6-03B-REVIEW-REQUEST · Targeted QDarte admission-repin gate assembled** — Round 14 pins the immutable a6 release identity, QDarte API `c0940fb..96fe5f0`, worker `abeaac1..21bd880`, disposable database metadata/raw rows, dedicated-role boundary, reserve-before-plan implementation, admitted replay with a forbidden planner, and direct/effect oracle equality. READY may open only the already-frozen local C6-04 rollback exercises; it cannot authorize a worker/provider run, production migration, direct-queue retirement, non-contact lane, C7, or Stage 6.

- [x] **S5-QD-C6-03B-REVIEW · Targeted repin/evidence accepted internally** — registered the owner-authorized internal Round-14 response (SHA-256 `24fcc4f53e2f73f88829be8c9a8fcd629eb9b64d7c650c422127f1cfe7fccc6c`) with its non-independent provenance explicit. The review re-downloaded and hashed a6, derived the 0001–0007 artifact and lock pins, executed facade-role negatives, inspected exact SQL 0.1.5 metadata/admission/job rows, source-audited reserve-before-plan and no-fallback behavior, and reran API 62/62 plus worker 73/73 with lint/format/MyPy gates. No blocker or Contract question was found. READY opens only local C6-04.

- [x] **S5-QD-C6-04 · Local zero-row-copy rollback exercises** — QDarte API evidence commit `7a74458` records all three frozen postures with no manual queue DML. Explicit legacy mode performed zero package observations and created no package row. With the package harness/workers stopped, the dedicated operator transport paused the queue and cancelled both zero-attempt queued package jobs; replay returned `already_paused`/`already_terminal`, the durable admission retained its job/receipt, and the incumbent direct plus effect/provider full-row hashes stayed byte-identical. The post-effect posture used the retained CV-05 hard-kill job: an authorized safe projection remains succeeded, its attempts are exactly expired then succeeded, and one stable effect/contact-method record resolves the outcome without provider work or cross-backend replay. Evidence: `qdarteAPI/docs/taskq-contact-c6-04-local-rollback-evidence.md`. The queue remains paused and history intact. Stop for C6-AUDIT before C7, production, retirement, non-contact work, or Stage 6.

- [x] **S5-QD-C6-AUDIT-REQUEST · Local compatibility/cutover completion gate assembled** — Round 15 pins C6-01 mode isolation, C6-02 direct-drain interlock, C6-03 canonical admission and a6 repin, C6-04 typed rollback, the exact API/worker/release tips, and the retained disposable database raw state. It attacks dual publication, direct insertion, active-row import, fallback, broadened workers, package-table access, result/effect ambiguity, and manual-DML rollback. READY may open only C7-00 environment planning; no production mutation, worker/provider action, retirement, cohort, deployment, non-contact lane, or Stage 6 is authorized.

- [x] **S5-QD-C6-AUDIT · Internal local cutover accepted** — registered the owner-authorized internal Round-15 response (SHA-256 `0ad659ae143fd1fdff29a7e3718bda747a084cd10bf7fef0942cde792e5488fd`) with its non-independent provenance explicit. The audit derived closed one-publisher dispatch, the process-local direct-drain interlock, reserve-before-plan/admitted-no-plan behavior, and all three rollback postures from source and live read-only ledgers. It independently reproduced the exact six full-row conservation hashes, paused/zero-active package state, retained admission and stable post-effect history, API 62/62, worker 73/73, and clean Ruff/format/MyPy gates. No blocker or Contract question was found. READY opens only C7-00 planning.

- [x] **S5-QD-C7-00 · Production-evidence environment plan frozen** — the Tier-3 C7 plan selects Mac-mini local production on `mini87`, a separate `qdarte_contact_verify` database on the existing PostgreSQL 18 cluster, a one-process private-network package facade, and one network-isolated HTTP-only closed worker. It records the current API/worker/runtime remote divergence and forbids deploying the isolated pilot tree; C7-01 must identify the live deployed tips and build a zero-unclassified-path integration candidate. It fixes dedicated taskq/domain/operator identities, a capped 1+2 incremental connection budget with `H + 3 <= M - 20`, two-database backup/restore coverage, full-row direct/effect baselines, a valid containing-scope plus exact one-place allowlist, independent network-enforced egress counter, readiness-bearing health, and sixteen fail-closed stop conditions. Post-commit Markdown hard-break whitespace was removed in a dedicated hygiene follow-up before review. No source, production/database/credential/service/queue, worker/provider, deployment, or retirement action occurred. Targeted delta review is required before C7-01.

- [x] **S5-QD-C7-00-REVIEW-REQUEST · Environment/preflight gate assembled** — Round 16 pins the frozen Mini87 plan, accepted C6/release identities, current three-repository graph evidence, and ten adversarial programs covering environment ownership, one-publisher topology, source convergence, privilege walls, exact connection arithmetic, two-database restore, full-row conservation, independent egress counting, sequencing, rollback, and scope. The owner-authorized review is explicitly internal/non-independent. READY may open only C7-01 preflight, never a package cohort/provider call, direct retirement, non-contact lane, or Stage 6.

- [x] **S5-QD-C7-00-REVIEW · Internal environment-plan review recorded BLOCKED** — registered the owner-authorized internal Round-16 response (SHA-256 `f7c8cef4a68ad44eb345b26b8062d7f7f41befb517c2d4ae79e951344c66ad39`) with its non-independent provenance explicit. The review independently confirmed Mini87 ownership, all three remote graph identities, separate identity/pool need, the three-connection formula, historical-backup limitation, full-row oracles, rollback, and docs-only scope. It found three Tier-3 blockers: invalid `scope_kind=place`, no enforceable proxy seam in the closed handler, and unreachable loopback between the proposed separate containers. No Contract question. C7-01 remains closed pending a docs-only delta.

- [x] **S5-QD-C7-R16-01-03 · Environment-plan delta** — the cohort now uses valid `country` scope plus the selected place's stored country, exact one-item `place_ids`, `limit=1`, and an equality oracle on the planned entity. The production graph is an unpublished exact-origin facade and dedicated closed worker on an `internal: true` Compose network; a dual-homed bounded proxy is the worker's sole egress, and the runtime-owned verifier cannot be selected or bypassed by payload. Readiness now requires exact taskq metadata plus usable capped domain/auth storage. All topology, identity, sequence, counter, health, and sixteen stop-condition rows match. No source, production/database/credential/service/queue, worker/provider, deployment, or retirement action occurred. Targeted delta review still gates C7-01.

- [x] **S5-QD-C7-R16-DELTA-REQUEST · Targeted environment-plan recheck assembled** — pins remediation `5d08f7c` and the immutable Round-16 response hash, with five checks for the valid one-place allowlist, runtime-owned verifier and network-enforced proxy, reachable unpublished Compose topology, readiness-bearing health, corpus consistency, and docs-only scope. The review remains explicitly internal/non-independent. READY may open only C7-01 preflight.

- [x] **S5-QD-C7-R16-DELTA-RESPONSE · Environment plan internally accepted** — registered the owner-authorized internal delta response (SHA-256 `50dfbc214985bee7d7d8c8fff7a150f50e0e8069582b599bc561bd1c1246aadb`) with its non-independent provenance explicit. It verifies the valid country-plus-one-place contract shape, runtime-owned proxy verifier, network-isolated worker and dual-homed counter, reachable unpublished Compose service graph, readiness-bearing health, corpus consistency, and docs-only scope. All R16 blockers pass with zero Contract questions. READY opens only C7-01 preflight.

- [x] **S5-QD-C7-01 · Mini87 production preflight** — only after explicit owner authorization: identify the live deployed source/database identities; construct zero-unclassified-path API/worker/runtime candidates; run complete gates; create and execute the fresh two-database-plus-globals restore drill; measure `M` and normal-production `H` and prove `H + 3 <= M - 20`; implement and test the capped domain session, exact private service origin, readiness, runtime-owned proxy verifier, and network isolation; prove all runtime/role/token negatives; provision/migrate/verify the lasting package database and disabled services; finish healthy in `legacy`, queue paused, worker stopped, and zero package publish/provider action. Stop for targeted acceptance before C7-02.
  - **Progress 2026-07-22:** all four Mini87 shares are mounted and the three live checkout identities were read without modifying them. Reviewed integration candidates are pushed on `codex/taskq-c7-01`: API `a4d90e2` (all 30 accepted C6 commits forward-ported onto current `origin/main`, capped 1+2 facade/domain pools, readiness and production guards), worker `c8c03bb` (all 14 C6 commits plus the Mini87 typing cleanup, private proxy-only verifier, closed image), and runtime `21ccce3` (disabled profiles, internal-only worker network, two-database backup/restore unit). Focused gates are API 95/95, worker 627/627 plus MyPy 53 files, and runtime 1124/1124 plus MyPy 192 files; the production-shaped Compose graph parses, default Compose excludes all C7 services, and the candidate worker image has no dev packages or credential-bearing history. The broad API suite is honestly not a green gate at this point: 1692 passed and 15 unrelated/order-sensitive baseline tests failed outside the C7 paths. C7-01 remains open at stop condition 1 because the SMB mounts do not provide live command execution: new IPv4 and IPv6 connections to Mini87 currently return `No route to host`, so deployed container identity, database identity, fresh backup/restore, `M/H`, grants, and deployment are deliberately unclaimed and untouched.
  - **Progress 2026-07-22 (live preflight):** remote execution is restored. Live identity, candidate convergence, fresh globals/API backups and copied manifests, disposable API and package restore drills, exact 0001–0007 package verification, and a 15-minute 180-sample connection window are complete. The measured peak is `H=16`, so `H+3=19 <= 80` with 61 connections of headroom; the pre-lasting full-row baseline is recorded and no lasting package database/job/provider action exists. The live inventory then found the incumbent API connected as cluster superuser and able to recover the owner secret through environment, Docker socket, broad projects mount, and backup control. C7-CQ-02 correctly stopped package creation. The owner chose the full same-cluster least-privilege conversion rather than a separate database service. C7-01A now owns API/worker credential separation, owner-free startup, host-only backup control, secret-free worker desired state, exact grant verification, restored-production proof including OutLabsAuth, and reversible Mini87 rotation before package creation resumes.
  - **Result 2026-07-22:** API `65fbd22`, worker `c8c03bb`, and runtime `36bfe69` are live-proven at the disabled boundary. The incumbent API/worker credentials are distinct restricted logins; the exact named-table domain and package role manifests converge; host migration `20260721_0076` and package migrations 0001–0007 verify; queue `qdarte_contact_verify` is paused with zero jobs/attempts/events/admissions; only the unpublished facade runs. Backup `20260722-185358` atomically covers API/package/Intake/globals and its three-database restore passed. Human login, service authorization, ordinary-worker zero-job claim, exact grants, secret absence, direct/effect full-row conservation, and `H+3=19 <= 80` pass. Mode remains `legacy`; worker and egress are absent; no package job/provider action occurred. Evidence: QDarte API `docs/taskq-contact-c7-01-production-preflight-evidence.md`. Stop for Round 17 before C7-02.

- [x] **S5-QD-C7-01A · Same-cluster runtime privilege separation** — implement the adopted C7-CQ-02 resolution across QDarte API/runtime/worker candidates. Define distinct non-superuser API and ordinary-worker logins, retain the exact contact-domain and package identities, revoke default database connect, remove every owner/migration/backup secret and control mount from long-lived services, move migrations/backups to a host-only control path, keep API-managed desired states secret-free with controller-only credential injection, and ship an exact declarative grant verifier including future default privileges. Prove on a restored production clone: real API boot, OutLabsAuth login plus service-token authorization, representative read and rolled-back write, every worker database path including legacy direct queue, complete backup/restore, and negative admin/operator/package access. Rotate Mini87 reversibly and prove health/continuity/secret absence before C7-01 may create the lasting package database.
  - **Progress 2026-07-22, live preflight:** owner-authorized Remote Login established the missing live channel. The production image reports deployed API `fee29d2` against database identity `production/45677dd9-2717-4d80-bdf7-a09a94a95221`; the three reviewed candidate tips are now cleanly checked out on Mini87 after preserving 92 API paths and two runtime paths as separate local recovery commits. Fresh API/intake backups at `20260722-155220` were byte-verified on `/Volumes/Server87` and `/Volumes/Server87 Backup`, their disposable restores matched production counts and migration heads, and the package 0001→0007 disposable install/dump/ownership-preserving restore passed `verify()` twice with exact 0.1.5 metadata before both named scratch databases were dropped. A 15-minute/180-sample connection trace measured `M=100`, `H=16`, so `H+3=19 <= 80` with 61 connections of headroom. Runtime candidate `a79812d` additionally fixes portable backup checksums, preserves package ownership/ACLs, and validates the actual `taskq.admissions` / `taskq.schema_migrations` relations; 1124 tests and MyPy 192 remain green. The pre-lasting full-row baseline proves six direct jobs, zero active jobs/leases, no stable-effect table yet, and no lasting package database. Lasting creation/deployment is now paused at S5-QD-C7-CQ-02; no package job, provider call, or C7-02 action occurred.
  - **Result 2026-07-22:** restored-clone and live proofs pass under the exact identities. The API and ordinary worker no longer hold owner/admin powers or control mounts; controller-only `worker.env` is mode 0600; API startup is migration-free; recurring backup is host-owned; `PUBLIC CONNECT` and cross-database access are closed; exact current/default grants and every negative vector verify. The reversible rotation completed without losing health, OutLabsAuth, direct-queue access, or worker continuity.

- [x] **S5-QD-C7-01-REVIEW-REQUEST · Disabled production-preflight gate assembled** — Round 17 pins taskq's accepted plan, QDarte API `5e25ab6..3303126`, worker `f7427cb..c8c03bb`, runtime `a6117c6..9fec99c`, live image/artifact identities, the fresh atomic backup/restore, exact grants and cross-database negatives, OutLabsAuth lifecycle, connection arithmetic, disabled topology, raw package/direct/effect oracles, and final gates. The response is owner-authorized internal/non-independent because the separate reviewer is unavailable. READY may open only C7-02's already-frozen one-place cohort; no job/provider action occurs in this request.

- [x] **S5-QD-C7-01-REVIEW-ADDENDUM · Installed recurring wrapper proven** — the internal review executed the exact host-installed backup wrapper, not merely the underlying script. Run `20260722-191547` atomically included API/package/globals, copied API/package and Intake sets to both Server87 roots, uploaded five plus four files to object storage, and exited zero. The additive evidence is recorded without source/image/service/queue/provider mutation; QDarte API docs-only tip is `4de9228`.

- [x] **S5-QD-C7-01-REVIEW · Disabled production preflight internally accepted** — registered the owner-authorized internal/non-independent Round-17 response byte-for-byte (SHA-256 `780b56012b9910080db854ffc7bff649666cc9424a80b5260d9b940b0d79087d`). The review re-derived branch/artifact identity, exact runtime/domain/package grants, auth lifecycle, migration chains, paused zero-row state, private topology, 180-sample connection budget, wrapper-level backup plus three-database restore, full-row conservation, live human/worker continuity, and final gates. All twelve programs pass with zero Contract questions. Three LOWs remain explicit: one inherited worker commit lacks the trailer, the broad API baseline has eight unrelated environment/order failures, and Mini87 has about 12 GiB system-disk headroom. READY opens only the frozen C7-02 one-place cohort.

- [x] **S5-QD-C7-02 · One-place production cohort** — executed only the frozen `country=AR` plus exact place `aa09c75c-823d-4805-9147-2fbddabd90d8`, `limit=1`, under the accepted Mini87 identities. The provider-free planner selected that one place; keyed calls returned `created` then `existed` for job `019f8b5e-c842-720a-b652-83f6af9eade6`; canonical read and raw state show `succeeded`, one stable application, one contact-method row, one usage unit, and one successful gateway access. The first two durable attempts failed inside the private gateway before external invocation because its accidental 64-character carrier rejected the canonical 170-character URL-shaped candidate; the bounded 2,048-character fix let the same job converge on attempt three. Direct job/attempt/event full-row hashes stayed byte-identical. The packet also records the fail-closed missing drain env, stale token, missing gateway executable, accidental `--no-deps` omission with unchanged production identity/digests, and absent structured counter line; all are fixed and regression-pinned. Production ends healthy in `draining`, queue paused, worker/egress absent, ephemeral tokens removed. Evidence: QDarte API `docs/taskq-contact-c7-02-one-place-cohort-evidence.md`. Stop for targeted acceptance before C7-03.

- [x] **S5-QD-C7-02-REVIEW-REQUEST · One-place cohort targeted gate** — Round 18 pins the C7-02 cross-repo source and cohort/follow-up image identities, exact evidence hash, one-place/keyed-admission/raw-attempt/effect/direct-conservation oracles, least-privilege topology, cleanup scope, fail-closed incidents, final posture, and the missing-historical-structured-counter judgment. The response is owner-authorized internal/non-independent because the separate reviewer is unavailable. READY may open only C7-03; no direct retirement, another lane, or Stage 6.

- [x] **S5-QD-C7-02-REVIEW · One-place cohort targeted acceptance** — registered the owner-authorized internal/non-independent Round-18 response byte-for-byte (SHA-256 `818ca59df840dd73cabfdb73866119211e314b569e9a9b93936afe54557beb8b`). The audit regenerated source/artifact identities, one-place planning, created/existed admission, raw three-attempt history, canonical terminal read, byte-identical direct hashes, one stable effect/method/usage unit, exact private topology, final paused/draining posture, 505/1 taskq, 628 workers, 1,144 runtime, and 49 API boundary gates. READY explicitly accepts the one actual access line plus reconciled durable oracles and the network-disabled final-image structured-counter proof; three LOWs bind C7-03 to persist that line, retain the honest `3/2/0` history, and use explicit `--no-deps`. Zero Contract questions. C7-03 alone is open.

- [x] **S5-QD-C7-03 · Production continuity and rollback evidence** — API evidence commit `78d5ce5` records two distinct normal replacements on immutable image `e0f60c9`, both healthy in `package` with the queue paused and no worker/gateway; the full-window direct job/attempt/event hashes stayed byte-identical. The installed host wrapper created timestamp `20260722-203544`, atomically covering API/package/globals plus the matching Intake set, with local/external checksum parity and successful object-store uploads. The supported drill restored all three databases and dropped them; two additional network-isolated PostgreSQL 18 containers loaded the actual globals and restored API/contact and Intake with ownership/contract/count oracles. A mode-only, explicit-`--no-deps` replacement rehearsed zero-DML rollback to healthy `draining`. Package/domain hashes and the honest `succeeded / 3 attempts / 2 failures / 0 releases` history remained exact. The bounded no-network structured counter is persisted mode 0600 on Server87 with SHA-256 `f061b8d506007636a3f5683f79698f7ce5465e2a934f7faf80fd1de0a8779109`. Evidence file SHA-256 is `a85a015a988e9845c3d33d5addc6664f214c1439954554c9d3c3e44f87ed35f5`; runtime status commit is `17e78a4`. Stop for C7-AUDIT before direct retirement, another lane, or Stage 6.

- [x] **S5-QD-C7-AUDIT-REQUEST · Production contact-lane completion gate assembled** — Round 19 pins all C7 repository/build/image/database identities, evidence hashes, the accepted one-place history, both deployment containers, exact direct/package/domain conservation oracles, installed recurring backup timestamp, local/external/object-store continuity, in-cluster and network-isolated globals restores, persisted structured counter, zero-DML rollback, known host-port posture, gates, hygiene, and scope. The response is owner-authorized internal/non-independent because the separate reviewer remains unavailable. READY may open only a separately frozen direct-retirement specification; no retirement, another lane, or Stage 6 is authorized.

- [x] **S5-QD-C7-AUDIT · Production contact-lane completion gate** — registered the owner-authorized internal/non-independent Round-19 response byte-for-byte (SHA-256 `6c80b8ca38b70e1dcd4c170d7c1ce4f13f3dfffc6f2e41aa0319a339aa0be224`). The audit regenerated all C7 identities, exact role/topology/budget boundaries, retained one-place `3/2/0` truth, both deployment cycles, ten full-row conservation oracles, recurring API/package/Intake/globals backup, in-cluster and network-isolated restore proofs, structured no-network counter, zero-DML rollback, final safe posture, 505/1 taskq, 628 workers, 1,144 runtime, and 60 API boundary gates. READY has zero Contract questions and two LOWs: the reinstalled LaunchAgent has not yet reached its next schedule, and one exactly cleaned-up failed isolated restore attempt was absent from the implementation packet. Only a separate direct-retirement specification is opened.

- [x] **S5-QD-C8-SPEC · Direct contact retirement sequence frozen** — the new Tier-3 specification derives the still-active QDarte admin caller, three direct producer surfaces, two direct consumer families, shared `qdarte_ops`/old-taskq history, and package/domain dependencies from source. C8 first makes the admin's package admission + exact-ID read behavior the rollback caller floor; then removes every direct producer and observes seven days/two API cycles; only afterward may it add a server-side no-claim guard, remove contact-only consumers, and observe seven days/two worker cycles. The shared ledgers/migrations/models/domain code and package history remain; rollback is paired-image/config-only with zero DML. R19-01's next scheduled 03:15 run is a binding pre-implementation gate. No caller, source, configuration, service, database, IAM, worker, or production state changed; targeted review is next.

- [x] **S5-QD-C8-REVIEW-REQUEST · Direct retirement design gate assembled** — Round 20 pins the docs-only C8 proposal and exact QDarte API/worker/runtime/admin source identities, then requires an independently derived executable inventory before reading the claimed dispositions. It attacks wrapper/mapping risk, caller response/list/cancel migration, exact-ID read and enqueue+read-only IAM, both direct producers, producer-before-consumer ordering, stale-worker no-claim, shared-ledger/package-domain preservation, two seven-day windows, both rollback floors, and R19-01's naturally scheduled backup. READY may open only C8-R1 after eligibility; no producer/consumer removal, data/schema deletion, another lane, or Stage 6 is authorized.

- [x] **S5-QD-C8-REVIEW · Round-20 BLOCKED response recorded** — registered the owner-authorized internal/non-independent response byte-for-byte (SHA-256 `df8b7e3b52432720072e3f5f14903eb31b8a0d8d8794f340e3cd20820a621574`). The audit re-derived every current direct/package/admin/worker/shared dependency and accepts the replacement architecture, producer-before-consumer ordering, no-claim guard, shared-ledger preservation, both seven-day windows, and paired rollback floors. R20-01 blocks because accepted production is draining/paused with worker/gateway absent while R1 assumed package service; R20-02 blocks an unreviewed jump from C7's one-place proof to the admin's `limit: 500`; R20-03 requires an explicit owner/UI decision for exact-ID-only rediscovery and operator-only cancellation. Zero Contract questions. Docs-only remediation plus targeted delta review is the sole path to C8-R1.

- [x] **S5-QD-C8-R20 · Retirement-design remediation** — a read-only production aggregate measured six historical direct jobs at `[1,25,86,100,176,293]` planned entities (681 total; no candidate data read). The spec now sequences the actual draining/paused/no-worker/no-gateway baseline through server-disabled caller deployment, exact package readiness, gateway/closed-worker start, fresh drain, unpause, submission enablement, and inverse safe unwind. Production input requires an explicit accepted cap, rejects absent/over-limit before reservation/planning, fixes depth/concurrency/worker at one, and advances through independently accepted 25/100/300 cohorts; direct producer removal depends on acceptance of the supported envelope. The adopted least-privilege UI uses exact-ID status plus a client-side hint and leaves cancellation operator-only, with reload/hint-loss vectors. No source, configuration, service, IAM, database, queue, worker, or production state changed.

- [x] **S5-QD-C8-R20-DELTA-REQUEST · Targeted retirement-design recheck assembled** — pins only `a355f47..39100fa` and the immutable Round-20 response hash. It checks the exact safe transition/unwind, reproduced `[1,25,86,100,176,293]` aggregate, explicit-limit pre-reservation rejection, one-depth/one-concurrency/one-worker controls, 25/100/300 staged gates, and the exact-ID/operator-only UI posture. READY may open C8-R1 only after the scheduled 03:15 backup gate; it cannot authorize enablement, a cohort, producer/consumer removal, data/schema deletion, another lane, or Stage 6.

- [x] **S5-QD-C8-R20-DELTA · Retirement design internally accepted** — registered the owner-authorized internal/non-independent delta response byte-for-byte (SHA-256 `431fdc036b314acf71117f137a49c9065fc3bef08d10bf356bf3eaf7b633a90f`). It accepts the exact draining/paused→serving→safe-unwind choreography, reproduced `[1,25,86,100,176,293]` historical envelope, explicit-limit pre-reservation refusal, one-depth/one-concurrency/one-worker controls, independently accepted 25/100/300 cap stages, and exact-ID/operator-only UI posture. Zero Contract questions. C8-R1 opens only after the next naturally scheduled 03:15 backup and all remaining §4 eligibility evidence; no service enablement, cohort, producer/consumer removal, data/schema deletion, another lane, or Stage 6 is authorized by this acceptance.

- [x] **S5-QD-C8-R1-ELIGIBILITY-PRECAPTURE · Six non-calendar eligibility rows recorded** — QDarte API commit `760f0ba` records current API/admin/facade/source/image/config identities, healthy draining/paused/no-worker/no-gateway topology, the exact ten byte-identical direct/package/domain full-row oracles, zero active direct jobs and running attempts, SQL 0.1.5 profile/capabilities/IAM, and a successful official-client exact-ID read under an in-memory read-only service token. The source/log/manual-client sweep confirms one deployed admin caller and no unclassified live request; its direct response/list/cancel behavior and the intended admission/exact-ID/operator-only disposition are explicit. Immutable API/admin/ordinary-worker/facade/closed-worker/gateway rollback artifacts exist. The reinstalled 03:15 LaunchAgent is loaded but still reports `runs=0`; the manual C7 backup cannot substitute, so implementation remains blocked until the 2026-07-23 natural run and checksum/copy/object-store/retention proof. No source, config, IAM, queue, service, job, database, worker, gateway, or production setting changed.

- [x] **S5-QD-C8-R1-ELIGIBILITY-WAIVER · Calendar-only backup gate waived by owner** — the owner explicitly authorized C8-R1 to proceed without waiting for the 2026-07-23 03:15 run. The waiver does not pretend that run happened: manual set `20260722-203544` contains API, package contact, Intake, and globals artifacts in primary and Server87-copy locations; every checksum passes and checksum manifests match byte-for-byte. The latest object-store proof remains natural run `20260722-061506`. This bounded exception opens implementation only; the next natural scheduler/object-store/retention result remains mandatory C8-AUDIT evidence, and a failed natural run stops production enablement. No caller, source, config, IAM, queue, service, job, database, worker, gateway, or production setting changed in this docs-first decision.

- [x] **S5-QD-C8-R1-SOURCE · Disabled-first caller/status floor implemented** — QDarte API `accf7ba` adds a server-disabled bounded admission caller, fixed `qdarte.contact_verify.scope` concurrency key, combined enqueue+read token, safe exact-ID projection, one hiding posture, and readiness endpoint; admin `7862698` replaces direct list/cancel coupling with exact-ID status and a per-scope browser-local job hint; runtime `9072269` adds default-false submission, accepted stage caps, separate caller-token wiring, and manifest-v2 equality checks for `max_depth=1` plus concurrency one while retaining the C7 enqueue-token mapping solely for rollback images. Gates pass at API 238, admin 113, and runtime 1147 with clean linters/type checks/build. A disposable PG18.3 fresh package install proved migrations 0001–0007, exact `verify()`, first apply `created`, second apply `unchanged`, paused queue, zero jobs/admissions, and absent external identities treated as already denied. All branches are pushed but non-deploying; no production config, IAM, queue, service, job, worker, gateway, or direct path changed.

- [x] **S5-QD-C8-R1-DEPLOYMENT-PACKET · Disabled-first choreography made executable** — runtime `6841f34` adds the operator runbook and example environment for the exact API/admin/runtime candidates. It freezes the combined-token scope, false-before-build submission gate, draining deployment, manifest-v2 convergence while paused, private-facade proof, next-natural-backup stop, bounded worker/gateway start order, one-entity evidence, and inverse safe rollback. Read-only SMB inspection confirms Mini87's current C7 checkouts are ancestors of the candidates, but new network connections to the host currently fail with `No route to host`; production was not edited over SMB and remains untouched pending a verifiable remote session.

- [x] **S5-QD-C8-R1-LOCAL-READINESS · Disposable local cutover gate complete** — API `900449c` and runtime `632139f` add a self-cleaning one-command PostgreSQL 18.3 rehearsal that never reads the copied QDarte database and never calls a provider. It applies taskq 0001–0007, verifies the exact non-superuser facade wall and bounded queue controls, boots the real private facade, enters through QDarte's retained cutover route, proves disabled 503 before admission, then `created -> existed -> succeeded` through the actual admission adapter and closed worker with one deterministic verifier/effect. Exact-ID status, read-only admission denial, one-job/one-admission/one-attempt conservation, and paused zero-DML rollback all pass. Two consecutive fresh-container executions produced the same structural ledger; the wrapper removed every container afterward. Runtime 1147, workers 628, admin 113, and API C8/contact 128 pass with their lint/type/build gates. This completes the owner-requested local production-test readiness but does not claim a production deployment, real provider call, natural backup, C8-R1 acceptance, or direct retirement.

- [x] **S5-QD-C8-R1 · SUPERSEDED by full replacement** — the source/local-readiness work remains evidence, but the contact-only caller floor and production choreography are not an active destination. FR-03..08 replace them without a compatibility mode or second ledger.

- [x] **S5-QD-C8-R2 · SUPERSEDED by full replacement** — a contact-only producer observation would preserve the broader old queue. FR-04 migrates all lanes without dual publication and FR-06 deletes the complete old producer surface.

- [x] **S5-QD-C8-R3 · SUPERSEDED by full replacement** — preserving unrelated old consumers and the shared execution ledger conflicts with the adopted end state. FR-04/06 replace and remove the entire fleet; FR-07 drops the execution schema without row migration.

- [x] **S5-QD-C8-AUDIT · SUPERSEDED by FR-AUDIT** — the narrower contact audit cannot declare the adopted architecture complete. Its useful oracles feed FR-AUDIT, which additionally requires every lane, clean fresh/contraction database paths, and zero executable legacy surface.

- [x] **S5-AR-RELEASE-A6-PREP · Admission release candidate frozen** — package version `0.1.0a6` is prepared from the Round-13-accepted source and carries Protocol 1.0.8, SQL contract 0.1.5, immutable migrations 0001–0007, trusted reporter support, and the complete typed admission surface. Root status/layout docs now match the accepted repository. Publication requires green CI at this exact release-prep commit before annotated tag and immutable wheel/sdist upload; no QDarte pin, database migration, host, production, or provider action occurs in this prep task.

- [x] **S5-AR-RELEASE-A6 · Immutable admission alpha published** — annotated tag `v0.1.0a6` peels to release-prep commit `c2f6827`, whose complete CI run `29921600639` is green across both PostgreSQL majors, both Python versions, artifact/isolation, race, migration, audit, and lint lanes. The published wheel SHA-256 is `a731a6dc69e7346e2069ea9ac71257bf832be6e73bd4a2d01d709fd82d0d5419`; the sdist SHA-256 is `44a1ea77f8b189c955c1274f862e9c04c2c1f7ceea24160d15fee181b03d1df6`. Both release assets were redownloaded and matched byte-for-byte; the wheel declares `0.1.0a6` and contains immutable migrations 0001–0007. This opens only the isolated exact-hash QDarte repin and C6-03 proof; no production migration, existing queue, provider, retirement, C6-04, or Stage 6 action is authorized.

- [x] **S4-POST-L1-SPEC · Legacy-tools retirement eligibility frozen** — amended the Tier-3 retirement plan to close Round-8 R8-02/03/05 before observation starts: `TASKQ_TOOLS_ALLOWLIST` remains an enrollment gate after `TASKQ_TOOLS_MODE` removal; disabled, not-ready, and registered non-allowlisted tools share the exact fail-closed `503 {"detail":"Queued task processing is unavailable"}` response and never enqueue legacy work; `umami` uses a target access-log counter while the read-only flight lane's host-counter/taskq reconciliation is explicitly non-independent; and the retired 200 response now has an explicit caller-sweep gate. L2 owns the restricted-runtime proof rewrite and compatible settings/documentation update. No host source, taskq SQL/wire/IAM/capability, deployment, database, or producer/consumer behavior changed.

- [ ] **S4-POST-L1 · Seven-day tools-retirement eligibility observation** — Day 0 opened in host commit `b7cda5c`: the authoritative production API is healthy and in taskq mode for `umami,aerolineas`; the frozen legacy oracle is count 2 / max `2026-07-20 11:37:34.886547` / zero active rows. The runtime login's denied raw `taskq.jobs` read is retained as capability evidence, so taskq observations use the authorized canonical read plus the audit oracle. Six further consecutive days, two normal authoritative-host deploys, lane invocation reconciliations, and final caller attestation remain; any legacy insert resets the window. Stop for targeted independent acceptance before S4-POST-L2; no producer or consumer removal is authorized in this task.

## Later

- [x] **S5-QD-P0 · QDarte local-first pilot design frozen** — added the Tier-3 [Stage 5 QDarte Pilot Specification](docs/Task%20Queue%20Stage%205%20QDarte%20Pilot%20Specification.md) after a source-backed audit of QDarte's API-owned worker ledger, HTTP worker fleet, shared registry, and isolated compose smoke. It selects a separate `qdarte_pilot` queue and a non-chaining adapter over deterministic empty-input `cluster_research_scope`; no existing QDarte queue row, content/provider/browser/writeback lane, or production stack participates. It fixes exact a3 bridge pinning, owner-only 0001–0005 local provisioning, capability-sized runtime/worker identities, pure shadow digest, keyed canary, response-loss/local hard-kill recovery, and zero-DML disablement, while preserving the future side-effecting hard-kill gate. Targeted review required before source/local DB/IAM/worker/compose change.

- [x] **S5-QD-REVIEW-REQUEST · QDarte pilot targeted review assembled** — [Round 11 request](docs/design-review-11/REQUEST.md) requires independent derivation from the current QDarte source baseline rather than the potentially stale local clones. It attacks the legacy `qdarte_ops` isolation boundary, a3/0001–0005 bridge, identities and connection arithmetic, pure handler claim, keyed/replay/hard-kill oracles, compose isolation, and zero-DML disablement. It authorizes no source, local DB/IAM, worker, compose, deployment, production, existing-lane, side-effecting, retirement, or Stage-6 change.

- [x] **S5-QD-REVIEW-RESPONSE · QDarte pilot targeted review accepted** — registered the immutable Round-11 response verbatim. It independently confirms a3 is the correct route-free 0001–0005 bridge and source-confirms the pilot handler's pure deterministic path and the structural separation from `qdarte_ops`. R11-01..04 land docs-first in this commit: a dedicated non-superuser facade DSN/pool, fixed synthetic payload, explicit legacy closed-literal/shared-registry non-touch stop, and a six-table count/max-id/max-updated-at drift oracle. READY opens P0–P5 in isolated disposable `qdarte-dev` only; production, Mac-mini/cloud, existing-lane migration/retirement, external effect, chaining, UI/read models, and Stage 6 remain closed.

- [x] **S5-QD-P0 · QDarte isolated-dev preflight accepted** — the amended baseline is intentionally QDarte-only: guarded PG18/Redis/qdarteAPI/MinIO health plus the pure no-network `cluster_research_scope` drill. `intake-worker` and the broad multi-worker smoke are excluded because their non-pilot lanes have un-sandboxed egress/storage/write effects; it was never started. The guarded local PostgreSQL is 18.4 with `max_connections=100`, and the API currently uses the `postgres` superuser, confirming R11-01’s dedicated-facade-role requirement. The pure drill passed while its worker was temporarily narrowed; because cleanup restored the broad legacy allowlist, it was stopped immediately. QP-09 now uses a stable complete-row digest as the six-table mutation oracle (with high-waters diagnostic only). P1 must use current-source local checkouts, a distinct non-superuser facade DSN/pool, and a pilot-only worker allowlist fixed by construction.

- [x] **S5-QD-P0B · QDarte direct-queue disposition frozen** — S5-QD-CQ-01 is resolved as Option B, not a cleanup or compatibility project: the current QDarte direct-SQL contact-verify queue remains untouched in `qdarteapi_dev`; the package pilot owns only a newly created disposable `qdarte_pilot_dev` database on the same guarded local cluster. The fixed `taskq` schema therefore remains package-owned within its database, without a schema/catalog overlap or a renamed schema. Round 11's safety findings remain binding, but its greenfield/no-collision inventory is superseded by current staging source. P0B's targeted re-check confirms the old schema is confined to `qdarteapi_dev` and the pilot database is absent until P2; it creates no database, role, queue, IAM, migration, worker, or source change.

- [x] **S5-QD-HOST-GATE-01 · QDarte fresh migration baseline** — repaired the incumbent source migration narrowly: `20260715_0070_host_native_worker_lanes` accepts only the inherited fresh-chain `media=1` seed created by revisions 0044/0053, then performs its existing normalization to six zero-desired host-native lanes; every other nonzero state remains fail-closed and requires explicit scale-down. A newly created disposable `qdarteapi_p1_test` reached 0075 and showed that exact lane posture. This is not a package migration, created no `qdarte_pilot_dev`, and left QDarte's incumbent direct queue untouched.

- [x] **S5-QD-P1 · QDarte disabled host boundary** — exact a3 wheel URL/SHA pins now land separately in fresh API and worker worktrees. The API's optional, disabled-by-default mount accepts only a development `postgresql+asyncpg` DSN for `qdarte_pilot_dev` under a dedicated non-superuser login, uses its own one-connection runtime pool, and never falls back to the incumbent API DSN; disabled boot leaves the facade unmounted and opens no pilot-database connection. The worker has no process or handler yet, but its future pilot configuration has an immutable one-item allowlist: `qdarte.cluster_research.pilot` on queue `qdarte_pilot`; it cannot inherit or widen the broad legacy worker allowlist. Focused API/worker tests, Ruff, format, and MyPy pass. The direct contact-verify queue and copied `/ops/taskq`/`/worker/taskq` surface are untouched; no pilot database, IAM, public producer, or worker started. P2 only may create/provision `qdarte_pilot_dev`.

- [x] **S5-QD-P2 · QDarte isolated pilot provisioning** — created only the disposable local `qdarte_pilot_dev` database on guarded PG18.4; immutable package migrations 0001–0005 and two `verify()` runs passed. A one-queue `qdarte_pilot` profile was created through a distinct local operator login. The facade login has only producer/runner/observer/housekeeper membership and independently failed `SET ROLE taskq_operator`, `ensure_queue`, direct job reads, and role creation. QDarte's real service-token signer/verifier proved exact `read`/`run` scope behavior; its `outlabs_auth` schema was read only, with a byte-identical pre/post canonical digest (`1e6d6523…139ff878`) and no catalog record added. No worker, facade boot, public producer, incumbent queue/schema access, or production action occurred.

- [x] **S5-QD-P3 · QDarte deterministic pilot adapter** — factored the incumbent calculation into a side-effect-free `compute_cluster_research_scope(payload)` and registered only `qdarte.cluster_research.pilot` in a closed package registry. The frozen AR synthetic input rejects every alternate shape; its bounded taskq-only output has no payload echo/followups and pins the inherited-result digest `14b7f6ef…63d4971`. Focused source tests prove it remains absent from QDarte’s legacy handler map and default `JobType` set. No worker, producer, HTTP client, database connection, queue claim, legacy enqueue, QDarte domain write, or shared-registry mutation occurred.

- [x] **S5-QD-P4 · QDarte isolated worker canary** — a3’s conforming omitted-nullable projection exposed a client decode defect, corrected only in immutable exact-a3-baseline release `v0.1.0a3.post1` (`sha256:bbf5c1fa…6764aecf`); the broader read-model `v0.1.0a4` remains deliberately unadopted. Both QDarte components exact-repinned the post release. The local-only harness issued distinct environment-only one-day `enqueue`/`read`/`run` self-contained credentials, proving cross-action and wrong-queue denial; the run credential only bootstrapped metadata and drove fixed worker `qdarte-pilot-p4-002`. Key `qdarte-pilot:p4-canary-20260721-002` returned `created` then `existed` for `019f8651-966e-7492-8a0e-5668defb33b5`; canonical authorized `read` reached `succeeded`, with exactly one succeeded raw attempt, three events, and zero failures/releases/expiry. Ordered full-row digests of six `qdarte_ops` legacy-ledger tables were byte-identical before/after. The local facade/worker were stopped cleanly and their command lines contained no token. P5 later closed replay/hard-kill recovery; no public producer, broad worker, or side-effecting lane is authorized.

- [x] **S5-QD-P5 · QDarte isolated recovery and rollback** — local evidence proves both recovery obligations through the real mounted facade in disposable `qdarte_pilot_dev`: a committed-response-loss drill replayed the original settlement (`handler_calls=1`, `complete_calls=2`) and reached one successful attempt; then a held pure handler was frozen for six seconds past its five-second soft-stop grace and force-killed without a release. Its actual 15-second lease expired, after which normal poll-driven micro-reap reclaimed the same job id under a second closed worker and reached `succeeded` with two conserved attempts (`expired/lease_expired`, then `succeeded/success`) and events `enqueued=1, claimed=2, lease_expired=1, succeeded=1`. P4's full-row digest of all six protected `qdarte_ops` tables remains byte-identical, the local pilot facade/workers were stopped with no remaining active facade connection, and the isolated API/Postgres/Redis/MinIO stack stayed healthy. The self-contained auth tokens remained process-only; the immediate canonical auth digest was unchanged across teardown. This pure-lane hard kill does not waive a future side-effecting-lane hard-kill gate. Host evidence: `qdarteAPI/docs/taskq-pilot-p5-local-evidence.md`. P5 authorizes no incumbent direct-queue change, cloud/Mac-mini/production target, or broad worker start.

- [x] **S5-QD-CONSOLIDATION-SPEC · QDarte direct-queue convergence proposal frozen** — current QDarte source and read-only local catalog inspection establish that the host-owned direct contact-verify catalog cannot share a database with immutable package `taskq`; it has a public execute grant and no current local jobs. The new Tier-3 consolidation proposal selects a future one-way package migration through a separate package database, keeps direct contact verification authoritative now, bans dual publishing/active-row import/cross-backend fallback, and treats result application plus probe usage as an independently idempotent side-effect boundary. It defines C1–C7 compatibility, preflight, effect, hard-kill, rollback, and production gates. No QDarte source, DB, IAM, worker, route, queue state, deployment, or production behavior changed.

- [x] **S5-QD-CONSOLIDATION-REVIEW · Targeted direct-queue decision review** — Round 12 reconstructed the direct catalog/routes/worker/result path, challenged separate-database and mode-exclusivity claims, and found three docs-first preconditions. The immutable response and targeted delta are recorded: the server-owned result bridge, exact direct-catalog inventory, and effective-base-path matrix close them. READY opens a separate implementation specification only; it authorizes no current QDarte source, database, IAM, worker, route, deployment, provider, retirement, cloud, or production change.

- [x] **S5-QD-CONSOLIDATION-R12-REMEDIATION · Round-12 docs-first closure** — recorded the immutable Round-12 response verbatim and corrected the Tier-3 proposal without changing QDarte source, local databases, IAM, workers, routes, deployments, or production. The incumbent inventory now names thirteen functions and its measured source/live role discrepancy; C1 freezes direct-origin versus `/content-api` proxy joined paths plus authenticated claim/result vectors; and §5.1 fixes the server-owned runner-heartbeat plus observer-projection result bridge, stable job/entity effects, and lost/reclaimed-attempt behavior. Targeted delta acceptance is still required before implementation planning.

- [x] **S5-QD-CONSOLIDATION-IMPLEMENTATION-SPEC · Isolated-local contact-verify sequence frozen** — following Round-12 delta acceptance, added the Tier-3 CV-01..CV-05 sequence: compatibility/base-path evidence first; stable server-owned result bridge and host idempotency before package admission; disposable least-privilege package preflight; one closed-worker controlled effect canary; then response-loss/hard-kill recovery and local rollback. Each slice has an explicit stop condition; no QDarte source, database, IAM, worker, route, deployment, provider, direct-queue, retirement, cloud, or production change occurs in this docs-only task. CV-01 is next.

- [x] **S5-QD-CV-01 · Direct contact compatibility and base-path evidence** — source-backed C1 closes the direct-worker URL ambiguity without starting a worker or touching a database: QDarte worker commit `433b447` proves credential-bearing claim and contact-result requests for both `http://<api-origin>/worker/taskq/...` and `http://<admin-origin>/content-api/worker/taskq/...`; its full suite is 567/567 with Ruff clean. QDarte API’s `/worker/*` permission routing remains covered by 27 focused allowlist vectors, and the existing direct catalog/role/grant/high-water inventory remains the read-only Round-12 record. No QDarte queue, auth record, route, deployment, provider, or production state changed. CV-02 may implement host-side stable result idempotency.

- [x] **S5-QD-CV-02 · Stable package result bridge and domain ledger** — QDarte API commit `d883371` adds the additive `qdarte_ops.contact_verify_result_applications` ledger keyed by `(job_id, entity_key)`, so reservation, place/contact writes, and monthly usage consumption share one transaction and a reclaimed attempt cannot reapply the effect. Its fresh disposable full migration chain creates the exact primary key/index and is dropped afterward. The new server-owned runner/observer bridge heartbeats before authoritative payload validation, rejects lost/cancelled/wrong-queue/unplanned results before a domain write, and never settles for the worker. Focused tests are 41/41, Ruff is clean, and changed-file MyPy is clean; the unconfigured repository-wide MyPy invocation still reports unrelated baseline debt. Nothing is mounted, provisioned, or enabled; CV-03 alone may bind this component to the new disposable package runtime.

- [x] **S5-QD-CV-03-DESIGN · Contact preflight topology frozen** — CV-03 now fixes the disposable package database as `qdarte_contact_verify_dev`, the one package queue/type as `qdarte_contact_verify` / `qdarte.contact_verify.scope`, and the local-only result adapter as `POST /internal/taskq/contact-verify/jobs/{job_id}/results`. The normal QDarte application never mounts `/taskq` or a generic package producer route; only the checked-in local harness has the package facade, and it has no enqueue credential before CV-04. These names must not enter the incumbent direct client, worker map, or copied `/worker/taskq/*` API. This docs-first boundary authorizes only the CV-03 source/preflight work that follows.

- [x] **S5-QD-CV-03 · Isolated contact package preflight** — QDarte API commits `1b01f24` and `d19b7dd` add only the disabled local contact harness and its evidence record. The harness accepts solely a development `postgresql+asyncpg` DSN for disposable `qdarte_contact_verify_dev`, rejects the incumbent/pilot databases, superuser, non-development, and simultaneous-pilot configurations, and leaves the normal QDarte application without `/taskq` or a package result route. On guarded PG18.4 (`max_connections=100`), immutable a3.post1 migrations `0001`–`0005` and two owner `verify()` passes succeeded; the distinct operator provisioned only `qdarte_contact_verify`. The dedicated facade login has producer/runner/observer/housekeeper roles and proved denial of operator assumption/administration, base-job reads, role/database creation, and RLS bypass; the real harness lifecycle used one package connection against usable budget 80 (headroom 79) and closed it cleanly. The queue has zero jobs, attempts, workers, and events. Focused host tests are 26/26 with Ruff/format and changed-file MyPy clean. No worker, enqueue credential, provider, direct-queue, deployment, or production action occurred. A harmless stray empty profile was accidentally added to retained `qdarte_pilot_dev` during signature inspection and is explicitly documented for separately authorized cleanup; it did not touch the contact or direct databases. CV-04 alone may issue an ephemeral local harness enqueue credential and start the closed contact worker.

- [x] **S5-QD-CV-04A · Trusted effect reporter kernel** — ADR-022’s worker extension is now implemented without exposing a fence through `JobContext`: a handler gets only bounded async `report_effect()` (8KB JSON object), while the optional runtime-owned reporter alone receives an immutable active-attempt record. The supervisor rejects reports after cancellation/ownership loss/settlement, replays the identical report on retryable response loss under its existing bounded backoff, and remains the sole terminal-settlement owner. Deterministic regressions cover absent-reporter/cancellation bounds, handler fence absence, exact active-attempt identity, response-loss replay, ownership loss, and normal completion (27 focused worker tests; 304 DB-free tests, Ruff, and format clean). This is Python-only: no SQL, migration, wire, facade, client, or package runtime credential changed. CV-04 still needs a closed QDarte reporter/worker and controlled local canary.

- [x] **S5-QD-CV-04B-DESIGN · Reporter-side effect sequence frozen** — ADR-022’s QDarte use is now precise: the existing private local result path accepts only reporter-owned `inspect` (current-attempt plus authoritative-plan validation → `pending` or stable committed domain result) and `apply` (the same validation → one idempotent application). A closed handler asks `inspect` before any provider call and skips an already committed result; the trusted reporter, never the handler, supplies/retries the active attempt and never settles. This is a Tier-3 local-harness clarification only: no public route, producer, direct-worker reuse, database credential, wire-contract, SQL, migration, or current package/database state changes.

- [x] **S5-QD-CV-04C · Trusted-reporter pre-release** — immutable `v0.1.0a5` targets `4652cdf` and publishes wheel SHA-256 `a667bf53aefc743c6fdaf9aaaa9509a590276d6d812d1dea9c34999268d57d49` (sdist `f5ac7822…94ec6d32`). It contains the ADR-022 reporter kernel and no SQL-contract, migration, Protocol-v1, facade-route, or generated-client change; its installed core artifact smoke passes outside the checkout. QDarte must exact-pin this wheel before it configures the closed contact reporter/worker; no local package contact database migration or canary is implied by this release.

- [x] **S5-QD-CV-04D · QDarte reporter result adapter** — QDarte API commit `6b9e263` turns the one private local result path into the frozen closed reporter union: `inspect` reuses runner-heartbeat and authoritative payload/entity validation before it reads the stable domain-effect ledger, returning only `pending` or its bounded committed response; `apply` repeats validation before the existing idempotent application. A stale/lost attempt cannot read the ledger, the handler never selects queue/type/place authority, and neither operation settles a package job. Focused bridge/harness/idempotency tests are 20/20 with Ruff clean. The ordinary app remains unmounted; no worker, enqueue credential, package database mutation, direct queue, provider, deployment, or production state changed. The next CV-04 increment exact-pins a5 and adds the one closed HTTP reporter/worker.

- [x] **S5-QD-CV-04E · Closed contact package worker** — QDarte workers commit `605828c` adds a separate async package registry containing only `qdarte.contact_verify.scope` on `qdarte_contact_verify`. Its loopback-only reporter holds the process-local run token, sends only the frozen private `inspect`/`apply` requests, and overwrites nested/top-level attempt identity from the runtime-owned `WorkerEffectAttempt`; the handler cannot supply a fence or use a database/direct-worker client. It probes the stable effect before a provider call and skips a committed result. Focused worker tests (16/16) prove the closed registry, loopback/token guard, and reporter identity binding. No process was started, no enqueue credential was issued, and no provider/direct queue/database/deployment/production action occurred. CV-04 now needs its explicit service configuration and single controlled local canary.

- [x] **S5-QD-CV-04F · Controlled local contact canary** — QDarte API commits `de25515` and `77f6d82` close the safe first-canary blocker: the local normal-QDarte DSN is explicit for the harness, checked-in migration `20260721_0076` supplies the CV-02 result ledger, and the private reporter route now binds its run authorization as a real FastAPI dependency rather than an accidental required query parameter (19/19 focused DB-free boundary/token/bridge tests, Ruff, and format clean). A final loopback-only worker with its fixed one package queue/type proved cross-action and wrong-queue 403s, then keyed `qdarte-contact:cv04-canary-20260721-007` converged `created` → `existed` and an authorized canonical read reached `succeeded`. Raw package state has one succeeded attempt and zero failure/release/expiry events; the QDarte stable-effect oracle has exactly one application, contact-method, and probe-usage effect. All incumbent direct `taskq` tables remain zero-row, and protected legacy-table counts/latest-write bounds remain unchanged from the pre-canary baseline. Harness and worker stopped; failed diagnostics remain auditable package history. Evidence: `qdarteAPI/docs/taskq-contact-cv04-local-evidence.md`. CV-05 alone may run the separately bounded response-loss and hard-kill recovery drills; no production, broad worker, retirement, or non-contact action is authorized.

- [x] **S5-QD-CV-05 · Contact side-effect recovery and local rollback** — QDarte workers commit `abeaac1` adds a local-only recovery harness over the same closed contact registry: it drops only the first post-commit `apply` response, or holds only after a committed apply for an explicit force-kill. Focused recovery/contact tests are 10/10 with Ruff and format clean. In guarded local `qdarte-dev`, the response-loss job replayed exactly two identical apply reports yet finished with one succeeded attempt and one stable effect row. A separate held job was hard-killed after apply commit; its real lease expired, a different closed worker reclaimed the same ID, `inspect` returned the durable effect, and the final result recorded `replayed_entities=1`, `completed_entities=0`. Its raw attempts are `expired/lease_expired` then `succeeded/success`, with exactly one effect row and no release. The current probe usage counter is three across CV-04/CV-05’s three controlled provider calls, and every incumbent direct `taskq` table remains zero-row. Harness/workers stopped; rollback remains zero-DML and never recreates a direct job. Evidence: `qdarteAPI/docs/taskq-contact-cv05-local-evidence.md`. This completes isolated-local CV-01..CV-05 evidence only; the direct lane remains authoritative and C6/C7, production, retirement, broad workers, and non-contact work remain separately gated.

- [x] **S5-QD-C6-SPEC · Contact compatibility and cutover sequence frozen** — added the Tier-3 [C6/C7 Compatibility and Cutover Specification](docs/Task%20Queue%20Stage%205%20QDarte%20Contact%20Verify%20Compatibility%20and%20Cutover%20Specification.md) after CV-05. It sequences C6-00 inventory, closed `legacy`/`draining`/`package` modes, direct-drain/package-admission interlock, caller-compatible scoped adapter, and three no-row-copy rollback exercises into a targeted C6 acceptance. It also boards C7’s later environment/preflight/cohort/two-cycle/audit gates, each still separately authorized. This docs-only task changes no QDarte source, direct or package database, IAM, route, worker, provider, deployment, production configuration, or retirement behavior. C6-00 alone is next.

- [x] **S5-QD-C6-00 · Direct contact compatibility ledger and high-water baseline** — the Tier-3 [Compatibility Ledger](docs/Task%20Queue%20Stage%205%20QDarte%20Contact%20Verify%20Compatibility%20Ledger.md) records the exact QDarte API/worker source revisions, router authorization, request/response shapes, direct worker/result paths, and the current route-level compatibility delta: `/ops/cutover/...` still selects the incumbent legacy versus host-owned direct-taskq backend at request time, so it cannot be silently carried into the frozen closed `legacy`/`draining`/`package` model. A guarded read-only `qdarte-dev` observation confirmed the durable database identity is development and the direct `contact_verify_scope` lane has zero jobs, active rows, attempts, and events. No QDarte source, queue row, database, credential, worker, provider, deployment, or production state changed. C6-01 alone may design the closed-mode/no-fallback local implementation.

- [x] **S5-QD-C6-01-DESIGN · Contact package mode boundary frozen** — C6-01 now assigns package contact selection to one new exact `QDARTE_CONTACT_VERIFY_MODE` setting, defaulting only to `legacy` and accepting only `legacy`/`draining`/`package`; it cannot read or reinterpret the incumbent `QDARTE_TASKQ_*` staging switch. Invalid/mixed values fail startup, `draining` is a fixed safe refusal, and `package` remains unavailable until the later drain-attestation and scoped-adapter slices exist. The legacy `/ops/cutover` route discriminator is explicitly owned by C6-03 rather than preserved accidentally. This docs-only decision changes no QDarte source, queue state, credential, worker, package admission, provider, deployment, or production behavior. C6-01 implementation may now add the parser and no-producer refusal vectors locally.

- [x] **S5-QD-C6-01 · Closed local contact mode boundary** — QDarte API commit `1379f3f` installs the exact contact-only mode parser and validates it before authentication initializes: `legacy` is the sole default; malformed/mixed values reject boot; `draining` cannot combine with the incumbent contact selector and returns only the fixed host-owned 503 before either producer is constructed; and grammar-valid `package` is deliberately rejected at startup until C6-02 drain evidence and C6-03’s scoped adapter exist. The `/ops/cutover/...` contact route now ignores the incumbent `QDARTE_TASKQ_*` selector and dispatches direct legacy only in `legacy`, eliminating request-time fallback to the host-owned direct-taskq backend. Focused config/route/lifespan vectors are 41/41 with changed-file Ruff/format and MyPy clean; the broader taskq-related host suite is 199/199 with 12 pre-existing opt-in skips when its documented test-only token variables are present. An ad-hoc full host run still has unrelated baseline environment/migration/media-root and repository-wide formatter drift, recorded without changing them. No local service was started, and no QDarte database, queue row, package admission, worker, provider, deployment, credential, or production state changed. C6-02 alone may implement the fresh direct-drain/package-admission interlock.

- [x] **S5-QD-C6-02-DESIGN · Direct-drain interlock mechanics frozen** — C6-02 uses no mutable flag, queue-table write, or reusable evidence file: a process-owned opaque attestation is issued only in `draining` mode for one named local exercise, verified development database identity, and source revision. It requires two direct-only observations 1–60 seconds apart with no active/leased contact work and equal job/attempt/event counts and high-waters; every later package admission must re-observe the same posture and expires within five minutes. Restart, tampering, mode/identity change, or any direct insert invalidates and evicts it. No QDarte source, database, queue, worker, provider, deployment, credential, or production state changes in this docs-first decision. C6-02 implementation may add only the local direct-ledger observer and refusal vectors; package admission remains unavailable until C6-03.

- [x] **S5-QD-C6-02 · Direct drain and package-admission interlock** — QDarte API commit `145ca1a` adds a route-free, write-free observer over only the incumbent `qdarte_ops` contact job/attempt/event ledger and a process-owned opaque attestation registry. It issues only in explicit `draining` mode after two stable bounded observations, is bound to one development database identity/exercise/source revision, expires within five minutes, vanishes on restart, and re-observes before every future package admission. The six new vectors reject active work, bad bounds, forged/expired records, cross-mode use, a direct insertion during observation, and a direct insertion after issuance; the query assertion proves no package table or payload read. Focused C6/config/route/lifespan vectors are 48/48 with Ruff, format, and changed-file MyPy clean under the documented unreachable integration DSN. No service was started, no direct producer was disabled in a lasting environment, no package job/route/database write/worker/provider/deployment/credential/production action occurred. C6-03 may use only the resolved same-process lifecycle; it must not weaken the process-owned proof or add a fallback.

- [x] **S5-QD-C6-03A · Canonical admission boundary and local package controller** — QDarte API commit `c0940fb` replaces the retiring `/ops/cutover/jobs/contact-verify-scope` backend discriminator with the frozen opaque canonical admission (`job_id`, `created | existed`, supplied-or-derived key, bounded planned count). Its direct path derives the same key before admission and refuses a differently keyed active scope rather than disguising coalescing. A configured development-only package process validates loopback/token/exercise/revision inputs, begins internally in draining, earns the C6-02 proof in its own lifespan, and re-observes before each one-way keyed HTTP admission to the isolated contact facade. Package failure maps to the fixed host 503 and never constructs the direct producer; `/ops/taskq/*` and `/worker/taskq/*` remain untouched. Focused config/route/drain/controller/adapter vectors are 57/57 with Ruff, format, and changed-file MyPy clean under the documented unreachable integration DSN. No service, database, queue, worker, provider, credential, deployment, or production action occurred. C6-03 still requires its isolated local created/existed and raw-ledger evidence before C6-04 can open.

- [x] **S5-RM-DESIGN · H-08/H-11 read-model activation proposal prepared** — added the Tier-3 [Read Model Specification](docs/Task%20Queue%20Read%20Model%20Specification.md): queue-scoped finite `ready|running|finished` keyset pages; fixed safe job projection; observer-safe queue profile; and a real version/ETag conditional-update path that preserves bootstrap `ensure_queue`. It names the docs-first ADR/Protocol/Manifest/migration sequence plus PG16/PG18 B9, SQL/HTTP parity, redaction, authorization, pagination, and conflict evidence. It changes no current contract, SQL, host, UI, producer, consumer, or L1 observation behavior; both deferred routes remain `TQ501` pending ADR acceptance.

- [x] **S5-RM-ADR · H-08/H-11 contract reactivation accepted docs-first** — ADR-019 accepts Protocol v1 document revision 1.0.5, Function Manifest / SQL contract 0.1.3, and migration `0004_read_models.sql` identity before implementation. It fixes the 13-field queue-scoped job page, three independently gated views with explicit `TQ501` fallback, observer-safe versioned queue profile, ETag/`If-Match` matrix, `TQ409 profile_version_conflict` carrying only `current_version`, and direct-SQL/HTTP projection parity. R5-29 is closed by this package. No SQL, migration, generated client, facade, host, or L1 observation behavior changes in this docs-only task.

- [x] **S5-RM-01A · SQL-contract bridge runtime** — ADR-020's closed membership check replaces the sole exact startup pin: `TaskqRuntime.start()` accepts only `0.1.2` or `0.1.3`, while a preserved simulated pre-bridge `{0.1.2}` pin rejects `0.1.3` with the same typed version error/details. The bridge adds no read-model capability, generated command, facade route, client call, or 0004 function call; CLI/verify, metadata clients, and host preflight retain their exact reporting/verification roles. No migration or production database action occurred.

- [x] **S5-RM-01B · Read-model migration and catalog parity** — immutable `0004_read_models.sql` adds `profile_version`, the four manifest composites, hardened observer `list_jobs`/`get_queue_profile`, and operator `update_queue_profile`; all H-08 capabilities remain inactive and no B9 index is introduced. The machine manifest, `verify()`, parity/grant/error ledgers, fresh install, and full 0001→0004 upgrade chain now assert the 16-column queue shape, 43-function catalog, 0.1.3 metadata, grants, and exact inactive `TQ501` disposition. Fresh direct-SQL vectors prove profile create/unchanged/update/conflict and no observer base-table read. Full suites pass on PG18.3 and a disposable exact PG16.14 container (457 passed, 1 opt-in skip each); production migration, generated HTTP/client work, and B9 activation remain out of scope.

- [x] **S5-RM-02A · Generated SQL read-model transport** — added the H-08/H-11 typed domain models and the three manifest-backed commands to the same generated SQL transport ledger: observer `list_jobs`/`get_queue_profile` and operator `update_queue_profile`. The SQL transport remains capability-sized and decodes only the fixed composites; the closed registry and observer capability-surface oracle now pin all 33 commands and Protocol 1.0.6. No HTTP route, official HTTP-client method, capability activation, index, production migration, or B9 claim occurs in this increment.

- [x] **S5-RM-02B · Read-model facade, HTTP clients, parity, B9, and ready activation** — Protocol 1.0.7’s generated `GET /queues/{queue}` and `GET /jobs?queue=&view=` identities mount in the facade and both official clients. The dispatcher authenticates then queue-authorizes the query queue before cursor decoding; queue profile GET stays flat with ETag while conditional PUT returns only the canonical `{"profile": {...}}` envelope plus ETag. The independent live parity vector runs the `ready` page through direct SQL and mounted ASGI HTTP, then checks every projection field against `taskq.jobs`. Immutable metadata-only 0006, justified by B9 evidence `7fe2c6b`, activates exactly `read_model_list_ready`; its 0005→0006 transition and exact `verify()` posture run on PostgreSQL 16/18. `running` and `finished` remain structurally rejected by B9. Stop for targeted review before host adoption.

- [x] **S5-RM-REVIEW-REQUEST · Targeted read-model review gate assembled** — Round 9 pins the complete `7826cbc..c1fac41` range and demands independent contract/catalog derivation, 0004→0006 immutability and upgrade evidence, exact ready-only capability verification, SQL/HTTP/auth/cursor/redaction parity, profile ETag conformance, and B9 plan evidence on both PostgreSQL majors. The request authorizes neither host adoption nor production migration; its immutable response decides whether a separately specified adoption slice may open.

- [x] **S5-RM-REVIEW-RESPONSE · Round 9 recorded BLOCKED** — immutable Tier-4 response identifies R9-01..05: conditional PUT unknown-queue conformance, stale opt-in B9 assertion, formatter/CI publication, missing wire vectors, and inactive-view details. No contract question or architectural redesign is required; a narrow remediation range followed by targeted delta review remains the only path to a later host-adoption decision.

- [x] **S5-RM-R9-01-05 · Read-model error conformance** — conditional profile PUT now maps the SQL NULL composite for an authorized missing queue to typed `TQ001`/404 instead of crashing while decoding `current_version`; the generated SQL/protocol error ledgers record the existing Tier-0 missing-queue outcome. Inactive list views retain typed `TQ501` and the facade supplies only the contracted safe `reason` + requested `view` details, never SQL text. Direct transport and mounted-wire regressions pin both paths.

- [x] **S5-RM-R9-02 · B9 post-activation gate corrected** — the opt-in million-row plan gate now asserts the immutable 0006 post-state exactly (`read_model_list_ready` only), alongside its existing ready-plan and rejected-view structural assertions. It no longer freezes the superseded all-inactive metadata posture.

- [x] **S5-RM-R9-04 · Read-model wire evidence completed** — mounted-facade vectors cover list success and cursor pagination, malformed/foreign/oversized/duplicate cursor/query rejection, stale `If-Match` TQ409 with current-version-only details, weak ETag rejection, and the version-bearing canonical PUT envelope. The official async client decodes that published `{"profile": {...}}` shape with `profile_version` intact, satisfying S5-CQ-02’s standing compatibility condition.

- [x] **S5-RM-R9-03A · Formatter drift repaired** — applied the repository-pinned Ruff formatter to the two range-owned files identified by Round 9 (`http/facade.py` and `test_s3_facade.py`); `ruff format --check .` and `ruff check .` are clean. Publication/CI remains coupled to the final remediation range after all evidence gates pass.

- [x] **S5-RM-R9-03B · Artifact migration ledger repaired** — the installed-wheel/sdist smoke script now asserts the complete immutable 0001→0006 migration chain and the current 43-function catalog rather than the historical 0001→0003 / 40-function state. Fresh core, HTTP, and OutLabs artifact installs pass outside the checkout; the corrected range is republished for CI.

- [x] **S5-RM-R9-DELTA-REQUEST · Targeted remediation review assembled** — pins the published `8b1547a..1610b5a` delta and the immutable Round-9 response hash. It requires only R9-01/02/03/04/05 evidence: missing-queue PUT, safe inactive-view details, post-0006 B9 state on PostgreSQL 16/18, full mounted-wire/client vectors, and published CI/artifact/formatter proof. It cannot authorize host adoption, production migration, further activation, UI work, retirement, or Stage 5.

- [x] **S5-RM-R9-DELTA-RESPONSE · Read-model slice independently accepted** — immutable delta response accepts all R9 remediation: typed unknown-queue profile PUT, safe inactive-view details, exact post-0006 B9 state, mounted wire/client regressions, formatting, artifact ledger, and published CI. Its dual-PG full-chain rerun records 469 passed with 1 opt-in skip on each major and zero Contract questions. Only a future separately specified host-adoption decision for the already-active `ready` view may open; production 0004→0006 migration, further activation, UI, retirement, and Stage 5 remain closed.

- [x] **S5-RM-HOST-00 · First-host read-model adoption frozen** — the new Tier-3 plan resolves the a2→0.1.4 deployment discontinuity without weakening ADR-020: immutable route-free bridge artifact `a3` deploys before 0004→0006, becomes the post-migration zero-DML rollback floor, and only then may an immutable full `a4` expose generated `tools` profile/`ready` GET routes. It forbids host-owned read paths, operator/profile-write exposure, production pagination injection, manual metadata DML, and any impact on L1 retirement observation. Round 10 must independently attack the artifact ordering, privilege boundary, rollback rehearsals, and read-only authorization vectors before package, host, or database work begins.

- [x] **S5-RM-HOST-REVIEW-REQUEST · First-host adoption gate assembled** — Round 10 may review only the frozen deployment specification. Its response cannot authorize a release, pin, deployment, production migration, queue/IAM mutation, producer/consumer behavior, UI, retirement, side-effecting lane, or Stage-5 pilot.

- [x] **S5-RM-HOST-REVIEW-RESPONSE · Round 10 recorded BLOCKED** — immutable response accepts the two-artifact architecture, rollback floor, credential/host boundary, and read-only oracle design, but finds the Step-C artifact mismatch: a3 (`40aa9b5`) cannot apply or verify 0006. It requires a docs-only resequencing (a3 applies/validates 0004–0005; a4 applies/validates 0006), an exact a4 base identity, one pre-C backup test-restore, and precise deferred-route evidence. A targeted delta review is the only path to READY; no release, host, database, or production action is authorized.

- [x] **S5-RM-HOST-R10 · Round-10 adoption-plan remediation** — Step C now uses a3 only for immutable 0004→0005, verifies exact 0.1.4 / empty capabilities twice, and requires a backup test-restore to a disposable target. Step D pins a4 to accepted `1610b5a`, applies/verifies immutable 0006 under that exact artifact, then exposes generated `ready` routes; post-D rollback is a4→a3 with the bridge’s typed TQ501 responders. a3 evidence now asserts DEFERRED TQ501, OpenAPI absence, and client-method absence rather than an imprecise “no route.” No artifact, host, database, or production mutation occurred; targeted delta review remains required.

- [x] **S5-RM-HOST-R10-DELTA-REQUEST · Targeted adoption-plan delta assembled** — pins only the Round-10 remediation range and immutable response hash. It must prove a3’s 0004→0005/empty-capability verifier posture, a4’s `1610b5a` provenance and 0006/ready-active posture, the precise deferred bridge response and zero-DML rollback, and the one-time pre-C backup test-restore claim. It cannot authorize any package, host, database, production, UI, retirement, side-effecting, or Stage-5 action.

- [x] **S5-RM-HOST-R10-DELTA-RESPONSE · First-host A→E sequence READY** — immutable targeted response accepts the corrected two-artifact sequence with zero Contract questions: a3 verifies only 0004→0005 at 0.1.4/empty capabilities and exposes only deferred TQ501 responders; a4 is pinned to `1610b5a`, alone verifies 0006 at ready-active metadata, and becomes the first route-exposure artifact. READY authorizes only the frozen A→E sequence and its per-step evidence; `running`/`finished`, UI, retirement, side-effecting lanes, and Stage 5 remain closed.

- [x] **S5-RM-HOST-A3 · Route-deferred bridge published** — immutable `v0.1.0a3` targets release commit `899defc` on the isolated `codex/read-model-a3-bridge` branch, based on frozen source `40aa9b5` with only package-version metadata plus this task record. The public wheel SHA-256 is `2b01b056c234548afe59fc34bad1d95eb591795f0cae0b4a724ffba4113b4209` and sdist SHA-256 is `5756e71d6b3f70e2e66bb22ffe6f3606676f245a07b6d1a83306346e74ce1cfe`; both were re-downloaded and matched. Fresh/full 0001→0005 chain vectors passed on PG18.3 and exact PG16.14, the installed wheel proved typed deferred H-08/H-11 metadata with no official client methods, and no 0006 migration is present. The historical a3 source has one unrelated formatter drift under the current Ruff formatter (`tests/test_contract_0_1_3.py`); lint is clean and the frozen source was not reformatted to preserve its exact bridge identity. This authorizes only the host a3 pin and local validation before the separately evidenced production bridge deployment.

- [x] **S4-POST-F01 · Coolify build-secret containment** — every configured API (39) and worker (21) variable is runtime-only in Coolify, so no runtime secret is available during image build; the Dockerfiles contain no secret `ARG` instruction. The restricted runtime PostgreSQL login was re-proven, the runtime DB credential plus host auth-signing and documentation secrets were rotated, API rollout health/public health passed, and the worker replacement started without a recorded deployment failure. The new deployment transcripts contain neither an affected environment-variable name nor a Docker build-argument record; deployment-log access remains restricted. Host evidence is recorded in `outlabsAPI` as `docs/taskq-s4-post-f01-build-secret-remediation.md`. The owner explicitly accepted deferral of Redis, Umami, Telegram, and unused `TOOLS_API_KEY` cleanup for this low-value host; the record makes no claim those older credentials are invalidated. No taskq SQL, wire, capability, or application-source change occurred.

- [ ] **S4-POST-F02 · Deferred low-value-host credential cleanup** — owner-accepted residual from F01: rotate Redis, Umami, and Telegram credentials and remove the unused `TOOLS_API_KEY` configuration. This is nonblocking for the current tools lane, but must be re-evaluated before a host expansion or new side-effecting lane treats the historical build exposure as fully remediated.

- [x] **ADR-018 · Operator UI tech stack locked** — React + Vite + TypeScript + TanStack Router/Query/Table + Base UI (OutlabsAuthUI / qdarte-admin family); Bun + Cloudflare static deploy; standalone app first, embeddable mount later; Nuxt stays docs-only. Does not accept Growth §4/§5 endpoint designs — console waits on read-model ADR/H-11.

*(subsequent stages remain sequenced by the Build Plan)*

## Contract questions (STOP-and-record before coding around)

### S5-QD-FR-CQ-24 — Promotion work units cannot preserve producer-owned content identity *(resolved: additive planned identity)*

**Blocking evidence:** the frozen native rescue specification requires every
create-path work unit to carry producer-minted place and content-item
identities before enqueue. `NativeRegionRescuePromotionUnit` carries the
authoritative existing `place_id` only through its promotion candidate, while
the preserved promotion mutation creates a `ContentItem` whenever that place
has no item in the target collection. The reporter therefore cannot bind the
promotion kernel without either minting identity during settlement or routing
through the old completion helper; both are forbidden.

**Resolution:** the Tier-3 native-effects specification requires a
`content_item_id` on
`NativeRegionRescuePromotionUnit`. The stable producer command derives it from
the immutable source identity before enqueue, exactly as it already does for a
buzz-lead unit. Validation requires it to differ across distinct promotion
units, exact replay preserves it, dedupe may return an existing authoritative
item without rewriting that identity, and the unused planned id remains only
bounded task intent. No taskq Protocol, Function Manifest, SQL contract,
migration or wire-command identity changes.

### S5-QD-FR-CQ-23 — A proxy lease receipt without host-selected connection material cannot execute browser work *(resolved: direction-sensitive secret response)*

**Blocking evidence:** ADR-032 correctly forbids handler-supplied proxy
credentials, but its initial typed claim response carried only lease identity
and expiry. The worker cannot open the selected proxy session from that receipt.
Letting the request choose or echo credentials would move host authority into
task input and logs.

**Resolution:** only a successful trusted claim/replay returns the
host-selected proxy endpoint, optional username/password, bounded bypass list
and session generation. Those fields are secret-safe in representations and
diagnostics but serialize on the authenticated private response. Pending,
denied and expired-unsettled receipts contain none; settlement echoes none.
Requests still cannot carry endpoint or credential authority, and no taskq row,
effect receipt or task payload stores it. This is a private reporter amendment,
not a public taskq Protocol or SQL change.

### S5-QD-FR-CQ-22 — Newly discovered buzz leads cannot have exact rescue jobs preplanned before provider work *(resolved: artifact-revision producer command)*

**Blocking evidence:** CQ-19's preplanned rescue branch predates the actual
buzz result. The exact selected leads, artifact revision and any create-path
identities do not exist until the provider result is committed. Constructing
the child afterward in the handler would violate the producer-planned branch
rule; calling a planner from the reporter would make settlement own domain
orchestration.

**Resolution:** buzz apply commits only the immutable authoritative artifact.
A separate QDarte producer command keyed by artifact identity/revision then
reserves selected leads and creates/seals the complete finite rescue workflow
under stable identities. It runs after artifact commit at the ordinary
application producer/scheduler boundary, never in a worker, reporter or taskq
settlement hook. Exact replay returns the same workflow receipt and changed
artifact revision fails closed. The rescue implementation removes the former
buzz child from the native worker graph while preserving artifact-driven
automatic orchestration outside settlement.

### S5-QD-FR-CQ-21 — Region rescue cannot preserve completion-time planning as a recursive native job *(resolved: finite producer-planned workflow)*

**Blocking evidence:** the old `region_rescue_scope` result and completion
paths mint place/content identities, update a reserved discovery artifact,
create `photo_find_scope`, and enqueue another rescue job when candidates
remain. The frozen “preplanned photo child” text could not be implemented from
the inherited list payload because the child identities do not yet exist.
Porting the autocontinue loop would preserve completion-time planning and make
the native job a recursive queue wrapper.

**Resolution:** the producer expands the request into a finite sealed workflow
of exactly one-unit media-assignment, promotion or reserved-buzz jobs. It
mints stable create-path place/content identities, binds the discovery
artifact reservation to the native taskq job id before egress, and supplies
the exact optional photo child. The native handler never creates another
rescue job and may select only the stored photo branch after one authoritative
effect disposition commits. Workflow edges replace the autocontinue loop;
replay cannot add work. This is Tier-3 QDarte planning only and changes no
taskq Protocol, Manifest, SQL contract or migration.

### S5-QD-FR-CQ-20 — Region rescue external controls are reachable only through the retiring queue client *(resolved: ADR-032 private controls)*

**Blocking evidence:** the old rescue handler uses queue-client services for
grounded-model reservation/usage, search-API quota consumption, browser-proxy
claim/release and proxy health events. Calling those services from the native
handler would retain the old queue boundary; bypassing them would silently
remove cost, quota, lease, failover and health guarantees. Taskq admission
reservations bind job creation and cannot substitute for any of them.

**Resolution:** ADR-032 adds closed queue-independent `search_api_control` and
`browser_proxy_control` members to ADR-022's trusted reporter and adds only
`region_rescue_grounded/grounded_search` to ADR-031. The reporter owns attempt
identity, authoritative queue authorization, PostgreSQL time, row-locked
replay, expiry and generation rollover. External calls remain in the worker;
worker loss retains a typed `expired_unsettled` unknown posture. Catalog,
parity, extraction and hard-kill vectors must prove the old guarantees. No
public taskq route, SQL function, migration or generic provider proxy is
created.

### S5-QD-FR-CQ-19 — Buzz completion owns a durable discovery artifact as well as the rescue handoff *(resolved: closed artifact effect plus preplanned rescue branch)*

**Blocking evidence:** the completion-hook sweep recorded the
`buzz_discover_scope` region-rescue handoff but did not record
`persist_discovery_artifact_for_completed_job()`. The legacy completion
transaction materializes every successful buzz result as `buzz_report` or
`region_buzz` domain truth before it invokes the rescue planner. Binding the
frozen input as provider-read plus child only would silently delete that
artifact; retaining the old completion path would preserve the queue wrapper
the replacement forbids.

**Resolution:** the existing closed `buzz_discovery` family owns one bounded,
idempotent discovery-artifact mutation keyed by the stable taskq
job/family/entity/operation identity. The strict producer input carries at
most one fully materialized, scope-equal `NativeRegionRescueInput`; only a
committed effect outcome may select it. The worker inspects before provider
work, applies the bounded result, and returns the exact artifact receipt and
preplanned child. PostgreSQL supplies mutation time. Apply replay reuses the
same artifact and child; changed intent fails closed. The old result route,
job/event model, completion-time artifact hook and completion-time planner are
forbidden. This is a Tier-3 QDarte contract correction only; no taskq
Protocol, Manifest, SQL, migration or public route changes.

### S5-QD-FR-CQ-18 — Review needs authoritative input plus conditional publish/repair/translation branches *(resolved: revision-bound packet, closed review effect and complete preplanned branch matrix)*

**Blocking evidence:** the old `review_scope` handler reads a live review
entity through the retiring queue client and submits an attempt-bound result.
The completion hook does substantially more than persist a grade: it applies
media and review-current/history mutations, evaluates publishable-minimum and
preflight gates, chooses copy/photo/translation repair, records repair-ladder
state, may queue a stale-English translation after a pass, and writes pipeline
blockers. The frozen native definition had only the inherited review payload
and no exact input revision, effect outcome, or fully planned alternatives.
Binding that shape would either preserve the old client/hook or silently remove
business behavior.

**Resolution:** the producer materializes one bounded revision-stamped review
packet and complete applicable branch matrix per entity before enqueue. The
private reporter adds the closed `review` effect and ADR-031 adds only
`review/review`. The API authorizes the current stored task/packet, rejects
stale input without mutation, applies review/media/pipeline/repair truth at
database time, and returns one closed branch-selection outcome. The handler
inspects before provider work, uses shared provider control, applies once and
returns only the matching preplanned child. Missing alternatives fail before
egress; repair exhaustion is structural/domain-owned; replay returns the same
receipt and child. No old review read/result route, queue client, attempt model,
result file or completion-time planner enters the native graph.

### S5-QD-FR-CQ-17 — Translation still depends on old provider admission and completion-time review planning *(resolved: revision prepare, native effect and preplanned review)*

**Blocking evidence:** the old `translation_scope` handler receives bounded
source content but reserves and settles provider usage through the retiring
queue client, writes an attempt-local result file, and submits through the old
result route. The completion hook upserts the locale and chooses ordinary
versus repair review from old job origin, with a blocker side effect if the
handoff fails. Binding it as the inventory's former read-only/provider result
would preserve an old provider wrapper, allow stale source translation, or
plan a child after provider work.

**Resolution:** the strict native input retains producer-inlined source
content and adds at most one fully materialized `review | repair_review`
branch. A closed translation `prepare` validates the current task and current
canonical source revision before provider egress. The terminal set is
`translated | source_stale`; only translated upserts the target locale at
database time and returns the planned review child. Stale source performs no
provider call, locale mutation or child. Metered calls use ADR-031
`translation/translate`; exact replay skips provider work and returns the
same receipt and child. The handler imports no old client, job, attempt,
event, result route or filesystem source authority.

The CQ-11 completion-hook sweep is recorded with this amendment for every
remaining unbound family. This changes only the private Tier-3 integration
contract and ADR-031's closed lane set; it changes no taskq Protocol,
Function Manifest, SQL contract, migration or public facade. The owner has
authorized internal self-review while the external reviewer is unavailable;
the resolution is recorded before implementation.

### S5-QD-FR-CQ-16 — Native synthesis cannot read its planned domain artifact or preserve completion outcomes *(resolved: exact prepare plus closed terminal effects)*

**Blocking evidence:** CQ-15 deliberately keeps source bundles out of taskq
and gives the synthesis child only a stable `bundle_id`, but the private
reporter currently supports effect inspection/application rather than a
bounded authoritative domain read. The old synthesis handler obtains the
bundle through queue payload expansion and submits a draft through the old
result route. Its completion hook applies content and selects either ordinary
review or repair-review from old job origin. It also has non-submit exits for a
missing/thin bundle, writer-contract failure and geographic contradiction.
Binding only the happy path would either restore an old client, duplicate the
bundle in taskq, call a planner after model work, or silently discard those
blocked outcomes.

**Resolution:** extend the closed private union with a
`content_synthesis` family. A bounded `prepare` request may resolve only the
exact bundle id already stored in the current strict task input and returns
either committed terminal truth or the synthesis-ready writer bundle under
the 64KB response ceiling. The terminal operation set is
`synthesized | bundle_blocked | geo_blocked | writer_blocked`; at most one may
commit per job/entity. The API applies either the strict draft or authoritative
pipeline blocker and effect receipt atomically at database time. The producer
preselects at most one fully materialized `review_scope` branch with kind
`review | repair_review`; only a committed synthesized outcome returns it.

Every metered synthesis and repair call uses ADR-031 provider control lane
`content_synthesis`, operation `synthesize`. The handler performs no discovery,
never sees an attempt id, and cannot request arbitrary artifacts. Prepare,
apply and ambiguous-response replay return identical terminal truth and
children. Missing, expired, superseded, entity-mismatched or firewall-invalid
artifacts fail closed before provider work.

This is a Tier-3 internal integration correction. It changes no taskq
Protocol, Function Manifest, SQL contract, migration or public facade. The
owner has authorized internal self-review while the external reviewer is
unavailable; the resolution is recorded before implementation.

### S5-QD-FR-CQ-15 — Listing artifact identity and synthesis eligibility exist only at completion time *(resolved: producer-minted bundle identity plus authoritative typed disposition)*

**Blocking evidence:** the source-derived table correctly requires a closed
`listing_research` mutation plus a preplanned synthesis branch, but the old
completion hook generates the reusable artifact identity while applying the
worker result and decides synthesis eligibility from current content and
launch-pipeline state. A fully preplanned native child therefore cannot name
the artifact it will consume, while a worker choosing from its own readiness
flag would bypass curated holds and exhaustion. Planning the child after
provider work violates CQ-10; returning to the old result route preserves the
retiring wrapper; embedding the complete research bundle in taskq duplicates
application data and may exceed the bounded child contract.

**Resolution:** the producer mints a stable per-entity `bundle_id` before the
parent enqueue and uses it in both the listing plan and optional fully
materialized synthesis child. The listing effect persists the bounded,
writer-firewalled, synthesis-ready exact-place bundle under that identity and atomically evaluates
authoritative current state. Its family-specific response returns the stable
effect receipt plus exactly one disposition:
`synthesis_ready | curated_hold | blocked_exhausted`. The handler selects the
already-planned synthesis child only for `synthesis_ready`; the other
dispositions select none. A future native synthesis handler resolves the
bundle through a bounded domain read by `bundle_id`, never through taskq or an
old queue client.

The reporter envelope remains capped at 64KB. Ready results contain exactly a
canonical writer-input bundle whose manifest is semantically revalidated;
underfilled results contain no bundle. Counts, warnings, fingerprints and
guard reasons are finite and bounded, and arbitrary result metadata, raw
provider bodies, diagnostics, credentials, caller timestamps and filesystem
paths are forbidden. Inspect/apply replay returns the same receipt and
disposition; response-loss replay creates neither a second artifact nor a
different child.

This is a Tier-3 internal integration correction. It changes no taskq
Protocol, Function Manifest, SQL contract, migration or public facade. The
owner has authorized the primary agent to self-review while the external
reviewer is unavailable; this resolution is recorded before implementation.

### S5-QD-FR-CQ-14 — Editorial content cannot fit the private reporter's 8KB envelope *(resolved: bounded 64KB content-effect envelope)*

**Blocking evidence:** the source-derived inventory correctly classifies
`editorial_enrich_scope` as a trusted `editorial_enrichment` domain effect,
but its authoritative mutation carries a content draft. The canonical native
task models permit a bounded 64KB aggregate while the private reporter
transport was frozen at 8KB. A valid title/body/translation draft can
therefore be accepted by the native task contract and then become
unreportable. Truncating the draft changes domain truth; restoring the old
result route preserves the queue wrapper; and passing a worker-local artifact
path couples the API to worker storage.

**Resolution:** the existing private reporter remains the sole path and keeps
authenticate plus authoritative queue-scoped `run` authorization before any
body decode. Its aggregate request/envelope ceiling becomes 64KB, equal to the
already-executable native input/output ceiling. Closed non-content members keep
their narrower field bounds; this is not an arbitrary JSON surface. The new
editorial member accepts only a strict bounded draft and
`enriched | unchanged` status, derives content/review row identity from the
stored strict task input, uses the database clock, and records mutation plus
receipt atomically. It accepts no old attempt model, result metadata, caller
timestamp, provider diagnostic, credential, arbitrary locale map, or request
echo.

Each planned entity has at most one optional, fully materialized
`review_scope` branch. Plan identities cover the entity set exactly, child
scope equals parent scope, and the job-wide maximum remains 20. The handler
inspects before provider work, applies one effect, and returns only the
preplanned child after the receipt commits. Response-loss replay returns the
same receipt and child; it cannot plan another review. Any metered model call
uses ADR-031's shared provider control and remains worker-owned.

This is a Tier-3 internal integration correction. It changes no taskq
Protocol, Function Manifest, SQL contract, migration, public facade route, or
production state. The owner had already authorized the primary agent to
self-review and continue while the external reviewer was unavailable; the
resolution preserves every Tier-0 boundary and is recorded before source.

### S5-QD-FR-CQ-13 — Cross-attempt reservation takeover can erase unknown provider cost *(approved 2026-07-23)*

**Blocking evidence:** implementing ADR-031's required classification
hard-kill history exposed a contradiction between its unknown-cost rule and
its current reclaim wording. The host control kernel finds the stable
job/lane/entity/operation reservation. When a different taskq attempt arrives
while that reservation is still `reserved`, it currently adds the new
reporter-owned attempt id and returns `reserve_replayed`. The new worker may
therefore call the provider again immediately. If the first process was killed
after provider egress but before domain apply or settlement, the second call
can settle the reservation as known usage. The first incurred call then has no
event and no retained `expired_unsettled` posture. That is silent cost loss,
not the owner-approved worker-loss semantics. The executable
`test_reserve_replay_and_task_attempt_reclaim_consume_one_unit` currently pins
the unsafe cross-attempt reuse, while same-attempt response-loss replay is
legitimate.

**Recommended adjudication:** distinguish same-attempt reserve replay from
cross-attempt takeover without adding provider authority to taskq. Exact
reserve replay by the same reporter-owned attempt remains byte-stable. A
different attempt must not be authorized to perform provider egress while the
earlier reservation is live; it receives a typed retryable
`reservation_pending` posture and leaves the hold untouched. At the
database-stamped expiry, the first observer atomically records and receives
`expired_unsettled` with unknown cost. That exact attempt may replay the same
expiry receipt. A later task attempt may create the next numbered reservation
generation under the same stable logical control identity, preserving the old
unknown-cost row and consuming a new honest budget unit before any new provider
call. The host stores generation and expiry-observer identity in the existing
bounded metadata, selects the latest generation under the existing advisory
and row locks, and never mutates the old row back to known usage.

The private reporter contract needs a docs-first ADR-031 amendment that adds
the typed pending posture and freezes the generation transition. No taskq
Protocol, Function Manifest, SQL migration or public client change is
required. Required vectors are: same-attempt reserve response loss; different
attempt before expiry with zero second provider call; database-time expiry
with retained unknown cost; exact expiry-response replay; next-attempt new
generation with a second budget unit; and a real process kill after provider
egress proving either one settled provider event or one retained
`expired_unsettled` row, with no silent call and no double settlement.

**Stop:** do not reinterpret `reserve_replayed` as cross-attempt provider
authority, do not overwrite an expired generation, do not invent client time
or a taskq admission dependency, and do not claim the classification family or
its hard-kill gate complete until this transition is adjudicated docs-first.

**Adjudication:** approved as recommended. ADR-031 and both Tier-3 reporter
specifications now freeze same-attempt replay, typed cross-attempt pending,
database-time expiry observation, immutable unknown-cost history and
later-attempt numbered generation rollover before source changes resume.
Implementation and the process-kill gate are complete: one real `SIGKILL`
history conserved two observed external calls as exactly one durable provider
event plus one retained unknown-cost generation, then settled a second
generation and one domain effect on the same job with no legacy queue rows.

### S5-QD-FR-CQ-12 — Native LLM handlers need the durable provider-budget control plane *(approved 2026-07-23)*

**Blocking evidence:** `tripadvisor_classification_scope` is the first FR-03
effect family whose external model call is protected by QDarte's durable
provider reservation, failover and usage-event service. The old worker reaches
that service through its retiring queue API client. A native handler cannot
call that client without preserving the old queue boundary, cannot move the
provider call into taskq settlement, and cannot substitute taskq admission
reservations because those bind job creation rather than provider/token
budgets. Calling the provider directly without the reservation would silently
remove an existing resource and cost-control guarantee.

**Recommended adjudication:** extract the existing provider guardrail into one
queue-independent private control family used by every native LLM handler.
The control travels through the existing reporter-owned attempt boundary as a
new closed `llm_provider_control` member; handler code still never receives an
attempt id. Before a provider call it submits only the closed lane,
entity/operation identity, provider/model option, request fingerprint and
bounded token estimate. The host authenticates, resolves and authorizes the
current task's authoritative queue before body decode, validates
lane/entity/provider membership against stored strict input, and derives the
reservation idempotency key from current job plus reporter-owned attempt and
the canonical request. No caller idempotency key or timestamp is accepted;
PostgreSQL time owns reservation and settlement instants.

The worker performs the provider call itself. It then reports one closed
success/transport/capacity settlement carrying only bounded token counts and
safe classification. The host row-locks the reservation, validates ownership,
stores a canonical settlement hash in the existing bounded metadata, records
the provider event/state transition, and settles in one transaction. Exact
settlement replay returns the same typed receipt; mismatch fails closed. The
handler orders inspect-domain-effect → reserve-provider → call-provider →
apply-domain-effect → settle-provider. An ambiguous domain-apply response
therefore replays without another provider call; a process crash before apply
may repeat provider work only under the same already-counted attempt
reservation, matching the standing non-exact provider-read rule. Credentials,
prompts, provider bodies and exception text never cross or persist. The
surface is not taskq Protocol v1, not a provider proxy, and imports no old
queue job, attempt, client, lifecycle service or table.

**Required evidence:** bad credentials and queue denial precede body decode;
wrong task/lane/entity/provider/fingerprint and stale/cancelled attempts fail
before reservation mutation; client time and caller idempotency are impossible
by model shape; reserve and settle replay are byte-stable; a settlement race
records one event and one state transition; provider failure leaves no domain
effect; committed provider success plus lost effect response does not cause a
second provider call; task-attempt retry consumes no duplicate reservation
unit; usage and effect receipts each conserve one logical operation; worker
secrets and error text remain absent; all native LLM families consume this one
closed control rather than inventing per-lane wrappers.

**Stop:** do not call the old worker API client, do not bypass durable
metering, do not proxy arbitrary provider requests, do not use taskq admission
as a token-budget substitute, and do not commit the partial classification
source until this boundary is adjudicated docs-first.

**Adjudication:** approved as recommended through ADR-031. The owner requires
the standard docs-first reporter amendment and closed-member catalog/parity
oracles; database-time expiry must release the hold while retaining a typed
`expired_unsettled` unknown-cost posture; classification must prove hard-kill
reclaim with exactly one provider event or that typed posture and this does not
waive FR-04; and extraction parity must preserve reservation, failover and
usage-event guarantees. Protocol v1, the Function Manifest, taskq SQL and
migrations remain unchanged.

### S5-QD-FR-CQ-11 — Photo verification is not read-only at the old completion boundary *(resolved: closed effect + structural branches)*

**Blocking evidence:** the frozen native effect table currently classifies
`photo_verify_scope` as provider/filesystem verification with no domain
mutation. Direct source tracing disproves that classification. The worker
returns per-entity verdicts, then old job completion calls
`LaunchPipelineTransitionService.queue_photo_verify_content_handoff()`. A pass
updates the photo gate and continues the content path; a first failure may
plan and enqueue a retry `photo_find_scope`; a terminal failure records a
launch-pipeline blocker. Those writes and handoffs currently happen inside the
old settlement transaction and are therefore deletion targets, not behavior
that may disappear.

**Required adjudication:** reclassify photo verification as provider read plus
immutable artifact plus a closed `photo_verification` domain-effect family.
Each bounded entity verdict is applied through `context.report_effect()` using
the stable `(taskq job id, family, entity, operation)` identity. The
authoritative API validates the entity against the stored native payload,
updates launch-pipeline state idempotently, and returns only a bounded receipt.
Conditional retry/review children must be fully planned before the parent is
enqueued and carried as closed branches in the strict native input; the
handler selects at most one already-valid branch per entity and returns native
taskq follow-ups atomically with settlement. Cap total planned/selected
children at 20, require scope equality, and prove pass/fail/retry/replay plus
response-loss conservation. Amend the Tier-3 specification, machine effect
inventory, native definitions and source-derived oracles together before
implementation.

**Stop:** do not bind `photo_verify_scope` as read-only, do not preserve its
old completion hook, do not let the reporter enqueue a child, do not ask the
handler to call an API planner after provider work, and do not construct an
old job/client as a bridge.

**Adjudication:** approved as recommended. The owner additionally requires the
retry ladder to be encoded structurally in producer-planned branches (branch
presence, never a handler counter or reporter state, determines retry versus
terminal blocker); `terminal_blocker` is a closed `photo_verification`
operation with its own stable idempotent identity; and the remaining unbound
families' old completion hooks must be swept and recorded before
FR-03C-PHOTO binds. First-failure, terminal-failure and replay vectors must
prove that replay returns identical receipts and children and can never extend
the ladder. The photo family uses the shared ADR-031 provider control and does
not waive FR-04's side-effecting hard-kill gate.

### S5-QD-FR-CQ-10 — Content-enrich follow-ups lack executable child payloads *(resolved: fully planned native children)*

**Blocking evidence:** after CQ-09 moved scope into native payloads, direct
source derivation of `content_enrich_scope` found that its old follow-up calls
send `entity_keys`/`content_item_ids` selectors to the generic worker enqueue
path. The registered `PhotoFindScopePayload` has no `entity_keys` field and
the old model silently ignores extras; both photo and editorial worker inputs
require fully planned entity data that `ContentEnrichScopeTarget` does not
contain. The generic enqueue service performs validation/storage only and does
not call the specialized API planners. A literal port would therefore create
typed child jobs with empty entity lists and preserve a silent no-op bug.

**Required adjudication:** the native `content_enrich_scope` input must replace
target flags/selectors with a closed, maximum-20 discriminated union of fully
planned follow-ups. Each item contains a stable step plus either an exact
`NativePhotoFindInput` or `NativeEditorialEnrichInput`; child scope must equal
parent scope. The native handler only converts these already-validated plans
to taskq `Followup` values and returns one `Complete`, so validation and child
insertion are atomic with parent settlement. QDarte API owns the future
producer planner and must obtain the complete child payloads before enqueueing
the coordinator. Empty plans yield typed `no_change`; selector-only forms and
more than 20 children fail before settlement. The native input need not remain
a subclass of the insufficient legacy payload.

**Stop:** do not pass selectors as unknown child fields, do not call the old
generic enqueue route, do not query QDarte from the handler to fill payloads,
and do not translate through an old job/client.

**Resolution:** FR-03 §3.1 replaces the insufficient native coordinator input
with a closed maximum-20 discriminated union of photo/editorial plans. Every
plan contains a unique stable step and a complete strict child input whose
scope equals the parent. The future API producer plans before enqueue; the
handler performs no read or enqueue call and returns atomic taskq follow-ups.
Empty is `no_change`; selectors, duplicates, overflow and scope conflict fail
before settlement. No taskq SQL, wire, migration or generic planner surface
changes.

### S5-QD-FR-CQ-09 — Native payloads omit legacy envelope scope authority *(resolved: scope belongs to the typed payload)*

**Blocking evidence:** FR-03C began deriving native `Followup` values for
`content_enrich_scope`. Its old handler builds child jobs from
`job.scope_kind` and `job.scope_key`, but those values are not fields of
`ContentEnrichScopePayload`. Taskq deliberately has no QDarte-specific scope
columns. The same inherited omission exists in native inputs for content
synthesis, editorial enrichment, frontend deployment, listing research, photo
find, photo verification, region rescue and review. Existing source reads the
old envelope identity directly in handler output, child planning, reporter
authority or diagnostics. The strict native subclasses therefore do not yet
contain all authoritative input needed to execute without an old job object.

**Required adjudication:** make `(scope_kind, scope_key)` required canonical
fields of every native input. Existing narrower task literals remain narrower;
the nine inherited omissions use the already-closed QDarte `ScopeKind` enum and
a bounded non-empty key until their producer-specific validators narrow them.
Native producers and follow-ups put scope only in the typed payload. Headers
remain bounded diagnostics and runtime settings remain dependencies, never
domain authority. Reporter plan validation obtains scope from the
authoritative stored payload. Add a 21-task field-equality oracle plus negative
vectors proving missing scope and conflicting follow-up scope fail before
settlement. No taskq SQL, wire, migration or generic job column is proposed.

**Stop:** do not implement `content_enrich_scope` follow-ups, do not place
scope in headers/settings, and do not construct an old job envelope as a
bridge.

**Resolution:** the FR-03 specification now requires every native input to
carry canonical `scope_kind` and `scope_key`. Existing narrow literals remain
unchanged; the nine omissions inherit the finite QDarte `ScopeKind` union and
bounded key. Headers are diagnostics, settings are dependencies, and neither
may carry scope authority. Current follow-ups preserve the parent identity;
reporters validate the stored payload. The implementation must make all 21
fields required and prove missing/conflicting scope fails before settlement.
No taskq SQL, wire, migration or generic job-column change is needed.

### S5-QD-FR-CQ-08 — The 12 old result routes are not the complete native effect surface *(resolved: complete per-task effect inventory)*

**Blocking evidence:** FR-03's new Tier-3 specification derived 12 effect
families from the old API's result-route classifications. Source inspection
immediately falsified that as a complete handler-effect inventory.
`publish_scope` performs authoritative QDarte mutations inside the old
`complete_job` settlement transaction rather than through a result route.
`frontend_deploy_scope`, discovery/import, classification and session-prime
handlers also perform external, filesystem, provider or domain operations that
the 12-route list cannot represent. Encoding the incomplete list into the
native definitions would leave real side effects outside ADR-022's
inspect-before-act and stable-idempotency boundary.

**Required adjudication:** derive a checked-in per-task effect manifest from all
21 executable handler call graphs. Every effect is classified as pure read,
native follow-up, authoritative QDarte domain mutation, external/provider
operation, filesystem artifact, or deployment operation. Each non-read effect
must then either (a) join the closed trusted reporter protocol with a stable
`(job_id, family, entity_key, operation_key)` identity and same-transaction
domain receipt, or (b) name a separate idempotent owner and replay oracle that
is safe under response loss and reclaim. Settlement-triggered domain mutation
is forbidden in the final system. Amend the FR-03 specification and machine
inventory docs-first before adding native definitions. No taskq SQL, wire or
migration change is implied unless the completed inventory proves ADR-022
insufficient.

**Stop:** do not implement FR-03B definitions or handler adapters from the
12-route approximation, and do not start either old worker.

**Resolution:** the FR-03 specification now classifies all 21 executable tasks
individually. It adds the hidden settlement-owned `publish` mutation, direct
discovery/open-source/import writes, filesystem artifacts, metered provider
reads, proxy/session mutation and frontend deployment. The 19 authoritative or
separately idempotent operation families use either ADR-022's stable
inspect/apply transaction or a named operation-specific receipt state machine;
pure/read/follow-up-only tasks are explicit too. Native settlement may mutate
only taskq state. The machine effect manifest and native definitions must match
this table exactly before handler binding; no taskq SQL, wire or migration
change is required.

### S5-QD-FR-CQ-07 — ADR-030's approved invariant failure was omitted from the workflow-page error rows *(resolved: ADR-030 propagation)*

**Blocking evidence:** immutable-repair review found that ADR-030 and Manifest
§18.8 explicitly require a missing live counter invariant to fail as TQ500,
while Protocol §2.10 and Manifest §18.2 omitted TQ500 from the exact public
workflow-page error rows. The existing closed TQ500 family already defines the
opaque wire behavior; there is no new code, status, retry or detail decision.

**Resolution:** propagate the owner-approved ADR-030 decision docs-first:
Protocol §2.10 and Manifest §18.2 now include invariant TQ500 and explicitly
forbid identity/catalog detail on the wire. Protocol document revision 1.0.13,
SQL 0.2.3 and every identity remain unchanged. Migration 0013 implementation
may follow only after this commit.

### S5-QD-FR-CQ-06 — Exact workflow counters cannot foreign-key the workflow row without breaking cancellation concurrency *(resolved: ADR-030)*

**Blocking evidence:** the first FR-02D full-suite run reached the existing
choreographed `cancel_workflow` versus held-open `complete_job` history and
stopped. Migration 0011's owner-private counter row had the Manifest §18
primary-key/FK shape. The settlement's job-status trigger updated that counter;
PostgreSQL's referential-integrity machinery retained a key-share lock on the
parent workflow. The concurrent operator's required `SELECT ... FOR UPDATE`
then waited on the settlement transaction instead of recording cancellation
intent while the job row remained SKIP-LOCKED. `pg_blocking_pids` and
`pg_locks` identified the held settlement transaction and the workflow tuple;
the pre-0011 race is green. This is a contradiction between new Manifest §18
and ADR-026's accepted cancellation linearization, not a test timeout to relax.

**Required decision:** preserve the exact materialized counts but remove the
counter-to-workflow foreign key. The recommended owner-private integrity
mechanism is a separate workflow lifecycle trigger that creates the zero
counter row on workflow insert and deletes it on workflow delete. The job-state
trigger then performs UPDATE-only bucket transitions and raises an internal
error if the invariant row is missing; it never inserts through referential
integrity during settlement. Backfill precedes both triggers. Freeze this as
ADR-030 plus a docs-first Manifest §18 correction; Protocol 1.0.13, SQL 0.2.3,
the public function identity and migration number 0011 stay unchanged. Then
re-run the cancellation race before any other SQL work.

**Current state:** stopped with the uncommitted 0011 implementation and tests
preserved. The scratch database only was recreated during diagnosis; no QDarte
or production state changed.

**Scratch-only adjudication proof:** removing only the counter FK, creating the
counter row from an owner-private workflow lifecycle trigger, and making the
job trigger UPDATE-only restored the exact held-open settlement versus
cancellation history to 1/1 in 0.20s. The queued→running→succeeded counter
vector remained 1/1. No committed source or contract was changed by this
prototype. This confirms the recommended repair addresses the measured lock
cause without weakening count exactness or ADR-026 concurrency.

**Resolution:** the owner approved ADR-030 on 2026-07-23. The private counter
has no workflow FK; owner-private workflow lifecycle owns identity, job
transitions are UPDATE-only and fail loudly on a missing live invariant row.
Protocol 1.0.13, SQL 0.2.3, migration 0011 and every public identity remain
unchanged. Implementation may resume docs-first.

### S5-QD-FR-CQ-05 — The seeded maintenance schedule has no public HTTP authorization or profile shape *(resolved: ADR-028)*

**Blocking evidence:** Protocol §2.9 defines GET/PUT/DELETE schedule routes as
`control`-authorized on an authoritative queue and freezes an exact public
profile whose target is a queue-bound job. Migration 0010 also creates the
caller-immutable `taskq-janitor-daily` definition with target
`{"kind":"maintenance","maintenance":"janitor"}`. Its authorization projection
necessarily has no queue, while direct `get_schedule` returns that maintenance
target. The Protocol says the reserved definition has no HTTP mutation route,
but does not say whether GET is hidden, globally authorized, or allowed to
return a second wire shape. The facade therefore cannot mount the frozen routes
without either inventing authority, leaking an uncontracted profile, or
silently creating a special case.

**Resolution:** the owner approved ADR-028 / Protocol document revision 1.0.12
with no SQL or migration change. The exact seeded identity is outside the HTTP
schedule-name grammar, so GET/PUT/DELETE uniformly return `TQ422` with the
fixed name-field detail before lookup, header/body processing or SQL. Ordinary
queue schedules are unchanged. Runtime housekeeper health and bounded telemetry
are the operational path; privileged definition inspection stays direct SQL.
Any future enumeration must exclude package maintenance definitions and prove
that negative explicitly.

### S5-QD-FR-CQ-04 — PostgreSQL owns due truth but cannot natively compile cron, and janitor takeover cannot use runner authority *(resolved: ADR-027)*

**Blocking evidence:** FR-02C requires PostgreSQL's clock to be the only due
clock and requires migration 0010 to replace ADR-009's hardwired janitor
trigger. Core PostgreSQL has no cron evaluator. Accepting a caller-computed
initial `next_fire_at` would reintroduce client wall-clock truth; keeping host
cron would retain a second scheduler; sending janitor through an ordinary job
would grant maintenance power to a runner; and accepting a function name in a
schedule would create an arbitrary privileged execution surface. The existing
Tier-3 sketch did not freeze an honest boundary or public identities.

**Resolution:** the owner approved ADR-027. SQL stamps a compile-first due row
and projects only database `as_of`/due instants to one package-owned closed
interval/five-field-cron evaluator with explicit DST semantics. The first
evaluation emits nothing, so create/resume cannot accidentally fire.
Subsequent finite `skip|fire_once|fire_all` lists are token/version validated
and atomically become permanently keyed jobs plus strict advancement.
Housekeeper actions are direct SQL only. Operator schedule GET/PUT/retire is
queue-authorized. The sole non-job target is migration-seeded,
caller-immutable `maintenance:janitor`, which directly invokes only the
existing bounded janitor pass while 0010 disables the old tick branch in the
same transaction. Protocol 1.0.11, Manifest target 0.2.2 and migration 0010 are
the exact identities; no arbitrary callback/function target or host scheduler
is permitted.

### S5-QD-FR-CQ-03 — Multi-call workflow construction has no graph-closure linearization point *(resolved: ADR-026)*

**Blocking evidence:** the frozen FR-02B design creates a replay-safe workflow,
then admits member jobs through separate calls, while a bounded housekeeper
finalizer derives terminal workflow status from the current member set. Neither
the Native Orchestration Specification nor the Unified Design Spec defines when
membership closes. An empty workflow, or one whose currently admitted members
finish before a later HTTP enqueue arrives, is indistinguishable from a complete
graph. Finalizing it would either permit post-terminal membership and reopen
terminal state, or reject a legitimate planner retry. QDarte's current
single-database transaction does not solve the general HTTP/client contract and
cannot survive as a wrapper.

**Resolution (owner-approved 2026-07-23):** ADR-026 adds a producer-granted, replay-safe
`seal_workflow` command in the docs-first 0.2.1 package. Creation leaves the
workflow open. Workflow-row locking serializes member enqueue against sealing;
only sealed workflows may finalize. After sealing, an exact replay of an
already-admitted workflow step remains `existed`, while a new step is a typed,
non-retryable conflict. A sealed empty workflow succeeds. Terminal workflow
state is immutable in this minimum release, so individual member redrive is
rejected and a corrected run uses a new workflow key; any future workflow-level
redrive is a separate contract. The ADR must also reconcile the older
`create_workflow(..., actor)` sketch with FR-02B's declared-queue authority by
freezing both bounded declared queues and the authenticated actor in one
identity.

### S5-QD-FR-CQ-02 — Inconsistent follow-up holder has no declared internal-error raise *(resolved)*

**Blocking evidence:** the migration-0008 collision vector can encounter an
active job that already owns the derived `chain:<parent_job_id>:<step>` key but
does not match the contracted child. Manifest §15.5 requires that case to be a
“registered internal failure, never a second child,” while §15.2 declares the
private helper as raising only `TQ422`, and the public `complete_job` row likewise
does not name the internal failure. Treating the holder as `existed` would attach
the wrong child; treating it as deterministic `TQ422` would misclassify database
state as caller input. Implementation stopped with migration 0008 and its tests
uncommitted.

**Resolution (owner-approved 2026-07-23):** use the existing registered non-retryable `TQ500` internal
error for this residual invariant breach. Amend Manifest §15.2 and the
`complete_job` raises row docs-first to include `TQ500` only for inconsistent
derived-key holders; keep the helper private, migration identity/signature and
Protocol wire envelope unchanged. Then resume the immutable migration and prove
that the exception rolls back parent settlement and every child insert.

### S5-QD-FR-CQ-01 — Manifest names a nonexistent private follow-up return composite *(resolved: ADR-025)*

**Blocking evidence:** the first migration-0008 implementation pass found no
`taskq.enqueue_result` type in migrations 0001–0007 or the machine catalog,
while Manifest §15 named that type as `_enqueue_followup`'s return. Writing SQL
against it would fail installation; adding the type would invent a contract
surface not authorized by ADR-024. Migration work stopped before a file was
created.

**Resolution:** ADR-025 corrects only the private helper return to
`TABLE(job_id uuid, created boolean)`, the existing ordinary-enqueue projection.
It adds no type, route, wire field or application grant and leaves ADR-024's
atomicity/authorization/key semantics unchanged. Docs-first correction landed
before migration 0008; implementation may resume.

### S5-QD-C7-CQ-02 — Same-cluster package isolation conflicts with the incumbent superuser application login *(resolved: full runtime privilege separation)*

**Trigger:** the live C7-01 identity proof established that the ordinary
production `qdarteapi` service still connects to the shared PostgreSQL cluster
as the `postgres` superuser. The frozen C7 topology places
`qdarte_contact_verify` on that same cluster while stating that the ordinary
QDarte app receives no package-database password and that package access is
limited to the dedicated capability identities.

**Why this cannot be coded around:** PostgreSQL credentials authenticate a
cluster role, not one database. The existing `postgres` credential can connect
to and fully control every database on the cluster; a superuser bypasses
`CONNECT`, ownership, grants, RLS, and every taskq capability role. Merely
omitting `QDARTE_TASKQ_CONTACT_DSN` from the ordinary app would therefore make
the documented isolation claim false. Creating the lasting database anyway,
granting broader roles, or treating absence of a configured DSN as a security
boundary would violate the accepted C7 plan.

**Decision:** retain the same-cluster package topology, but make the broader
runtime-credential conversion a prerequisite to package creation. Long-lived
QDarte API and ordinary-worker paths receive distinct non-superuser logins;
the exact contact-domain, package facade, operator, and owner identities remain
separate. The API no longer runs migrations or backup control and receives no
owner secret through environment, Docker socket, mounts, desired state, image,
or logs. API-managed worker desired state becomes secret-free; only a
controller-owned mode-0600 environment injects the worker DSN/token. Both
databases use explicit `CONNECT` allowlists, and a versioned declarative grant
manifest plus exact verifier owns role attributes, memberships, current grants,
future default privileges, and forbidden paths.

**Required proof before package creation:** rehearse against a restored
production database under the exact proposed logins: real API boot, OutLabsAuth
login and service-token authorization, representative API read and rolled-back
write, every deployed worker database path including the legacy direct queue,
host-only backup/restore, and negative owner/operator/admin/package access.
Then rotate Mini87 reversibly and prove health, direct-queue continuity,
OutLabsAuth behavior, secret absence, and package blindness. Only after that
may the lasting package database be created under the already-frozen C7
sequence.

**Scope opened:** C7-01A documentation, implementation, disposable-clone proof,
and reversible incumbent runtime credential rotation. It does not authorize a
lasting package database before the rotation proof, a package job, provider
call, C7-02 cohort, retirement, non-contact lane, or Stage 6.

### S5-QD-C7-CQ-01 — Accepted network isolation conflicts with QDarte's blanket worker-container ban *(resolved: narrow closed-worker exception)*

**Trigger:** C7-01 source convergence read the QDarte worker and runtime
`AGENTS.md` files before implementation. Both require the normal content/media
worker fleet to run as host-local `uv` processes and prohibit worker
containers, while the accepted C7 plan requires the dedicated contact-package
worker to join only an `internal: true` network so a separately dual-homed
verification gateway is its sole egress path.

**Why this cannot be coded around:** a host-local process cannot be attached to
the private Compose network or structurally denied direct host egress. Keeping
the blanket ban would make the accepted network-enforcement oracle impossible;
silently weakening the oracle would violate the reviewed C7 plan.

**Decision:** the owner-approved C7 topology is a narrow exception for the
single closed `qdarte.contact_verify.scope` worker only. It may run as one
disabled-by-default Compose service with no database credential, no enqueue
credential, no host port, one fixed queue/type, and only the internal contact
network. The ordinary QDarte worker fleet remains host-local and the general
container ban remains in force. QDarte's repository guides and environment
documentation must state this exception docs-first before service code lands.

**Scope opened:** documentation alignment and C7-01 implementation only. This
does not authorize C7-02, a provider call, another containerized worker lane,
direct-queue retirement, or Stage 6.

### S5-QD-C6-CQ-01 — Static closed modes cannot consume a process-owned drain attestation *(resolved)*

**Blocking evidence:** C6-01 freezes `QDARTE_CONTACT_VERIFY_MODE` as a
startup-validated `legacy | draining | package` selector. C6-02 correctly
issues its opaque direct-drain attestation only while the selected mode is
`draining`; it correctly removes that attestation on restart or mode change.
C6-03 would need the selected mode to become `package` before it can admit its
first package job. With the current static configuration that transition
requires a restart, which intentionally erases the only valid attestation.
Therefore no process can satisfy both preconditions: the package path is
unreachable without weakening the proof, persisting/hand-editing it, or
inventing a fallback.

**Recommended adjudication:** amend the Tier-3 C6 mode semantics docs-first:
`legacy` and explicit `draining` remain steady states, while a configured
`package` process starts internally in a non-serving draining posture, performs
the complete C6-02 direct observation in that same process, and atomically
opens its package selector only after the in-memory attestation is issued.
Every restart repeats the drain before serving; failure leaves no package
producer callable and fails startup or remains a fixed draining refusal. The
mode is still sampled once per request after this one startup transition, no
opaque record crosses a route/environment/database boundary, and no direct or
package fallback is introduced. Alternative: choose a different, explicitly
reviewed durable attestation mechanism. Do not start C6-03 until one of these
semantics is adopted docs-first.

**Resolution:** adopted the recommended same-process lifecycle transition.
`package` is a requested terminal posture: before FastAPI serves any request,
the process behaves internally as `draining`, disables the direct producer,
and performs the complete C6-02 observation. It atomically opens package
admission only while retaining that process-owned proof. A restart repeats the
observation; a failure exposes no package producer. Explicit `draining`
remains a fixed refusal, while no route/config/database record can forge or
preserve the transition. C6-03 may implement exactly this local lifecycle
controller and no alternative durable/fallback path.

### S5-QD-C6-CQ-02 — The existing cutover response and idempotency semantics are backend-specific *(resolved: canonical admission)*

**Blocking evidence:** `POST /ops/cutover/jobs/contact-verify-scope` currently
returns `ContactVerifyCutoverEnqueueResponse(route, legacy_job | taskq_job)`.
The legacy producer returns an incumbent `WorkerJobDetail`, checks an explicit
idempotency key only when supplied, and also has an active-scope coalescing
path. The incumbent direct taskq producer instead derives
`contact_verify_scope:<scope_kind>:<scope_key>` when the caller omits a key and
returns a typed `created` disposition, queue/type, key, and planned count. The
package producer has neither an honest legacy-job projection nor an authority
to masquerade as the host-owned direct taskq catalog. Retaining one of those
shapes silently would either expose a fake backend, alter deduplication, or
break callers that branch on the discriminator.

**Recommended adjudication:** make the existing authorized cutover URL's
package-era response deliberately backend-neutral: a bounded canonical
admission result (`job_id`, `created | existed`, canonical idempotency key, and
planned entity count), with no route/queue/job-type projection. Freeze one
canonical key rule for both modes before package admission; migrate or retire
any discriminator-dependent caller as a C6-03 acceptance row. The old
backend-specific `/ops/taskq/*` and `/worker/taskq/*` paths remain incumbent
only and are not aliases or fallbacks. Alternative: approve a versioned public
API response. Do not implement C6-03's producer until the public shape and
key rule are chosen docs-first.

**Resolution:** adopted the recommended canonical admission. The existing
authorized cutover URL retains its request grammar but returns only opaque
`job_id`, `created | existed`, the canonical supplied-or-derived idempotency
key, and bounded planned entity count. Both modes derive the supplied key or
exactly `contact_verify_scope:<scope_kind>:<scope_key>` before admission;
there is no route discriminator, queue/type projection, backend impersonation,
or fallback after package ambiguity. Legacy active-scope coalescing that does
not share that canonical key is a typed host refusal until a later explicit
caller contract. C6-03 must migrate or retire discriminator callers, prove the
canonical response in both modes, and leave `/ops/taskq/*` and
`/worker/taskq/*` incumbent-only.

### S5-QD-C6-CQ-03 — A package keyed replay cannot depend on a fresh volatile direct plan *(resolved: ADR-023 queue-native admission)*

**Blocking evidence:** the local C6-03 exercise at QDarte API `c0940fb`
started only a loopback package facade and a package-mode caller API; no worker
or provider ran. The direct ledger was stable at five completed
`contact_verify_scope` jobs, five attempts, and twenty events. A keyed
`country:AR` request returned the frozen canonical `created` result and added
one queued package job with zero package attempts/events; the direct counts
and high-waters remained unchanged. The identical keyed replay then returned
host `422` before package enqueue. Source explains the counterexample:
`ContactVerifyPackageAdapter.admit()` rebuilds the volatile direct candidate
plan before it can call package `enqueue`, while the legacy canonical-admission
method checks its idempotency key before planning. If candidates, operator
quota, or ordering change between calls, the package adapter cannot reach its
authoritative keyed `existed` outcome. A cache, direct fallback, or a new
untracked host mapping would only hide the broken replay contract.

**Required adjudication:** decide a *durable atomic admission* primitive before
further C6 code. A lookup alone is not enough: `taskq.enqueue` currently
deduplicates only an active row, so a matching job can settle between lookup
and later publish. The contract-first queue-native option is a two-stage,
key-scoped reservation/admission protocol: reserve returns an existing opaque
job ID or a short-lived opaque admission handle; finishing that handle creates
exactly one package job from the computed payload; replay returns the same
existing job/handle without re-planning; expiry/cancellation and response-loss
semantics are explicit. It requires the normal Protocol/Manifest/SQL migration
sequence and a new bridge release if the package database floor moves.

The alternative is a separately specified durable host admission ledger that
owns the canonical request/key, payload snapshot, package job identity,
response-loss replay, retention, and eventual retirement. Neither an
in-memory cache nor a read-only lookup can provide the cross-process,
settlement-race guarantee. Do not change the canonical response,
re-plan-on-replay behavior, use direct queue lookup, add a fallback, start a
worker, or open C6-04 until one choice is made docs-first.

**Resolution:** adopted the queue-native option as a general library feature,
not a QDarte ledger or permanent wrapper. ADR-023, Protocol document revision
1.0.8, Manifest/SQL contract 0.1.5, and the Durable Admission Reservation
Specification freeze a durable `(queue, idempotency_key)` authority with a
pre-plan SHA-256 intent binding, retry-stable UUID handle, single planning
owner, atomic job+receipt finish, typed pending/expiry/cancellation, bounded
retention/cleanup, and bridge-first migration order. QDarte must call reserve
before planning, return an admitted receipt without replanning, and keep no
host mapping/cache or direct fallback. C6-04 remains closed until S5-AR-01/02
and S5-AR-AUDIT complete and the isolated C6-03 created/existed proof is rerun
against the accepted package release.

### S5-QD-CV-CQ-01 — A package contact-result bridge needs the active attempt, but the safe worker handler context intentionally withholds it *(resolved: ADR-022 trusted reporter)*

**Blocking evidence:** CV-02's server-owned bridge correctly requires the
package `job_id`, current `attempt_id`, and `worker_id` to heartbeat before it
will authorize a QDarte domain write. The existing package `WorkerService`
correctly constructs a fence-free `JobContext`: it exposes the job identity,
payload, headers, and cancellation state to a handler but never the active
attempt/fence. This is a deliberate Stage-2 safety contract, not an accidental
redaction. Therefore a normal closed registry handler cannot call the CV-02
result adapter. A raw HTTP claim loop could see the attempt, but it would make
QDarte reimplement worker supervision, cancellation, heartbeat, and
settlement-replay behavior that the package already owns.

**Decision adopted:** ADR-022 adds a runtime-owned trusted side-effect reporter
plus bounded async `JobContext.report_effect()`. The worker passes the current
attempt only to that configured reporter; user handlers never receive a fence.
The reporter does not settle, while `WorkerService` retains heartbeat,
cancellation, ownership-loss, unsafe-sync exit, and fixed-verb settlement
replay. QDarte must use the reporter to ask its stable-effect ledger before an
external probe and to apply the result afterwards. Do not expose an attempt or
fence through `JobContext`, weaken the bridge heartbeat, or add an ad-hoc
QDarte raw claim/settle loop. CV-04 may implement this package extension and
one closed local contact worker; CV-05 remains its response-loss/hard-kill
gate.

### S5-QD-CQ-05 — The run-only pilot worker cannot negotiate its mandatory HTTP metadata read *(resolved: metadata-bootstrap exception)*

**Blocking evidence:** the official `AsyncTaskqHttpClient` calls
`GET /taskq/v1/meta` before its first claim. Tier-0 Protocol v1 pins that
route to the `read` action, and the QDarte host adapter correctly maps it to
`taskq_qdarte_pilot:read`. The approved P4 worker token carries only
`taskq_qdarte_pilot:run`, so its startup receives a typed `AUTH403` before it
can write presence or claim. The isolated facade was then stopped; no job,
worker, QDarte auth row, or legacy-ledger mutation occurred.

**Decision adopted — metadata-bootstrap exception:** the Protocol command
identity remains `meta → read`; the QDarte pilot host adapter may authorize a
`taskq_qdarte_pilot:run` token for that deployment-scoped metadata negotiation
only. It must not translate `run` into a queue-scoped `read` grant: profile,
job detail, job pages, queue stats, and every other `read` command remain
denied to the worker. P4 must prove the positive metadata startup plus direct
negative job/profile reads under the run-only credential. It does not skip
compatibility negotiation, add a broader scope, or modify Tier-0 command
identity.

### S5-QD-CQ-04 — P4 requires a keyed harness producer but freezes no authorized producer identity *(resolved: local-only enqueue token)*

**Blocking evidence:** P3/P4 require an internal/local keyed harness producer to
prove the `created` then `existed` canary, while the pilot privilege model
freezes only two self-contained service-token scopes: the worker receives
`taskq_qdarte_pilot:run` and the acceptance principal receives
`taskq_qdarte_pilot:read`. The same section expressly forbids a public enqueue
route, and P2 evidence proves generic enqueue is rejected by the host-owned
authorizer. No P3 harness exists in either QDarte checkout. Issuing an
unmentioned `:enqueue` token, borrowing the facade database login, or writing
directly through SQL would each introduce a producer path without an accepted
authority and would weaken the isolated HTTP/capability proof.

**Decision adopted — local-only enqueue token:** P4 may issue a third,
short-lived self-contained `taskq_qdarte_pilot:enqueue` service token only to
the checked-in local harness. The harness calls the mounted package facade
over HTTP and owns no route, API setting, database credential, direct SQL, or
persistent token record. It is disposed with the P4 local configuration. The
worker remains `run`-only and the acceptance principal remains `read`-only;
positive enqueue plus every cross-action/wrong-queue denial are required
evidence. The token may not reach a public producer path, use the facade's
PostgreSQL login as a bypass, or access `qdarteapi_dev.taskq` / `qdarte_ops`.

### S5-QD-CQ-01 — Current QDarte staging already carries an incompatible direct-SQL taskq surface *(resolved: Option B)*

**Blocking evidence:** the fresh authoritative staging checkouts contradict the Round-11 source
inventory. `qdarteAPI@9364dd0` contains migration
`20260709_0061_add_taskq_schema.py`, a direct `TaskqClient` that calls a separate
function/catalog family (`taskq.enqueue`, `claim_jobs`, `heartbeat`, `complete_job`, and others),
and copied `/ops/taskq/*` plus `/worker/taskq/*` routes. `qdarte-workers@02ea8fe` contains the
matching direct HTTP worker loop. The guarded local `qdarteapi_dev` database already has a
`taskq` schema. The Stage-5 pilot instead requires the immutable `v0.1.0a3` 0001→0005 contract,
a package-owned mounted facade, and no copied taskq SQL or wire surface. Treating the two as
compatible without proof risks a catalog collision and violates the explicit pilot boundary.

**Decision adopted — Option B:** retain QDarte's direct implementation untouched in
`qdarteapi_dev`; the package pilot uses only a newly created, disposable `qdarte_pilot_dev`
database on the same guarded local cluster. The package keeps its fixed `taskq` schema name,
but the two schemas are in different databases and therefore never share a catalog, route
ownership, credentials, worker, or migration ledger. The dedicated non-superuser facade DSN
targets the pilot database only. P0B confirms the current direct schema remains confined to
`qdarteapi_dev` and the pilot database is absent before P2; P2 alone may create and migrate it.
The existing direct client/routes are neither reused nor retired by this pilot. A later,
separately reviewed convergence decision may evaluate whether QDarte's active contact-verify
queue should migrate to the package. The immutable Round-11 response remains historical; its
`no taskq` inventory was based on a stale source baseline and cannot override this
current-source finding.

### S5-QD-CQ-02 — P2's isolated-database boundary conflicts with P1's QDarte-auth binding *(resolved: Option A)*

**Blocking evidence:** P1's mounted facade deliberately constructs
`OutlabsQueueAuthorizer(auth=app.auth.auth, session_dependency=get_async_session)`. Both
objects are bound to QDarte's existing `outlabs_auth` schema and SQLAlchemy engine in
`qdarteapi_dev`. The adapter resolves that session for every authentication and
queue-authorization check. P2, however, requires queue-scoped worker/read permissions issued
through QDarte's existing service-token lifecycle while also forbidding any query or grant
against `qdarteapi_dev`. Those requirements cannot all hold: the existing authorizer needs that
database for authorization, and the required permission catalog/token scopes cannot be created
there without a narrow IAM mutation. QDarte's own `app.auth` also records that the current
generic OutLabsAuth dependency can consume a valid service token through the ordinary JWT path
and reject it before its service-token backend runs; its host routes use a separate explicit
service-token wrapper to compensate. `OutlabsQueueAuthorizer` invokes the generic dependency,
so the frozen worker-service-token path cannot be assumed to authenticate at the mounted facade.

**Superseded by S5-QD-CQ-03:** the former additive-catalog branch is retained here as
the evidence that exposed the lifecycle mismatch. P2 now uses the verifier-only posture
adopted below; it performs only QP-03's read-only digest against `qdarteapi_dev.outlabs_auth`.

### S5-QD-CQ-03 — Option-A pilot IAM cannot meet the byte-identical teardown oracle through QDarte's supported public service *(resolved: verifier-only self-contained tokens)*

**Blocking evidence:** Option A permits P2 to add the exact
`taskq_qdarte_pilot:read` and `taskq_qdarte_pilot:run` records through QDarte's
public OutLabsAuth permission service, while QP-10 requires owner-only teardown
to delete exactly those pilot records and restore QP-03's full auth content
digest byte-identically. In the pinned QDarte OutLabsAuth artifact,
`PermissionService.delete_permission()` is a soft archive: it retains the row,
sets its status inactive/archived, invalidates caches, and appends a
permission-definition-history event. It also refuses system permissions
entirely. Therefore neither `is_system=True` nor `is_system=False` can restore
the pre-P2 permissions/history digest through the approved public API; direct
SQL cleanup would violate Option A's no-ad-hoc authorization-bypass rule.

**Decision adopted — verifier-only self-contained tokens:** QDarte's supported
service-token verifier is retained, but P2 does not provision a QDarte auth
catalog, role, API key, or persisted token record. The future local worker and
read principal receive distinct ephemeral credentials carrying only the exact
`taskq_qdarte_pilot:run` or `:read` scope; the host-owned `QueueAuthorizer`
validates and checks that embedded scope through QDarte's supported service.
No wildcard, generic-dependency bypass, or operator permission is introduced.
The QDarte auth database is mutation-out-of-scope: QP-03 and QP-10 prove its
digest byte-identical before and after the pilot by construction, using only
the canonical read-only digest. P2 may resume only for the disposable queue
database and its package IAM; P4 alone may issue the ephemeral local credentials
after P3 opens.

### S5-CQ-02 — H-11 flat profile response conflicts with the existing generated PUT envelope

**Blocking evidence:** Protocol v1 §2.5 says canonical `PUT /taskq/v1/queues/{queue}` success
data is the same flat queue-profile projection returned by the new GET. The existing generated
`ensure_queue` command for that identical route, however, has shipped a distinct H-13 model
`EnsureQueueWireData` whose data shape is `{ "profile": { ... } }`; both official HTTP clients
decode that wrapper. The new conditional-update function can supply the profile and ETag, but it
cannot decide whether the established wrapper is replaced, retained as a compatibility envelope,
or split into a new identity without a Tier-0 compatibility decision. Treating the old wrapper as
"close enough" violates §2.5's exact field set; silently replacing it would break existing clients.

**Decision required:** amend the Protocol docs-first to name the canonical H-11 success shape and
the explicit compatibility/rollout posture for the existing generated PUT command, including the
ETag and `If-Match` cases. The decision must say whether clients accept both shapes, whether a new
route/command identity is required, and how old clients behave. Do not add a facade special case or
make the client decoder permissive until that authority is frozen.

### S5-CQ-01 — SQL-contract compatibility window for migration 0004 is unspecified

**Blocking evidence:** `0004_read_models.sql` must advance
`taskq.meta.contract_version` to `0.1.3` (Manifest §11), but the existing
`TaskqRuntime.start()` accepts only exact `0.1.2`. Applying the migration to a
running supported host would therefore make its runtime fail startup. Protocol
§3 requires compatibility-window tests, while the accepted S5 sequence defers
HTTP/client work; neither Tier-0 document defines whether the existing runtime
must accept `0.1.2..0.1.3`, whether migration and a strict runtime bump must be
released atomically, or the supported rollback posture.

**Resolution:** ADR-020 accepts a general closed supported-contract-set rule.
The bridge runtime declares `{0.1.2,0.1.3}` and exposes no read-model surface;
the historical `{0.1.2}` pin remains a regression proof. Applying 0004 raises
the database rollback floor to the bridge. Production application is a later,
separately gated deployment decision after the bridge is both deployed and the
rollback baseline; it is not authorized by S5-RM-01. The runtime decides exact
membership from the database-reported version, with no wire change.

### S5-CQ-03 — active H-08 list function cannot distinguish an unknown queue from an empty view

**Blocking evidence:** Protocol v1 §2.5 requires an authorized missing queue to return `TQ001`,
and requires direct SQL and HTTP to share the same bounded-page semantics. Immutable migration 0004's
`taskq.list_jobs(text,text,integer,jsonb)` instead checks the per-view capability and then queries
`taskq.jobs` without establishing that `taskq.queues.name = p_queue` exists. Once a view capability
is active, an unknown authorized queue therefore returns a successful empty page. A facade-side
`get_queue_profile()` preflight would make HTTP differ from direct SQL and would be an impermissible
workaround.

**Decision required:** authorize a docs-first repair path: a new Manifest/SQL-contract revision and
immutable migration 0005 which keeps the `list_jobs` identity and fixed page composite but raises
typed `TQ001` for an unknown queue before the capability gate/query. The decision must define the
runtime bridge set and production rollback floor for the additional migration. Do not activate or
expose the list route/client until that authority, fresh/full-chain proofs, and SQL/HTTP parity are
frozen.

### S5-CQ-04 — approved H-11 revision number is already occupied by the bridge amendment

**Blocking evidence:** the approved envelope correction names Protocol document revision `1.0.6` /
amendment 13, but the current locked Protocol log already assigns revision `1.0.6` to ADR-020's
supported-contract-set bridge (amendment 13 in the existing log). Reusing that revision would
silently overwrite an accepted compatibility decision and make the document revision non-unique.

**Decision required:** confirm that the approved H-11 envelope correction is the next additive
Protocol revision **1.0.7** (with the next sequential amendment-log number), retaining every
approved envelope/ETag/drafting-error condition. No wire-major or SQL identity changes follow from
this numbering correction. Do not reuse or edit the already accepted 1.0.6 amendment.

**Resolution:** ADR-021 records the approved correction as Protocol document revision 1.0.7 /
amendment 14. It keeps the existing generated `{"profile": {...}}` PUT response as the single
canonical success shape, leaves GET flat, preserves the ETag/If-Match matrix, and records the
revision-1.0.5 flat-PUT statement as a drafting error. The same docs-first ADR reserves Manifest /
SQL contract 0.1.4 and immutable migration 0005 for `list_jobs` existence-before-capability
conformance; no 0004 edit or new wire identity is authorized.

### S5-CQ-05 — approved `ready` B9 evidence has no frozen activation vehicle

**Blocking evidence:** B9 passed for `read_model_list_ready` on PostgreSQL 16 and 18, while
`running` and `finished` remain rejected. ADR-021 / Manifest §12 deliberately say migration 0005
does **not** activate a view, and the manifest exposes no operator function that may mutate
`taskq.meta.capabilities`. The generated facade and direct SQL now correctly return `TQ501` outside
the isolated parity vector, but no immutable migration identity or deployment authority says how an
approved capability becomes active. Updating metadata manually would evade the migration ledger and
would make verification unable to distinguish the approved posture from drift.

**Decision required:** freeze a docs-first activation vehicle and rollback posture for the
ready-only capability. The narrow candidate is an immutable metadata-only migration 0006 under the
existing SQL contract 0.1.4, named in the Manifest before implementation, which asserts 0.1.4
metadata and sets exactly `{"active":["read_model_list_ready"]}`. It must preserve `running` and
`finished` inactive, extend fresh/full-chain PG16/18 and `verify()` proofs, and state whether a
post-0006 database has a new runtime rollback floor. Do not enable the capability through manual
SQL, an HTTP configuration route, or a facade-side exception before this authority is frozen.

**Resolution:** S5-CQ-05 is approved. Manifest §13 reserves immutable, metadata-only migration
0006 under unchanged SQL contract 0.1.4. It asserts 0.1.4 metadata and writes exactly
`{"active":["read_model_list_ready"]}` on the committed `7fe2c6b` B9 evidence; `verify()` and
the PostgreSQL 16/18 fresh/full-chain transition vectors must assert that exact posture. A future
deactivation requires a successor metadata migration, never manual DML.

### S4-CQ-04 — Canonical OutLabs authorization rejects the live system-integration API key

**Blocking evidence:** before any canary enqueue, production was switched to taskq mode with only
the read-only `umami` lane allowlisted. The pre-existing `TOOLS_API_KEY` is not a valid OutLabsAuth
credential: both the host queued-tools dependency and `/taskq/v1/meta` returned 401. A replacement
ephemeral system-integration principal/key was then created through exact a24's public services with
only `tools:run` and `taskq_tools:read`. The host route authenticated it and reached its post-auth
validation boundary (422 for deliberately invalid parameters), proving `tools:run`; the canonical
taskq facade returned `TQ503` with fixed reason `auth_infrastructure_unavailable` on every one of
three retries for the same `X-API-Key`. Bearer presentation returned 401 and presenting both did not
change the typed 503. This contradicts the accepted Stage-3/Stage-4 posture that one supported
OutLabs system-integration credential can enqueue through the host route and perform canonical
queue-scoped readback. No enqueue request was sent, worker/tool invocation markers remained zero,
the ephemeral principal and owned key were archived through the public service, and its temporary
file was deleted. The production producer was restored to `legacy`, the temporary production and
preview allowlist variables were deleted, the rollback deployment finished healthy, and the live
container reports health 200, taskq enabled, mode `legacy`, and no allowlist.

**Recommended adjudication:** reproduce the exact a24 system-integration-key path against
`OutlabsQueueAuthorizer` with its real session dependency and Redis-backed auth configuration, then
repair the canonical adapter or the pinned OutLabsAuth dependency at the failing supported surface.
The regression must prove authenticate → queue-scoped authorize for `taskq_tools:read`, denial for a
key lacking that scope, principal-fingerprint stability across the two phases, and unchanged typed
429/503 normalization. Do not authorize from the host's custom `require_outlabs_api_key` helper,
weaken fail-closed rate limiting, substitute direct SQL readback, or grant a global/wildcard scope.
If taskq source changes, publish a new immutable alpha and update the host URL/hash pin before
resuming the exact pre-enqueue probe. Production remains in legacy mode until the canonical 202→GET
path passes with the real supported credential.

**Resolution:** closed in taskq commit `36db7cf` and immutable release `v0.1.0a2` (wheel SHA-256
`d3c37b0e30dbc75cbbb279c3e3f64a7df7416bf51ca1acfd016544c03e745f42`). The adapter now obtains
and caches OutLabsAuth's checker on the first post-startup request instead of freezing the
pre-initialization service; an exact a24/Redis-backed regression proves the real system-integration
key path, queue-scoped allow/deny, stable two-phase fingerprint, and unchanged sanitized 429/503
failure posture. Host commit `76ff5e1` pins the exact release artifact. A production ephemeral key
then returned 200 from `GET /taskq/v1/stats/queues/tools` and 403 from undeclared global
`GET /taskq/v1/meta`; every proof principal/key was revoked and archived. No SQL, migration,
contract, ADR, role, grant, or wildcard scope changed.

### S4-CQ-03 — Immutable migration cannot execute after `SET ROLE taskq_owner`

**Blocking evidence:** S4-CQ-02 condition 4 requested `taskq migrate/verify` under the owner via
`SET ROLE taskq_owner`. An executed local scratch-database probe connected as the owner, granted
database `CREATE` to the existing `NOLOGIN taskq_owner`, ran `SET ROLE taskq_owner`, and invoked the
packaged installer. Migration 0001 failed with `InsufficientPrivilegeError: permission denied to
alter role` at its required capability-role hardening. This is structural: the immutable migration
must create/validate and alter the producer, runner, observer, operator, and housekeeper roles;
`taskq_owner` correctly has neither CREATEROLE nor admin membership, and adding either would violate
the locked reserved-role manifest. Do not change migration 0001, broaden `taskq_owner`, or improvise
a partial production install.

**Recommended adjudication:** name the PostgreSQL owner/admin login as the execution credential for
`taskq migrate` and `taskq verify`, without `SET ROLE`. The immutable migration itself assigns all
taskq objects to the `NOLOGIN taskq_owner`, revokes PUBLIC access, grants only capability roles, and
`verify()` independently proves those ownership/grant facts. Retain the approved restricted runtime
and operator login boundaries unchanged. This is the smallest correction and matches ADR-004,
ADR-010, the Function Manifest, and the already executed S4-01/S4-03B migration evidence. Making
`taskq_owner` capable of managing roles would require a new contract/ADR/migration design and is not
recommended.

**Resolution:** approved. `taskq migrate` and `taskq verify` execute directly as the PostgreSQL
owner/admin without `SET ROLE`. The immutable migration remains responsible for assigning every
object to `taskq_owner`, and `verify()` remains the independent ownership/grant oracle. No role
attribute, SQL, migration, manifest, ADR, runtime, or operator-boundary change is authorized or
needed. The restricted-login real-boot proof and rotation may resume.

### S4-CQ-02 — The production app pool is PostgreSQL superuser

**Blocking evidence:** the approved same-cluster preflight connected through the exact deployed
`POSTGRES_DSN` and measured `current_user=postgres`, `rolsuper=true`, `rolcreatedb=true`, and
`rolcreaterole=true`. Taskq's six contract roles are correctly `NOLOGIN`, but PostgreSQL superuser
bypasses their privilege boundaries. Therefore the ordinary application pool can execute
operator-only functions even if it is not granted `taskq_operator`; this directly contradicts
ADR-011 and Stage-4 specification §4.1's requirement that the operator credential is never present
in the app pool. Do not migrate the production database, provision production IAM/queue state, or
set `TASKQ_ENABLED=true` while the runtime DSN remains superuser.

**Recommended adjudication:** keep the approved one-database topology, but introduce a dedicated
non-superuser host runtime login. Grant it only the existing host runtime access plus
`taskq_producer`, `taskq_runner`, `taskq_observer`, and `taskq_housekeeper`; prove it cannot
`SET ROLE taskq_operator`, call `ensure_queue`, create roles, create databases, or bypass row-level
security. Rotate the application's `POSTGRES_DSN` to that login. Run host/auth/taskq migrations and
operator provisioning through an owner/operator credential used only by an explicit one-off
pre-deploy action and never injected into the running application. Before rotation, derive and test
the exact public/outlabs-auth runtime grants on a disposable database; after rotation, prove host
health and the disabled taskq posture before production taskq migration. Keeping the superuser app
pool would require explicitly reopening the accepted privilege-separation design and is not
recommended.

**Resolution:** approved. Before rotation, a disposable same-cluster database must boot the real
application and worker under the proposed login, pass startup, one authenticated request, and a
legacy `outbound_tasks` operation, and prove denial of operator `SET ROLE`/`ensure_queue`,
CREATEROLE, CREATEDB, and RLS bypass. The prior owner DSN remains available outside the running app
for immediate env-flip rollback. The deploy record names every credential: host Alembic and Auth
migrations use the owner; taskq migrate/verify use the owner with taskq-owner authority; queue
ensure and IAM use an operator-only login; the app and workers use only the restricted runtime.
Sequence is grants proof → rotate → healthy disabled posture → production taskq provisioning →
legacy-mode enablement. Restore/PITR rehearsal stays an explicit host backlog item, and the audit
packet records the rotation as a host-security improvement.

### S4-CQ-01 — The live production database is not the frozen Neon target

**Blocking evidence:** the accepted Stage-4 specification states that production is managed
PostgreSQL behind the canonical Neon DSN, and S4-01 proved migrations, split taskq roles, IAM,
queue profile, TLS, and `max_connections=901` only on a disposable Neon branch. The first real
Coolify deployment showed that the production application actually tracks `staging-prep` and its
`POSTGRES_DSN` points to Coolify's internal PostgreSQL service. Host commit `d1b00fe` is now deployed
healthy with taskq explicitly disabled; `alembic current` is `20260616_0005 (head)`, the public
health check is 200, `/taskq/v1/meta` is 404, unauthorized enqueue is 401, and a read-only query in
the running container reports `to_regnamespace('taskq') IS NOT NULL = false`. Enabling against the
current DSN would therefore provision a database that the frozen production proof never covered;
switching the whole host to Neon would be a separate data migration; adding a taskq-only DSN would
be a new topology. Do not set `TASKQ_ENABLED=true`, run taskq migrations, or reconcile taskq IAM in
production until this is adjudicated.

**Recommended adjudication:** amend the living Stage-4 deployment target to the actual Coolify
PostgreSQL service for this first-host dogfood, then repeat the S4-01 production preflight in place:
record server/TLS/pool facts and `max_connections`, prove the migration owner can create the split
roles, run immutable migrations plus `verify()`, reconcile the exact IAM catalog and `tools` queue
profile, and only then acknowledge production enablement in legacy mode. This avoids an unrelated
application-data migration and preserves the one-database embedded-host topology. If Neon remains
mandatory, authorize and spec either the host data migration or a separate taskq DSN before source
or production changes.

**Resolution:** approved for the actual Coolify-internal PostgreSQL service. The Tier-3 specification
records the deployed `staging-prep` reality and stale-default-branch audit, the Postgres-backed legacy
path, no-WhatsApp boundary, deployed gate counts, mutual-exclusion/R6-06 analysis, and superseded
Neon-specific facts. Before enablement, a disposable database on the same cluster must reproduce the
complete S4-01 role/migration/verify/IAM/profile proof, record connection/TLS plus backup/durability
posture, and be removed by dropping only that database. The Neon mechanics remain evidence; its
pooler/TLS-proxy/901 facts are non-applicable. Stage 4 also owns post-acceptance reconciliation of
`main` and the production line.

### S3-CQ-01 — HTTP worker presence is absent from Protocol v1

**Blocking evidence:** the Tier-0 Function Manifest 0.1.2 exposes runner command `taskq.worker_heartbeat(...)` with closed `continue | shutdown_requested` semantics, ADR-011 requires the facade runtime to call it on behalf of HTTP workers, and the completed Stage-2 `WorkerService` depends on it for advisory presence and remote drain. But the Tier-0 Protocol v1 adopted HTTP table defines claim, per-job heartbeat, worker reads, and shutdown requests without a canonical worker-presence HTTP command. ADR-005 makes route shape, authorization inputs, outcomes, and HTTP mapping contract-owned; S3-00 cannot invent them in Tier 3 or claim SQL/HTTP worker parity without this closure.

**Recommended adjudication:** accept ADR-014 plus an additive Protocol-v1 revision defining `POST /taskq/v1/workers/heartbeat`: body `worker_id`, non-empty distinct `queues`, and bounded safe presence fields; authenticate first, authorize `run` for every distinct declared queue, treat `worker_id` as an advisory validated label while actor remains the authenticated subject, call `worker_heartbeat`, and return HTTP 200 with typed `continue | shutdown_requested`. The route must never accept actor, credentials, attempts, payloads, or fences. Add it to the H-13 generated HTTP-client/conformance surface. SQL contract 0.1.2 and the Function Manifest remain unchanged; the adjudication must state the required additive protocol-document version marker before S3-00 resumes.

**Resolution:** accepted ADR-014 and additive Protocol v1 document revision 1.0.1. The canonical route is `POST /taskq/v1/workers/heartbeat`; every distinct declared queue requires `run`; `worker_id` remains advisory while the authenticated subject is the actor; the two typed success outcomes are `continue | shutdown_requested` on HTTP 200. Worker presence extends no lease and carries no fence. H-13 generation and SQL/HTTP parity include the command. SQL contract 0.1.2 and the migration chain are unchanged.

Resolved history: ADR-014 resolves S3-CQ-01 as Protocol v1 document revision 1.0.1; ADR-013 resolves S2-CQ-01 as contract 0.1.2; ADR-012 resolves round-3 CQ-01/CQ-02.

### S3-CQ-02 — Active queue-profile read route has no SQL-safe backing

**Blocking evidence:** the adopted Tier-0 Protocol v1 base declares
`GET /taskq/v1/queues/{queue}` as an active `read` command backed by a “safe queue projection.”
Function Manifest 0.1.2 has no queue-profile read function, and migration 0001 exposes exactly three
observer views: `queue_stats`, `dead_jobs`, and `worker_status`. The observer role has no base-table
`SELECT`; ADR-010/011 forbid broadening the ordinary facade credential or falling back to its
separate operator pool. `taskq.ensure_queue` is operator-only and mutating, so it cannot honestly
serve a GET. Unlike `list_jobs`, this route is not marked deferred by H-08 or another capability.

**Recommended adjudication:** accept ADR-015 plus additive Protocol v1 document revision 1.0.2 that
marks queue-profile GET unavailable in 0.1 (`TQ501`, capability inactive) and defers its exact safe
projection plus optimistic-concurrency contract to the already-deferred H-11 interactive-admin
slice. `PUT /taskq/v1/queues/{queue}` remains the bootstrap/admin command and returns its canonical
profile. This closes Stage 3 without changing SQL contract 0.1.2 or adding migration 0004. The
alternative is a docs-first Function Manifest 0.1.3 addition for an observer-safe
`get_queue_profile(text)` plus immutable migration and PG16/PG18 fresh/upgrade evidence.

**Resolution:** accepted ADR-015 and additive Protocol v1 document revision 1.0.2. The queue-profile
GET moves visibly into the deferred-routes section, returns `TQ501` while inactive, and is excluded
from H-13's active generated client/OpenAPI/conformance surface. H-11 must reactivate it through the
Growth §4 / R2-16 exact observer projection and read-model design. Observers retain queue stats;
administrators receive canonical profiles from idempotent ensure. SQL contract 0.1.2 and migrations
0001–0003 are unchanged; there is no migration 0004.

### S3-CQ-03 — Remaining active wire models are not fully implementable

**Blocking evidence:** the final H-13 model derivation found three independent gaps in the adopted
Tier-0 wire text. First, every response requires `request_id` from a “validated inbound correlation
header,” but the header name, accepted grammar, length, and generation/echo behavior are absent.
Second, active `PUT /taskq/v1/queues/{queue}` promises a canonical profile **plus version**, while
`taskq.ensure_queue` returns the canonical profile with no version column or value; H-11 explicitly
defers optimistic concurrency. Third, active `GET /taskq/v1/workers` promises a safe presence
projection but freezes neither fields nor pagination; the only SQL backing is observer-granted
`worker_status`, whose `w.*` includes hostname, pid, and arbitrary direct-SQL `meta`, so forwarding
the view would violate the no-secret/network-detail promise and an invented projection would violate
R2-16/H-13. These are protocol-owned inputs/outputs, not Tier-3 implementation choices.

**Recommended adjudication:** accept ADR-016 plus additive Protocol v1 document revision 1.0.3 as
the final Stage-3 wire normalization: (1) reserve `Taskq-Request-Id`, accept 1–128 ASCII characters
matching `[A-Za-z0-9._:-]+`, generate a lowercase UUID when absent, and echo the value in the body
and response header; (2) correct queue ensure's 0.1 response to the exact canonical profile with no
version, reject `If-Match` as H-11-inactive (`TQ501`), and add version/If-Match only with H-11; (3)
move worker list into the explicit deferred-routes section with `TQ501`, excluded from H-13's active
generated surface, until Growth §4/R2-16 freezes a bounded observer projection, redaction, cursor,
authorization, and plan evidence. Keep per-worker presence writes and worker shutdown/expiry
commands active; no SQL contract or migration changes.

**Resolution:** accepted ADR-016 and additive Protocol v1 document revision 1.0.3. The canonical
`Taskq-Request-Id` is bounded, server-minted when absent, and echoed without unbounded persistence;
queue ensure returns its exact version-free SQL profile and rejects premature `If-Match` with
`TQ501`; worker list remains declared in H-13 behind a typed `TQ501` gate with no success schema
until R2-16 freezes the safe projection. Queue detail remains deferred out because its whole read
model is absent, while worker list stays declared because only its public projection is pending.
SQL contract 0.1.2 and migrations 0001–0003 remain unchanged.

### R5-CQ-A — General job list has no adjudicated 0.1 disposition

**Blocking evidence:** Protocol amendment 3 calls the adopted `GET /taskq/v1/jobs` row
“operator-minimal, pre-H-08,” while the Protocol exit status says H-08 is deferred behind a
capability gate. Function Manifest §7 implies an operator-minimal form exists, but migration 0001
explicitly records `list_jobs: absent`, and the exact 0.1.2 catalog contains no function or view that
can serve any list form. Tier 3 cannot decide whether the route is active, gated, or deferred.

**Recommended adjudication:** ADR-017 / Protocol document revision 1.0.4 applies ADR-016's
undesigned-command rule: defer `GET /taskq/v1/jobs` out of H-13, add it visibly to the §2.2 deferred
table with H-08's Growth §4/R2-16 projection/cursor/index/plan reactivation gate, correct amendment
3, and state in the Function Manifest that no `list_jobs` exists in 0.1. A reserved-path negative
vector returns typed `TQ501` while remaining absent from OpenAPI/client success surfaces. No SQL or
migration change.

**Decision needed:** approve the recommended deferral or select a contract-backed 0.1 disposition.
Do not amend Tier 3 or start S3-01 first.

**Resolution:** accepted ADR-017 and additive Protocol v1 document revision 1.0.4. The general list
route is deferred out with a hidden typed `TQ501` responder, no success model or generated client /
OpenAPI operation, and an H-08 Growth §4/R2-16 reactivation gate. Protocol H-08 and amendment 3 plus
Manifest §7/errata now agree that no `list_jobs` exists in 0.1. SQL contract 0.1.2 and migrations are
unchanged.

### R5-CQ-B — Enqueue `created_at` has no contract-backed source

**Blocking evidence:** the adopted Protocol base promises `created_at` in the single-enqueue
`created` response, while `taskq.enqueue(...)` returns only `(job_id, created)`. The core model has no
`created_at`; its queue/job-type/idempotency/schedule fields are request echoes, not durable row
truth—especially for `existed`. A follow-up observer read would mix capabilities and add a round
trip; a facade timestamp would be invented.

**Recommended adjudication:** the same ADR-017 manifest-wins amendment removes `created_at` from
0.1 enqueue responses. The wire result contains durable `job_id` plus created/existed disposition;
any request-echo fields are explicitly labeled non-authoritative and cannot masquerade as stored
state. Clients needing timestamps use authorized job detail. H-13's independent catalog oracle
asserts exact response-field sets per command. No SQL or migration change.

**Decision needed:** approve the manifest-backed response or authorize a different Tier-0 source.
Do not add an observer lookup or client-clock field in Tier 3.

**Resolution:** accepted ADR-017 and additive Protocol v1 document revision 1.0.4. Single enqueue
returns exact envelope outcome `created | existed` and authoritative `data.job_id` only; queue is
implied by the path, `created_at` and request echoes are absent, and authorized job detail owns stored
timestamps/state. The same amendment completes invalid request-id mint/reject ordering. SQL contract
0.1.2 and migrations are unchanged.

## Round-5 finding dispositions

The immutable response verdict was **BLOCKED**. ADR-017 resolves R5-CQ-A/B and R5-09. The amended
Stage-3/Auth/Harness designs close R5-01..08, R5-10/11, and R5-16 with the approved mechanisms and
acceptance vectors. No SQL, migration, grant, or source change was required. Residual findings are
owned explicitly rather than treated as closed:

- **S3-01:** R5-27, R5-37, R5-38, R5-39, R5-40, R5-41, R5-43 (artifact/package boundary, exact capability methods and view
  close behavior, retry request IDs, sync thread safety, 1-based bulk index, worker-owned settle retry).
- **S3-02 (closed):** R5-14, R5-17, R5-18, R5-19, R5-22, R5-23, R5-24, R5-33, R5-42 (hiding equality, diagnostic truncation,
  dynamic listener/disconnect races, stats semantics, envelope wording, metrics default, gated-worker
  action before activation, normative long-poll sequence).
- **S3-03 (closed):** R5-20 (runtime-owned unsafe-sync process-exit actor and live-ASGI evidence).
- **S3-04 (closed):** R5-12, R5-13, R5-15, R5-31, R5-32, R5-34, R5-35, R5-36 (auth 429/503, session lifecycle, queue input grammar,
  seed side effects, API-key wildcard honesty, legacy candidates, alpha/API names, transaction savepoint).
- **S3-AUDIT (closed):** R5-21, R5-25, R5-26, R5-28, R5-30 plus the accepted S3-02 §8 route-mechanism wording correction (independent oracle proof, exact CI/artifact claims, raw-read
  parity mutation, front-door freshness, documentation accuracy). R5-29 is closed by S5-RM-ADR's exact Growth §4 / H-08/H-11
  reactivation contract; implementation evidence remains owned by S5-RM-01 and its follow-on surface/B9 tasks.

## Round-4 finding dispositions

The response verdict was **BLOCKED**. R4-01..12 are accepted as source-backed implementation, evidence, or CI findings; no Tier-0 conflict exists. R4-01..08 were the worker-kernel remediation gate; the Stage-2C audit closes R4-09..12 with the pre-0.1.2 decode pin, SQL claim bounds, cancelled-stop-waiter ledger, and scheduled million-row plan lane.

## Round-3 finding dispositions

All seven findings are **accepted as source-backed**; ADR-012 resolved the two Contract questions. R3-01, R3-02, and both Contract questions were independently reproduced after the response landed; R3-03..07 agree with the cited ADR/harness/source gaps. R3-07 is an evidence-hardening item rather than a direct contract violation. No finding is rejected or deferred into Stage 2.

## Done

- [x] **T-HARNESS-01 · Capability-fixture ordering pinned** — `role_conn` now explicitly depends on the per-test `pg` truncation fixture, preventing pytest-asyncio from initializing capability sessions before the state reset and erasing a freshly provisioned queue mid-test; the scratch-only truncation also retries a transient deadlock while prior capability connections unwind. This is harness-only: no taskq SQL, wire, capability, application, or production behavior changed.

- [x] **S4-POST-R3 · Authoritative-main and deployment-branch cutover** — R8A-01's immediate recheck passed; `main` advanced without force from `7df6b7f` to exact-tree candidate `2ed736b`, and Coolify API plus standing worker now run that identical revision with unchanged settings digests. A keyed read-only Aerolineas request proved `created`→`existed` same-id convergence and authorized canonical `succeeded` readback; a validation-only newsletter probe left all three legacy rows unchanged. The annotated `3f50b7d` rollback tag, pinned to its peeled SHA for a platform-verifiable revision, booted both resources against the unchanged PG16.14/Alembic database, preserved auth, health, worker command, zero active depths, and required no manual DML; both resources were then restored to exact `main@2ed736b`. One simultaneous worker rebuild hit a transient BuildKit snapshot-cache failure and succeeded on sequential retry without replacing the running worker. Host evidence commit `6f566c1` records the complete transcript; independent BR-06..10 acceptance remains the only open gate.

- [x] **S4-POST-R-AUDIT-RESPONSE · Candidate independently accepted** — registered the targeted response byte-for-byte as immutable Tier 4 (SHA-256 `2e86e692b35d62f70b0aa4d96f103035ac47367c5b002ed432f23b9337c5b78f`). Raw Git regeneration confirms candidate `2ed736b`, exact ordered parents, accepted tree `ded6d43`, empty recursive diff, both histories as ancestors, true fast-forward eligibility, 27/3 ledger counts, all four ledger checksums, source-backed default dispositions, zero forward ports, and all three annotated remote tags. Lock, host 72/72 plus five skips, Ruff, 64-file MyPy, Alembic/import gates, exact dependency pins, and same-tree harness inheritance pass; zero Contract questions. R8A-01 binds an immediate pre-move recheck of refs, Coolify branch/revision, and live health because platform policy state was not inspectable. READY authorizes only `main` fast-forward and frozen deployment cutover; retirement, deletion, side-effecting lanes, and Stage 5 remain closed.

- [x] **S4-POST-R2 · Exact-tree two-parent candidate constructed** — host candidate `2ed736b` on non-deploying `codex/s4-post-r2-reconcile` has parent 1 `9348f85`, parent 2 old `main` `7df6b7f`, and exact tree `ded6d43ace2fced88600f19128dedcfcfe9fe0be`; raw and name-status diffs from the accepted parent are empty, both histories are ancestors, and current `main` is fast-forward eligible. Three remote annotated rollback tags peel exactly to old main, deployed `3f50b7d`, and accepted evidence `9348f85`. Host evidence commit `a2500a4` records the construction separately and is not a candidate input. Lock, 72/72 plus five-skip suite, Ruff, 64-file MyPy, offline Alembic, taskq/configured-host imports, and API/worker images (`e84309e`, `b1dd914`) are green. `origin/main` remains `7df6b7f`, `origin/staging-prep` remains `3f50b7d`; no Coolify/deployment/database/environment/production, retirement, deletion, side-effecting-lane, or Stage-5 change occurred.

- [x] **S4-POST-R-AUDIT-REQUEST · Targeted pre-move gate assembled** — the request requires independent R1 regeneration, raw Git parent/tree/diff/ancestry proof, annotated tag peeling, remote/deployment non-mutation, exact dependency and host gates, explicit local-harness sufficiency judgment, Contract questions, and BR/R8-01 dispositions. The reviewer may create only `docs/design-review-8/R-AUDIT-RESPONSE.md`; no authoritative ref, deployment, production, retirement, deletion, side-effecting-lane, or Stage-5 action is authorized.

- [x] **S4-POST-R1 · Host reconciliation ledger derived** — host evidence commit `b78ca5e` records all 27 production/evidence-only and three default-only commits, each with affected surfaces, one allowed disposition, semantic evidence, a named wrong-disposition oracle, and independent-review status. The three default changes are superseded or already present: there are zero forward ports and zero rejected production behaviors. R8-01 is frozen to base parent `9348f85`, old-main parent `7df6b7f`, and expected tree `ded6d43ace2fced88600f19128dedcfcfe9fe0be` with no allowed differing path. The host ledger commit is evidence-only on the non-deploying branch and is deliberately not the future candidate parent/tree. Host gates remain 72/72 regular plus five infrastructure skips, Ruff clean, and MyPy clean across 64 files. No merge candidate, tag, branch/default ref movement, deployment, database command, environment change, or production probe occurred.

- [x] **S4-POST-R8-RESPONSE · Round-8 response recorded; reconciliation READY** — registered the external response byte-for-byte as immutable Tier 4 (SHA-256 `957dbb3cad99a13b87ec1ee9eee5c72d5434e30d8ca070086c69395f90678732`). The reviewer independently reproduced the graph, complete legacy-tools surface, taskq 450/450 plus one opt-in skip, host 72/72 plus five infrastructure skips, and clean linters; it returned READY with zero Contract questions and no R1 preconditions. R8-01 binds R1/R-AUDIT to candidate-tree equality with the accepted tree plus only named forward ports and zero unclassified paths. R8-02/03/05 require docs-first retirement amendments before L1; R8-04/06 belong to L2. READY authorizes reconciliation only—no retirement, branch deletion, side-effecting lane, or Stage 5.

- [x] **S4-POST-R8-REQUEST · Round-8 gate assembled** — the immutable request pins taskq `fef775e..9feaf79` and independently re-derivable host identities (`a0019cd`, `7df6b7f`, `3f50b7d`, `9348f85`). It requires an authority-first governance sweep, independently generated branch and legacy-call inventories, adversarial exact-tree/fast-forward/tag/branch-cutover analysis, high-water and invocation-oracle falsification, all four mixed-version producer/consumer windows, security/data/non-tools preservation, BR-01..10 and LR-01..12 dispositions, and explicit Contract questions. The reviewer may create only `docs/design-review-8/RESPONSE.md`; no implementation, ref movement, deployment, production mutation, retirement, side-effecting-lane migration, or Stage-5 work is authorized.

- [x] **S4-POST-00 · Host convergence and tools-retirement plans frozen** — added separate Tier-3 specifications for (1) production-derived, ledger-driven branch reconciliation and (2) tools-only legacy producer/consumer retirement. Reconciliation starts from host common ancestor `a0019cd`, stale default `7df6b7f`, deployed `3f50b7d`, and accepted evidence `9348f85`; it forbids blind merge/rebase/force-push and requires a two-parent exact-tree oracle, fast-forward-only `main`, identical-commit deployment-branch cutover, rollback tags, and independent audit. Retirement follows only after accepted reconciliation, requires seven days/two deploys with zero new legacy `tool_run` rows, removes producer then consumer across separate rollback windows, and explicitly preserves the shared table, migration, worker, non-tools lanes, and future hard-kill gate. Pre-change gates reproduce taskq 450/450 plus one opt-in skip and host 72/72 plus five infrastructure skips, with Ruff and host MyPy clean. No source, branch, deployment, SQL, migration, Tier-0, IAM, or production change occurred.

- [x] **S4-AUDIT-ACCEPT · Stage 4 independently accepted** — registered the targeted delta response byte-for-byte as immutable Tier 4 (SHA-256 `982ec8594b8f621089f4963486a7e2487ed1d9e1b5b4e51e474f145db0b6405d`). The reviewer independently reproduced all five delta checks and declared `ACCEPTED — Stage 4 complete`: the production Aerolineas `created`→`existed`→canonical `succeeded` chain and one-attempt raw oracle, honest 28-connection usable headroom, corrected graceful-release versus hard-kill semantics, docs-only scope, response identity, and both repositories' green gates. This acceptance authorizes neither legacy retirement nor branch reconciliation; each requires a separate specification, and the hard-kill lease-expiry drill remains mandatory before any side-effecting lane migrates.

- [x] **S4-R7-DELTA-GATE · Targeted acceptance packet assembled** — the immutable delta request pins taskq `5fef55c..96194a8`, host `7c60229..9348f85`, and the byte-identical round-7 response. It limits re-review to R7-01, R7-02/R7-04, exact hygiene, and unchanged-source gates; acceptance explicitly authorizes neither legacy retirement nor branch reconciliation. Taskq passes 450/450 with one opt-in skip against PostgreSQL 18 and a disposable CI-shaped Redis plus Ruff clean; host passes 72/72 with five existing infrastructure skips plus Ruff and 64-file MyPy clean.

- [x] **S4-R7-02 · Cycle-2 production canonical closure recorded** — host `9348f85` corrects the earlier local-versus-production wording and records one live safe Aerolineas request submitted twice with the same idempotency key: HTTP 202 `created` then 202 `existed`, identical job `019f7f95-3c93-71ce-9c8a-7c610212dead`, followed by authorized canonical HTTP 200 `succeeded`. A separate read-only production-table oracle proves exactly one successful attempt, zero failures/releases/expiry streak, and `enqueued -> claimed -> succeeded`; no sensitive columns were selected, and the temporary key/principal were revoked/archived. The same packet now computes 52 connections against the usable 80 ceiling-minus-reserve budget, leaving honest headroom 28. Targeted delta acceptance remains the only S4-AUDIT gate.

- [x] **S4-R7-01 · Frozen controlled-failure drill corrected** — living Stage-4 §6 now states the mechanism production actually proved: a graceful rolling replacement releases the held async job as budget-free `worker_shutdown`, then a different worker process reclaims the same job id and succeeds. It no longer claims that a graceful stop can prove lease expiry. Before any side-effecting lane migrates, the named future side-effecting-lane expansion slice must hard-kill the owning process past platform grace and produce a read-only `expired/lease_expired` → same-id reclaim → terminal convergence oracle with correct budget arithmetic and zero manual DML.

- [x] **S4-R7-RESPONSE · Round-7 response recorded** — registered the external response byte-for-byte as immutable Tier 4 (SHA-256 `d110e13a7edd3300bfe9f911a22edd58cd2867aa2abbf74cc4e5267e19370bdd`). Its verdict is BLOCKED by exactly two documentation/evidence preconditions: R7-02 requires one production Aerolineas keyed `created`→`existed` pair plus canonical succeeded GET and honest 28-connection budget headroom wording; R7-01 requires the frozen §6 drill text to distinguish graceful worker-shutdown release from lease-expiry reap and to gate every future side-effecting lane on a true hard-kill drill. Everything else is accepted in substance; S4-AUDIT, legacy retirement, and branch reconciliation remain closed pending the targeted delta acceptance.

- [x] **S4-AUDIT-EVIDENCE-DELTA · Independent production oracles recorded** — host `7c60229` and the immutable round-7 evidence addendum record a read-only production-table oracle for the controlled-failure job (`attempt_count=2`, `failure_count=0`, `release_count=1`, `released/worker_shutdown` then `succeeded/success`, and `enqueued -> claimed -> released -> claimed -> succeeded` across two worker actors) without selecting payloads, results, errors, messages, attempt ids, or fences. The deliberately retained legacy proof row naturally exhausted attempt 5 of 5 and became terminal `failed`, unleased, at `2026-07-20T12:20:10.105189Z`; no retry acceleration or manual DML occurred. Round-7 acceptance remains required.

- [x] **S4-AUDIT-EVIDENCE · Production completion packet assembled** — host `5a8cb78` records immutable release/host identities, actual PG16.14 provisioning and durability facts, full connection arithmetic, both normal cycles, the 25.434478-second same-job attempt-2 recovery, canonical queue-scoped readback, and the complete producer-switch → zero-depth → disabled-runtime → authenticated legacy enqueue → corrected taskq re-enable transcript. The invalid intermediate `tools_mode=true` candidate is disclosed: strict settings rejected it before readiness and Coolify retained the old healthy container. Final production is healthy at `3f50b7d`, taskq mode is restored with both selected read-only tools, the private probe is absent, active depth is zero, one new worker is online, and temporary credentials were revoked/archived. No manual DML, schema/IAM repair, legacy retirement, branch reconciliation, taskq SQL/migration/Tier-0/ADR/source change, or performance claim occurred. Taskq passes 450/450 plus one opt-in skip on PostgreSQL 18.3 and isolated 16.14 with Ruff clean; host passes 72/72 plus five existing infrastructure skips, Ruff, and 64-file MyPy. Round 7 owns independent acceptance; S4-AUDIT remains open until its response is recorded.

- [x] **S4-03F · Local acceptance matrix and platform-drain completion** — host commit `97b154c` adds an idempotent local production-shape setup and real mounted-route harness. Against restricted PostgreSQL and real Redis it proves 20-way keyed convergence with one invocation, queue-hiding equality and no denied mutation, committed-response-loss settlement replay with one invocation, typed depth refusal, sub-five-second endpoint responsiveness during held work, immediate poll-only recovery after a killed SQL connection, same-id budget-free soft-stop recovery, and a zero-session post-shutdown ledger. The host passes 72/72 regular tests with five pre-existing infrastructure skips, Ruff, and 64-file MyPy; taskq passes 450/450 on PostgreSQL 18.3 with one opt-in skip and Ruff clean. A normal Coolify rolling deployment then held job `019f7f21-59e3-7683-8a77-bc875a5c49bf`; replacement health preceded old-container removal, which completed in 25.434478 seconds inside the 35-second grace. The same job succeeded on attempt 2 with zero failures and no manual DML. Host commit `1fd5050` records the transcript. A final healthy deployment applied `TASKQ_DOGFOOD_PROBE_ENABLED=false`, and the running container reports `probe_flag=false probe_registered=false`. S4-03 is complete; rollback/re-enable and independent acceptance remain S4-AUDIT-owned.
- [x] **S4-03E · Cycle-2 host hardening and local production-shape proof** — production first exposed two host-only defects after `aerolineas` joined the allowlist: the optional credential field was serialized as null, then the public flight gateway rejected the default programmatic-client fingerprint; host `b1b5604` and `8084dfc` omit the absent field and send the official public-web channel headers. A real external 200 then exposed the 8KB result boundary: the oversized result made settlement fail closed, stopped the embedded worker, and changed health to 503. Host `3f50b7d` now converts bulky successful tool output into a 247-byte honest omission record (`result_omitted`, original byte count, SHA-256), with a regression below H-09. A fresh isolated local environment ran host/Auth/taskq migrations, exact IAM and queue provisioning, restricted runtime grants, real Redis, the mounted API, and embedded worker. Its canonical Aerolineas flow returned 202, authorized GET `succeeded`, one attempt, zero failures, a 247-byte result, and health 200. A private 60-second hold job then soft-stopped in 20.25 seconds, became queued with a null lease and no budget charge, and the same job id succeeded after restart on attempt 2; no manual DML occurred. Production is healthy on `3f50b7d`, but the platform-specific drain transcript and remaining S4-03 adversarial vectors stay open; the private probe must be disabled after that transcript.
- [x] **S4-CQ-04 · Real OutLabs system-key remediation** — taskq `36db7cf` lazy-binds the exact a24 checker after startup and ships as immutable `v0.1.0a2`; the host pins its exact wheel/hash at `76ff5e1`. Local real-Redis and production ephemeral-key proofs establish queue-scoped 200/403 authorization with stable identity and fail-closed sanitized 429/503 handling. Production cycle 1 then exposed a host-only FastAPI response-model 500 after a committed enqueue; host `464965d` adds the ASGI regression and union response projection. The redeployed keyed canary returned canonical 202, authorized GET 200, and terminal `succeeded`; temporary credentials were revoked/archived. No taskq SQL, migration, Tier-0, ADR, role, grant, or wildcard-scope change occurred.
- [x] **S4-03D · Credential-log remediation and Redis credential rotation** — host commit `ffad218` installs an exact-source filter for the upstream auth Redis logger before application startup and renders any Redis userinfo as `[redacted]`; its 68/68 regular tests plus five existing infrastructure skips, Ruff, 64-file MyPy, formatting, and deployment gates are green. The fix was deployed before rotation and startup proved the retired credential absent while the authority appeared only in redacted form. The replacement credential was then staged for both API and worker, the Coolify Redis password metadata was updated and persisted across a real Redis restart, and marker-only terminal proofs showed environment-based and direct replacement authentication succeeded while the retired credential was rejected. Both consumers redeployed successfully: API health is 200, the worker is running, taskq remains enabled in `legacy` mode with an empty allowlist, and exact checks prove neither retired nor replacement credential appears in API or worker logs. No canary traffic ran during remediation; the allowlisted canary is now unblocked.

- [x] **S4-03C · Restricted-runtime proof, production rotation, and legacy-mode taskq base** — a clean same-cluster disposable database ran host/Auth/taskq migrations twice, exact IAM report→apply→idempotent report, queue `created`→`unchanged`, the real API health/login/logout flow, and a real legacy enqueue/claim/settle through the separate worker under `outlabs_api_runtime`; all operator role-switch/queue-admin, role/database creation, superuser/CREATEDB/CREATEROLE/RLS-bypass negatives held, and the exact disposable database was dropped and proved absent. Production runtime/operator grants were then applied under the retained owner, the API pre-deploy hook became an explicit no-op, and both API and legacy worker deployed commit `0e6417c` with only the restricted DSN. The disabled checkpoint proved API health 200, taskq meta 404, `current_user=outlabs_api_runtime`, all elevated flags false, unchanged legacy row count, and no taskq schema. Direct owner taskq migrate/verify converged twice; operator IAM converged without conflicts and queue `tools` returned `created`→`unchanged`; the runtime retained all negative capability proofs. The final healthy deployment enabled taskq with connection ceiling 100, reserve 20, one expected production process, tools mode `legacy`, empty allowlist, and production acknowledgement. Live evidence is health 200, taskq meta 401, one visible queue-stats row, a persistent restricted worker session from its deployment, zero taskq jobs by the owner oracle, and the unchanged single legacy row. The prior owner credential remains outside both running pools for rollback. The proof also exposed a credential-bearing Redis connection URI in upstream auth logs; S4-03D blocks canary traffic until logging is sanitized and that credential is rotated.
- [x] **S4-03B · Actual-cluster disposable-database preflight** — on the exact Coolify PostgreSQL service, measured PostgreSQL 16.14, direct internal port 5432, TLS disabled, `max_connections=100`, and superuser/CREATEDB/CREATEROLE migration authority. A disposable same-cluster database ran taskq 0001→0002→0003, `verify: ok`, no-op second migrate, and a second green verify; OutLabs Auth reached `20260715_0020` twice; IAM converged report→14 creates→14 existing with zero changes/conflicts; and the complete poll-only `tools` profile returned `created`→`unchanged` with an authoritative-field oracle. Production stayed at app head `20260616_0005`, one legacy `outbound_tasks` row, and no `taskq` schema before/after. The exact disposable database was dropped and proved absent; only the six contract `NOLOGIN` roles remain cluster-wide. Coolify has a named PostgreSQL data volume and a successful daily S3 backup from 2026-07-19. The drill exposed S4-CQ-02: the app DSN itself is superuser, so enablement remains paused.

- [x] **S4-CQ-01 · Actual production database adjudicated docs-first** — approved the Coolify-internal PostgreSQL service for first-host dogfood rather than introducing a host data migration or second taskq DSN. The living Stage-4 specification now records the real `staging-prep`/`d1b00fe` production line, 53/53 plus five-skip host gate, interim Postgres `outbound_tasks` legacy path, removed WhatsApp boundary, stale S4-00 inventory, exact no-dual-execution/R6-06 posture, and superseded Neon-only facts. A complete same-cluster disposable-database proof plus backup/durability record gates enablement, only that database may be dropped, and post-Stage-4 branch reconciliation is explicit. No source, SQL, migration, Tier-0, ADR, or Tier-4 file changed.

- [x] **S4-03A · Disabled production deployment** — reconciled the accepted three-host-commit taskq slice onto Coolify's actual `staging-prep` production line while preserving its newer Postgres-backed legacy publisher and removed broker/domain code; the merged host passes 53/53 with five pre-existing infrastructure skips, Ruff, 61-file MyPy, lock, offline Alembic through `20260616_0005`, and image build. The first guarded candidate failed safely before replacement because the production image does not ship `uv`; Coolify retained the healthy old container, the pre-deploy command was corrected to `alembic upgrade head`, and a regression note was added. A second guarded candidate exposed OutLabs Auth a24's required Redis namespace; host commit `d1b00fe` adds the explicit setting/pass-through/test, Coolify now persists `OUTLABS_AUTH_REDIS_KEY_PREFIX=outlabs-auth:production:outlabs-api` and `OUTLABS_AUTH_AUTO_MIGRATE=false`, and the rolling deployment completed healthy on its first health attempt. Production evidence is health 200, application migration head `20260616_0005`, taskq meta 404, unauthenticated enqueue 401, and no `taskq` schema. S4-CQ-01 blocks enablement; no taskq production migration, role, IAM, queue, job, worker, or canary invocation occurred.

- [x] **S4-02-ACCEPT · Disabled host integration independently accepted** — the reviewer reproduced host 62/62 with three pre-existing infrastructure skips, taskq 449/449 with one opt-in skip, Ruff, 111-file MyPy including Alembic, the offline full-upgrade compile, live `alembic current` at `20260313_0004`, the exact Docker image digest, and the live scratch-database active-window/post-settlement harness with a raw-table oracle. Source inspection accepted the real `NonRetryable`/`Retry` mapping, classification-only durable errors, recursive credential rejection, single-snapshot producer policy, flag-only private probe, exact 202/no-fallback behavior, health/CORS/OpenAPI vectors, and unchanged contracts. Both worktrees were clean; taskq matched origin and the host remained deliberately three commits ahead/unpushed with nothing deployed. S4-03 is open, but its first host push is an explicit production deployment action.
- [x] **S4-02 · Disabled-by-default outlabsAPI integration** — host commit `7df6b7f` adds a frozen fail-fast Stage-4 policy, exactly one canonical tools task plus the flag-only private probe, the poll-only single-process embedded runtime, host-first composed lifespan, authorized lifespan-free `/taskq` mount without operator transport, generated OpenAPI composition, exact CORS headers, and backlog-independent health readiness. The existing queued route samples mode/allowlist once, validates bounded credential-free params, awaits taskq enqueue only for enabled allowlisted requests, returns exact 202/readback fields, and never falls back after an ambiguous error; disabled/non-allowlisted requests remain legacy-only. Handler outcomes use real `NonRetryable`/`Retry` types with classification-only durable errors, and the raw Umami auth-body leak is removed. The pre-existing Alembic ghost import is deleted, MyPy now covers `alembic`, and an offline full-upgrade test permanently imports the migration environment. Host verification is 62/62 with the same three infrastructure skips, Ruff clean, MyPy clean across 111 files, lock exact, Docker image green, and `alembic current` at `20260313_0004`. The live local harness independently observed active-key convergence, post-settlement new execution, and two raw one-attempt succeeded rows through the actual embedded worker. No host deployment, production mutation, taskq SQL/migration/contract/ADR/source change, or unrelated lane migration occurred; independent acceptance is required before S4-03.
- [x] **S4-01-ACCEPT · Stage-4 preflight independently accepted** — external verification reproduced taskq 449/449 with one opt-in skip, the host's 44/44 regular tests with three gated skips, Ruff, configured-scope MyPy, the immutable a1 release/tag/hash/host lock chain, both FastAPI router-surface lanes, the scoped credential-rendering fix, byte-identical round-6 record, and every R6-02..R6-15 living-spec closure. Managed-role/IAM/profile evidence and the persistence-verified 35-second Coolify setting were accepted; independent Neon deletion verification was unavailable because the review credential lacked the organization, with provider auto-expiry and S4-AUDIT final-state evidence retained as backstops. The review also reproduced a pre-existing `alembic/env.py` ghost import that makes `alembic current`/`upgrade` fail before database access; S4-02 owns its removal and a permanent import/CI guard. No Contract question was raised, and S4-02 remains open.
- [x] **S4-R6-DOC · Round-6 documentation closure** — amended the living Stage-4 specification for every R6-02..R6-15 finding without touching source, SQL, migrations, contracts, ADRs, or immutable review files. The handler now names real `NonRetryable`/`Retry` results and sanitized durable errors; keyed replay is limited to the active deduplication window; queued credentials are forbidden; cross-path replay risk is explicit and read-only-bounded; probe-registry and rollback-drain observables are exact; the cycle-2 failure drill is S4-AUDIT-owned; production enablement keys are complete; and the S4-02/S4-03/AUDIT rows name health, classification, payload secrecy, independent invocation, responsiveness, depth, and platform-grace oracles. S4-02 is open.
- [x] **S4-01B · Immutable dependency and managed-platform preflight** — published `outlabs-taskq==0.1.0a1` as an immutable GitHub release wheel (`sha256:01ac3129866a8db34281688d65a95e9f30437b52739cec75c287c69e4d11a6ab`) after the managed drill exposed and pinned the auth CLI's display-redacted-password defect. Host commit `ef084ab` locks that exact wheel and `outlabs-auth==0.1.0a24`, rewrites the two router-internals tests against application OpenAPI, and passes them under FastAPI 0.135.1 and 0.139.2; the locked host passes 44/44 regular tests with three opt-in infrastructure skips, Ruff, MyPy, Docker build, and import checks. A disposable Neon PG18.4 branch proved the a20→a24 auth upgrade, taskq 0001→0003 migrate/verify/idempotency, separated runtime/operator membership, exact IAM reconciliation, queue-profile idempotency, direct unpooled transport, TLS observation, and `max_connections=901`; the branch was deleted and production data was untouched. Host commit `90fa63d` records the live Coolify `outlabs API` application reloaded with a 35-second Stop Grace Period, exceeding the image's 30-second ASGI grace and 20-second soft stop. S4-01 is complete; S4-R6-DOC opens before any host integration.
- [x] **S4-01A · Managed-auth artifact correction** — the real password-authenticated managed preflight found that `taskq auth sync-permissions` passed SQLAlchemy's display-redacted `***` URL into OutLabs Auth. The CLI now renders its owned asyncpg DSN with `hide_password=False`, a special-character regression proves the driver/password/query are preserved without logging the value, and the package advances to `0.1.0a1` so the already-published a0 remains immutable rather than being replaced. No SQL, migration, contract, ADR, facade, worker, or permission semantics changed.
- [x] **S4-00-R6 · Round-6 response recorded** — registered the external response byte-for-byte as immutable Tier 4. Its READY verdict opens S4-01 with no Contract questions, BLOCKERs, HIGHs, or preconditions; the exact a24 resolution/FastAPI test repair and platform-grace check belong to S4-01, while seven remaining MEDIUM and eight LOW wording/vector findings are board-owned before S4-02. The reviewer independently reproduced 448/448 taskq tests with one opt-in skip, the host's 44/44 regular tests with three gated infrastructure skips, the known two-test resolver failure, source inventory, profile/wire producibility, and clean review scope. Neither repository source, dependency lock, SQL, migration, contract, ADR, or prior Tier-4 file changed.
- [x] **S4-00 · First-host dogfood plan frozen** — added the Tier-3 outlabsAPI specification and round-6 adversarial gate after inspecting both clean repositories at taskq `8a13262` and host `a0019cd`. The plan resolves R2-17 with an exact a24 upgrade (the host's complete 47-test collection remains 44 green/3 opt-in skips under a real a24 overlay), requires an immutable hashed taskq alpha rather than a local path, and makes managed-PostgreSQL role/pooler/SSL/ceiling/migration proof a preview-branch precondition. One `tools` queue and canonical `outlabs.tools.run` task migrate only allowlisted read-only tools through a mutually exclusive producer switch; HTTP 202 returns job id/disposition/canonical authorized result URL, keyed replay is honest, callers receive read but never generic enqueue, and external-effect lanes stay untouched. The poll-only one-process embedded topology has explicit pool/grace/health/CORS/IAM arithmetic; two deploy cycles, a side-effect-free process-termination probe, zero-DML rollback/re-enable, and delayed legacy retirement form the exit gate. No host source/dependency/deployment, taskq SQL/migration/Tier-0/Tier-1, or existing Tier-4 file changed; S4-01 stays closed pending round 6.
- [x] **S3-AUDIT-ACCEPT · Stage 3 independently accepted** — external verification reproduced the identical 448/448 suite with one opt-in skip on exact PostgreSQL 18.3 and 16.14, 289/289 DB-free on Python 3.12 and 3.13.9, the 2/2 million-row plan gate, Ruff/format, and representative wheel/sdist dependency corners. Source inspection accepted both deliberate oracle-drift proofs, the nullable redaction decode fix, exact CI/harness/front-door corrections, legitimate ADR-014 context-only attribution repair, and real-path B11/B14 evidence. All round-5 findings are closed or Growth-owned; no SQL, migration, Tier-0, Tier-4, or Stage-4 host change exists. Stage 3 is complete and S4-00 may proceed.
- [x] **S3-AUDIT · Stage-3 completion evidence** — added the contract-derived live SQL↔mounted-ASGI scenario and raw-table/function read oracles, with deliberate generated-catalog and projection mutations proving the two oracles fail independently. That path found and pinned one implementation defect: redacted nullable job-detail fields now decode when absent, matching the accepted projection contract. The existing security, malformed-input, fence, authorization, long-poll, lifespan, cancellation, process-exit, and resource suites run in the dedicated warnings-as-errors Stage-3 CI gate; full SQL lanes install the exact OutLabs extra, artifacts now matrix Python 3.12/3.13, and the scheduled million-row gate remains explicit. B11 and B14 are executable report-only scenarios through the real runtime/generated-client→ASGI→SQL paths; the toy audit reported B11 facade-only/embedded median p99 2.664/1.831 ms (the negative delta is environmental noise, not a win) and B14 SQL/client median p99 1.378/3.516 ms with 2.084 ms facade overhead. The identical suite passes 448/448 on PostgreSQL 18.3 and 16.14 with one opt-in skip; DB-free passes 289/289 on both Python versions; wheel+sdist × core/HTTP/OutLabs × Python 3.12/3.13 is 12/12; Ruff/format and the 2/2 million-row plan gate are green. Harness/front-door wording now matches the repository. No SQL, migration, Tier-0, Tier-4, Stage-4 host, or future-capability implementation changed.
- [x] **S3-04-ACCEPT · S3-04 independently accepted** — external verification reproduced 443/443 on live PostgreSQL 18.3 with one opt-in skip, 288/288 DB-free, Ruff/format, wheel+sdist, and fresh core/HTTP/OutLabs wheel isolation against exact `outlabs-auth==0.1.0a24`. Source inspection accepted all eight owned round-5 remediations: opaque 429/503 mapping, owned three-shape session resolution with subject revalidation, strict queue plus real permission validation, side-effect-free imports/config-free seeding, deterministic API-key policy notes, explicit legacy candidates, exact public alpha APIs, and SAVEPOINT/caller-transaction semantics. The real-schema first-apply/idempotency/drift/reconcile and Enterprise/Simple policy vectors passed; the Tier-3 edits are as-built precision, SQL/migrations/Tier 0/Tier 4 are unchanged, and S3-AUDIT may proceed while Stage 4 remains closed.
- [x] **S3-04 · OutLabs authorizer, catalog, provisioning, and auth CLI** — added the explicitly imported `taskq.http.outlabs` boundary against exact `outlabs-auth==0.1.0a24`: real-validator queue/global/legacy any-of authorization with concurrent checker caching, bounded subject-derived actors, owned awaitable/async-generator/context-manager session scopes, and sanitized auth 429/503 envelopes with `Retry-After`. The strict pure catalog emits five global plus five per canonical queue; explicit report/apply/reconcile provisioning uses `include_config=False`, non-system standard roles, the public role service, caller-owned transactions, and a SAVEPOINT, with deterministic policy notes for wildcard/API-key/SimpleRBAC limits. The lazy `taskq auth sync-permissions` CLI and non-atomic queue/IAM composition report partial failure without secrets. A real isolated-schema OutLabs installation proves first apply, idempotency, public-service drift conflict/reconciliation, and no global logging leakage; Enterprise/Simple policy, session, error, rollback, artifact, and import boundaries close all eight owned round-5 residuals. PG18.3 passes 443/443 with one opt-in skip and the DB-free lane passes 288/288; Ruff/format, wheel/sdist, and installed core/HTTP/OutLabs isolation are green. SQL contract 0.1.2, migrations, Tier 0, and Tier 4 are unchanged; S3-AUDIT is open and PG16 remains its gate.
- [x] **S3-03-ACCEPT · S3-03 independently accepted** — external verification reproduced 428/428 on live PostgreSQL 18.3 with one opt-in skip, 274/274 DB-free, Ruff/format, wheel+sdist, both core and HTTP artifact-isolation proofs, clean worktree, and trailer/board hygiene. Source scrutiny accepted the SQL/HTTP stop split, runtime budgets and unwind, dynamic listener registration, response-loss settlement replay, nullable progress decode, and R5-20's live-thread process-exit evidence; no Tier-3 drift or SQL/migration/Tier-0/Tier-4 change exists. S3-04 may proceed while PG16 remains honestly deferred to S3-AUDIT.
- [x] **S3-03 · Composable runtime, housekeeper, embedded/HTTP workers, and process budgets** — added the idempotent `TaskqRuntime` state machine, exact host-first lifespan composition/app-state restoration/DI, compatibility/readiness snapshots, five-second jittered housekeeper with transient recovery and fatal cleanup, lazy reconnectable long-poll listener ownership, and explicit resource ownership. Embedded execution is default-off and acknowledgement-gated, reuses the Stage-2 worker unchanged over separate runner pool/LISTEN resources, reports single/multi-process pool/handler/listener arithmetic, refuses database-ceiling oversubscription, and warns on unknown budgets or inverted ASGI grace. The worker CLI now selects exactly one SQL or secret-safe HTTP transport; HTTP mode forbids LISTEN and multi-queue long polling, cancels only its in-flight long-poll claim on stop, and retains worker-owned settlement replay. Live mounted PostgreSQL proves ordinary presence/settlement, dynamic long-poll wake, duplicate-housekeeper advisory-lock safety, HTTP response-loss convergence/remote drain, and R5-20's runtime process-exit actor firing while an ASGI-hosted sync thread remains live. A nullable claim projection decode found by that real HTTP path is pinned. The unchanged SQL/migration/Tier-0/Tier-4 surface passes 428/428 on PG18.3 with one opt-in skip and 274/274 DB-free; Ruff/format, wheel/sdist, and core/HTTP artifact isolation are green. S3-04 is open; PG16 remains for S3-AUDIT.
- [x] **S3-02-ACCEPT · S3-02 independently accepted** — external verification reproduced 411/411 on live PostgreSQL 18.3 with one opt-in skip, 262/262 DB-free, Ruff/format, wheel+sdist, core/HTTP artifact isolation, clean worktree, trailer/board hygiene, and exact absence of SQL/migration/Tier-0/Tier-4 drift; the phased authorization, envelope/hiding/fence boundaries, long-poll hub, dynamic listener lifecycle, pool split, all nine owned round-5 residuals, and the legitimate order-independent Stage-2 import test were accepted. The non-blocking stale §8 `TaskqRoute` wording is owned by S3-AUDIT; S3-03 may proceed while PG16 remains honestly deferred to that audit.
- [x] **S3-02 · Mounted facade, authoritative authorization, pool split, and long poll** — added a lifespan-free FastAPI sub-application whose generated active/gated/deferred routes own every envelope and OpenAPI projection; phased static, bearer, callable, legacy, and explicit-test authorizers authenticate before parsing and authorize authoritative queue sources without exposing fences or lookup oracles. Operator routes require a separate transport/authorizer pair, metrics use global read, worker presence checks every declared queue, and queue stats preserve the empty snapshot posture. A generation-safe in-process wait hub plus dynamically reconnectable notification channels implement the exact capture/claim/subscribe/recheck/wait sequence with disconnect, shutdown, cancellation, stale-listener, and cleanup evidence. Mounted live SQL proves enqueue/claim/presence/settlement parity and 2,048-byte diagnostic truncation. All nine owned round-5 residuals are closed; the unchanged SQL/migration/Tier-0/Tier-4 surface passes 411/411 on PG18.3 with one opt-in skip, Ruff/format, wheel/sdist, and core-only artifact isolation. S3-03 is open; PG16 remains for S3-AUDIT.
- [x] **S3-01-ACCEPT · S3-01 independently accepted** — external verification reproduced 390/390 on live PostgreSQL 18.3 with one opt-in skip, Ruff/format, wheel+sdist, clean worktree, and a fresh core-only wheel proof; the hand-derived oracle, retry/fence/wire/client/capability boundaries and all seven owned round-5 residuals were accepted, no SQL/Tier-0/Tier-4 drift exists, and S3-02 may proceed while the PG16 Stage-3 delta remains honestly unclaimed until CI/audit.
- [x] **S3-01 · Capability protocols, generated wire surface, and HTTP clients** — split the SQL intersection into exact producer/runner/observer/authorization/operator/housekeeper protocols with non-owning close-safe views; added the independently-oracled Protocol-v1.0.4 HTTP catalog, strict bounded request/result models, fence-only claim wire projection, and metadata-driven active/gated/deferred generation; shipped side-effect-free sync/async HTTP clients with exact credentials, protocol/request-id negotiation, typed SQL-domain normalization, fresh-per-attempt retry IDs, keyed-only producer replay, worker-owned settlement replay, no claim replay, owned/borrowed cleanup, cancellation/fork/thread guards, and typed `TQ501`; moved the benchmark runner under `taskq`, removed wheel placeholders/top-level `bench`, and strengthened artifact missing-extra smoke evidence. The unchanged SQL/migrations pass 390/390 on PG18.3 with one opt-in skip, Ruff/format and wheel/sdist builds are clean; S3-02 is open.
- [x] **S3-R5-DELTA · Round-5 remediation delta accepted** — independent review of `49c0d0b..11bba1a` confirmed the nine-path docs-only range, both trailers and same-commit board updates, byte-identical round-5 response hash, every ADR-017/remediation condition and acceptance vector, explicit residual ownership, clean worktree, Ruff, and 366/366 PG18 tests with one opt-in skip; S3-01 is open without a full round 6.
- [x] **S3-R5-DOC · Round-5 documentation remediation** — froze the mounted lifespan-free sub-application and complete envelope ownership, operator-only queue ensure/pool/authorizer split, explicit five-name admin role plus reconcilable non-system roles, mode-honest personal-key policy, hidden deferred-route responders, single-queue-only HTTP long poll with scoped stop cancellation, timeout→`ClaimState.EMPTY`, generated retry classification, canonical authorization matrix, B14 benchmark identity, and the required S3-02/S3-04 vectors; every remaining R5 finding has an owning board slice and no source/SQL/grant/migration changed.
- [x] **S3-R5-CQ · Round-5 Contract questions adjudicated** — accepted ADR-017 / Protocol document revision 1.0.4 defers the SQL-unbacked general list behind a hidden `TQ501`, corrects every surviving operator-minimal statement, removes the unproducible enqueue `created_at` and all non-authoritative echoes, and pins authenticated/non-reflective invalid-request-id behavior; the manifest records no 0.1 `list_jobs`, while SQL contract 0.1.2, grants, source, and migrations remain unchanged.
- [x] **S3-R5-RESPONSE · Round-5 response recorded** — registered the 235-line external response byte-for-byte as immutable Tier 4; verdict BLOCKED, architecture and scope accepted, two Contract questions plus three BLOCKER/five HIGH documentation findings gate S3-01, and the board sequences ADR-017 before docs-only remediation and a targeted delta check.
- [x] **S3-00-R5 · Round-5 design gate assembled** — the immutable Tier-4 request pins the Stage-2 baseline through S3-00 and requires an independently derived Protocol-v1.0.3 route/backing/action/outcome catalog, ADR-014..016 governance audit, H-13/capability feasibility, fence/client/retry security, authorization and credential split, long-poll/lifespan/R2-11 races, OutLabs source validation, packaging/CI/benchmark honesty, scope proof, and an explicit S3-01 verdict; no implementation landed.
- [x] **S3-00-SPEC · Stage-3 integration contracts frozen** — the Tier-3 specification fixes capability-sized transport boundaries, H-13-generated active/gated/deferred HTTP surfaces, exact envelopes/client replay and ownership, authoritative queue authorization with separate operator credentials, connection-free long polling, composable housekeeper/embedded runtime and process budgets, OutLabs catalog/provisioning, and the S3-01..04/AUDIT acceptance matrix; no integration code or SQL change landed.
- [x] **S3-CQ-03 · Final HTTP wire models normalized docs-first** — accepted ADR-016 and Protocol v1 document revision 1.0.3 define bounded request-id mint/echo behavior, correct queue ensure to the exact version-free SQL profile, and retain worker list as a generated typed-capability gate pending R2-16, with the declared-vs-deferred rule explicit and no SQL or migration change.
- [x] **S3-CQ-02 · Queue-profile read contradiction adjudicated docs-first** — accepted ADR-015 and Protocol v1 document revision 1.0.2 visibly defer the unbacked GET route to H-11's Growth §4/R2-16 read-model design, exclude it from H-13's active generated surface, pin `TQ501`, retain stats/admin-ensure as the honest interim posture, and leave SQL contract 0.1.2 plus migrations 0001–0003 unchanged.
- [x] **S3-CQ-01 · HTTP worker presence adjudicated docs-first** — accepted ADR-014 and Protocol v1 document revision 1.0.1 define the canonical route, all-declared-queue `run` authorization, advisory label/authenticated actor split, typed 200 outcomes, presence/job-heartbeat non-confusion rule, shared-fleet honesty edge, and H-13 generation/parity obligation without changing SQL contract 0.1.2 or adding a migration.
- [x] **S2-06-AUDIT · Stage 2D permanent completion evidence** — repeated cancellation, followup, drain-cap, and task-ledger probes return transports and asyncio resources to baseline; CI collects the consumer suite on Python 3.12/3.13 and imports testing without pytest; wheel/sdist × core/HTTP/OutLabs artifact smokes exercise the installed fake/assertion surface. The identical full suite is 366/366 with one opt-in skip on PostgreSQL 18.3 and 16.14, the PG18 million-row gate is 2/2, the clean Python-3.13 no-DB lane is 219/219, Ruff/format are clean, and the exact slice changes no SQL migration, Tier-0/Tier-4, HTTP, OutLabs, listener, CLI, or Stage-3 source.
- [x] **S2-06B · Consumer work, assertion, inline, and drain helpers** — added shared-supervisor synthetic and caller-transaction PostgreSQL `work`, fixed-text safe `require_enqueued`, immediate inline execution with record-only/opt-in bounded followups and cancellation-safe restoration, and sequential real/fake drains that reject unbounded or runaway work; SQL runner adapters now accept an optional borrowed connection without changing transport ownership.
- [x] **S2-06A · Fake client and replacement boundary** — added a core-isolated, fence-free fake with typed single/bulk enqueue, active-key dedup, FIFO due claim, heartbeat, replay-aware settlement intents, safe nested matchers, loud unsupported-command/closed behavior, and exact non-owning `TaskQ.replace_client` restoration across normal, exceptional, nested, and cancellation exits.
- [x] **S2-06-SPEC · Consumer testing contracts frozen** — the Tier-3 Stage-2D specification fixes the test-runner-neutral fake client, exact replacement ownership, fence-free enqueue matchers, shared handler normalization, inline/followup bounds, caller-owned PostgreSQL work/drain transactions, packaging isolation, and the S2-06A/B/audit acceptance matrix; no runtime or Stage-3 code was added.
- [x] **S2-05-AUDIT · Stage 2C permanent completion evidence** — repeated notification/poll, reconnect/close, fatal-admission, cancellation, and resource races join the existing ten-family matrix; live SQL proves poll-only, notification reconnect/wake, fair queues, remote drain, and CLI signal/process-exit paths; R4-09..12 are closed, B8/B13 run as honest fresh-database report-only scenarios, and wheel/sdist × core/HTTP/OutLabs plus Python 3.13 gates pass. The identical suite is 350/350 on PG18.3 and PG16.14 with one opt-in skip, the PG18 million-row gate is 2/2, and Ruff/format are clean; Stage 3 remains untouched.
- [x] **S2-05C · pydantic-settings, worker CLI, and observability** — added core `pydantic-settings`, frozen secret-safe environment/CLI precedence and deployment interlocks, explicit instance/factory registry loading before database construction, bounded SQL/listener ownership, unique worker ids, temporary soft/hard signal handling with unsafe-sync process exit, stable structured events, and fence-free monotonic snapshots; 330/330 pass on PG18 with one opt-in skip and Ruff clean.
- [x] **S2-05B · Capacity-safe claim, presence, and shutdown** — advisory presence now completes before first claim, reports bounded safe metadata, drives degraded/recovered readiness and sticky remote drain; claim-to-submit admission survives graceful/hard stop ordering, fatal reports auto-stop the service, and external `run()` cancellation performs shielded cleanup then re-raises.
- [x] **S2-05A · Notification and authoritative poll kernel** — added a dedicated reconnectable PostgreSQL notification source plus a core worker service with generation-safe coalesced nudges, mandatory monotonic polling, fair queue rotation, capacity-bounded immediate submission, poll-only degradation, and listener catch-up/reconnect; deterministic option, wake, fairness, and reconnect vectors keep notification payloads non-authoritative.
- [x] **S2-05-SPEC · Claim loop and worker CLI contracts frozen** — the Tier-3 Stage-2C specification fixes notification-as-hint plus authoritative monotonic polling, reconnect catch-up, fair capacity-bounded claim admission, advisory presence/remote shutdown, `taskq worker` lifecycle, `pydantic-settings` precedence/interlocks, fence-safe observability, deterministic fault/race machinery, packaging boundaries, and the S2-05A/B/C/audit matrix; no runtime or Stage-3 code was added.
- [x] **R4-AUDIT · Round-4 remediation completion evidence** — the identical 299-test suite passes with one pre-existing opt-in plan skip on PostgreSQL 18.3 and an isolated PostgreSQL 16.14 lane; Ruff and diff hygiene are clean, R4-01..08 are closed, no contract question was opened, and the worker surface stops before S2-05.
- [x] **R4-F04 · Replay oracle and error normalization (R4-07/R4-08)** — the scripted ledger now retains every semantic settlement argument behind fence-safe representations and replay tests assert exact equality; validation/capability failures in no-handler release and invalid-follow-up escape now return fatal runtime reports, pinned for both typed error classes.
- [x] **R4-F03 · Process-exit honesty and dispatch arity (R4-04/R4-05/R4-06)** — lease loss now marks a still-live sync handler as `abandoned_sync`, exposes immediate process-exit necessity, and preserves that history in the terminal report; dispatch consumes registry-frozen positional arity, while regressions cover sync/async keyword-only dispatch, competing capacity waiters, post-deadline heartbeat, fatal auto-drain, and external `run_job` cancellation.
- [x] **R4-F02 · External cancellation (R4-02/R4-06)** — cancelling a submitted job now initiates soft stop, completes shutdown release inside a shielded critical section, and re-raises `CancelledError`; a cancellation callback recovers the before-first-step window, with deterministic mid-handler and immediate-cancel regressions.
- [x] **R4-F01 · Settlement-liveness heartbeat (R4-01/R4-03)** — heartbeat lifetime is now controlled by terminal settlement rather than handler completion; retry backoff is interruptible by lease loss, and deterministic long-backoff vectors prove heartbeat interleaving plus `settling → ownership_lost` suppression.
- [x] **S2-04-R4-RESPONSE · Round-4 response recorded** — registered the external response verbatim as immutable Tier 4; its executed counterexamples leave the SQL safety core intact but block S2-05 on settlement-heartbeat liveness, external-cancellation semantics, process-exit honesty, dispatch arity, and their regression oracles (285/285 baseline on PG18, Ruff clean).
- [x] **S3-PREP-03 · Batch boundary adapters and measured delta** — module-level adapters now validate bulk-enqueue items in one `TaskQ` boundary call and decode each SQL claim batch as one state-checked projection. Fixed-seed toy B2/B3 runs used five repetitions and fresh databases before/after: B2 median throughput 33,029.69→33,216.71 rows/s (+0.57%), worst p99 30.35→30.95 ms (+2.00%); B3 median throughput 798.42→799.90 rows/s (+0.18%), worst p99 3.76→3.09 ms (-17.63%). B2/B3 call SQL directly and do not traverse these Pydantic adapters, so all deltas are recorded as harness/environment noise, not a performance win (285/285 on PG18).
- [x] **S3-PREP-02 · Tagged protocol result unions** — split enqueue dispositions on `status` and all six fenced settlement dispositions on `result` into Pydantic discriminated unions with public concrete variants and module-level parsers; Tier-0 parity proves the tag sets equal the closed protocol outcomes, while eight frozen representative vectors prove byte-identical JSON with no wire-contract change, ADR, or version bump (283/283 on PG18).
- [x] **S3-PREP-01 · Direction-aware extras policy** — documented the ADR-005 boundary rule in `taskq.protocol`: inbound enqueue command/bulk-item models now forbid unknown fields so typos fail locally, while outbound projections/results explicitly ignore additive fields for forward-compatible decoding; typo and unknown-result vectors bring PG18 to 281/281 without changing wire or SQL contracts.
- [x] **S2-04-R4 · Round-4 review packet** — registered an immutable, contract-first adversarial request covering the contract-0.1.2 additive upgrade and verifier, every S2-04 execution/heartbeat/replay/lifecycle acceptance row, mandatory R2-11 live-sync counterexamples, repeated races, real-SQL conservation, resource cleanup, artifact/import isolation, CI collection, and strict absence of S2-05/Stage-3 scope; the reviewer may add only `docs/design-review-4/RESPONSE.md` and must decide whether S2-05 may open.
- [x] **S2-04-AUDIT · Stage 2B permanent completion evidence** — five repeated, barrier-choreographed race families cover both winner orders without correctness sleeps; live SQL vectors prove complete/retry/snooze/cancel/shutdown/no-handler budget and exact event conservation plus committed-response replay; task, exception, executor-thread, and SQL-pool ledgers return to baseline; source CI imports the worker on Python 3.12/3.13 and every fresh wheel/sdist core/HTTP/OutLabs install smokes it outside the checkout. The exact full suite is 279/279 plus the million-row plan gate on PostgreSQL 18.3 and 16.14, with 149/149 in the clean Python 3.13 worker/unit lane.
- [x] **S2-04D · Bounded concurrency and soft stop** — added synchronous slot reservation, duplicate-attempt rejection, capacity waiting, lazy bounded sync execution with active heartbeat while thread-queued, atomic intake close, cooperative/infinite drain, monotonic deadline, shared escalation, async shutdown release, honest live-sync process-exit signaling, fatal auto-stop, and complete task/executor joining; 8 deterministic vectors bring PG18 to 264/264.
- [x] **S2-04C · Verb-aware settlement replay and fault injection** — settlement now retries only the original verb with bounded exponential backoff, validates command-specific outcomes, converges after a committed-but-lost response, keeps heartbeats live until certainty, classifies exhausted certainty as fatal, and applies the frozen invalid-follow-up terminal escape; 13 deterministic vectors bring PG18 to 256/256 and prove one semantic settlement plus one handler invocation under response loss.
- [x] **S2-04B · Monotonic heartbeat and fenced per-job supervision** — added the core worker options/clock/state/report API, exact `lease_seconds/3` cadence, one heartbeat coroutine per active handler, generation-safe checkpoint flush, two-failure recovery/third-failure ownership loss, typed loss, operator grace cancellation, non-retryable runtime failure, no-handler release, async/sync dispatch, and joined lifecycle; 10 deterministic vectors bring PG18 to 243/243 without reading absolute expiry for scheduling.
- [x] **S2-04A · Execution primitives and deterministic harness** — added frozen closed handler intents, thread-safe escalating cancellation, fence-free `JobContext` with generation-safe 2KB checkpoints, exact sync/async one-/two-argument handler registration, public core exports, and private manual-clock/scripted-response-loss utilities; 12 boundary/concurrency vectors bring the PG18 suite to 233/233 with no optional imports or construction-time work.
- [x] **S2-04-SPEC · Worker-runtime contracts frozen** — the new Tier-3 specification fixes the S2-04-only module/API boundary, closed result normalization, cancellation precedence, monotonic lease-derived heartbeat state machine, verb-aware replay, R2-11 sync honesty, bounded supervisor/soft stop, deterministic harness, and A/B/C/D/audit acceptance matrix; S2-05 and Stage 3 remain excluded.
- [x] **S2-CI-01 · Contract 0.1.2 implemented and proven** — immutable migration `0003` appends `claimed_job.lease_seconds`, returns the exact effective duration, advances meta without changing the 40-function surface, and is decoded by the Python transport; `verify()` plus an independent ordered catalog assertion, default/stamped/override vectors, fresh install, and the full `0001 → 0002 → 0003` upgrade chain pass on PG18.3 and PG16.14 (221/221 plus the million-row plan gate on both).
- [x] **S2-CQ-01 · Effective claimed lease adjudicated docs-first** — accepted ADR-013 and amended Protocol v1, the Function Manifest, and Unified Spec §14 before SQL: contract 0.1.2 appends the exact effective `lease_seconds`, retains `lease_expires_at`, and bans client-wall-clock duration derivation; implementation was separately gated as S2-CI-01.
- [x] **S2-AUDIT-03 · Function-specific outcome enforcement** — every scalar and composite transport result is checked against its command's own protocol-owned outcome set; rollback-only wrong-command outcomes become `TQ500` even when the value is valid for a different command (217/217 on PG18 and PG16, plus the plan gate on both).
- [x] **S2-AUDIT-02 · Permanent acceptance evidence** — transport-level 20-way dedup proves one `created`/19 `existed`, captured logs remain fence-free, SQL construction/commands leave no background tasks or checked-out connections, transaction vectors conserve domain/job/event rows, and CI now runs the full suite on PG16/PG18 plus explicit core/HTTP/outlabs isolation on Python 3.12/3.13 and on every Python-3.12 wheel/sdist; all local mirrors pass (216/216 + plan gate on both PG versions, 73 Python-3.13 unit tests).
- [x] **S2-AUDIT-01 · Protocol single-source correction** — `taskq.protocol` now owns the closed 30-command names, SQL identities, capability roles, outcomes, TQ errors/retryability, and replay rules; typed settle/job/operator enums reject invented values, while independent parity proves exact agreement with the Tier-0-derived machine manifest (214/214 on PG18).
- [x] **S2-03 · Typed facade and transactional enqueue** — `TaskQ` compiles registered canonical tasks and retry stamps exactly once, keeps raw enqueue explicitly opt-in, and executes single/bulk enqueue on the caller's exact `AsyncSession`/`AsyncConnection` without owning its lifecycle; commit, rollback, autobegin, savepoint, cancellation/error ownership, non-SQL rejection, and no-background-work contracts pass on PG16/PG18, while clean wheel/sdist core installs import the complete Stage 2A surface (212/212 each).
- [x] **S2-02 · Complete async SQL transport** — runtime-checkable `TaskqTransport` and lazy `SqlTaskqTransport` cover all 30 manifest-public functions with fixed bound calls, typed/fence-safe adapters, no table DML or implicit retries, owned/borrowed engine semantics, SQLSTATE-only failures, malformed-bulk invariants, and transaction rollback/cancellation; every method passes through its least-capability role with cross-role denials on PG16 and PG18 (201/201 each).
- [x] **S2-01 · Typed task registry and protocol values** — immutable generic Pydantic task metadata validates canonical names, queues, aliases, stamped retry policy, handler annotations, and JSON payloads; collision-atomic deterministic registration preserves rename dispatch; the closed enqueue/TQ models, fence-redacted claim projection, SQLSTATE-only typed errors, safe public exports, and 62 unit/property vectors bring the PG18 suite to 188/188.
- [x] **R3-F08 · Cross-version exact-catalog normalization** — the exact constraint axis now excludes PostgreSQL 18's version-specific `NOT NULL` projection while table shapes continue to close nullability; the identical 126-test suite and opt-in million-row plan gate pass on PostgreSQL 16.14 and 18.3.
- [x] **R3-F07 · Plan-query drift detection** — every representative million-row structural query is now bound to normalized fragments from the actual owning function definition; a rollback-only full-scan mutation proves the regular guard fails on function drift and recovers after rollback (126/126 plus the opt-in gate on PG18).
- [x] **R3-F06 · Benchmark reset and conservation** — every B1–B4 scenario now creates/migrates/fingerprints/drops its own fresh database; B4 stops and joins producers before a bounded worker drain, then records and asserts accepted = terminal + active with zero active/running jobs or attempts (all four toy smokes green, no databases leaked).
- [x] **R3-F05 · Built-artifact CI gate** — CI builds wheel + sdist, installs each core and HTTP extra into clean environments outside the checkout, proves optional-import isolation and installed-package provenance, exercises both entry points, asserts the packaged 0001+0002/40-function manifest, and performs a fresh database CLI migrate + exact verify; the identical four-environment smoke is green locally.
- [x] **R3-F04 · Manifest-complete T2/T8 coverage** — closed ledgers cover all 30 public functions, registered errors, replay declarations, and exact grants; direct vectors fill bulk/runner/observer/operator/housekeeper gaps, assert safe views and shadow resistance, add concurrent install + CLI gates, reuse failure/sync/upgrade/corruption T8 evidence, and extend T4 with heartbeat and worker-cancel replay transitions (125/125 on PG18).
- [x] **R3-F03 · Reserved-role validation** — migration preflight now rejects colliding reserved names with LOGIN, SUPERUSER, CREATEROLE, CREATEDB, REPLICATION, BYPASSRLS, or inherited membership before target-database DDL; seven fresh-database probes prove atomic refusal and lock cleanup, while the exact verifier enforces the installed role manifest (113/113 on PG18).
- [x] **R3-F02 · Migration lock failure recovery** — caller-owned migrations now use a transaction advisory lock while runner-owned multi-transaction applies retain an explicitly released session lock; async/sync-adapter × caller/runner failure probes leave zero locks and prove immediate second-connection recovery (106/106 on PG18).
- [x] **R3-F01 · Exact machine-readable manifest + verifier** — the independent 0.1.1 catalog projection closes the 40-function surface and exact role/relation/type/index/constraint/view/ACL/seed axes; read-only verification rejects 36 rollback-only corruptions, including all five R3-01 counterexamples, then proves restoration green (102/102 on PG18).
- [x] **R3-CI · Implement contract 0.1.1** — immutable migration `0002_contract_0_1_1` adds the owner-only byte-safe truncation helper, applies ADR-012 null boundaries and diagnostic caps, advances the contract version, and passes fresh-chain plus `0001` upgrade vectors (64/64 on PG18).
- [x] **R3-CQ · Contract questions adjudicated docs-first** — accepted [ADR-012](docs/adr/ADR-012-null-boundaries-byte-safe-diagnostics.md) makes explicit null invalid (`TQ422`), caps stored diagnostics by UTF-8 bytes with settlement-safe truncation, adds the owner-only helper to the Function Manifest before SQL, and advances the immutable migration chain to contract 0.1.1/`0002`.
- [x] **R3-01 · External response processed** — the immutable [round-3 response](docs/design-review-3/RESPONSE.md) was independently adjudicated: verdict BLOCKED; all 7 findings accepted; CQ-01/CQ-02 recorded above; S2-01 remains closed.
- [x] **S2-00 · Stage-2A implementation specification** — the new Tier-3 spec fixes the typed task/registry boundary, closed 0.1 outcomes and TQ errors, complete async SQL transport scope, caller-vs-transport transaction ownership, fence/import safety, and the S2-01..03 acceptance matrix; it remains subordinate to the blocked round-3 remediation.
- [x] **Design phase** — spec v1.6, ADR-001..011, two review rounds folded, Protocol v1 + Function Manifest canonical, docs constitution (`6cf6793`..`e1237c5`)
- [x] **S1 opening slice** — migration `0001_initial.sql` (6 roles, 39 hardened functions, self-checking), ADR-004 runner (`migrate`/`migrate_sync`/`verify` + CLI), T1 (26) + T2 (15) suites, 42/42 green vs PG 18.3, wheel packaging fixed, single-writer ledger + typed-cancel reconciliations in manifest errata §8 (`3e7d55d`)
- [x] **S1-01 · T3 choreographed races** — six advisory-barrier/hold-open race cases run deterministically for 20 rounds each: same-key convergence, double-claim exclusion, post-reap fence loss, cross-verb settle conflict, ten-way cap admission, and the single permitted pause slip.
- [x] **S1-02 · T3-R randomized stress** — seed-replayable, env-scalable producer/worker/operator load mixes all 0.1 settle verbs, then drains and asserts durable duplicate-claim, attempt-token, conservation, terminal-state, and no-wedge invariants (30s default run green with seed `424242`).
- [x] **S1-03 · T4 stateful model** — Hypothesis drives enqueue/claim/complete/fail/release/snooze/cancel/lease-rewind+tick/redrive through capability roles; every step reconciles budget, fence, attempt-ledger, terminal-shape, dedup, and conservation invariants (20×40 default green with seed `24680`).
- [x] **S1-04 · verify corruption matrix** — T2 now corrupts and restores each hardening axis; `verify()` precisely names missing pinned paths, PUBLIC EXECUTE, wrong ownership, ledger checksum drift, and a missing capability role, then proves the restored catalog green.
- [x] **S1-05 · PG16 lane** — the identical 54-test suite passes on PostgreSQL 16.14 and 18.3, including the uuid7 fallback, races, stress, model, and verifier corruption matrix; no PG16 manifest caveat was required.
- [x] **S1-06 · 1M-row plan checks** — opt-in `tests/test_plans.py` seeds mixed states, stabilizes stats/visibility, runs `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`, and structurally asserts claim/dedup/reap/stats index families, bounded hot-path rows, and no full `jobs` scan (two consecutive PG18 runs green).
- [x] **S1-07 · B1–B4 benchmark smoke** — packaged `taskq-bench` runs single enqueue, 1000-row bulk, empty/deep claim→settle, and mixed producer/worker load for ≥3 repetitions; toy tests and the CLI print/write JSON with method, machine/PG/settings, WAL/storage/tuple/lock/connection, latency/throughput, event-loop, and structural EXPLAIN evidence. No baseline was created.
- [x] **S1-08 · CI wiring** — GitHub Actions now gates Ruff check/format, Python 3.12/3.13 core+HTTP import isolation and T1, PostgreSQL 16/18 SQL contracts, PG18 races/T4, migrations, and B1–B4 smoke; README records the required branch-protection checks.
- [x] **S1-09 · Stage-1 exit review packet** — the Build Plan records every exit gate green and the immutable Tier-4 [round-3 request](docs/design-review-3/REQUEST.md) gives Andi a contract-first audit program for migration 0001, runner/verifier, SQL suites, plans, benchmarks, packaging, and CI.
