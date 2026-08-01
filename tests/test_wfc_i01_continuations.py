"""WFC-I01 pure continuation contracts, compiler, and identity vectors."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

from hypothesis import assume, given, strategies as st
from pydantic import BaseModel, ValidationError
import pytest

from taskq import (
    CONTINUATION_PROTOCOL_DOCUMENT_REVISION,
    ClaimedJob,
    ContinuationClaimWireOptions,
    ContinuationCompleteWireOptions,
    ContinuationWorkflowWireOptions,
    Followup,
    FollowupTarget,
    InvalidFollowupError,
    Task,
    TaskRegistry,
    TaskqConfigError,
    WorkflowReadProfile,
    compile_continuation_policy,
    derive_continuation_idempotency_key,
    derive_continuation_workflow_step,
    validate_producer_idempotency_key,
)
from taskq.protocol import WorkflowKind, WorkflowStatus

_VECTORS = json.loads(
    (
        Path(__file__).parents[1]
        / "docs"
        / "workflow-continuations"
        / "wfc-i01-canonical-vectors.json"
    ).read_text(encoding="utf-8")
)


class Input(BaseModel):
    value: int


class Output(BaseModel):
    value: int


def _task(
    name: str,
    queue: str,
    *,
    aliases: tuple[str, ...] = (),
    targets: tuple[FollowupTarget, ...] = (),
) -> Task[Input, Output]:
    return Task(
        name=name,
        queue=queue,
        input_model=Input,
        output_model=Output,
        aliases=aliases,
        followup_targets=targets,
    )


def _member(queue: str, job_type: str, revision: str = "1") -> FollowupTarget:
    return FollowupTarget(
        queue=queue,
        job_type=job_type,
        workflow_member=True,
        continuation_revision=revision,
    )


def test_two_key_opt_in_is_strict_and_legacy_defaults_stay_detached() -> None:
    detached = FollowupTarget(queue="child", job_type="graph.child")
    assert detached.workflow_member is False
    assert detached.continuation_revision is None
    assert detached.model_dump(exclude_defaults=True) == {
        "queue": "child",
        "job_type": "graph.child",
    }

    with pytest.raises(ValidationError, match="required exactly"):
        FollowupTarget(
            queue="child",
            job_type="graph.child",
            workflow_member=True,
        )
    with pytest.raises(ValidationError, match="required exactly"):
        FollowupTarget(
            queue="child",
            job_type="graph.child",
            continuation_revision="1",
        )
    with pytest.raises(ValidationError):
        FollowupTarget(
            queue="child",
            job_type="graph.child",
            workflow_member=1,
            continuation_revision="1",
        )
    with pytest.raises(ValidationError):
        _member("child", "graph.child", "bad revision")

    legacy = Followup(step="next", job_type="graph.child")
    assert legacy.workflow_member is None
    assert legacy.model_dump(exclude_none=True) == {
        "step": "next",
        "job_type": "graph.child",
        "payload": {},
        "headers": {},
    }
    assert Followup(step="next", job_type="graph.child", workflow_member=True).workflow_member
    for rejected in (False, 0, 1, "true"):
        with pytest.raises(ValidationError):
            Followup(
                step="next",
                job_type="graph.child",
                workflow_member=rejected,
            )


def test_member_request_requires_member_enabled_declaration() -> None:
    child = _task("graph.child", "child")
    detached_parent = _task(
        "graph.parent",
        "parent",
        targets=(FollowupTarget(queue="child", job_type="graph.child"),),
    )
    registry = TaskRegistry((detached_parent, child))

    with pytest.raises(InvalidFollowupError, match="not member-enabled"):
        registry.normalize_followups(
            detached_parent,
            (
                Followup(
                    step="next",
                    job_type="graph.child",
                    queue="child",
                    payload={"value": 1},
                    workflow_member=True,
                ),
            ),
        )

    member_parent = _task(
        "graph.member_parent",
        "parent",
        targets=(_member("child", "graph.child"),),
    )
    registry.register(member_parent)
    normalized = registry.normalize_followups(
        member_parent,
        (
            Followup(
                step="next",
                job_type="graph.child",
                queue="child",
                payload={"value": 1},
                workflow_member=True,
            ),
            Followup(
                step="detached",
                job_type="graph.child",
                queue="child",
                payload={"value": 2},
            ),
        ),
    )
    assert normalized[0].workflow_member is True
    assert normalized[1].workflow_member is None


def test_canonical_policy_vector_aliases_sorting_detached_edges_and_closure() -> None:
    leaf = _task("graph.leaf", "leaf_q", aliases=("graph.leaf_old",))
    sibling = _task("graph.sibling", "sibling_q")
    middle = _task(
        "graph.middle",
        "middle_q",
        aliases=("graph.middle_old",),
        targets=(
            _member("leaf_q", "graph.leaf_old", "middle-v2"),
            FollowupTarget(queue="sibling_q", job_type="graph.sibling"),
        ),
    )
    root = _task(
        "graph.root",
        "root_q",
        aliases=("graph.root_old",),
        targets=(_member("middle_q", "graph.middle_old", "root-v1"),),
    )
    registry = TaskRegistry((leaf, sibling, middle, root))

    compiled = compile_continuation_policy(registry, ("graph.root_old",))
    vector = _VECTORS["canonical_policy"]
    expected = vector["canonical_json"].encode("utf-8")
    assert compiled.canonical_bytes == expected
    assert compiled.continuation_policy_hash == vector["sha256"]
    assert compiled.reachable_queues == tuple(vector["reachable_queues"])
    assert json.loads(compiled.canonical_bytes)["format"] == 1
    assert registry.compile_continuation_policy(("graph.root_old",)) == compiled


def test_cycles_terminate_and_each_parent_compiles_once() -> None:
    first = _task(
        "cycle.first",
        "a",
        targets=(_member("b", "cycle.second", "a1"),),
    )
    second = _task(
        "cycle.second",
        "b",
        targets=(_member("a", "cycle.first", "b1"),),
    )
    compiled = compile_continuation_policy(TaskRegistry((second, first)), (first,))
    assert [parent.job_type for parent in compiled.manifest.parents] == [
        "cycle.first",
        "cycle.second",
    ]
    assert compiled.reachable_queues == ("a", "b")


def test_policy_is_independent_of_registry_and_root_order() -> None:
    one = _task("root.one", "b")
    two = _task("root.two", "a")
    forward = compile_continuation_policy(TaskRegistry((one, two)), (one, two))
    reverse = compile_continuation_policy(TaskRegistry((two, one)), ("root.two", "root.one"))
    assert forward == reverse
    assert [(root.queue, root.job_type) for root in forward.manifest.roots] == [
        ("a", "root.two"),
        ("b", "root.one"),
    ]


def test_compiler_rejects_duplicate_canonical_roots_targets_and_revision_conflicts() -> None:
    child = _task("graph.child", "child", aliases=("graph.child_old",))
    parent = _task(
        "graph.parent",
        "parent",
        aliases=("graph.parent_old",),
        targets=(
            _member("child", "graph.child", "1"),
            _member("child", "graph.child_old", "1"),
        ),
    )
    registry = TaskRegistry((parent, child))
    with pytest.raises(TaskqConfigError, match="canonically distinct"):
        compile_continuation_policy(registry, ("graph.parent",))

    clean_parent = _task(
        "graph.clean_parent",
        "parent",
        aliases=("graph.clean_parent_old",),
        targets=(_member("child", "graph.child", "1"),),
    )
    clean = TaskRegistry((clean_parent, child))
    with pytest.raises(TaskqConfigError, match="roots must be canonically distinct"):
        compile_continuation_policy(
            clean,
            ("graph.clean_parent", "graph.clean_parent_old"),
        )
    with pytest.raises(TaskqConfigError, match="share one continuation revision"):
        _task(
            "graph.bad_revision",
            "parent",
            targets=(
                _member("child", "graph.child", "1"),
                _member("other", "graph.other", "2"),
            ),
        )


def test_compiler_rejects_empty_policy_and_more_than_32_reachable_queues() -> None:
    with pytest.raises(TaskqConfigError, match="at least one root"):
        compile_continuation_policy(TaskRegistry(), ())

    leaves = tuple(_task(f"leaf.t{i}", f"q{i}") for i in range(32))
    root = _task(
        "graph.root",
        "root",
        targets=tuple(_member(task.queue, task.name) for task in leaves),
    )
    with pytest.raises(TaskqConfigError, match="more than 32 queues"):
        compile_continuation_policy(TaskRegistry((root, *leaves)), (root,))


@given(
    parent=st.uuids().filter(lambda value: value.int != 0),
    step=st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", fullmatch=True),
)
def test_derived_identity_property(parent: UUID, step: str) -> None:
    workflow_step = derive_continuation_workflow_step(parent, step)
    idempotency_key = derive_continuation_idempotency_key(parent, step)
    assert workflow_step == f"c:{str(parent).lower()}:{step}"
    assert idempotency_key == f"chain:{str(parent).lower()}:{step}"
    assert len(workflow_step.encode("ascii")) <= 103
    assert len(idempotency_key.encode("ascii")) <= 107


@given(
    first_parent=st.uuids().filter(lambda value: value.int != 0),
    first_step=st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", fullmatch=True),
    second_parent=st.uuids().filter(lambda value: value.int != 0),
    second_step=st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", fullmatch=True),
)
def test_distinct_parent_step_pairs_have_distinct_derived_identities(
    first_parent: UUID,
    first_step: str,
    second_parent: UUID,
    second_step: str,
) -> None:
    assume((first_parent, first_step) != (second_parent, second_step))
    assert derive_continuation_workflow_step(
        first_parent, first_step
    ) != derive_continuation_workflow_step(second_parent, second_step)
    assert derive_continuation_idempotency_key(
        first_parent, first_step
    ) != derive_continuation_idempotency_key(second_parent, second_step)


def test_derived_identity_exact_max_vector_and_invalid_inputs() -> None:
    parent = UUID("AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA")
    step = "S" * 64
    assert derive_continuation_workflow_step(parent, step) == (
        "c:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa:" + step
    )
    assert len(derive_continuation_workflow_step(parent, step)) == 103
    assert len(derive_continuation_idempotency_key(parent, step)) == 107
    for bad in ("", "bad step", "x" * 65, "é"):
        with pytest.raises(TaskqConfigError, match="local_step"):
            derive_continuation_workflow_step(parent, bad)
    with pytest.raises(TaskqConfigError, match="non-nil"):
        derive_continuation_idempotency_key(UUID(int=0), "step")


def test_machine_identity_vector() -> None:
    vector = _VECTORS["derived_identity"]
    parent = UUID(vector["parent_job_id"])
    assert (
        derive_continuation_workflow_step(parent, vector["local_step"]) == vector["workflow_step"]
    )
    assert (
        derive_continuation_idempotency_key(parent, vector["local_step"])
        == vector["idempotency_key"]
    )


def test_reserved_producer_namespace_and_utf8_bound() -> None:
    assert validate_producer_idempotency_key(None) is None
    assert validate_producer_idempotency_key("public:key") == "public:key"
    assert validate_producer_idempotency_key("CHAIN:case-sensitive") == "CHAIN:case-sensitive"
    with pytest.raises(TaskqConfigError, match="reserved chain"):
        validate_producer_idempotency_key("chain:guessed")
    with pytest.raises(TaskqConfigError, match="255 UTF-8 bytes"):
        validate_producer_idempotency_key("é" * 128)


def test_supported_policy_advertisement_is_exact_bounded_and_canonical() -> None:
    hashes = ("f" * 64, "0" * 64)
    assert ContinuationClaimWireOptions(
        supported_policy_hashes=hashes
    ).supported_policy_hashes == tuple(sorted(hashes))
    assert ContinuationClaimWireOptions().supported_policy_hashes == ()
    assert (
        len(
            ContinuationClaimWireOptions(
                supported_policy_hashes=tuple(f"{index:064x}" for index in range(32))
            ).supported_policy_hashes
        )
        == 32
    )
    for rejected in (
        ("A" * 64,),
        ("a" * 63,),
        ("a" * 64, "a" * 64),
        tuple(f"{index:064x}" for index in range(33)),
    ):
        with pytest.raises(ValidationError):
            ContinuationClaimWireOptions(supported_policy_hashes=rejected)


def test_protocol_1015_option_models_do_not_activate_legacy_commands() -> None:
    policy_hash = "a" * 64
    assert _VECTORS["active_protocol_revision"] == "1.0.14"
    assert CONTINUATION_PROTOCOL_DOCUMENT_REVISION == _VECTORS["protocol_candidate_revision"]
    assert ContinuationWorkflowWireOptions().model_dump() == {
        "member_limit": 10_000,
        "continuation_policy_hash": None,
    }
    assert (
        ContinuationCompleteWireOptions(
            continuation_policy_hash=policy_hash
        ).continuation_policy_hash
        == policy_hash
    )


def test_additive_claim_and_workflow_read_fields_preserve_legacy_decode() -> None:
    claim = ClaimedJob.model_validate(
        {
            "job_id": uuid4(),
            "queue": "queue",
            "job_type": "graph.root",
            "priority": 100,
            "payload": {},
            "headers": {},
            "progress": None,
            "attempt_id": uuid4(),
            "attempt_number": 1,
            "failure_count": 0,
            "max_attempts": 3,
            "lease_expires_at": "2026-07-28T12:00:00Z",
            "lease_seconds": 60,
        }
    )
    assert claim.continuation_policy_hash is None

    profile_data = {
        "workflow_id": uuid4(),
        "kind": WorkflowKind.DAG,
        "status": WorkflowStatus.RUNNING,
        "sealed": False,
        "cancel_requested": False,
        "declared_queues": ("queue",),
        "created_at": "2026-07-28T12:00:00Z",
        "updated_at": "2026-07-28T12:00:00Z",
    }
    profile = WorkflowReadProfile.model_validate(profile_data)
    assert profile.member_limit is None
    assert profile.continuation_policy_hash is None
    budgeted = WorkflowReadProfile.model_validate(
        {
            **profile_data,
            "member_limit": 10,
            "admitted_total": 4,
            "remaining_capacity": 6,
            "continuation_policy_hash": "b" * 64,
        }
    )
    assert budgeted.remaining_capacity == 6
    with pytest.raises(ValidationError, match="must equal"):
        WorkflowReadProfile.model_validate(
            {
                **profile_data,
                "member_limit": 10,
                "admitted_total": 4,
                "remaining_capacity": 7,
            }
        )


def test_continuation_core_imports_remain_framework_independent() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from taskq import compile_continuation_policy; "
            "assert compile_continuation_policy; "
            "assert 'fastapi' not in sys.modules; "
            "assert 'outlabs_auth' not in sys.modules",
        ],
        check=True,
    )
