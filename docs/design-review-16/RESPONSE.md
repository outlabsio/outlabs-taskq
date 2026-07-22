# Internal targeted review response — QDarte C7 environment and preflight plan

## Verdict

**BLOCKED.** The environment choice, source-convergence rule, identity split,
connection arithmetic, backup/restore expansion, raw conservation oracles,
and rollback ordering are sound. Three docs-first corrections are required
before C7-01 may open.

This was an **internal, non-independent review** performed by the
implementation session with the owner's explicit authorization because the
usual separate review session was unavailable. All findings below were
derived from current source and remote refs rather than inferred from the plan.

## Exact identities

- taskq plan/hygiene tip reviewed: `7f9977b`
- review-request tip: `95ea99a`
- accepted QDarte API C6 tip:
  `7a744582b0d824a559aa29dfaf03ef1081058064`
- accepted QDarte workers C6 tip:
  `21bd880d5f2688f04cf323326512e6b630073d70`
- QDarte API remote refs: main
  `5e25ab695b6c8f7c5bf92c649c0f78413553e467`, staging
  `9364dd0d9b74cfba7d1f0dfaaf2582977e786d55`
- QDarte workers remote refs: main
  `f7427cb7ffd759eb7d2a0ec7d00a1dd830b23497`, staging
  `02ea8fe124883955f238d1b1c824e3728ebf130c`
- QDarte runtime remote refs: main
  `a6117c6e22a855ce1d1f57ed059be0eeda7b15fa`, staging
  `0921e46112cfa9c9dd06bac2367ec90ac11c24a5`

## Findings

### R16-01 — the proposed `place` scope is not a valid caller shape

**Severity:** BLOCKER

The plan freezes C7-02 as `scope_kind=place`. QDarte's shared `ScopeKind` is
the closed literal `cluster | geo | country | system | content_item`; `place`
does not exist. The contact planner already has the correct bounded mechanism:
it accepts `place_ids` and `limit` in addition to one valid scope.

**Required correction:** freeze the cohort as one valid containing scope
(normally `country` plus the place's stored country value), exact
`place_ids=[<one UUID>]`, and `limit=1`. The provider-free preflight must prove
the selected entity's `place_id` equals that sole allowlisted id and the total
entity count is exactly one. A zero, different, or additional entity stops.

### R16-02 — the closed worker has no enforceable egress-proxy seam

**Severity:** BLOCKER

The plan says the worker's existing explicit proxy seam will feed the
independent counter. That seam exists in the incumbent direct worker, not the
closed taskq handler. The closed handler calls `verify_whatsapp_number` without
an injected HTTP client, proxy, or runtime-owned network policy. It can reach
the external target directly, so a proxy count could be zero while a provider
call occurred.

**Required correction:** C7-01 must add a runtime-owned verifier/client seam to
the closed handler. Production startup requires the exact dedicated proxy;
job payload cannot select or disable it. More importantly, the worker service
must have no direct external network path: attach it only to an internal
contact network, and dual-home the bounded egress proxy to that network plus
the external network. Tests must make the default/direct verifier path fail,
prove the configured proxy receives the cohort traffic, and prove proxy loss
fails closed before a provider result or domain effect.

The proxy log remains bounded and sensitive-data-free as the plan states.

### R16-03 — loopback cannot connect the proposed separate containers

**Severity:** BLOCKER

The production QDarte API runs in its own Compose container. `127.0.0.1` or
`localhost` inside that container cannot reach a separate package-facade
container, and a host-loopback publish plus host-gateway exception would
unnecessarily complicate the authority boundary. The current contact URL and
worker guards are development-loopback-only, so the proposed production path
is unreachable as written.

**Required correction:** freeze the production topology as dedicated Compose
services on a private internal contact network. The facade has no `ports`
publication; QDarte API and closed worker reach one exact service origin such
as `http://qdarte-contact-taskq:8021`. The production-only acknowledgement may
permit exactly that service identity, while development retains loopback-only
validation. The worker is a dedicated Compose service rather than a member of
the broad host-native fleet. Operator actions use `docker compose exec` or the
database directly, never a published facade route.

The contact health endpoint must also become readiness-bearing: it returns
success only when the taskq runtime is ready at exact contract/capability
metadata and the separately capped domain/auth dependency is usable. Wrong
contract, unavailable database, or privilege failure must be a startup error
or 503; a static environment/queue JSON is insufficient for production.

## Accepted attack areas

1. **Environment:** current runtime authority confirms Mini87 local production
   owns the full API, worker fleet, effects, Postgres, and external-drive
   durability. Cloud staging owns different/read-only or intake surfaces;
   MacBook dev is rehearsal only.
2. **Source convergence:** recomputed graphs match the plan. API staging and
   worker staging are ancestors of their C6 tips, while both main refs diverge;
   runtime main/staging also diverge. Live build identity plus a
   zero-unclassified-path ledger is mandatory and sufficient—no merge policy
   is implied.
3. **Privilege wall:** separate package and domain logins are necessary. The
   existing harness's global QDarte engine would otherwise combine authority
   and default-pool capacity. The proposed role and service-principal negatives
   are correctly blocking.
4. **Connections:** taskq source recomputes to one request-pool connection and
   no operator/housekeeper/listener/embedded-worker connections. The worker is
   HTTP-only. The QDarte global engine has an uncapped default pool for this
   purpose; requiring a separate 2/zero-overflow dependency is correct. The
   incremental total is 3, and `H + 3 <= M - 20` is honest when H is the
   measured non-C7 peak.
5. **Backup/restore:** the copied 2026-07-20 dump hash and byte count match, and
   its local copy lacks the manifest-listed globals file, so the plan correctly
   treats it as historical only. A fresh two-database-plus-globals backup and
   executed disposable restore is the right gate.
6. **Conservation:** canonical in-database full-row JSONB aggregates over the
   direct job/attempt/event and stable effect/contact/usage tables are stronger
   than counts or high-waters. Active work and later direct insertion correctly
   stop rather than trigger replay.
7. **Rollback:** C7-01 performs no publish; C7-02 starts only after a fresh
   direct drain, uses one publisher, and preserves the three C6 zero-DML
   postures. No active-row import, row copy, or automatic backend switching is
   present.
8. **Scope:** the range is docs-only. No production, database, credential,
   service, worker, provider, cohort, retirement, non-contact, or Stage-6 state
   changed.

## Contract questions

None. All three findings concern the Tier-3 host integration plan and current
QDarte construction. Protocol v1 revision 1.0.8, Function Manifest / SQL
contract 0.1.5, and ADR-020/022/023 remain internally consistent.

## Delta gate

A docs-only delta must:

1. replace the invalid place scope with the exact valid-scope plus one-item
   `place_ids` and `limit=1` vector;
2. freeze the network-enforced egress proxy and runtime-owned verifier seam;
3. replace loopback production topology with the internal Compose service
   graph and readiness-bearing health; and
4. update every affected stop condition, connection/topology table, sequence,
   and review oracle consistently.

A targeted internal delta check may then return READY. READY would open only
C7-01 preflight and would still authorize no package cohort/provider call,
direct retirement, non-contact lane, C7-02+, or Stage 6.
