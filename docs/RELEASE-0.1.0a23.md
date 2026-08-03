# outlabs-taskq 0.1.0a23 release notes

**Base release:** 0.1.0a22

**SQL contract:** 0.3.0, unchanged

**Protocol wire major:** 1, unchanged  
**Packaged migrations:** 0001–0020, unchanged

## What changes

`taskq migrate` now bootstraps and retains `taskq_owner` membership for a
managed PostgreSQL database-owner role with `CREATEROLE`. PostgreSQL 18 gives a
role creator administrative control but not the `SET` option required by
`ALTER ... OWNER TO`; a22 therefore failed safely on a fresh Neon database at
`ALTER SCHEMA taskq OWNER TO taskq_owner`.

The membership grant is part of the same transaction as the migration. A
failed first migration leaves neither the reserved role nor its membership
behind. Superuser installation behavior is unchanged, and immutable migration
files/checksums are unchanged.

The migration credential is deliberately privileged and must remain separate
from every API, worker, and scheduler runtime login. A runtime credential must
receive only the required TaskQ capability roles.

## Rollout

1. Install the exact 0.1.0a23 artifact with the owner credential available
   only to the migration process.
2. Run `taskq migrate`; on a fresh scheduler installation, expect 0019 to
   commit and 0020 to refuse the unbound target.
3. Inspect and bind the target using the same owner credential and reviewed
   CAS values.
4. Resume `taskq migrate`, run `taskq verify`, and remove the owner credential
   from the process scope.
5. Configure the API/worker/scheduler with a distinct restricted runtime login
   and static environment/installation expectations.

## Release gates

- managed-owner fresh install, bind, resume, and exact verify on PostgreSQL 16;
- the same managed-owner lane on PostgreSQL 18, including the `SET` membership
  semantics that exposed the a22 failure;
- full unit and PostgreSQL 16/18 SQL-contract suites;
- wheel/sdist installed-artifact smoke and dependency audit;
- Créditos staging migration/bind/doctor proof before any recurring timer;
- OutLabs backup verification and owner-only production closeout before its
  scheduler can be enabled.
