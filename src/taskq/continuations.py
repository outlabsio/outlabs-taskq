"""Pure workflow-continuation policy, negotiation, and identity contracts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from taskq.errors import InvalidFollowupError, TaskqConfigError
from taskq.protocol import Followup

if TYPE_CHECKING:
    from taskq.registry import Task, TaskRegistry


CONTINUATION_PROTOCOL_DOCUMENT_REVISION = "1.0.15"
CONTINUATION_POLICY_FORMAT = 1
DEFAULT_WORKFLOW_MEMBER_LIMIT = 10_000
MAX_WORKFLOW_MEMBER_LIMIT = 1_000_000
MAX_CONTINUATION_POLICY_HASHES = 32
MAX_CONTINUATION_POLICY_QUEUES = 32
RESERVED_CONTINUATION_IDEMPOTENCY_PREFIX = "chain:"

_POLICY_HASH = re.compile(r"[0-9a-f]{64}\Z")
_LOCAL_STEP = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class ContinuationPolicyNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_type: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$",
    )
    queue: str = Field(pattern=r"^[a-z0-9_]{1,57}$")


class ContinuationPolicyParent(ContinuationPolicyNode):
    revision: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    targets: tuple[ContinuationPolicyNode, ...] = Field(min_length=1)


class ContinuationPolicyManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal[1] = CONTINUATION_POLICY_FORMAT
    roots: tuple[ContinuationPolicyNode, ...] = Field(min_length=1)
    parents: tuple[ContinuationPolicyParent, ...]


@dataclass(frozen=True, slots=True)
class CompiledContinuationPolicy:
    """Immutable compiler result; the hash covers only ``canonical_bytes``."""

    manifest: ContinuationPolicyManifest
    canonical_bytes: bytes
    continuation_policy_hash: str
    reachable_queues: tuple[str, ...]


class ContinuationWorkflowWireOptions(BaseModel):
    """Protocol-1.0.15 workflow-create fields, not yet bound to a transport."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    member_limit: int = Field(
        default=DEFAULT_WORKFLOW_MEMBER_LIMIT,
        ge=1,
        le=MAX_WORKFLOW_MEMBER_LIMIT,
    )
    continuation_policy_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ContinuationClaimWireOptions(BaseModel):
    """Protocol-1.0.15 claim negotiation, where empty means null-policy only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supported_policy_hashes: tuple[str, ...] = Field(
        default=(),
        max_length=MAX_CONTINUATION_POLICY_HASHES,
    )

    @field_validator("supported_policy_hashes")
    @classmethod
    def _canonical_supported_hashes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(_POLICY_HASH.fullmatch(item) is None for item in value):
            raise ValueError("supported_policy_hashes must contain full lowercase SHA-256 values")
        if len(set(value)) != len(value):
            raise ValueError("supported_policy_hashes must be distinct")
        return tuple(sorted(value))


class ContinuationCompleteWireOptions(BaseModel):
    """Protocol-1.0.15 runtime-owned complete witness."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    continuation_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def derive_continuation_workflow_step(parent_job_id: UUID, local_step: str) -> str:
    """Derive the engine-owned workflow step for one member continuation."""

    parent = _validated_parent_uuid(parent_job_id)
    step = _validated_local_step(local_step)
    value = f"c:{parent}:{step}"
    if len(value.encode("ascii")) > 103:  # defensive contract assertion
        raise TaskqConfigError("derived continuation workflow step exceeds 103 ASCII bytes")
    return value


def derive_continuation_idempotency_key(parent_job_id: UUID, local_step: str) -> str:
    """Derive the engine-owned queue idempotency key for one continuation."""

    parent = _validated_parent_uuid(parent_job_id)
    step = _validated_local_step(local_step)
    value = f"{RESERVED_CONTINUATION_IDEMPOTENCY_PREFIX}{parent}:{step}"
    if len(value.encode("ascii")) > 107:  # defensive contract assertion
        raise TaskqConfigError("derived continuation idempotency key exceeds 107 ASCII bytes")
    return value


def validate_producer_idempotency_key(value: str | None) -> str | None:
    """Validate a public producer key and reject the engine-owned namespace."""

    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise TaskqConfigError("idempotency_key must be a non-empty string")
    if len(value.encode("utf-8")) > 255:
        raise TaskqConfigError("idempotency_key exceeds 255 UTF-8 bytes")
    if value.startswith(RESERVED_CONTINUATION_IDEMPOTENCY_PREFIX):
        raise TaskqConfigError("idempotency_key uses the reserved chain: namespace")
    return value


def compile_continuation_policy(
    registry: TaskRegistry,
    roots: Iterable[Task[Any, Any] | str],
) -> CompiledContinuationPolicy:
    """Compile a finite member-enabled registry graph without performing work."""

    registry.validate_followup_graph()
    resolved_roots = tuple(registry.require(root) for root in roots)
    if not resolved_roots:
        raise TaskqConfigError("continuation policy requires at least one root task")

    root_identities = [(task.queue, task.name) for task in resolved_roots]
    if len(set(root_identities)) != len(root_identities):
        raise TaskqConfigError("continuation policy roots must be canonically distinct")

    pending = list(resolved_roots)
    visited: set[str] = set()
    reachable_queues = {task.queue for task in resolved_roots}
    parents: list[ContinuationPolicyParent] = []

    while pending:
        parent = pending.pop()
        if parent.name in visited:
            continue
        visited.add(parent.name)

        member_targets = tuple(
            target for target in parent.followup_targets if target.workflow_member
        )
        if not member_targets:
            continue

        revisions = {target.continuation_revision for target in member_targets}
        if len(revisions) != 1:
            raise TaskqConfigError(
                f"member-enabled targets for {parent.name!r} must share one continuation revision"
            )
        revision = next(iter(revisions))
        if revision is None:  # guarded by FollowupTarget; retains a total compiler
            raise TaskqConfigError(
                f"member-enabled targets for {parent.name!r} require a continuation revision"
            )

        compiled_targets: list[ContinuationPolicyNode] = []
        for declared in member_targets:
            child = registry.require(declared.job_type)
            reachable_queues.add(child.queue)
            pending.append(child)
            compiled_targets.append(ContinuationPolicyNode(job_type=child.name, queue=child.queue))

        target_identities = {(item.queue, item.job_type) for item in compiled_targets}
        if len(target_identities) != len(compiled_targets):
            raise TaskqConfigError(
                f"member-enabled targets for {parent.name!r} must be canonically distinct"
            )
        parents.append(
            ContinuationPolicyParent(
                job_type=parent.name,
                queue=parent.queue,
                revision=revision,
                targets=tuple(
                    sorted(compiled_targets, key=lambda item: (item.queue, item.job_type))
                ),
            )
        )

    if len(reachable_queues) > MAX_CONTINUATION_POLICY_QUEUES:
        raise TaskqConfigError(
            f"continuation policy reaches more than {MAX_CONTINUATION_POLICY_QUEUES} queues"
        )

    manifest = ContinuationPolicyManifest(
        roots=tuple(
            ContinuationPolicyNode(job_type=task.name, queue=task.queue)
            for task in sorted(resolved_roots, key=lambda task: (task.queue, task.name))
        ),
        parents=tuple(sorted(parents, key=lambda item: (item.queue, item.job_type))),
    )
    canonical_bytes = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return CompiledContinuationPolicy(
        manifest=manifest,
        canonical_bytes=canonical_bytes,
        continuation_policy_hash=sha256(canonical_bytes).hexdigest(),
        reachable_queues=tuple(sorted(reachable_queues)),
    )


def validate_compiled_continuation_policy(
    registry: TaskRegistry,
    policy: CompiledContinuationPolicy,
) -> None:
    """Validate one retained policy against a compatible live registry.

    The live registry may add member edges or change its current continuation
    revision.  A retained policy remains authoritative for its old edge set, so
    compatibility requires every pinned node and edge to remain runnable rather
    than requiring the live registry to compile to the same hash.
    """

    canonical_bytes = json.dumps(
        policy.manifest.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if policy.canonical_bytes != canonical_bytes:
        raise TaskqConfigError("continuation policy canonical bytes do not match its manifest")
    if (
        _POLICY_HASH.fullmatch(policy.continuation_policy_hash) is None
        or sha256(canonical_bytes).hexdigest() != policy.continuation_policy_hash
    ):
        raise TaskqConfigError("continuation policy hash does not match its manifest")

    roots = tuple((item.queue, item.job_type) for item in policy.manifest.roots)
    parents = tuple((item.queue, item.job_type) for item in policy.manifest.parents)
    if roots != tuple(sorted(roots)) or len(set(roots)) != len(roots):
        raise TaskqConfigError("continuation policy roots are not canonical and distinct")
    if parents != tuple(sorted(parents)) or len(set(parents)) != len(parents):
        raise TaskqConfigError("continuation policy parents are not canonical and distinct")

    reachable_queues = {queue for queue, _ in roots}
    for queue, job_type in roots:
        _require_policy_node(registry, queue, job_type)
    for parent_policy in policy.manifest.parents:
        parent = _require_policy_node(
            registry,
            parent_policy.queue,
            parent_policy.job_type,
        )
        targets = tuple((item.queue, item.job_type) for item in parent_policy.targets)
        if targets != tuple(sorted(targets)) or len(set(targets)) != len(targets):
            raise TaskqConfigError(
                f"continuation policy targets are not canonical for parent: "
                f"{parent_policy.job_type!r}"
            )
        live_member_edges: set[tuple[str, str]] = set()
        for declared in parent.followup_targets:
            child = registry.require(declared.job_type)
            if declared.workflow_member:
                live_member_edges.add((declared.queue, child.name))
        for queue, job_type in targets:
            child = _require_policy_node(registry, queue, job_type)
            identity = (queue, child.name)
            if identity not in live_member_edges:
                raise TaskqConfigError(
                    f"retained continuation edge is not runnable for parent: "
                    f"{parent.name!r} -> {identity!r}"
                )
            reachable_queues.add(queue)

    if policy.reachable_queues != tuple(sorted(reachable_queues)):
        raise TaskqConfigError("continuation policy reachable queues do not match its manifest")


def enforce_continuation_policy(
    parent: Task[Any, Any],
    followups: Iterable[Followup],
    policy: CompiledContinuationPolicy | None,
) -> tuple[Followup, ...]:
    """Apply the claimed workflow's immutable member-edge policy."""

    normalized = tuple(followups)
    member_followups = tuple(item for item in normalized if item.workflow_member is True)
    if not member_followups:
        return normalized
    if policy is None:
        raise InvalidFollowupError("workflow member followups require a continuation policy")

    parent_policy = next(
        (
            item
            for item in policy.manifest.parents
            if item.queue == parent.queue and item.job_type == parent.name
        ),
        None,
    )
    allowed = (
        set()
        if parent_policy is None
        else {(item.queue, item.job_type) for item in parent_policy.targets}
    )
    for followup in member_followups:
        queue = followup.queue or parent.queue
        identity = (queue, followup.job_type)
        if identity not in allowed:
            raise InvalidFollowupError(
                f"followup target is absent from the claimed continuation policy: {identity!r}"
            )
    return normalized


def _require_policy_node(
    registry: TaskRegistry,
    queue: str,
    job_type: str,
) -> Task[Any, Any]:
    task = registry.resolve(job_type)
    if task is None or task.name != job_type or task.queue != queue:
        raise TaskqConfigError(
            f"continuation policy node is not runnable in the worker registry: "
            f"{(queue, job_type)!r}"
        )
    return task


def _validated_parent_uuid(value: UUID) -> str:
    if not isinstance(value, UUID) or value.int == 0:
        raise TaskqConfigError("parent_job_id must be a non-nil UUID")
    return str(value).lower()


def _validated_local_step(value: str) -> str:
    if not isinstance(value, str) or _LOCAL_STEP.fullmatch(value) is None:
        raise TaskqConfigError(
            "local_step must match [A-Za-z0-9][A-Za-z0-9._-]* and be at most 64 ASCII bytes"
        )
    return value


__all__ = [
    "CONTINUATION_POLICY_FORMAT",
    "CONTINUATION_PROTOCOL_DOCUMENT_REVISION",
    "DEFAULT_WORKFLOW_MEMBER_LIMIT",
    "MAX_CONTINUATION_POLICY_HASHES",
    "MAX_CONTINUATION_POLICY_QUEUES",
    "MAX_WORKFLOW_MEMBER_LIMIT",
    "RESERVED_CONTINUATION_IDEMPOTENCY_PREFIX",
    "CompiledContinuationPolicy",
    "ContinuationClaimWireOptions",
    "ContinuationCompleteWireOptions",
    "ContinuationPolicyManifest",
    "ContinuationPolicyNode",
    "ContinuationPolicyParent",
    "ContinuationWorkflowWireOptions",
    "compile_continuation_policy",
    "derive_continuation_idempotency_key",
    "derive_continuation_workflow_step",
    "enforce_continuation_policy",
    "validate_producer_idempotency_key",
    "validate_compiled_continuation_policy",
]
