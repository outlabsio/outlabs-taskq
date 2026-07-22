# Internal targeted delta response — QDarte C7 environment plan

## Verdict

**READY.** R16-01, R16-02, and R16-03 are closed consistently. This was an
**internal, non-independent review** performed by the implementation session
with the owner's explicit authorization because the usual separate review
session was unavailable.

READY opens only C7-01 preflight under the frozen plan. It authorizes no
package cohort, provider call, production job, direct retirement, non-contact
lane, C7-02+, or Stage 6.

## Immutable identity and scope

- immutable Round-16 response SHA-256:
  `f7c8cef4a68ad44eb345b26b8062d7f7f41befb517c2d4ae79e951344c66ad39`
- docs-only remediation commit: `5d08f7c`
- delta-request tip: `52b5851`
- remediated paths: C7 environment plan, `TASKS.md`, tier map, and Build Plan
- source, SQL, migration, contract, ADR, production, database, credential,
  service, queue, job, worker/provider, deployment, retirement, non-contact,
  and Stage-6 changes: zero

## Delta checks

### R16-01 — PASS

The invalid `place` literal is absent from the active plan. The cohort now
uses valid `country` scope, the selected place's stored country value, exactly
one `place_ids` UUID, `limit=1`, and `require_unverified_only=true`. The
provider-free oracle requires exactly one planned entity and exact equality
with the allowlisted place id; zero, a different id, or an additional entity
stops.

### R16-02 — PASS

C7-01 now owns a runtime-constructed verifier/client whose proxy cannot be
selected, replaced, or disabled by job payload. The direct/default verifier
path is forbidden in production and receives a failure-sentinel regression.

The dedicated closed worker joins only `qdarte-contact-internal`, declared
`internal: true`; it has no direct external route. The bounded egress proxy is
dual-homed to that network and the external application network, making it the
worker's sole outbound path. Proxy loss must fail before a provider result or
domain effect. Its log remains independent of package/domain/usage ledgers and
excludes phone, credential, body, and full URL data.

### R16-03 — PASS

The production graph is reachable and unpublished:

- QDarte API and package facade share the internal contact network;
- the facade also reaches PostgreSQL on the application network;
- the closed worker reaches only the facade and egress proxy on the internal
  network;
- the facade exposes its container port only to Compose peers and has no host
  `ports`; and
- both QDarte API and worker use the exact
  `http://qdarte-contact-taskq:8021` service origin.

Development remains loopback-only. Production accepts only the exact service
identity with the explicit production acknowledgement. The worker is a
dedicated Compose service, not a broadened host-native fleet member.

Health is no longer a static label: success requires a ready taskq runtime at
SQL 0.1.5 with admission active plus a usable separately capped domain/auth
dependency. Wrong metadata, database loss, or privilege failure prevents
startup or returns 503.

### Consistency — PASS

Topology and identity tables, request path, connection budget, cohort,
independent counter, C7-01 sequence, sixteen stop conditions, board, tier map,
and Build Plan describe the same repaired design. The incremental connection
formula remains three: one taskq connection plus two capped domain/auth
connections; the network/proxy repair adds no PostgreSQL connection.

### Scope — PASS

The remediation range is documentation only. Nothing contacts Mini87, a
provider, or a production database, and nothing changes a QDarte source branch
or deployable artifact.

## Contract questions

None. These corrections resolve host-integration mechanics under the existing
Protocol v1 revision 1.0.8, Function Manifest / SQL contract 0.1.5, and
ADR-020/022/023.

## Scope opened

C7-01 may now perform only the frozen preflight sequence after explicit
authorization: identify live deployed source, build reviewed convergence
candidates, execute fresh backup/restores, measure the connection high-water,
prove least privilege and private-network readiness, provision the lasting
package database/disabled services, and finish in `legacy` with the worker
stopped and zero package publish.

C7-02 and every provider/cohort action remain closed pending separate C7-01
acceptance.
