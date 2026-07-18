"""taskq.sql — canonical migration runner and read-only schema verifier.

Implements ADR-004 (ordered, immutable package migrations are the single
source of truth) and borrowed-feature 13 §3 (the migration API):

    from taskq.sql import migrate, verify, current_version

    await migrate(conn)            # SQLAlchemy AsyncConnection
    report = await verify(conn)    # read-only drift check
    assert report.ok

Host Alembic revisions use the synchronous adapter (Alembic runs sync — no
ad-hoc async bridging, ADR-004 decision 4):

    def upgrade():
        from taskq.sql import migrate_sync
        migrate_sync(op.get_bind())

Design notes
------------
- Migrations are the ``.sql`` files packaged under ``taskq/sql/migrations/``,
  applied in filename order. A file is immutable once shipped: its sha256
  checksum (over the raw file bytes, BEFORE any placeholder substitution) is
  recorded in ``taskq.schema_migrations`` and re-verified by :func:`verify`.
- The literal ``{{CHECKSUM}}`` inside a migration is replaced with that
  migration's own checksum before execution, so a migration can self-record
  ledger/meta rows that stay consistent with the runner's accounting.
- Concurrent runners serialize on :data:`MIGRATE_LOCK_KEY`: runner-owned
  multi-transaction applies use a deliberately managed session lock, while a
  caller-owned transaction uses ``pg_advisory_xact_lock``.
- When the runner owns transaction scope, each migration (script + ledger
  row) commits in ONE transaction; inside a caller-managed transaction
  (Alembic) everything applies in the caller's transaction.
- Migration files must not contain transaction-control statements (BEGIN /
  COMMIT / ...) — the runner owns transaction boundaries and rejects them.
- The async entry points bridge through ``AsyncConnection.run_sync`` (the
  supported SQLAlchemy adapter), so sync and async share one implementation.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from sqlalchemy import bindparam, text

from taskq import __version__
from taskq.sql import manifest as _manifest

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import AsyncConnection

__all__ = [
    "CHECKSUM_PLACEHOLDER",
    "MIGRATE_LOCK_KEY",
    "SCHEMA",
    "TASKQ_ROLES",
    "Migration",
    "VerifyCheck",
    "VerifyReport",
    "current_version",
    "discover_migrations",
    "migrate",
    "migrate_sync",
    "plan_pending",
    "split_sql_statements",
    "verify",
    "verify_sync",
]

SCHEMA = "taskq"
CHECKSUM_PLACEHOLDER = "{{CHECKSUM}}"

#: Constant advisory-lock key for the migration runner (``int.from_bytes(b"taskqmig", "big")``).
#: Session-level, always released in a ``finally`` (and by disconnect as backstop).
MIGRATE_LOCK_KEY = int.from_bytes(b"taskqmig", "big")

#: The six capability roles of ADR-010 + ADR-011. ``verify`` asserts they all exist.
TASKQ_ROLES = (
    "taskq_owner",
    "taskq_producer",
    "taskq_runner",
    "taskq_observer",
    "taskq_operator",
    "taskq_housekeeper",
)

#: Pinned function search_path per the 0.1 Function Manifest §0 (ADR-010 hardening).
PINNED_SEARCH_PATH = ("pg_catalog", "taskq", "pg_temp")

_LEDGER = f"{SCHEMA}.schema_migrations"
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
_TXN_CONTROL = frozenset({"begin", "commit", "rollback", "start", "end", "savepoint", "release"})


# ---------------------------------------------------------------------------
# Migration files: discovery, checksum, statement splitting
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Migration:
    """One packaged migration file (immutable; ADR-004)."""

    id: str  # filename stem, e.g. "0001_initial"
    filename: str
    checksum: str  # sha256 hex of the raw file bytes (placeholder NOT substituted)
    sql: str  # decoded file text, exactly as packaged

    @property
    def executable_sql(self) -> str:
        """File text with ``{{CHECKSUM}}`` replaced by this file's own checksum."""
        return self.sql.replace(CHECKSUM_PLACEHOLDER, self.checksum)

    def statements(self) -> list[str]:
        """Individual executable statements, rejecting transaction control."""
        stmts = split_sql_statements(self.executable_sql)
        for stmt in stmts:
            keyword = _first_keyword(stmt)
            if keyword in _TXN_CONTROL:
                raise ValueError(
                    f"migration {self.id!r} contains transaction-control statement "
                    f"({keyword.upper()}); the runner owns transaction boundaries (ADR-004)"
                )
        return stmts


def discover_migrations(directory: Any | None = None) -> list[Migration]:
    """Load migrations from *directory* (default: the packaged ``migrations/`` dir).

    Returns them in filename order — the canonical apply order. Non-``.sql``
    entries are ignored; a missing directory yields an empty list.
    *directory* may be a ``pathlib.Path`` or an ``importlib.resources`` Traversable.
    """
    root = resources.files(__package__).joinpath("migrations") if directory is None else directory
    if not root.is_dir():
        return []
    entries = sorted(
        (e for e in root.iterdir() if e.is_file() and e.name.endswith(".sql")),
        key=lambda e: e.name,
    )
    out: list[Migration] = []
    for entry in entries:
        data = entry.read_bytes()
        out.append(
            Migration(
                id=entry.name[: -len(".sql")],
                filename=entry.name,
                checksum=hashlib.sha256(data).hexdigest(),
                sql=data.decode("utf-8"),
            )
        )
    return out


def plan_pending(migrations: Sequence[Migration], applied_ids: Iterable[str]) -> list[Migration]:
    """Migrations not yet recorded in the ledger, preserving filename order."""
    applied = set(applied_ids)
    return [m for m in migrations if m.id not in applied]


def current_version(directory: Path | None = None) -> str | None:
    """Id of the newest packaged migration (``None`` when none are packaged)."""
    migrations = discover_migrations(directory)
    return migrations[-1].id if migrations else None


def split_sql_statements(sql: str) -> list[str]:
    """Split a migration script on top-level ``;`` — quote/comment/dollar-aware.

    Handles: ``--`` line comments, nested ``/* */`` block comments, ``'...'``
    strings with ``''`` doubling, ``E'...'`` strings with backslash escapes,
    ``"..."`` identifiers, and ``$tag$ ... $tag$`` dollar quoting (so plpgsql
    bodies never split). Comment-only fragments are dropped.
    """
    statements: list[str] = []
    buf: list[str] = []
    has_code = False
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":  # line comment
            j = sql.find("\n", i)
            j = n if j == -1 else j + 1
            buf.append(sql[i:j])
            i = j
            continue
        if ch == "/" and nxt == "*":  # block comment (PostgreSQL nests these)
            depth, j = 1, i + 2
            while j < n and depth:
                if sql.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif sql.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            buf.append(sql[i:j])
            i = j
            continue
        if ch == "'":  # string literal ('' doubles; E'...' honors backslash)
            escapes = _is_estring_opener(sql, i)
            j = i + 1
            while j < n:
                if escapes and sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == "'":
                    if sql.startswith("''", j):
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            buf.append(sql[i:j])
            has_code = True
            i = j
            continue
        if ch == '"':  # quoted identifier ("" doubles)
            j = i + 1
            while j < n:
                if sql[j] == '"':
                    if sql.startswith('""', j):
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            buf.append(sql[i:j])
            has_code = True
            i = j
            continue
        if ch == "$":  # dollar quoting: $$ or $tag$ (never $1 positional refs)
            m = _DOLLAR_TAG.match(sql, i)
            if m:
                tag = m.group(0)
                j = sql.find(tag, m.end())
                j = n if j == -1 else j + len(tag)
                buf.append(sql[i:j])
                has_code = True
                i = j
                continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt and has_code:
                statements.append(stmt)
            buf, has_code = [], False
            i += 1
            continue
        buf.append(ch)
        if not ch.isspace():
            has_code = True
        i += 1
    tail = "".join(buf).strip()
    if tail and has_code:
        statements.append(tail)
    return statements


def _is_estring_opener(sql: str, quote_idx: int) -> bool:
    """True when the quote at *quote_idx* opens an ``E'...'`` escape string."""
    if quote_idx == 0 or sql[quote_idx - 1] not in "Ee":
        return False
    if quote_idx >= 2:
        prev = sql[quote_idx - 2]
        if prev.isalnum() or prev == "_":
            return False  # part of an identifier like TABLE_E'...', not an E-string
    return True


def _first_keyword(stmt: str) -> str:
    """First SQL keyword of a statement, skipping leading comments."""
    s = stmt
    while True:
        s = s.lstrip()
        if s.startswith("--"):
            nl = s.find("\n")
            if nl == -1:
                return ""
            s = s[nl + 1 :]
            continue
        if s.startswith("/*"):
            end = s.find("*/")
            if end == -1:
                return ""
            s = s[end + 2 :]
            continue
        break
    m = re.match(r"[A-Za-z_]+", s)
    return m.group(0).lower() if m else ""


# ---------------------------------------------------------------------------
# Migrate — advisory-locked, ledger-recorded, one transaction per migration
# ---------------------------------------------------------------------------


async def migrate(conn: AsyncConnection) -> list[str]:
    """Apply missing packaged migrations through an async connection.

    Serialized by the migration advisory key; each missing migration and its
    ``taskq.schema_migrations`` row commit together. Returns applied ids.
    """
    return await conn.run_sync(_migrate_impl)


def migrate_sync(bind: Connection) -> list[str]:
    """Synchronous twin of :func:`migrate` for Alembic/sync contexts (ADR-004)."""
    return _migrate_impl(bind)


def _migrate_impl(conn: Connection, migrations: Sequence[Migration] | None = None) -> list[str]:
    if migrations is None:
        migrations = discover_migrations()
    owns_txn = not conn.in_transaction()
    lock_function = "pg_advisory_lock" if owns_txn else "pg_advisory_xact_lock"
    conn.execute(text(f"SELECT {lock_function}(:key)"), {"key": MIGRATE_LOCK_KEY})
    try:
        pending = plan_pending(migrations, _applied_ids(conn))
        if owns_txn:
            conn.commit()  # close the probe txn; each migration gets its own
        applied: list[str] = []
        for migration in pending:
            try:
                for statement in migration.statements():
                    conn.exec_driver_sql(statement)
                _record_applied(conn, migration)
                if owns_txn:
                    conn.commit()
            except Exception:
                if owns_txn:
                    conn.rollback()
                raise
            applied.append(migration.id)
        return applied
    finally:
        if owns_txn:
            if conn.in_transaction():
                conn.rollback()
            released = conn.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATE_LOCK_KEY}
            ).scalar_one()
            conn.commit()
            if not released:  # pragma: no cover - proves runner lock accounting
                raise RuntimeError("taskq migration session lock was not held at release")


def _applied_ids(conn: Connection) -> set[str]:
    exists = conn.execute(text("SELECT to_regclass(:ledger)"), {"ledger": _LEDGER}).scalar()
    if exists is None:
        return set()
    return set(conn.execute(text(f"SELECT id FROM {_LEDGER}")).scalars())


def _ledger_columns(conn: Connection) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = 'schema_migrations'"
        ),
        {"schema": SCHEMA},
    ).scalars()
    return set(rows)


def _record_applied(conn: Connection, migration: Migration) -> None:
    """Ledger insert per ADR-004: (id, package_version, checksum, applied_at).

    Column-adaptive so a self-recording migration or a leaner ledger shape
    cannot abort the apply transaction; ``verify`` still enforces the full
    ADR-004 ledger shape. ``ON CONFLICT`` makes the runner's accounting win
    over any self-recorded row.
    """
    columns = _ledger_columns(conn)
    if "id" not in columns:
        raise RuntimeError(
            f"{_LEDGER} has no 'id' column — migration {migration.id!r} cannot be recorded"
        )
    names, values = ["id"], [":id"]
    params: dict[str, str] = {"id": migration.id}
    if "package_version" in columns:
        names.append("package_version")
        values.append(":package_version")
        params["package_version"] = __version__
    if "checksum" in columns:
        names.append("checksum")
        values.append(":checksum")
        params["checksum"] = migration.checksum
    if "applied_at" in columns:
        names.append("applied_at")
        values.append("now()")
    updates = [f"{c} = EXCLUDED.{c}" for c in ("package_version", "checksum") if c in columns]
    conflict = f"DO UPDATE SET {', '.join(updates)}" if updates else "DO NOTHING"
    conn.execute(
        text(
            f"INSERT INTO {_LEDGER} ({', '.join(names)}) "
            f"VALUES ({', '.join(values)}) ON CONFLICT (id) {conflict}"
        ),
        params,
    )


# ---------------------------------------------------------------------------
# Verify — read-only drift report (ADR-004 / ADR-011 §4, feature 13 §4)
# ---------------------------------------------------------------------------


class VerifyCheck(BaseModel):
    """One verify probe. ``details`` explains failures (empty when ok)."""

    name: str
    ok: bool
    details: tuple[str, ...] = ()


class VerifyReport(BaseModel):
    """Aggregate result of :func:`verify`. Never mutates the database."""

    ok: bool
    checks: tuple[VerifyCheck, ...]

    @classmethod
    def from_checks(cls, checks: Iterable[VerifyCheck]) -> VerifyReport:
        checks = tuple(checks)
        return cls(ok=all(c.ok for c in checks), checks=checks)

    @property
    def failures(self) -> tuple[VerifyCheck, ...]:
        return tuple(c for c in self.checks if not c.ok)


async def verify(conn: AsyncConnection) -> VerifyReport:
    """Read-only check of the live database against the packaged contract.

    The probes compare exact closed object sets, catalog identities and
    attributes, grants, role safety, relation shapes, seed identities, and
    packaged migration checksums against the machine manifest (ADR-004/011).
    """
    return await conn.run_sync(_verify_impl)


def verify_sync(bind: Connection) -> VerifyReport:
    """Synchronous twin of :func:`verify` (same probes, plain Connection)."""
    return _verify_impl(bind)


def _verify_impl(conn: Connection, migrations: Sequence[Migration] | None = None) -> VerifyReport:
    if migrations is None:
        migrations = discover_migrations()
    opened = not conn.in_transaction()
    try:
        checks = [
            _check_schema(conn),
            _check_ledger(conn, migrations),
            _check_function_catalog(conn),
            _check_function_hardening(conn),
            _check_public_execute(conn),
            _check_function_privileges(conn),
            _check_roles(conn),
            _check_role_attributes(conn),
            _check_relations(conn),
            _check_composites(conn),
            _check_table_shapes(conn),
            _check_constraints(conn),
            _check_indexes(conn),
            _check_views(conn),
            _check_relation_privileges(conn),
            _check_seed_state(conn),
            _check_external_foreign_keys(conn),
        ]
    finally:
        if opened and conn.in_transaction():
            conn.rollback()  # read-only: leave no dangling autobegun transaction
    return VerifyReport.from_checks(checks)


def _check_schema(conn: Connection) -> VerifyCheck:
    owner = conn.execute(
        text(
            "SELECT pg_catalog.pg_get_userbyid(nspowner) "
            "FROM pg_catalog.pg_namespace WHERE nspname = :schema"
        ),
        {"schema": SCHEMA},
    ).scalar()
    if owner is None:
        details = (f"schema '{SCHEMA}' does not exist (run `taskq migrate`)",)
    elif owner != _manifest.SCHEMA_OWNER:
        details = (f"schema '{SCHEMA}' is owned by {owner!r}, expected 'taskq_owner'",)
    else:
        details = ()
    return VerifyCheck(name="schema_exists", ok=not details, details=details)


def _check_ledger(conn: Connection, migrations: Sequence[Migration]) -> VerifyCheck:
    details: list[str] = []
    exists = conn.execute(text("SELECT to_regclass(:ledger)"), {"ledger": _LEDGER}).scalar()
    if exists is None:
        details.append(f"{_LEDGER} does not exist (run `taskq migrate`)")
        if migrations:
            details.append(f"{len(migrations)} packaged migration(s) not applied")
        return VerifyCheck(name="migration_ledger", ok=False, details=tuple(details))

    columns = _ledger_columns(conn)
    missing_cols = [c for c in ("package_version", "checksum", "applied_at") if c not in columns]
    if missing_cols:
        details.append("ledger is missing ADR-004 column(s): " + ", ".join(missing_cols))
    if "checksum" in columns:
        rows = dict(conn.execute(text(f"SELECT id, checksum FROM {_LEDGER}")).all())
    else:
        rows = {
            row_id: None for row_id in conn.execute(text(f"SELECT id FROM {_LEDGER}")).scalars()
        }

    by_id = {m.id: m for m in migrations}
    for row_id, recorded in sorted(rows.items()):
        packaged = by_id.get(row_id)
        if packaged is None:
            details.append(f"ledger row '{row_id}' has no packaged migration file")
        elif recorded is not None and recorded != packaged.checksum:
            details.append(
                f"checksum mismatch for '{row_id}': ledger {recorded[:12]}… "
                f"!= package {packaged.checksum[:12]}… (immutable-history violation)"
            )
    for m in migrations:
        if m.id not in rows:
            details.append(f"packaged migration '{m.id}' not applied")
    return VerifyCheck(name="migration_ledger", ok=not details, details=tuple(details))


_FUNCTIONS_SQL = text(
    """
    SELECT pg_catalog.format(
               '%I.%I(%s)', n.nspname, p.proname,
               pg_catalog.replace(pg_catalog.oidvectortypes(p.proargtypes), ', ', ',')
           ) AS signature,
           pg_catalog.pg_get_function_arguments(p.oid) AS arguments,
           pg_catalog.pg_get_function_result(p.oid) AS result,
           l.lanname AS language,
           p.provolatile::text AS volatility,
           p.proparallel::text AS parallel,
           p.proisstrict AS strict,
           p.proleakproof AS leakproof,
           pg_catalog.pg_get_userbyid(p.proowner) AS owner,
           p.prosecdef AS secdef,
           p.proconfig AS config,
           p.proacl IS NULL AS default_acl,
           EXISTS (SELECT 1 FROM pg_catalog.aclexplode(p.proacl) a
                    WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE') AS public_execute,
           ARRAY(
               SELECT COALESCE(r.rolname, 'PUBLIC') ||
                      CASE WHEN a.is_grantable THEN '*' ELSE '' END || '/' ||
                      pg_catalog.pg_get_userbyid(a.grantor)
                 FROM pg_catalog.aclexplode(p.proacl) a
                 LEFT JOIN pg_catalog.pg_roles r ON r.oid = a.grantee
                WHERE a.privilege_type = 'EXECUTE'
                  AND a.grantee <> p.proowner
                ORDER BY 1
           ) AS grants
      FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
      JOIN pg_catalog.pg_language l ON l.oid = p.prolang
     WHERE n.nspname = :schema AND p.prokind = 'f'
     ORDER BY 1
    """
)


def _pinned_search_path_of(config: Sequence[str] | None) -> tuple[str, ...] | None:
    for entry in config or ():
        key, _, value = entry.partition("=")
        if key == "search_path":
            return tuple(part.strip().strip('"') for part in value.split(","))
    return None


def _function_rows(conn: Connection) -> dict[str, Any]:
    return {
        row["signature"]: row for row in conn.execute(_FUNCTIONS_SQL, {"schema": SCHEMA}).mappings()
    }


def _set_details(label: str, expected: set[str], actual: set[str]) -> list[str]:
    details = [f"missing {label} '{name}'" for name in sorted(expected - actual)]
    details.extend(f"unexpected {label} '{name}'" for name in sorted(actual - expected))
    return details


def _check_function_catalog(conn: Connection) -> VerifyCheck:
    rows = _function_rows(conn)
    expected = set(_manifest.FUNCTIONS)
    details = _set_details("function", expected, set(rows))
    for identity in sorted(expected & rows.keys()):
        row = rows[identity]
        spec = _manifest.FUNCTIONS[identity]
        for field in ("arguments", "result", "language", "volatility", "parallel"):
            actual = row[field]
            wanted = getattr(spec, field)
            if actual != wanted:
                details.append(f"{identity}: {field} is {actual!r}, expected {wanted!r}")
        if row["strict"]:
            details.append(f"{identity}: unexpectedly STRICT")
        if row["leakproof"]:
            details.append(f"{identity}: unexpectedly LEAKPROOF")
    return VerifyCheck(name="function_catalog", ok=not details, details=tuple(details))


def _check_function_hardening(conn: Connection) -> VerifyCheck:
    """Every taskq function: SECURITY DEFINER, owner taskq_owner, pinned search_path."""
    details: list[str] = []
    for row in _function_rows(conn).values():
        signature = row["signature"]
        if not row["secdef"]:
            details.append(f"{signature}: not SECURITY DEFINER")
        if row["owner"] != "taskq_owner":
            details.append(f"{signature}: owned by '{row['owner']}', expected 'taskq_owner'")
        pinned = _pinned_search_path_of(row["config"])
        if pinned is None:
            details.append(f"{signature}: no pinned search_path (proconfig)")
        elif pinned != PINNED_SEARCH_PATH:
            details.append(
                f"{signature}: search_path pinned to {', '.join(pinned)!r}, "
                f"expected {', '.join(PINNED_SEARCH_PATH)!r}"
            )
    return VerifyCheck(name="function_hardening", ok=not details, details=tuple(details))


def _check_public_execute(conn: Connection) -> VerifyCheck:
    """No taskq function may be executable by PUBLIC (ADR-010).

    A NULL ``proacl`` counts as a violation: Postgres' default function ACL
    grants EXECUTE to PUBLIC, so a never-revoked function is world-callable.
    """
    details: list[str] = []
    for row in _function_rows(conn).values():
        if row["default_acl"]:
            details.append(
                f"{row['signature']}: default ACL — EXECUTE was never revoked from PUBLIC"
            )
        elif row["public_execute"]:
            details.append(f"{row['signature']}: EXECUTE granted to PUBLIC")
    return VerifyCheck(name="no_public_execute", ok=not details, details=tuple(details))


def _check_function_privileges(conn: Connection) -> VerifyCheck:
    rows = _function_rows(conn)
    details: list[str] = []
    for identity in sorted(set(_manifest.FUNCTIONS) & rows.keys()):
        actual = frozenset(rows[identity]["grants"] or ())
        expected = frozenset(
            f"{role}/{_manifest.SCHEMA_OWNER}" for role in _manifest.FUNCTIONS[identity].grants
        )
        if actual != expected:
            details.append(
                f"{identity}: EXECUTE grants are {sorted(actual)!r}, expected {sorted(expected)!r}"
            )
    return VerifyCheck(name="function_privileges", ok=not details, details=tuple(details))


_ROLES_SQL = text("SELECT rolname FROM pg_catalog.pg_roles WHERE rolname IN :names").bindparams(
    bindparam("names", expanding=True)
)


def _check_roles(conn: Connection) -> VerifyCheck:
    found = set(conn.execute(_ROLES_SQL, {"names": list(TASKQ_ROLES)}).scalars())
    missing = [role for role in TASKQ_ROLES if role not in found]
    details = tuple(f"role '{role}' does not exist" for role in missing)
    return VerifyCheck(name="capability_roles_exist", ok=not missing, details=details)


_ROLE_ATTRIBUTES_SQL = text(
    """
    SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb,
           rolcanlogin, rolreplication, rolbypassrls, rolconnlimit,
           rolvaliduntil, rolconfig
      FROM pg_catalog.pg_roles
     WHERE rolname IN :names
     ORDER BY rolname
    """
).bindparams(bindparam("names", expanding=True))


def _check_role_attributes(conn: Connection) -> VerifyCheck:
    details: list[str] = []
    for row in conn.execute(_ROLE_ATTRIBUTES_SQL, {"names": list(_manifest.ROLES)}).mappings():
        role = row["rolname"]
        expected = {
            "rolsuper": False,
            "rolinherit": True,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolcanlogin": False,
            "rolreplication": False,
            "rolbypassrls": False,
            "rolconnlimit": -1,
            "rolvaliduntil": None,
        }
        for field, wanted in expected.items():
            if row[field] != wanted:
                details.append(f"role '{role}': {field} is {row[field]!r}, expected {wanted!r}")
        actual_config = frozenset(row["rolconfig"] or ())
        if actual_config != _manifest.ROLE_CONFIGS[role]:
            details.append(
                f"role '{role}': settings are {sorted(actual_config)!r}, "
                f"expected {sorted(_manifest.ROLE_CONFIGS[role])!r}"
            )

    memberships = conn.execute(
        text(
            """
            SELECT member.rolname, granted.rolname
              FROM pg_catalog.pg_auth_members m
              JOIN pg_catalog.pg_roles member ON member.oid = m.member
              JOIN pg_catalog.pg_roles granted ON granted.oid = m.roleid
             WHERE member.rolname IN :names
             ORDER BY 1, 2
            """
        ).bindparams(bindparam("names", expanding=True)),
        {"names": list(_manifest.ROLES)},
    ).all()
    details.extend(
        f"role '{member}' is unexpectedly a member of '{granted}'"
        for member, granted in memberships
    )
    return VerifyCheck(name="role_manifest", ok=not details, details=tuple(details))


_RELATIONS_SQL = text(
    """
    SELECT c.relname, c.relkind::text AS relkind,
           pg_catalog.pg_get_userbyid(c.relowner) AS owner
      FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = :schema AND c.relkind IN ('r', 'v', 'S', 'c')
     ORDER BY c.relkind, c.relname
    """
)


def _check_relations(conn: Connection) -> VerifyCheck:
    rows = list(conn.execute(_RELATIONS_SQL, {"schema": SCHEMA}).mappings())
    by_kind = {
        kind: {row["relname"] for row in rows if row["relkind"] == kind}
        for kind in ("r", "v", "S", "c")
    }
    details = _set_details("table", set(_manifest.TABLES), by_kind["r"])
    details += _set_details("view", set(_manifest.VIEWS), by_kind["v"])
    details += _set_details("sequence", set(_manifest.SEQUENCES), by_kind["S"])
    details += _set_details("composite", set(_manifest.COMPOSITES), by_kind["c"])
    for row in rows:
        if row["owner"] != _manifest.SCHEMA_OWNER:
            details.append(
                f"{row['relname']}: owned by {row['owner']!r}, expected {_manifest.SCHEMA_OWNER!r}"
            )
    return VerifyCheck(name="relation_catalog", ok=not details, details=tuple(details))


def _check_composites(conn: Connection) -> VerifyCheck:
    rows = conn.execute(
        text(
            """
            SELECT c.relname, a.attname,
                   pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type
              FROM pg_catalog.pg_class c
              JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
              JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
             WHERE n.nspname = :schema AND c.relkind = 'c'
               AND a.attnum > 0 AND NOT a.attisdropped
             ORDER BY c.relname, a.attnum
            """
        ),
        {"schema": SCHEMA},
    ).mappings()
    actual: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        actual.setdefault(row["relname"], []).append((row["attname"], row["data_type"]))
    details: list[str] = []
    for name in sorted(set(_manifest.COMPOSITES) & actual.keys()):
        shape = tuple(actual[name])
        if shape != _manifest.COMPOSITES[name]:
            details.append(
                f"composite '{name}' shape is {shape!r}, expected {_manifest.COMPOSITES[name]!r}"
            )
    return VerifyCheck(name="composite_shapes", ok=not details, details=tuple(details))


_TABLE_SHAPES_SQL = text(
    """
    WITH cols AS (
        SELECT c.relname, a.attnum, a.attname,
               pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
               a.attnotnull,
               COALESCE(pg_catalog.pg_get_expr(ad.adbin, ad.adrelid), '') AS default_expr
          FROM pg_catalog.pg_class c
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_catalog.pg_attribute a
            ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
          LEFT JOIN pg_catalog.pg_attrdef ad
            ON ad.adrelid = c.oid AND ad.adnum = a.attnum
         WHERE n.nspname = :schema AND c.relkind = 'r'
    )
    SELECT relname, count(*) AS item_count,
           pg_catalog.md5(pg_catalog.string_agg(
               attnum::text || '|' || attname || '|' || data_type || '|' ||
               attnotnull::text || '|' || default_expr, E'\\n' ORDER BY attnum
           )) AS digest
      FROM cols GROUP BY relname ORDER BY relname
    """
)


def _digest_check(
    name: str, rows: Iterable[Any], expected: dict[str, tuple[int, str] | str]
) -> VerifyCheck:
    actual = {row["relname"]: row for row in rows}
    details = _set_details(name, set(expected), set(actual))
    for relname in sorted(set(expected) & actual.keys()):
        wanted = expected[relname]
        if isinstance(wanted, tuple):
            got = (actual[relname]["item_count"], actual[relname]["digest"])
        else:
            got = actual[relname]["digest"]
        if got != wanted:
            details.append(f"{name} '{relname}' definition differs from manifest")
    return VerifyCheck(name=name, ok=not details, details=tuple(details))


def _check_table_shapes(conn: Connection) -> VerifyCheck:
    rows = conn.execute(_TABLE_SHAPES_SQL, {"schema": SCHEMA}).mappings()
    return _digest_check("table_shapes", rows, _manifest.TABLE_SHAPES)


def _check_constraints(conn: Connection) -> VerifyCheck:
    rows = conn.execute(
        text(
            """
            SELECT rel.relname, count(*) AS item_count,
                   pg_catalog.md5(pg_catalog.string_agg(
                       con.conname || '|' || con.contype::text || '|' ||
                       pg_catalog.pg_get_constraintdef(con.oid, false),
                       E'\\n' ORDER BY con.conname
                   )) AS digest
              FROM pg_catalog.pg_constraint con
              JOIN pg_catalog.pg_class rel ON rel.oid = con.conrelid
              JOIN pg_catalog.pg_namespace n ON n.oid = rel.relnamespace
             WHERE n.nspname = :schema
             GROUP BY rel.relname ORDER BY rel.relname
            """
        ),
        {"schema": SCHEMA},
    ).mappings()
    return _digest_check("constraints", rows, _manifest.CONSTRAINTS)


def _check_indexes(conn: Connection) -> VerifyCheck:
    rows = conn.execute(
        text(
            """
            SELECT c.relname,
                   pg_catalog.md5(pg_catalog.pg_get_indexdef(c.oid)) AS digest
              FROM pg_catalog.pg_class c
              JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
              JOIN pg_catalog.pg_index i ON i.indexrelid = c.oid
             WHERE n.nspname = :schema
             ORDER BY c.relname
            """
        ),
        {"schema": SCHEMA},
    ).mappings()
    return _digest_check("indexes", rows, _manifest.INDEXES)


def _check_views(conn: Connection) -> VerifyCheck:
    rows = conn.execute(
        text(
            """
            SELECT c.relname,
                   pg_catalog.md5(pg_catalog.pg_get_viewdef(c.oid, false)) AS digest
              FROM pg_catalog.pg_class c
              JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = :schema AND c.relkind = 'v'
             ORDER BY c.relname
            """
        ),
        {"schema": SCHEMA},
    ).mappings()
    return _digest_check("views", rows, _manifest.VIEW_DEFINITIONS)


def _check_relation_privileges(conn: Connection) -> VerifyCheck:
    details: list[str] = []
    schema_grants = {
        (row["grantee"], row["privilege_type"], row["is_grantable"], row["grantor"])
        for row in conn.execute(
            text(
                """
                SELECT COALESCE(r.rolname, 'PUBLIC') AS grantee, a.privilege_type,
                       a.is_grantable,
                       pg_catalog.pg_get_userbyid(a.grantor) AS grantor
                  FROM pg_catalog.pg_namespace n,
                       LATERAL pg_catalog.aclexplode(n.nspacl) a
                  LEFT JOIN pg_catalog.pg_roles r ON r.oid = a.grantee
                 WHERE n.nspname = :schema AND a.grantee <> n.nspowner
                """
            ),
            {"schema": SCHEMA},
        ).mappings()
    }
    expected_schema = {
        (role, "USAGE", False, _manifest.SCHEMA_OWNER)
        for role in _manifest.ROLES
        if role != "taskq_owner"
    }
    if schema_grants != expected_schema:
        details.append(
            f"schema grants are {sorted(schema_grants)!r}, expected {sorted(expected_schema)!r}"
        )

    relation_grants = {
        (
            row["relname"],
            row["grantee"],
            row["privilege_type"],
            row["is_grantable"],
            row["grantor"],
        )
        for row in conn.execute(
            text(
                """
                SELECT c.relname, COALESCE(r.rolname, 'PUBLIC') AS grantee,
                       a.privilege_type, a.is_grantable,
                       pg_catalog.pg_get_userbyid(a.grantor) AS grantor
                  FROM pg_catalog.pg_class c
                  JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace,
                       LATERAL pg_catalog.aclexplode(c.relacl) a
                  LEFT JOIN pg_catalog.pg_roles r ON r.oid = a.grantee
                 WHERE n.nspname = :schema AND a.grantee <> c.relowner
                """
            ),
            {"schema": SCHEMA},
        ).mappings()
    }
    expected_relations = {
        (view, "taskq_observer", "SELECT", False, _manifest.SCHEMA_OWNER)
        for view in _manifest.VIEWS
    }
    if relation_grants != expected_relations:
        details.append(
            f"relation grants are {sorted(relation_grants)!r}, "
            f"expected {sorted(expected_relations)!r}"
        )

    privilege_list = "SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER"
    live_tables = {
        row["relname"]
        for row in conn.execute(_RELATIONS_SQL, {"schema": SCHEMA}).mappings()
        if row["relkind"] == "r"
    }
    live_roles = set(conn.execute(_ROLES_SQL, {"names": list(_manifest.ROLES)}).scalars())
    for role in set(_manifest.ROLES[1:]) & live_roles:
        for table in sorted(_manifest.TABLES & live_tables):
            granted = conn.execute(
                text("SELECT pg_catalog.has_table_privilege(:role, :relation, :privileges)"),
                {
                    "role": role,
                    "relation": f"{SCHEMA}.{table}",
                    "privileges": privilege_list,
                },
            ).scalar_one()
            if granted:
                details.append(f"role '{role}' has direct/effective privilege on table '{table}'")
    return VerifyCheck(name="relation_privileges", ok=not details, details=tuple(details))


def _check_seed_state(conn: Connection) -> VerifyCheck:
    details: list[str] = []
    live = {
        row["relname"]
        for row in conn.execute(_RELATIONS_SQL, {"schema": SCHEMA}).mappings()
        if row["relkind"] == "r"
    }
    if "meta" in live:
        meta = dict(conn.execute(text(f"SELECT key, value::text FROM {SCHEMA}.meta")).all())
        if meta != _manifest.META_SEEDS:
            details.append(f"meta rows are {meta!r}, expected {_manifest.META_SEEDS!r}")
    else:
        details.append("cannot verify meta seeds: table 'meta' is missing")
    if "control_state" in live:
        controls = set(conn.execute(text(f"SELECT key FROM {SCHEMA}.control_state")).scalars())
        details.extend(_set_details("control seed", set(_manifest.CONTROL_SEED_KEYS), controls))
    else:
        details.append("cannot verify control seeds: table 'control_state' is missing")
    if (
        "queues" in live
        and conn.execute(text(f"SELECT 1 FROM {SCHEMA}.queues WHERE name = '_system'")).scalar()
    ):
        details.append("deferred seed queue '_system' is present")
    return VerifyCheck(name="seed_state", ok=not details, details=tuple(details))


def _check_external_foreign_keys(conn: Connection) -> VerifyCheck:
    rows = conn.execute(
        text(
            """
            SELECT src_ns.nspname || '.' || src.relname || '.' || con.conname AS identity
              FROM pg_catalog.pg_constraint con
              JOIN pg_catalog.pg_class src ON src.oid = con.conrelid
              JOIN pg_catalog.pg_namespace src_ns ON src_ns.oid = src.relnamespace
              JOIN pg_catalog.pg_class dst ON dst.oid = con.confrelid
              JOIN pg_catalog.pg_namespace dst_ns ON dst_ns.oid = dst.relnamespace
             WHERE con.contype = 'f'
               AND ((src_ns.nspname = :schema) <> (dst_ns.nspname = :schema))
             ORDER BY 1
            """
        ),
        {"schema": SCHEMA},
    ).scalars()
    details = tuple(
        f"cross-schema foreign key '{identity}' is not in the manifest" for identity in rows
    )
    return VerifyCheck(name="external_foreign_keys", ok=not details, details=details)
