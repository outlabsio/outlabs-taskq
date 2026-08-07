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
        "0004_read_models",
        "0005_read_model_conformance",
        "0006_activate_ready_read_model",
        "0007_admission_reservations",
        "0008_followups",
        "0009_workflows",
        "0010_schedules",
        "0011_finite_projections",
        "0012_activate_finite_projections",
        "0013_workflow_page_composite_repair",
        "0014_worker_presence_projection",
        "0015_activate_worker_presence",
        "0016_workflow_continuations",
        "0017_activate_workflow_continuations",
        "0018_trusted_effect_fence",
        "0019_scheduler_target_identity",
        "0020_standalone_scheduler",
        "0021_cli_read_model",
        "0022_queue_counters",
        "0023_activate_queue_counters",
        "0024_flow_enforcement_producer",
        "0025_flow_enforcement_claim",
        "0026_flow_enforcement_enqueue",
        "0027_activate_flow_control",
        "0028_redrive_null_limit_guard",
        "0029_schedule_claim_smear",
        "0030_schedule_smear_write",
        "0031_circuit_breaker",
        "0032_activate_circuit_breaker",
        "0033_priority_aging",
        "0034_breaker_observability",
        "0035_breaker_rate_tripping",
    ]
    assert len(result["runs"]) == (6 if scenario in {"B8", "B11"} else 3)
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
    elif scenario == "B9":
        evidence = result["read_model_b9"]
        assert evidence["fixture_rows"] == 200
        assert evidence["activation_fixture"] is False
        assert evidence["view_dispositions"]["ready"]["view"] == "ready"
        assert evidence["write_path_comparison"]["added_read_model_indexes"] is False
        assert evidence["ready_median_p95_ms"] >= 0
        assert evidence["claim_median_p95_ms"] >= 0
        assert evidence["heartbeat_median_p95_ms"] >= 0
    elif scenario == "B13":
        assert result["summary"]["released_claims"] == 0
        assert result["summary"]["expired_claims"] == 0
        assert all(run["conservation_equal"] for run in result["runs"])
    elif scenario == "B11":
        assert {run["mode"] for run in result["runs"]} == {"facade_only", "embedded"}
        assert "embedded_overhead_p99_ms" in result["summary"]
    elif scenario == "B14":
        assert result["summary"]["client_median_p99_ms"] >= 0
        assert "facade_median_overhead_p99_ms" in result["summary"]
