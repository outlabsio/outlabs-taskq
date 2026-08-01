"""Independent DB-free oracle for the proposed SQL-0.2.5 catalog delta."""

from __future__ import annotations

import json
from pathlib import Path


def _delta() -> dict[str, object]:
    path = (
        Path(__file__).parents[1] / "docs" / "workflow-continuations" / "wfc-i00-catalog-delta.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _evidence() -> dict[str, object]:
    path = (
        Path(__file__).parents[1]
        / "docs"
        / "workflow-continuations"
        / "evidence"
        / "wfc-i00-catalog-pg16-pg18-20260728.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_wfc_i00_exact_identity_oracle() -> None:
    delta = _delta()

    assert delta["schema_version"] == 1
    assert delta["protocol_document_revision"] == "1.0.15"
    assert delta["contract_version"] == "0.2.5"
    assert delta["definition_migration"] == "0016_workflow_continuations.sql"
    assert delta["activation_migration"] == ("0017_activate_workflow_continuations.sql")
    assert delta["capability"] == "workflow_continuations"
    assert set(delta["public_overloads"]) == {
        "taskq.claim_jobs(text,text,integer,text[],integer,text,uuid,text[])",
        "taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb,text)",
        "taskq.create_workflow(text,text,jsonb,text[],text,integer,text)",
    }
    assert set(delta["private_functions"]) == {
        "taskq._enqueue_followup(uuid,text,jsonb,integer)",
        "taskq._reserve_workflow_members(uuid,integer,text)",
    }
    assert set(delta["constraint_additions"]) == {
        "jobs_continuation_policy_hash_ck",
        "jobs_continuation_policy_shape_ck",
        "workflow_member_counts_admitted_total_ck",
        "workflows_continuation_policy_hash_ck",
        "workflows_continuation_policy_shape_ck",
        "workflows_member_limit_ck",
    }
    assert delta["index_removals"] == ["jobs_affinity_idx"]
    assert set(delta["function_body_replacements"]) == {
        "taskq._enqueue_followup(uuid,text,jsonb,integer)",
        "taskq.advance_workflow_cancellations(integer)",
        "taskq.cancel_workflow(uuid,text,text)",
        "taskq.claim_jobs(text,text,integer,text[],integer,text,uuid)",
        "taskq.complete_job(uuid,uuid,text,jsonb,jsonb,jsonb)",
        "taskq.create_workflow(text,text,jsonb,text[],text)",
        "taskq.enqueue(text,text,jsonb,smallint,timestamp with time zone,text,text,text,smallint,integer,text,integer,integer,uuid[],uuid,text,uuid,jsonb)",
        "taskq.finalize_workflows(integer)",
        "taskq.get_workflow_page(uuid,integer,uuid)",
        "taskq.manage_workflow_member_counts()",
        "taskq.seal_workflow(uuid,text)",
    }


def test_wfc_i00_catalog_digest_oracle_is_dual_major_result() -> None:
    delta = _delta()

    assert delta["table_shape_deltas"] == {
        "jobs": {
            "columns": 41,
            "digest": "943b80b9adef46c731b9208b3710b820",
        },
        "workflow_member_counts": {
            "columns": 8,
            "digest": "f79bc1d967e8eca2cabd56a7d4bdb132",
        },
        "workflows": {
            "columns": 18,
            "digest": "6bc37874b900f701e146cda14552835a",
        },
    }
    assert delta["constraint_shape_deltas"] == {
        "jobs": {
            "constraints": 20,
            "digest": "9f7f486f864bcfaf09f43bfd4b8dffc2",
        },
        "workflow_member_counts": {
            "constraints": 8,
            "digest": "a189ce4052298ca2208e6f7518a0f5a3",
        },
        "workflows": {
            "constraints": 11,
            "digest": "b35b364d2ab059ce10e9248c5dd9abfc",
        },
    }
    assert {name: item["digest"] for name, item in delta["index_additions"].items()} == {
        "jobs_affinity_policy_idx": "71f8d3c1a6b25063b3c1e9bd7094689e",
        "jobs_claim_policy_idx": "46eb01bf1ce48d2ead1147847a637939",
        "jobs_workflow_cancel_idx": "092a4c56bd382e8444bd4b41125eb3df",
    }

    evidence = _evidence()
    assert [item["result"] for item in evidence["postgres"]] == [
        "passed",
        "passed",
    ]
    observed = evidence["identical_cross_major_result"]
    assert observed["tables"] == {
        name: [item["columns"], item["digest"]]
        for name, item in delta["table_shape_deltas"].items()
    }
    assert observed["constraints"] == {
        name: [item["constraints"], item["digest"]]
        for name, item in delta["constraint_shape_deltas"].items()
    }
    assert observed["indexes"] == {
        name: item["digest"] for name, item in delta["index_additions"].items()
    }


def test_wfc_i00_claim_shape_and_metadata_are_closed() -> None:
    delta = _delta()
    claim = delta["policy_claim_shape"]

    assert claim == {
        "affinity_frontier_index": "jobs_affinity_policy_idx",
        "direct_or_filter": "forbidden_unbounded_shape",
        "frontier_bound": ("(1 + cardinality(supported_policy_hashes)) * batch <= 1650"),
        "frontier_global_order": "priority, scheduled_at, id",
        "frontier_local_order": ("continuation_policy_hash, priority, scheduled_at, id"),
        "normal_frontier_index": "jobs_claim_policy_idx",
        "supported_policy_hash_limit": 32,
        "targeted_index": "jobs_pkey",
    }
    assert delta["metadata"]["after_0016"]["active"] == [
        "admission_reservations",
        "dependencies_workflows",
        "followups",
        "read_model_list_finished",
        "read_model_list_ready",
        "read_model_list_running",
        "read_model_workflow",
        "schedules",
        "worker_presence",
    ]
    assert delta["metadata"]["after_0017"]["active"][-1] == ("workflow_continuations")
