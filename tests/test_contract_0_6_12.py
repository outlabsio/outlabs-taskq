"""Queue-admission ownership closure checks that need no database."""

import hashlib
from pathlib import Path

from taskq.http.runtime import SUPPORTED_SQL_CONTRACT_VERSIONS
from taskq.sql import discover_migrations
from taskq.sql.manifest import CONTRACT_VERSION, FUNCTIONS


MIGRATIONS = Path(__file__).parents[1] / "src/taskq/sql/migrations"
MIGRATION = MIGRATIONS / "0048_queue_admission_owner_recovery.sql"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_owner_recovery_migration_is_packaged_and_preserves_history() -> None:
    migrations = discover_migrations()
    assert migrations[-1].id == "0048_queue_admission_owner_recovery"
    assert CONTRACT_VERSION == "0.6.12"
    assert "0.6.12" in SUPPORTED_SQL_CONTRACT_VERSIONS
    assert _sha256(MIGRATIONS / "0046_queue_admission_owner.sql") == (
        "7cc1aca7fe508c49886f808eb4dca98d52a21fd269a2b38e8b455370cc5562b9"
    )
    assert _sha256(MIGRATIONS / "0047_queue_admission_owner_upgrade.sql") == (
        "2453241444dbc4e94e9844088f6850c3565b83bd6d036cb7e14d70e61bf4cd43"
    )


def test_owner_recovery_closes_replay_scheduler_identity_and_lock_paths() -> None:
    sql = MIGRATION.read_text()
    assert "admission_owner_oid oid" in sql
    assert "FROM taskq.queues WHERE name = p_queue FOR KEY SHARE" in sql
    assert "FROM taskq.queues WHERE name = p_queue FOR UPDATE" in sql
    assert "PERFORM taskq._check_admission_owner(p_queue, p_workflow_id)" in sql
    assert "o.xmin = pg_current_xact_id()::text::xid" in sql
    assert "s.admission_owner_oid = v_target_owner" in sql
    assert "CREATE FUNCTION taskq.adopt_queue_admission_owner" in sql
    assert "CREATE FUNCTION taskq.rotate_queue_admission_owner" in sql
    assert "queue must be paused for ownership adoption" in sql
    assert "queue must be paused for ownership rotation" in sql
    assert "active schedules must be paused" in sql


def test_owner_recovery_public_surface_is_closed_and_least_privilege() -> None:
    assert FUNCTIONS[
        "taskq.adopt_queue_admission_owner(text,text,text,text,text,uuid,boolean)"
    ].grants == frozenset({"taskq_operator"})
    assert FUNCTIONS[
        "taskq.rotate_queue_admission_owner(text,text,text,text,text,uuid,boolean)"
    ].grants == frozenset({"taskq_operator"})
    assert FUNCTIONS["taskq.get_queue_admission_owner_identity(text)"].grants == frozenset(
        {"taskq_observer"}
    )
    assert FUNCTIONS["taskq._resolve_admission_owner_role(text)"].grants == frozenset()
    assert FUNCTIONS["taskq._schedule_admission_owner_guard()"].grants == frozenset()
