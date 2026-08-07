"""Scheduled scale-tier load gate.

The toy tier (`test_load_smoke`) runs on every CI push. The `small` tier — which
surfaced the half-open single-flight race (breaker review finding T1) that the toy
fan of 6 never trips — runs only on a schedule (see the `load-small` CI job). This
module runs the full L-series at whatever scale `TASKQ_LOAD_SCALE` names and enforces
each scenario's correctness invariants (no lost job, no duplicate execution, no
unexplained worker fatal, count conservation) — the scale-independent guarantees,
not the toy-calibrated metric envelopes.

Skipped entirely unless TASKQ_LOAD_SCALE is set, so it never runs in the normal lane.
"""

from __future__ import annotations

import os

import pytest

from taskq.loadlab import SCENARIOS, run_scenario

pytestmark = pytest.mark.taskq_sql

_SCALE = os.environ.get("TASKQ_LOAD_SCALE")


@pytest.mark.skipif(
    not _SCALE, reason="set TASKQ_LOAD_SCALE=small|full to run the scheduled scale gate"
)
@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_load_scenario_invariants_at_scale(
    scenario: str, taskq_dsn: str, migrated: None
) -> None:
    # L8's half-open probe election is single-flight only under real concurrency; the
    # T1 race was ~36% per run, so repeat it to catch a regression reliably. Every
    # scenario runs against its own fresh, migrated, then-dropped database.
    repetitions = 5 if scenario == "L8" else 1
    for rep in range(repetitions):
        result, _ = await run_scenario(
            scenario,
            dsn=taskq_dsn,
            scale_name=_SCALE,
            repetitions=1,
            seed=20260807 + rep,
        )
        failed = [c for c in result["invariants"]["checks"] if not c["ok"]]
        assert result["invariants"]["ok"], f"{scenario} @ {_SCALE} rep {rep}: {failed}"
