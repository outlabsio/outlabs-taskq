"""Main-owned regression for the already-deployed a39/0046 upgrade path."""

import asyncio
import hashlib
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.artifact_smoke import (
    _OLD_OWNER_SHA256,
    _OWNER_UPGRADE_ID,
    _drop_owner_upgrade,
    _exercise_owner_upgrade,
    _prepare_owner_upgrade,
)
from taskq.sql import discover_migrations

REPO = Path(__file__).parents[1]


def test_original_0046_is_immutable_and_forward_migration_is_packaged():
    from taskq.http import runtime
    from taskq.sql.manifest import CONTRACT_VERSION

    migrations = discover_migrations()
    assert migrations[45].checksum == _OLD_OWNER_SHA256
    assert (
        hashlib.sha256(
            (REPO / "tests/fixtures/0046_queue_admission_owner.original.sql").read_bytes()
        ).hexdigest()
        == _OLD_OWNER_SHA256
    )
    assert migrations[-1].id == _OWNER_UPGRADE_ID
    assert len(migrations) == 47
    assert CONTRACT_VERSION == "0.6.11"
    for name in (
        "SUPPORTED",
        "ADMISSION",
        "WORKFLOW",
        "WORKFLOW_READ",
        "SCHEDULE",
        "WORKER_PRESENCE",
    ):
        assert {"0.6.10", "0.6.11"} <= getattr(runtime, name + "_SQL_CONTRACT_VERSIONS")


@pytest.mark.taskq_sql
def test_packaged_cli_preserves_populated_old_0046_and_replay(taskq_dsn, tmp_path):
    database = "pr52_upgrade_" + uuid4().hex
    try:
        asyncio.run(_prepare_owner_upgrade(taskq_dsn, database, REPO))
        _exercise_owner_upgrade(
            Path(sys.executable).parent / "taskq", taskq_dsn, database, cwd=tmp_path
        )
    finally:
        asyncio.run(_drop_owner_upgrade(taskq_dsn, database))
