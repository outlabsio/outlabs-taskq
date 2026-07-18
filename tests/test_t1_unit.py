"""T1 — pure unit layer (no Postgres): migration machinery + verify report model.

Covers harness layer T1 for the Stage 1 runner: file discovery/ordering,
checksum computation, ``{{CHECKSUM}}`` substitution, statement splitting,
pending-plan derivation, and the ``VerifyReport`` contract object.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from taskq.sql import (
    CHECKSUM_PLACEHOLDER,
    MIGRATE_LOCK_KEY,
    Migration,
    VerifyCheck,
    VerifyReport,
    current_version,
    discover_migrations,
    plan_pending,
    split_sql_statements,
)


def _write(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


def _migration(migration_id: str, sql: str) -> Migration:
    return Migration(
        id=migration_id,
        filename=f"{migration_id}.sql",
        checksum=hashlib.sha256(sql.encode()).hexdigest(),
        sql=sql,
    )


# ---------------------------------------------------------------------------
# Discovery and ordering (feature 13 §2 layout; ADR-004 filename order)
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_orders_by_filename_and_ignores_non_sql(self, tmp_path: Path) -> None:
        _write(tmp_path, "0002_50_post_drop_old.sql", "SELECT 2;")
        _write(tmp_path, "0001_initial.sql", "SELECT 1;")
        _write(tmp_path, "0002_01_pre_add_col.sql", "SELECT 3;")
        _write(tmp_path, "README.md", "how to add a migration")
        _write(tmp_path, "notes.txt", "not sql")

        migrations = discover_migrations(tmp_path)

        assert [m.id for m in migrations] == [
            "0001_initial",
            "0002_01_pre_add_col",
            "0002_50_post_drop_old",
        ]
        assert [m.filename for m in migrations] == [
            "0001_initial.sql",
            "0002_01_pre_add_col.sql",
            "0002_50_post_drop_old.sql",
        ]

    def test_missing_directory_yields_empty(self, tmp_path: Path) -> None:
        assert discover_migrations(tmp_path / "does-not-exist") == []

    def test_packaged_directory_is_well_formed(self) -> None:
        # The packaged migrations/ dir may be empty or populated (0001 is
        # authored separately from the manifest) — either way discovery must
        # return a filename-ordered list without raising.
        migrations = discover_migrations()
        names = [m.filename for m in migrations]
        assert names == sorted(names)
        assert all(name.endswith(".sql") for name in names)
        assert all(m.id == m.filename[: -len(".sql")] for m in migrations)


# ---------------------------------------------------------------------------
# Checksums and {{CHECKSUM}} substitution (ADR-004 ledger)
# ---------------------------------------------------------------------------


class TestChecksum:
    def test_checksum_is_sha256_of_raw_file_bytes(self, tmp_path: Path) -> None:
        body = "CREATE SCHEMA taskq;\n-- trailing note\n"
        _write(tmp_path, "0001_initial.sql", body)

        (migration,) = discover_migrations(tmp_path)

        assert migration.checksum == hashlib.sha256(body.encode("utf-8")).hexdigest()

    def test_checksum_computed_before_placeholder_substitution(self, tmp_path: Path) -> None:
        body = (
            "INSERT INTO taskq.schema_migrations (id, checksum)\n"
            "VALUES ('0001_initial', '{{CHECKSUM}}');\n"
        )
        _write(tmp_path, "0001_initial.sql", body)

        (migration,) = discover_migrations(tmp_path)

        # Ledger checksum covers the file exactly as packaged (placeholder intact)…
        assert migration.checksum == hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert CHECKSUM_PLACEHOLDER in migration.sql
        # …while the executed SQL carries the substituted value.
        assert CHECKSUM_PLACEHOLDER not in migration.executable_sql
        assert migration.checksum in migration.executable_sql

    def test_without_placeholder_executable_sql_is_unchanged(self) -> None:
        migration = _migration("0001_initial", "SELECT 1;")
        assert migration.executable_sql == migration.sql

    def test_lock_key_is_the_documented_stable_constant(self) -> None:
        assert MIGRATE_LOCK_KEY == int.from_bytes(b"taskqmig", "big")
        assert 0 < MIGRATE_LOCK_KEY < 2**63  # fits pg_advisory_lock(bigint)


# ---------------------------------------------------------------------------
# Statement splitting (dollar-quote/comment/string aware)
# ---------------------------------------------------------------------------


class TestSplitter:
    def test_plain_statements(self) -> None:
        assert split_sql_statements("SELECT 1; SELECT 2;\nSELECT 3") == [
            "SELECT 1",
            "SELECT 2",
            "SELECT 3",
        ]

    def test_dollar_quoted_function_body_is_one_statement(self) -> None:
        sql = (
            "CREATE FUNCTION taskq.noop() RETURNS void\n"
            "LANGUAGE plpgsql SECURITY DEFINER\n"
            "SET search_path = pg_catalog, taskq, pg_temp AS $$\n"
            "BEGIN\n"
            "    PERFORM 1;\n"
            "    RAISE NOTICE 'semi; colons; everywhere';\n"
            "END $$;\n"
            "GRANT EXECUTE ON FUNCTION taskq.noop() TO taskq_operator;\n"
        )
        statements = split_sql_statements(sql)
        assert len(statements) == 2
        assert statements[0].startswith("CREATE FUNCTION")
        assert "END $$" in statements[0]
        assert statements[1].startswith("GRANT EXECUTE")

    def test_tagged_dollar_quotes(self) -> None:
        sql = "CREATE FUNCTION f() RETURNS int LANGUAGE sql AS $body$SELECT 1;$body$; SELECT 2;"
        statements = split_sql_statements(sql)
        assert len(statements) == 2
        assert "$body$SELECT 1;$body$" in statements[0]

    def test_line_comments_hide_semicolons(self) -> None:
        sql = "SELECT 1 -- not a break; still comment\n, 2;\nSELECT 3;"
        statements = split_sql_statements(sql)
        assert len(statements) == 2
        assert statements[0].endswith(", 2")

    def test_nested_block_comments(self) -> None:
        sql = "/* outer ; /* inner ; */ still outer ; */ SELECT 1;"
        assert split_sql_statements(sql) == ["/* outer ; /* inner ; */ still outer ; */ SELECT 1"]

    def test_single_quotes_with_doubling(self) -> None:
        assert split_sql_statements("SELECT 'it''s; fine'; SELECT 2") == [
            "SELECT 'it''s; fine'",
            "SELECT 2",
        ]

    def test_e_string_backslash_escapes(self) -> None:
        assert split_sql_statements(r"SELECT E'a\';b'; SELECT 2") == [
            r"SELECT E'a\';b'",
            "SELECT 2",
        ]

    def test_double_quoted_identifiers(self) -> None:
        assert split_sql_statements('SELECT 1 AS "odd;name"; SELECT 2') == [
            'SELECT 1 AS "odd;name"',
            "SELECT 2",
        ]

    def test_comment_only_input_yields_nothing(self) -> None:
        assert split_sql_statements("-- header only\n/* block */\n") == []
        assert split_sql_statements("") == []

    def test_positional_dollar_refs_are_not_dollar_quotes(self) -> None:
        # `$1` must not open a dollar-quote region.
        assert split_sql_statements("PREPARE p AS SELECT $1; SELECT 2") == [
            "PREPARE p AS SELECT $1",
            "SELECT 2",
        ]


class TestTransactionControlGuard:
    def test_transaction_control_statements_are_rejected(self) -> None:
        migration = _migration("0001_initial", "BEGIN;\nCREATE TABLE t (x int);\nCOMMIT;")
        with pytest.raises(ValueError, match="transaction-control"):
            migration.statements()

    def test_plpgsql_begin_inside_dollar_quotes_is_fine(self) -> None:
        migration = _migration(
            "0001_initial",
            "CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $$BEGIN PERFORM 1; END$$;",
        )
        assert len(migration.statements()) == 1


# ---------------------------------------------------------------------------
# Pending plan + current_version
# ---------------------------------------------------------------------------


class TestPlan:
    def test_pending_excludes_applied_and_preserves_order(self) -> None:
        migrations = [
            _migration("0001_initial", "SELECT 1;"),
            _migration("0002_01_pre_x", "SELECT 2;"),
            _migration("0002_50_post_x", "SELECT 3;"),
        ]
        pending = plan_pending(migrations, {"0001_initial"})
        assert [m.id for m in pending] == ["0002_01_pre_x", "0002_50_post_x"]

    def test_everything_applied_means_empty_plan(self) -> None:
        migrations = [_migration("0001_initial", "SELECT 1;")]
        assert plan_pending(migrations, {"0001_initial", "stray_ledger_row"}) == []

    def test_current_version_is_last_by_filename(self, tmp_path: Path) -> None:
        _write(tmp_path, "0001_initial.sql", "SELECT 1;")
        _write(tmp_path, "0002_01_pre_x.sql", "SELECT 2;")
        assert current_version(tmp_path) == "0002_01_pre_x"

    def test_current_version_none_when_empty(self, tmp_path: Path) -> None:
        assert current_version(tmp_path) is None


# ---------------------------------------------------------------------------
# Verify report contract (feature 13 §3: `assert report.ok`)
# ---------------------------------------------------------------------------


class TestVerifyReport:
    def test_ok_when_every_check_passes(self) -> None:
        report = VerifyReport.from_checks(
            [
                VerifyCheck(name="schema_exists", ok=True),
                VerifyCheck(name="migration_ledger", ok=True),
            ]
        )
        assert report.ok
        assert report.failures == ()

    def test_single_failure_fails_the_report_and_is_listed(self) -> None:
        failing = VerifyCheck(
            name="function_hardening",
            ok=False,
            details=("taskq.enqueue(...): owned by 'evil', expected 'taskq_owner'",),
        )
        report = VerifyReport.from_checks([VerifyCheck(name="schema_exists", ok=True), failing])
        assert not report.ok
        assert report.failures == (failing,)
        assert "taskq_owner" in report.failures[0].details[0]

    def test_empty_check_list_is_vacuously_ok(self) -> None:
        # all() of nothing is True — documented edge; the five real probes
        # (schema, ledger, hardening, public-execute, roles) are exercised
        # against live Postgres by T2's fresh-install verify test.
        report = VerifyReport.from_checks([])
        assert report.ok
        assert report.checks == ()
