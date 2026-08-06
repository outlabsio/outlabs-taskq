"""Load-behavior harness (L-series) per the Load Behavior Test Harness Specification.

Report-only, like the B-series: toy runs prove the harness and are never
baselines. Invariants are enforced at every scale; numeric envelopes apply only
once explicitly accepted for a scenario, scale, and runner class. Scenarios
that encode a known defect assert the defect on unmodified code (red before
green) and record it under ``defect_observations`` in the artifact.
"""

from taskq.loadlab._chassis import LOAD_SCALES, LoadScale, run_scenario
from taskq.loadlab._scenarios import SCENARIOS

__all__ = ["LOAD_SCALES", "SCENARIOS", "LoadScale", "run_scenario"]
