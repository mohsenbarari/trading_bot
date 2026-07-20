"""Closed typed adapter contract for failover operations.

This module deliberately contains no subprocess, shell, executable, URL, or
arbitrary argument surface.  A deployment backend must implement each reviewed
operation as a distinct method and return operation-bound evidence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from core.dr_event_protocol import canonical_json_bytes
from core.dr_failover_orchestrator import FailoverPlan, DrOrchestrationError
from core.secure_file_io import read_secure_text


TYPED_OPERATIONS = {
    "classification_verified": "connectivity.signed_evidence_verify.v1",
    "source_fenced": "writer.source_fence.v1",
    "target_ready": "readiness.target_verify.v1",
    "target_term_acquired": "witness.target_acquire.v1",
    "source_connections_drained": "postgres.source_drain.v1",
    "route_switched": "arvan.failover_test_origin_switch.v1",
    "public_route_verified": "tls.public_origin_verify.v1",
    "rollback": "writer.safe_rollback.v1",
}


class TypedOperationBackend(Protocol):
    async def classification_verified(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def source_fenced(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def target_ready(
        self,
        plan: FailoverPlan,
        *,
        source_tail_boundary: dict[str, Any],
    ) -> dict[str, Any]: ...
    async def target_term_acquired(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def source_connections_drained(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def route_switched(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def public_route_verified(self, plan: FailoverPlan) -> dict[str, Any]: ...
    async def rollback(
        self,
        plan: FailoverPlan,
        *,
        failed_step: str,
        completed_steps: tuple[str, ...],
    ) -> dict[str, Any]: ...


def load_typed_operation_manifest(path: Path, *, plan: FailoverPlan) -> dict[str, str]:
    try:
        payload = json.loads(
            read_secure_text(path, label="typed orchestration manifest", max_size=32 * 1024)
        )
    except Exception as exc:
        raise DrOrchestrationError("typed orchestration manifest is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema", "operation_id", "operations"}:
        raise DrOrchestrationError("typed orchestration manifest fields are invalid")
    if (
        payload["schema"] != "three-site-typed-operation-adapter-v1"
        or payload["operation_id"] != plan.operation_id
        or payload["operations"] != TYPED_OPERATIONS
    ):
        raise DrOrchestrationError("typed orchestration manifest is not the closed reviewed contract")
    if hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != plan.command_manifest_hash:
        raise DrOrchestrationError("typed orchestration manifest hash differs from the approved plan")
    return dict(TYPED_OPERATIONS)


# Transitional import name retained only to keep callers explicit while the
# generic command implementation is removed.  It accepts no command argv.
load_command_manifest = load_typed_operation_manifest


class TypedOrchestrationAdapter:
    def __init__(self, operations: dict[str, str], *, backend: TypedOperationBackend) -> None:
        if operations != TYPED_OPERATIONS:
            raise DrOrchestrationError("typed orchestration operations are incomplete")
        self.backend = backend

    async def classification_verified(self, plan: FailoverPlan) -> dict[str, Any]:
        result = await self.backend.classification_verified(plan)
        if any(
            result.get(key) != plan.classification[key]
            for key in (
                "mode", "confidence", "consecutive_rounds", "evidence_hash",
                "campaign_id", "policy_hash",
            )
        ):
            raise DrOrchestrationError(
                "fresh signed connectivity evidence differs from the approved plan"
            )
        return result

    async def source_fenced(self, plan: FailoverPlan) -> dict[str, Any]:
        return await self.backend.source_fenced(plan)

    async def target_ready(
        self,
        plan: FailoverPlan,
        *,
        source_tail_boundary: dict[str, Any],
    ) -> dict[str, Any]:
        result = await self.backend.target_ready(
            plan,
            source_tail_boundary=source_tail_boundary,
        )
        if result.get("readiness_hash") != plan.readiness_hash:
            raise DrOrchestrationError("target readiness hash differs from the approved plan")
        return result

    async def target_term_acquired(self, plan: FailoverPlan) -> dict[str, Any]:
        return await self.backend.target_term_acquired(plan)

    async def source_connections_drained(self, plan: FailoverPlan) -> dict[str, Any]:
        return await self.backend.source_connections_drained(plan)

    async def route_switched(self, plan: FailoverPlan) -> dict[str, Any]:
        return await self.backend.route_switched(plan)

    async def public_route_verified(self, plan: FailoverPlan) -> dict[str, Any]:
        return await self.backend.public_route_verified(plan)

    async def rollback(
        self,
        plan: FailoverPlan,
        *,
        failed_step: str,
        completed_steps: tuple[str, ...],
    ) -> dict[str, Any]:
        return await self.backend.rollback(
            plan,
            failed_step=failed_step,
            completed_steps=completed_steps,
        )
