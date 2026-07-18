# 08 — Blueprints and Namespaces

> **Priority:** SHOULD
> **Provenance:** the mature Python/Postgres task library Blueprint / App composition + task aliases
> **Depends on:** [09 Retry Value Surface](./09-retry-value-surface.md), handler registration

---

## 1. Intent

Allow domains to declare tasks in isolated modules and compose them onto one `TaskQ` app with a **namespace prefix**, without one mega-registry file. Support **aliases** so `job_type` renames do not strand in-flight rows.

---

## 2. API

    from taskq import Blueprint, TaskQ

    courts = Blueprint()

    @courts.task(
        job_type="missouri_casenet",   # local name before namespace
        queue="courts",
        retry=5,
        aliases=["missouri_casenet_v1"],
        lease_seconds=900,
    )
    async def scrape_missouri(ctx, payload: dict): ...

    app = TaskQ(dsn=...)
    app.add_tasks_from(courts, namespace="courts")
    # Registered job_type: "courts.missouri_casenet"
    # Also accepts alias: "courts.missouri_casenet_v1" OR bare alias policy below

### 2.1 Naming rules

| Piece | Rule |
|---|---|
| Namespace | `[a-z][a-z0-9_]*`, joined with `.` |
| Local job_type | `[a-z][a-z0-9_]*` |
| Fully qualified | `f"{namespace}.{local}"` when namespace non-empty |
| Empty namespace | Allowed; local name is global |

**Alias resolution:**

1. Aliases are registered as additional lookup keys → same handler.
2. Default: aliases are also namespaced (`courts.missouri_casenet_v1`).
3. Enqueue should use the **canonical** `job_type`; aliases exist for workers claiming old rows.
4. Startup assertion: every subscribed queue’s distinct `job_type`s seen in DB recently ⊆ handler keys ∪ aliases (optional warn mode).

---

## 3. Blueprint semantics

    class Blueprint:
        def task(self, *, job_type=None, queue="default", retry=..., aliases=None, **policy): ...
        def add_tasks_from(self, other: Blueprint, *, namespace: str = "") -> None: ...

    class TaskQ(Blueprint):  # App is a Blueprint
        def add_tasks_from(self, bp: Blueprint, *, namespace: str) -> None: ...

- Decorating on `TaskQ` directly is allowed (single-module apps).
- Double registration of the same fully-qualified `job_type` → error.
- Import discovery: `TaskQ(..., import_paths=["diverse_data_workers.tasks"])` imports modules for side-effect registration (the mature Python/Postgres task library pattern). Prefer explicit `add_tasks_from` in Diverse/QDarte.

---

## 4. Interaction with HTTP / SQL

- SQL `job_type` text is the fully-qualified string.
- HTTP enqueue uses the same string.
- Handlers matched by exact `job_type` or alias map.

---

## 5. Acceptance tests

1. Namespace composition yields `courts.missouri_casenet`.
2. Old alias still dispatches after rename.
3. Duplicate canonical registration raises.
4. Insert-only app can register blueprints for validation without callables (metadata-only) — optional.

---

## 6. Explicit non-goals

- Hierarchical RBAC on namespaces
- Auto-deriving queue name from namespace
- Plugin marketplace / entry-point loading in v1
