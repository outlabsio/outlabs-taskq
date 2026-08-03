# Security audit and release follow-up — outlabs-taskq

- **Original audit date:** 2026-08-02
- **Correction/remediation date:** 2026-08-02
- **Status:** corrected, released, and rollout-verified; this version supersedes the earlier contents of this file
- **Public base:** `origin/main` commit `96699f2` (`0.1.0a18`)
- **Remediated release:** `v0.1.0a19` at commit `b4ccb133b1b447f0d82638043e6455a2fc31932b`
- **Current compatibility successor:** `v0.1.0a21` updates only package identity and the optional
  OutLabs adapter dependency to `outlabs-auth>=0.1.0a27,<0.2.0`; SQL, protocol, and migrations
  remain unchanged. The lower bound is the first audited compatible release and the upper bound
  prevents an unreviewed major/minor contract change.
- **Contract:** SQL `0.2.6`, Protocol document `1.0.15`, migrations `0001`–`0018`

## Executive verdict

The core security assessment remains positive: no exploitable high- or medium-severity vulnerability
was found. The database privilege boundary, fenced job lifecycle, HTTP authorization ordering, and
secret-safe error paths are strong and are exercised against real PostgreSQL instances.

The original report was nevertheless too confident and contained material factual errors. Follow-up
review found one **release-blocking integrity compatibility regression** in 0.1.0a18 and one
**test-isolation defect** that hid fresh-cluster coverage. Neither was an attacker-controlled
privilege escalation, but both needed correction before the next release. They are fixed in the
published 0.1.0a19 release and covered by live and installed-artifact tests.

| Classification | Found | Current status |
|---|---:|---|
| Exploitable high severity | 0 | none found |
| Exploitable medium severity | 0 | none found |
| Release-blocking correctness/integrity | 1 | fixed and regression-tested |
| Test-isolation/coverage defects | 1 | fixed; dedicated blank-cluster CI gate added |
| Low hardening items requiring code/CI changes | 2 | fixed in published 0.1.0a19 |
| Accepted low/deployment observations | 6 | documented below |

## Corrections to the original report

| Original statement | Corrected statement |
|---|---|
| Scope was “full repository at `main`” package `0.1.0a17`. | The report did not record an audited commit SHA and mixed a17 package identity with a newer repository state. Public `main` was `0.1.0a18` at `96699f2`; this follow-up reviews that base plus the released a19 remediation diff. |
| “All 110 SQL functions.” | The migration history contains **108** `CREATE [OR REPLACE] FUNCTION` statements. The final 0.2.6 catalog contains **71** functions. All 71 final functions pass the owner, `SECURITY DEFINER`, pinned `search_path`, PUBLIC-revoke, and grant-manifest checks. |
| OpenAPI exposure was presented as an accidental low finding. | `/taskq/openapi.json` is an intentional, documented publication of the public Tier-0 wire contract. It remains a deployment fingerprinting consideration; hosts may gate it at the application or proxy boundary. |
| OutLabs API-key remediation was still open. | That tracked remediation had already been marked closed. The published release pins `outlabs-auth==0.1.0a26`; the stale observation is removed. |
| The audit’s partial test count established the release state. | The current evidence is the complete suite on clean PostgreSQL 16 and 18, plus independent blank-cluster and installed-artifact gates, recorded below. |
| “No files were modified” described this audit artifact indefinitely. | That was true only of the original read-only pass. This corrected report accompanies a remediation branch and explicitly records the changes. |

## Release-blocking issue found and fixed

### R1 — released migration checksum compatibility

0.1.0a18 changed a comment in the already-released `0001_initial.sql`. Executable SQL statements
remained equivalent, but the raw-file SHA-256 changed:

- 0.1.0a17 ledger checksum:
  `6d5b8196c091bbf08a2ea5ddec99eb5d386a018c462761caee15dad54f0571e3`
- 0.1.0a18/a19 packaged checksum:
  `6b4a2c2514ebf481d21093f75e31b3678e0ec63dba455f91812fb5703c461c5c`

An a17-installed database therefore failed a18 `taskq verify` with an immutable-history violation.
This was a real upgrade-verification regression and contradicts the original report’s claim that
migration replay/verification was fully clear.

The a19 verifier retains exact checksum comparison and adds one closed, migration-specific
compatibility entry for the released a17 hash. No normalization, comment stripping, prefix match, or
general historical bypass was added. An arbitrary checksum still fails. Migration files and ledger
rows are not rewritten.

### R2 — reserved-role test depended on cluster history

`tests/test_reserved_roles.py` mutated the six cluster-wide TaskQ roles without first creating its
target role. On an otherwise blank PostgreSQL cluster, all seven cases failed before reaching the
security assertion. They passed only after another test had installed the roles.

The test now creates only a missing target role, records ownership of that fixture, restores the
mutation, and removes only the role it created. It passes alone on blank PostgreSQL 16 and 18
clusters. CI now runs that file in an independent `fresh-cluster-security` matrix, before any shared
suite fixture can mask the defect.

## Hardening completed for 0.1.0a19

| Item | Resolution |
|---|---|
| Credentials in process arguments | `taskq migrate` and `taskq verify` accept an omitted DSN and read `TASKQ_DSN`. Worker and OutLabs auth commands retain compatible flags but recommend environment variables. Password-bearing DSNs and explicit HTTP token/value flags emit a warning that never repeats the credential. Tests cover environment-only operation, missing configuration, warnings, and non-disclosure. |
| Mutable CI action tags | Every `actions/checkout` and `astral-sh/setup-uv` use is pinned to a reviewed full commit SHA, with the release tag retained only as a comment. |
| No dependency vulnerability gate | CI exports the complete frozen, all-extras, third-party dependency graph and runs pinned `pip-audit==2.10.1`. The released graph reports no known vulnerabilities. |
| Incomplete public release documentation | Package/version references, migration range, release checklist, credential/TLS/OpenAPI guidance, and the public ADR index are corrected. Broken links to ADRs absent from the public distribution were removed and ADR-036 was added. |

## Verified security properties

- The final 71-function SQL catalog is owned by `taskq_owner`, uses `SECURITY DEFINER`, pins
  `search_path = pg_catalog, taskq, pg_temp`, revokes `EXECUTE` from PUBLIC, and matches the closed
  capability-grant manifest.
- The six TaskQ roles are `NOLOGIN` capability containers. Application roles receive no direct TaskQ
  table DML; the observer’s direct relation access is restricted to the designed read views.
- Claiming and settlement remain database-enforced: `FOR UPDATE SKIP LOCKED`, a unique running-attempt
  invariant, attempt fencing, verb-aware replay outcomes, and database-enforced idempotency.
- Job-addressed HTTP commands authorize from server-side projections, not caller assertions.
  Authentication precedes authorization, follow-up fan-out authorizes every child queue, and operator
  routes are absent unless a separate operator authorizer/transport pair is configured.
- Error normalization strips fence, attempt, payload, headers, progress, result, and SQL details.
  Worker settings store credentials as `SecretStr`; failure paths print exception types, not details.
- No dynamic SQL was found in migration function bodies. Python SQL identifiers interpolated by the
  package come from closed internal vocabularies; runtime values use bound parameters.
- `JobContext` does not expose the attempt ID or fence. The trusted effect helper binds job, attempt,
  worker, queue, and job type and holds the authoritative row lock inside the host transaction.

## Current validation evidence

| Gate | Result |
|---|---|
| Ruff lint and formatting | clean across 98 files |
| Complete suite, PostgreSQL 16 | **693 passed, 1 skipped** in 101.73s |
| Complete suite, PostgreSQL 18 | **693 passed, 1 skipped** in 93.93s |
| Expected skip | opt-in million-row plan test only |
| Reserved-role file alone on blank PG16 | **7 passed** |
| Reserved-role file alone on blank PG18 | **7 passed** |
| Released a17 checksum + arbitrary-tamper verification tests | **2 passed** against live PostgreSQL |
| Locked dependency graph, `pip-audit` 2.10.1 | **no known vulnerabilities** |
| Bandit 1.8.6 | **0 high-severity findings**; 11 medium-severity/low-confidence SQL-string findings manually reviewed as closed-constant construction |
| Workflow syntax | YAML parsed successfully; all third-party action tags replaced by full SHAs |
| Built artifacts | wheel and sdist built successfully |
| Installed-artifact matrix | **16/16 passed**: Python 3.12/3.13 × wheel/sdist × core/HTTP/OutLabs/all extras |
| Installed SQL smoke | fresh install, verify, 0016→0018 upgrade, and a17-ledger compatibility passed on PostgreSQL 18 |

## Published release identity

The release was integrated through pull request
[`outlabsio/outlabs-taskq#1`](https://github.com/outlabsio/outlabs-taskq/pull/1), then published as a
GitHub prerelease on 2026-08-02. The release is not published to PyPI.

| Identity | Immutable value |
|---|---|
| Release | [`v0.1.0a19`](https://github.com/outlabsio/outlabs-taskq/releases/tag/v0.1.0a19) |
| Annotated tag object | `3963e2ad58d392735757866e21db0119c6641a63` |
| Source commit | `b4ccb133b1b447f0d82638043e6455a2fc31932b` |
| Source tree | `d9d3eed4198358eeeda858b3e5248eae751e1702` |
| Wheel SHA-256 | `df39b3991f16ed66a3da100f9dbb919eb213d639b4fd8fb36661bb1cdf969dc4` |
| Sdist SHA-256 | `087648eaeb781913f7b7cbd0a6c0af170d2d70d8f8b1633b712ad15e727fe2a6` |

Both release assets were downloaded after publication and matched these hashes. Pull-request CI and
the post-merge `main` CI run passed the complete PostgreSQL 16/18, fresh-cluster, dependency-audit,
and installed-artifact gates.

## Downstream rollout status

Known live consumers and the public documentation were updated through separate reviewable pull
requests. “Merged” below identifies the exact branch that received the change; it does not imply
that an active cutover branch was promoted to a repository's default branch.

| Consumer | Target branch and integration | Verification |
|---|---|---|
| Outlabs API | `main`, [`outlabsio/outlabsAPI#6`](https://github.com/outlabsio/outlabsAPI/pull/6), commit `57c0ee54d3ad09436e580f922703cc1915ba68e4` | PR and post-merge CI passed, including PostgreSQL migration and delivery integration proof |
| Créditos del Norte API | `main`, [`outlabsio/creditos-del-norte-api#35`](https://github.com/outlabsio/creditos-del-norte-api/pull/35), commit `c1f14dfbc3594d5e5446e41450e9b40c0c225dc4` | PR and post-merge CI passed; staging API and shared admin staging jobs completed |
| Diverse Data API | active `codex/stage6-local-taskq-cutover`, [`meetDiverse/diverse-data-api#23`](https://github.com/meetDiverse/diverse-data-api/pull/23), commit `95d948c7eb47e1c1d1be15e38fc8eb98dff2ef92` | local full gate and post-merge hosted verification passed |
| Diverse Data workers | active `codex/stage6-local-taskq-cutover`, [`meetDiverse/diverse-data-workers#23`](https://github.com/meetDiverse/diverse-data-workers/pull/23), commit `91d1e61026f1b189a7d46fa52948b9be7c0277f6` | Ruff, mypy, import/adoption/recon gates, and 549 tests passed locally; this PR base has no hosted CI trigger |
| QDarte API | active `codex/qdarte-taskq-audit-fixes`, [`outlabsio/qdarteAPI#8`](https://github.com/outlabsio/qdarteAPI/pull/8), commit `88d5ae946485815d47104eb161d1364fc1ced457` | 110 TaskQ/queue tests passed on a fresh purpose-named PostgreSQL 18 database; this repository has no GitHub workflow |
| QDarte workers | active `codex/qdarte-taskq-audit-fixes`, [`outlabsio/qdarte-workers#2`](https://github.com/outlabsio/qdarte-workers/pull/2), commit `54080d5414b269eb5c90ec9bed3668e193ffcdd0` | Ruff, mypy across 61 source files, and 579 tests passed locally; this repository has no GitHub workflow |
| Public docs | `main`, [`outlabsio/outlabs-taskq-docs#2`](https://github.com/outlabsio/outlabs-taskq-docs/pull/2), commit `b3996f4effbaf57b57a2be9205aa1f53e25a4238` | PR and post-merge lint/typecheck CI passed |

## Accepted observations and operator guidance

1. **OpenAPI is public by design.** `/taskq/openapi.json` contains the public protocol, not
   credentials or attempt examples. Gate it at the host/proxy if deployment fingerprinting matters.
2. **Worker presence is advisory.** A runner can overwrite a caller-supplied `worker_id` presence
   row. Presence never grants authority, extends a lease, or drives recovery; spoof-resistance would
   require a future contract change.
3. **Observer access is payload-confidential.** Direct-SQL deployments must treat
   `taskq_observer` as able to read designed payload/result surfaces. Encrypt sensitive application
   fields when that role boundary is insufficient.
4. **HTTP permits a host-selected `http://` URL.** TLS verification is enabled when HTTPS is used.
   Internet-facing or routed deployments should use `https://`; protected private-network plaintext
   remains an explicit host policy decision.
5. **Attempt IDs are bearer capability material.** They remain absent from observer projections,
   handler context, error details, and package logs. Hosts must keep them out of application telemetry.
6. **Raw `compare_digest` length is observable in principle.** Packaged static credentials are
   high-entropy values, making the practical risk negligible; hashing both sides remains optional
   defense in depth.

## Release decision

The a18 verifier regression is fixed without weakening arbitrary migration-tamper detection, and the
test-order defect has an independent clean-cluster gate. Version 0.1.0a19 is merged, tagged, published
as a GitHub prerelease and hash-verified after publication. Rollout changes are integrated into each
known consumer's current target branch. Consumers already on default `main` are integrated there;
the Diverse and QDarte changes remain on their explicitly named active cutover branches pending those
projects' normal promotion decisions.
