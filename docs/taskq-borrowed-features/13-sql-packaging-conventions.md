# 13 — SQL Packaging Conventions

> **Priority:** MUST for 0.1 (ADR-004/009 — canonical migrations are first-release scope)
> **Provenance:** the mature Python/Postgres task library named queries + pre/post migrations; the Node/Postgres SQL-first worker numbered `.sql` files; Extraction Design Brief installer ownership
> **Rejects:** the Node/Postgres queue library SQL-in-TypeScript monolith; dual Django+SQL tracks

---

## 1. Intent

Keep the SQL contract reviewable, versioned, and installable from the `taskq` package without host apps forking function bodies.

---

## 2. Layout (normative)

    src/taskq/sql/
      schema.sql                 # GENERATED snapshot of the migration chain (ADR-004):
                                 # review / diffing / clean test fixtures ONLY — never
                                 # an install-or-upgrade path for a live database
      queries.sql                # named sections for client call sites
      migrations/                # THE canonical, ordered, immutable history (ADR-004)
        0001_initial.sql
        0002_01_pre_....sql
        0002_50_post_....sql
      README.md                  # how to add a migration

### 2.1 `queries.sql` named sections

    -- claim_jobs --
    SELECT * FROM taskq.claim_jobs(...);

    -- complete_job --
    SELECT * FROM taskq.complete_job(...);

Parser: peer-style `-- name --` section headers → `dict[str, str]` loaded by the client. Comments after the header (`-- prose`) allowed.

### 2.2 Versioned functions during rollout

Prefer creating `taskq.claim_jobs_v2(...)` then swapping grants/wrappers, over in-place signature breakage. Drop old versions only in a **post** migration after clients upgraded.

### 2.3 Pre / post migrations

| Kind | When | Examples |
|---|---|---|
| `pre` | Before new code deploy | Add nullable columns, add new functions beside old |
| `post` | After new code deploy | Drop old functions, NOT NULL constraints, break notify |

Filename pattern:

    {seq}_{pre|post}_{slug}.sql

Record applied migrations in `taskq.schema_migrations(id text primary key, applied_at timestamptz)`.

---

## 3. Migration API (ADR-004)

    from taskq.sql import migrate, verify, current_version

    await migrate(conn)           # applies missing ordered migrations under an advisory
                                  # lock; records id + package version + checksum +
                                  # applied_at in taskq.schema_migrations
    report = await verify(conn)   # read-only: objects, signatures, ownership,
                                  # privileges, checksums vs the migration chain
    assert report.ok

Host Alembic/revision uses the **synchronous adapter** (Alembic runs sync — no ad-hoc
async bridging):

    def upgrade():
        from taskq.sql import migrate_sync
        migrate_sync(op.get_bind())

Workers never migrate in production by default (`auto_migrate=False`); application
startup verifies compatibility and stops on skew (feature 12), it never migrates.

---

## 4. Verify contract

`verify()` checks at minimum:

- Required tables/views exist
- Required functions exist with expected argument names (best-effort)
- Critical indexes exist (`jobs_claim_idx`, `jobs_idem_uq`, attempts partial unique)
- every application capability role (and PUBLIC, and `taskq_housekeeper`) lacks direct DML on `taskq.jobs`; function owners/ACLs/`proconfig` match the manifest (ADR-011)

This is the anti-drift test Unified Spec mandates.

---

## 5. Acceptance tests

1. Fresh DB `migrate` → `verify().ok`
2. Double invocation of `migrate()` is idempotent (never `schema.sql` replay against a live DB)
3. Mutate an index away → `verify()` fails
4. Named query keys stable (`claim_jobs`, `enqueue`, `complete_job`, `fail_job`, `release_job`, `snooze_job`, `redrive_job`, `tick`)

---

## 6. Explicit non-goals

- Supporting Cockroach / SQLite / Yugabyte
- Generating SQL from Python ORM models as source of truth
- Hosts editing `schema.sql` copies in-tree (depend on package version instead)
