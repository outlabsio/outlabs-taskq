# outlabs-taskq 0.1.0a41 release notes

**Base release:** 0.1.0a40  
**SQL contract:** 0.6.12 (unchanged)  
**Protocol document:** 1.0.18 (unchanged)  
**Packaged migrations:** 0001–0048 (unchanged)

## PostgreSQL 18 restore verification

This package-only diagnostic release accepts one exact alternate constraint
digest produced when PostgreSQL 18 reparses a `pg_dump`/`pg_restore` copy of
the `taskq.schedules` table. The restored table retains all 21 expected
constraint identities and definitions. Four three-term boolean conjunctions
are reassociated from a nested `((A AND B) AND C)` parse tree to the equivalent
flat `(A AND B AND C)` tree, yielding relation digest
`5e82a3a9211d75f78cce26bdbdc71736` instead of the canonical in-place-upgrade
digest `c20e70ddf516bb88d89d20a44bade146`.

The verifier allowlist is deliberately bounded to that single relation and
single alternate digest. Counts, names, every other constraint relation,
functions, ownership, grants, roles, tables, indexes, views, triggers, seed
state, migration checksums, and target identity remain exact. A different
digest still fails closed.

## Runtime and rollout impact

There is no SQL, protocol, migration, queue, scheduler, worker, HTTP, or
authorization behavior change. Existing a40 API, scheduler, and worker
runtimes remain compatible with SQL contract 0.6.12. Operators validating a
PostgreSQL 18 dump/restore should use a41 for `taskq db verify`; runtime
consumer upgrades are optional and may follow their ordinary immutable-image
rollout instead of being coupled to a database move.

Before accepting a restore, separately reconcile schema/object ownership and
grant provenance, then require all 18 packaged verification checks to pass.
This release does not treat `--no-owner --no-acl` output as self-healing and
does not broaden any role or privilege.

## Validation

- The alternate digest was reproduced from a TaskQ-only PostgreSQL 18 restore
  of the accepted Diverse production source while the source itself passed all
  18 a40 checks.
- Regression coverage freezes both the canonical digest and the sole accepted
  alternate.
- Package version, installed-artifact smoke, lint, unit, SQL-contract, and
  wheel/sdist verification remain release gates.
