# ADR-018 — Operator UI tech stack

**Status:** Accepted 2026-07-20
**Resolves:** Frontend stack choice for the TaskQ operator console; aligns packaging intent with Growth §4 without accepting the read-model or SSE proposals themselves

## Context

TaskQ needs an operator console for queue health, keyset job lists, attempt timelines, worker
presence, and control actions (redrive/cancel), backed by Protocol v1 over the HTTP facade and
queue-scoped permissions from ADR-006. Live updates may later use the proposed Growth §5 SSE
bridge; 0.1 remains poll-based once Growth §4 read models exist.

OutLabs already ships two relevant frontend patterns:

- **Ops/admin SPAs:** `OutlabsAuthUI` and `qdarte-admin` — React, Vite, TanStack Router/Query/Table,
  Base UI, Bun, Cloudflare static deploy.
- **Documentation sites:** `outlabsAuth-docs` / `outlabs-taskq-docs` — Nuxt 4 + Nuxt UI.

Alternatives considered and rejected for the operator UI: Nuxt (docs-oriented defaults, weak reuse
of AuthUI table/auth patterns), a Vue Vite SPA without Nuxt (new in-house stack), server-driven
HTMX/Datastar (fast internal dogfood, weak open-source mount story), and prototype tools
(Streamlit/Reflex/Gradio).

## Decision

1. The TaskQ operator UI stack is **React + Vite + TypeScript + TanStack Router + TanStack Query +
   TanStack Table + Base UI**, in the same family as OutlabsAuthUI / qdarte-admin.
2. Package management and static deploy follow that family (**Bun**; Cloudflare Workers/Pages-style
   asset deploy). Nuxt remains the stack for **documentation sites only**, not the console.
3. The UI is a **Protocol-v1 HTTP client**. It must not open a second correctness path to Postgres,
   invent fence semantics, or widen authorization beyond the facade authorizer.
4. **Packaging sequence:** start as a **standalone** app configured with facade base URL +
   credentials; extract an **embeddable mount** (AuthUI-style) only after a host dogfood needs it
   in-process. Host-native-only pages (build solely into Diverse/QDarte admin) are not the primary
   open-source surface.
5. **Implementation gate:** do not build the console ahead of Growth §4 / H-11 read-model routes
   (stats, keyset job list, job detail). Until those exist, poll what the active facade exposes and
   treat missing list/detail shapes as `TQ501` / deferred — never invent parallel read SQL in the UI
   host. SSE (Growth §5) is a later swap from refetch to event invalidation on the same Query cache.
6. This ADR does **not** accept Growth §3–§5 endpoint designs, retention profiles, or dedicated-DB
   topology. Those still need their own ADRs. It only locks the frontend technology and packaging
   posture for when the console is built.

## Consequences

- New UI work targets a sibling repo/app in the AuthUI pattern, not an extension of
  `outlabs-taskq-docs`.
- Shared design tokens, auth-session habits, and table/query patterns with OutlabsAuthUI are
  preferred over inventing a third admin look.
- Agents must not propose Nuxt, HTMX, or Streamlit as the product console without a superseding ADR.
- Growth §4’s “dashboard is just a client” line is confirmed; the client’s framework is now fixed.
