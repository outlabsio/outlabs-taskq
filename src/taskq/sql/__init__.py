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
- Concurrent runners serialize on a session-level ``pg_advisory_lock`` keyed
  by :data:`MIGRATE_LOCK_KEY`.
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

    Serialized by a session advisory lock; each missing migration and its
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
    conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATE_LOCK_KEY})
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
        try:
            if owns_txn and conn.in_transaction():
                conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATE_LOCK_KEY})
            if owns_txn:
                conn.commit()
        except Exception:  # pragma: no cover - session close releases the lock anyway
            pass  # never mask the original error with unlock noise


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

    Probes: schema exists; ledger checksums match the packaged migration
    chain (no pending, no unknown, no drifted files); every ``taskq``
    function is SECURITY DEFINER, owned by ``taskq_owner``, with the pinned
    ``search_path``; no function is EXECUTE-grantable by PUBLIC; and the six
    capability roles exist (ADR-010/011).
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
            _check_function_hardening(conn),
            _check_public_execute(conn),
            _check_roles(conn),
        ]
    finally:
        if opened and conn.in_transaction():
            conn.rollback()  # read-only: leave no dangling autobegun transaction
    return VerifyReport.from_checks(checks)


def _check_schema(conn: Connection) -> VerifyCheck:
    found = conn.execute(
        text("SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = :schema"),
        {"schema": SCHEMA},
    ).scalar()
    details = () if found else (f"schema '{SCHEMA}' does not exist (run `taskq migrate`)",)
    return VerifyCheck(name="schema_exists", ok=found is not None, details=details)


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
    SELECT p.oid::regprocedure::text AS signature,
           pg_catalog.pg_get_userbyid(p.proowner) AS owner,
           p.prosecdef AS secdef,
           p.proconfig AS config,
           p.proacl IS NULL AS default_acl,
           EXISTS (SELECT 1 FROM pg_catalog.aclexplode(p.proacl) a
                    WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE') AS public_execute
      FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
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


def _check_function_hardening(conn: Connection) -> VerifyCheck:
    """Every taskq function: SECURITY DEFINER, owner taskq_owner, pinned search_path."""
    details: list[str] = []
    for row in conn.execute(_FUNCTIONS_SQL, {"schema": SCHEMA}).mappings():
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
    for row in conn.execute(_FUNCTIONS_SQL, {"schema": SCHEMA}).mappings():
        if row["default_acl"]:
            details.append(
                f"{row['signature']}: default ACL — EXECUTE was never revoked from PUBLIC"
            )
        elif row["public_execute"]:
            details.append(f"{row['signature']}: EXECUTE granted to PUBLIC")
    return VerifyCheck(name="no_public_execute", ok=not details, details=tuple(details))


_ROLES_SQL = text("SELECT rolname FROM pg_catalog.pg_roles WHERE rolname IN :names").bindparams(
    bindparam("names", expanding=True)
)


def _check_roles(conn: Connection) -> VerifyCheck:
    found = set(conn.execute(_ROLES_SQL, {"names": list(TASKQ_ROLES)}).scalars())
    missing = [role for role in TASKQ_ROLES if role not in found]
    details = tuple(f"role '{role}' does not exist" for role in missing)
    return VerifyCheck(name="capability_roles_exist", ok=not missing, details=details)
