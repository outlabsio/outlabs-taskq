"""Toy smoke: every implemented scenario runs and writes its JSON evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taskq.bench import SCENARIOS, run_scenario

pytestmark = pytest.mark.taskq_sql


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_benchmark_scenario_records_json(
    scenario: str,
    taskq_dsn: str,
    migrated: None,
    tmp_path: Path,
) -> None:
    artifact = tmp_path / f"{scenario.lower()}.json"
    result, written = await run_scenario(
        scenario,
        dsn=taskq_dsn,
        scale_name="toy",
        repetitions=3,
        seed=12345,
        output=artifact,
    )
    assert written == artifact and artifact.is_file()
    loaded = json.loads(artifact.read_text(encoding="utf-8"))
    assert loaded == result
    assert result["scenario"] == scenario
    assert result["scale"] == "toy"
    assert result["method"]["repetitions"] == 3
    assert result["method"]["database_reset"] == (
        "fresh database created for scenario and dropped afterward"
    )
    fingerprint = result["method"]["reset_fingerprint"]
    assert fingerprint["database"].startswith(f"taskq_bench_{scenario.lower()}_")
    assert fingerprint["jobs_rows"] == 0
    assert fingerprint["jobs_live_tuples"] == 0
    assert fingerprint["jobs_dead_tuples"] == 0
    assert fingerprint["migration_ledger"] == [
        "0001_initial",
        "0002_contract_0_1_1",
        "0003_contract_0_1_2",
    ]
    assert len(result["runs"]) == (6 if scenario == "B8" else 3)
    assert result["summary"]["median_throughput_rows_per_second"] > 0
    assert result["database"]["wal_bytes"] >= 0
    assert result["postgres"]["settings_fingerprint_sha256"]
    assert "jobs_claim_idx" in result["representative_explain"]["indexes"]
    assert result["representative_explain"]["bounded_actual_rows"] <= 1
    if scenario == "B4":
        for run in result["runs"]:
            assert run["accepted"] == run["terminal"] + run["remaining_active"]
            assert run["settled"] == run["terminal"]
            assert run["remaining_active"] == 0
            assert run["running_jobs"] == 0
            assert run["running_attempts"] == 0
            assert run["conservation_equal"] is True
            assert run["drained"] is True
    elif scenario == "B8":
        assert {run["mode"] for run in result["runs"]} == {"notify", "poll_only"}
        assert result["summary"]["notify_p50_ms"] >= 0
        assert result["summary"]["poll_only_p50_ms"] >= 0
    elif scenario == "B13":
        assert result["summary"]["released_claims"] == 0
        assert result["summary"]["expired_claims"] == 0
        assert all(run["conservation_equal"] for run in result["runs"])
