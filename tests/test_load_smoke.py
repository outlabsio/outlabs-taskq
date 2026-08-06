"""Toy smoke for the L-series: invariants always; red defects asserted reproduced.

Red-before-green (harness spec SS1.4): L4/L5 assert the *defective* behavior of
the unmodified runtime and contract. When P1b (survivable claim path) and later
P7/P8 (flow-control plane) land, the flipped expectations ship in the same PR.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taskq.loadlab import SCENARIOS, run_scenario

pytestmark = pytest.mark.taskq_sql


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_load_scenario_records_and_enforces(
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
        repetitions=1,
        seed=20260805,
        output=artifact,
    )
    assert written == artifact and artifact.is_file()
    loaded = json.loads(artifact.read_text(encoding="utf-8"))
    assert loaded["scenario"] == scenario == result["scenario"]
    assert result["scale"] == "toy"
    assert result["method"]["database_reset"] == (
        "fresh database created for scenario and dropped afterward"
    )
    assert result["method"]["reset_fingerprint"]["database"].startswith(
        f"taskq_load_{scenario.lower()}_"
    )
    failed = [check for check in result["invariants"]["checks"] if not check["ok"]]
    assert result["invariants"]["ok"], failed
    assert result["envelopes"]["accepted"] is None
    assert result["database"]["wal_bytes"] >= 0
    assert result["postgres"]["settings_fingerprint_sha256"]

    metrics = result["runs"][0]["metrics"]
    if scenario == "L1":
        assert metrics["claim_calls"] >= metrics["jobs"]
        assert 0.0 <= metrics["empty_claim_ratio"] <= 1.0
    elif scenario == "L2":
        assert metrics["retry_scheduled_dispersion_s"] > 0.0
        assert metrics["retry_observed_dispersion_s"] > 0.0
    elif scenario == "L4":
        defects = result["defect_observations"]
        assert defects["admission_frozen_during_consumer_outage"] is True
        assert defects["replay_of_existing_key_rejected_at_max_depth"] is True
        assert defects["backpressure_is_exception_shaped_without_retry_hint"] is True
        assert metrics["backpressure_rejections"] > 0
    elif scenario == "L5":
        defects = result["defect_observations"]
        assert defects["no_claim_error_backoff_reproduced"] is True
        assert defects["single_nonretryable_claim_error_fatal"] is True
        assert metrics["claim_errors_per_nudge"] >= 0.7
