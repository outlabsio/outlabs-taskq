"""Toy B1–B4 smoke: every scenario runs and writes its JSON evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.runner import SCENARIOS, run_scenario

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
    assert len(result["runs"]) == 3
    assert result["summary"]["median_throughput_rows_per_second"] > 0
    assert result["database"]["wal_bytes"] >= 0
    assert result["postgres"]["settings_fingerprint_sha256"]
    assert "jobs_claim_idx" in result["representative_explain"]["indexes"]
    assert result["representative_explain"]["bounded_actual_rows"] <= 1
