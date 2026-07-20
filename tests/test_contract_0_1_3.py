"""ADR-019/021 / SQL contract 0.1.3/0.1.4 read-model migration vectors."""

from __future__ import annotations

import json

import asyncpg
import pytest

pytestmark = pytest.mark.taskq_sql


async def test_profile_version_and_observer_projection_are_bounded(
    operator: asyncpg.Connection, observer: asyncpg.Connection
) -> None:
    created = await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, $2::jsonb, 'rm01')",
        "rm01_profile",
        '{"default_priority": 12}',
    )
    assert created is not None and created["result"] == "created"
    assert json.loads(created["profile"])["profile_version"] == 1

    unchanged = await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, $2::jsonb, 'rm01')",
        "rm01_profile",
        '{"default_priority": 12}',
    )
    assert unchanged is not None and unchanged["result"] == "unchanged"
    assert json.loads(unchanged["profile"])["profile_version"] == 1

    updated = await operator.fetchrow(
        "SELECT * FROM taskq.update_queue_profile($1, $2::jsonb, 'rm01', 1)",
        "rm01_profile",
        '{"default_priority": 13}',
    )
    assert updated is not None
    assert updated["result"] == "updated" and updated["current_version"] == 2
    assert updated["profile"]["profile_version"] == 2
    assert updated["profile"]["default_priority"] == 13

    conflict = await operator.fetchrow(
        "SELECT * FROM taskq.update_queue_profile($1, $2::jsonb, 'rm01', 1)",
        "rm01_profile",
        '{"default_priority": 14}',
    )
    assert conflict is not None
    assert dict(conflict) == {
        "result": "profile_version_conflict",
        "profile": None,
        "current_version": 2,
    }

    profile = await observer.fetchrow("SELECT * FROM taskq.get_queue_profile($1)", "rm01_profile")
    assert profile is not None
    assert set(profile.keys()) == {
        "name",
        "profile_version",
        "default_priority",
        "default_lease_seconds",
        "default_max_attempts",
        "default_backoff_mode",
        "default_backoff_base",
        "default_backoff_cap",
        "retention_hours",
        "failed_retention_hours",
        "max_depth",
        "notify_enabled",
        "paused",
    }
    assert profile["profile_version"] == 2


@pytest.mark.parametrize("view", ["ready", "running", "finished"])
async def test_all_read_model_views_remain_explicitly_inactive(
    operator: asyncpg.Connection, observer: asyncpg.Connection, view: str
) -> None:
    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'rm01')", "rm01_profile"
    )
    with pytest.raises(asyncpg.PostgresError) as exc_info:
        await observer.fetchrow("SELECT * FROM taskq.list_jobs('rm01_profile', $1)", view)
    assert exc_info.value.sqlstate == "TQ501"
    assert "read_model_view_inactive" in (exc_info.value.detail or "")


@pytest.mark.parametrize("view", ["ready", "running", "finished"])
async def test_list_jobs_distinguishes_unknown_inactive_and_empty(
    pg: asyncpg.Connection, operator: asyncpg.Connection, observer: asyncpg.Connection, view: str
) -> None:
    with pytest.raises(asyncpg.PostgresError) as missing:
        await observer.fetchrow("SELECT * FROM taskq.list_jobs('rm01_missing', $1)", view)
    assert missing.value.sqlstate == "TQ001"

    await operator.fetchrow(
        "SELECT * FROM taskq.ensure_queue($1, '{}'::jsonb, 'rm01')", "rm01_empty"
    )
    with pytest.raises(asyncpg.PostgresError) as inactive:
        await observer.fetchrow("SELECT * FROM taskq.list_jobs('rm01_empty', $1)", view)
    assert inactive.value.sqlstate == "TQ501"

    if view == "ready":
        await pg.execute(
            "UPDATE taskq.meta SET value='{\"active\":[\"read_model_list_ready\"]}'::jsonb "
            "WHERE key='capabilities'"
        )
        try:
            page = await observer.fetchrow("SELECT * FROM taskq.list_jobs('rm01_empty', $1)", view)
            assert page is not None and page["items"] == [] and page["next_after"] is None
        finally:
            await pg.execute(
                "UPDATE taskq.meta SET value='{\"active\":[]}'::jsonb WHERE key='capabilities'"
            )


async def test_observer_has_no_base_table_read_grant(observer: asyncpg.Connection) -> None:
    assert (
        await observer.fetchval("SELECT has_table_privilege(current_user, 'taskq.jobs', 'SELECT')")
        is False
    )
    assert (
        await observer.fetchval(
            "SELECT has_table_privilege(current_user, 'taskq.queues', 'SELECT')"
        )
        is False
    )
