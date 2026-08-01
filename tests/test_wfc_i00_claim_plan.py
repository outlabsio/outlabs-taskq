"""DB-free structural tests for the WFC-I00 claim-plan evidence parser."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "wfc_i00_claim_plan.py"
    spec = importlib.util.spec_from_file_location("wfc_i00_claim_plan", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_summary_detects_indexes_scans_filters_and_bounded_sort() -> None:
    module = _script()
    document = [
        {
            "Planning Time": 0.2,
            "Execution Time": 0.4,
            "Plan": {
                "Node Type": "Limit",
                "Actual Rows": 50,
                "Plans": [
                    {
                        "Node Type": "Sort",
                        "Actual Rows": 150,
                        "Sort Method": "top-N heapsort",
                        "Plans": [
                            {
                                "Node Type": "Index Scan",
                                "Relation Name": "jobs",
                                "Index Name": module.SELECTED_INDEX,
                                "Actual Rows": 50,
                                "Actual Loops": 1,
                                "Rows Removed by Filter": 0,
                                "Shared Hit Blocks": 4,
                                "Shared Read Blocks": 2,
                            }
                        ],
                    }
                ],
            },
        }
    ]

    result = module._plan_summary(document)

    assert result["indexes"] == [module.SELECTED_INDEX]
    assert result["jobs_sequential_scan"] is False
    assert result["actual_rows"] == 50
    assert result["sorts"] == [
        {
            "node_type": "Sort",
            "actual_rows": 150,
            "sort_method": "top-N heapsort",
        }
    ]
    assert result["scans"][0]["shared_read_blocks"] == 2
    assert result["scans"][0]["total_actual_rows"] == 50


def test_plan_summary_rejects_jobs_sequential_scan_shape() -> None:
    module = _script()
    document = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "jobs",
                "Actual Rows": 50,
                "Rows Removed by Filter": 999_950,
            }
        }
    ]

    result = module._plan_summary(document)

    assert result["jobs_sequential_scan"] is True
    assert result["indexes"] == []
