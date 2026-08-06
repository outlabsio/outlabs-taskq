"""Toy smoke for the L-series: invariants always; defect expectations tracked.

Red-before-green (harness spec SS1.4): L4 still asserts the *defective*
contract behavior (flips with P7/P8). L5 flipped green with P1b in the same PR
and now asserts the survivable claim path; its red evidence on unmodified a26
is retained under docs/evidence/load-red-a26-l5-2026-08-05.json.
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
        assert defects["claim_backoff_active"] is True
        assert defects["single_nonretryable_survived"] is True
        assert defects["sustained_corruption_fails_closed"] is True
        assert metrics["claim_errors_per_nudge"] <= 0.6
    elif scenario == "L3":
        defects = result["defect_observations"]
        assert defects["smeared_dealigns"] is True
        assert defects["plain_identical"] is True
        assert defects["deterministic"] is True
        assert metrics["smeared_dispersion_s"] >= 0.5 * metrics["smear_seconds"]
        assert metrics["plain_dispersion_s"] == 0.0
    elif scenario == "L7":
        defects = result["defect_observations"]
        assert defects["resume_ramp_flattens_spike"] is True
        assert defects["redrive_smear_disperses"] is True
        assert defects["redrive_plain_immediate"] is True
        assert metrics["ramp_admitted_first_window"] < metrics["control_admitted_first_window"]
        assert metrics["redrive_smeared_dispersion_s"] >= 0.5 * metrics["redrive_smear_seconds"]
        assert metrics["redrive_plain_dispersion_s"] < 1.0
    elif scenario == "L8":
        defects = result["defect_observations"]
        assert defects["trips_at_threshold"] is True
        assert defects["single_flight_probe"] is True
        assert defects["recovers_and_ramps"] is True
        assert metrics["probes_admitted_under_fan"] == 1
    elif scenario == "L10":
        defects = result["defect_observations"]
        assert defects["queue_rate_converges"] is True
        assert defects["dry_key_no_starvation"] is True
        assert defects["max_running_holds"] is True
        assert metrics["rate_throttled_verdicts"] > 0
        assert metrics["key_free_still_queued"] == 0
        assert metrics["cap_round2_claimed"] == 0
