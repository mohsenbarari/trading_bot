"""Root-manifest, no-shell adapter for the failover orchestration saga."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from core.dr_event_protocol import canonical_json_bytes
from core.dr_failover_orchestrator import FailoverPlan, STEPS, DrOrchestrationError
from core.secure_file_io import read_secure_text


COMMAND_STEPS = (*STEPS[1:-1], "rollback")
ALLOWED_EXECUTABLES = frozenset(
    {
        "/usr/bin/curl",
        "/usr/bin/docker",
        "/usr/bin/python3",
        "/usr/bin/ssh",
        "/usr/local/bin/docker",
        "/usr/local/bin/python3",
    }
)


def load_command_manifest(path: Path, *, plan: FailoverPlan) -> dict[str, tuple[str, ...]]:
    try:
        payload = json.loads(
            read_secure_text(path, label="orchestration command manifest", max_size=128 * 1024)
        )
    except Exception as exc:
        raise DrOrchestrationError("orchestration command manifest is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema", "operation_id", "commands"
    }:
        raise DrOrchestrationError("orchestration command manifest fields are invalid")
    if (
        payload["schema"] != "three-site-command-adapter-v1"
        or payload["operation_id"] != plan.operation_id
    ):
        raise DrOrchestrationError("orchestration command manifest is not bound to this plan")
    manifest_hash = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    if manifest_hash != plan.command_manifest_hash:
        raise DrOrchestrationError("orchestration command manifest hash differs from the approved plan")
    commands = payload["commands"]
    if not isinstance(commands, dict) or set(commands) != set(COMMAND_STEPS):
        raise DrOrchestrationError("orchestration command manifest step set is incomplete")
    result: dict[str, tuple[str, ...]] = {}
    for step, raw_argv in commands.items():
        if (
            not isinstance(raw_argv, list)
            or not 1 <= len(raw_argv) <= 64
            or any(not isinstance(value, str) or not value or "\x00" in value or "\n" in value for value in raw_argv)
            or raw_argv[0] not in ALLOWED_EXECUTABLES
        ):
            raise DrOrchestrationError(f"orchestration command for {step} is unsafe")
        result[step] = tuple(raw_argv)
    return result


class CommandOrchestrationAdapter:
    """Execute two-person-approved staging steps without a shell."""

    def __init__(self, commands: dict[str, tuple[str, ...]], *, timeout_seconds: int = 120) -> None:
        self.commands = commands
        self.timeout_seconds = max(5, min(600, int(timeout_seconds)))

    async def classification_verified(self, plan: FailoverPlan) -> dict[str, Any]:
        return {
            "status": "ok",
            "operation_id": plan.operation_id,
            "evidence_hash": hashlib.sha256(canonical_json_bytes(plan.classification)).hexdigest(),
        }

    async def _run(self, step: str, plan: FailoverPlan) -> dict[str, Any]:
        argv = self.commands[step]

        def invoke():  # noqa: ANN202
            return subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
            )

        completed = await asyncio.to_thread(invoke)
        if completed.returncode != 0:
            raise DrOrchestrationError(
                f"orchestration command {step} failed with exit {completed.returncode}"
            )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if not lines:
            raise DrOrchestrationError(f"orchestration command {step} returned no JSON evidence")
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise DrOrchestrationError(
                f"orchestration command {step} returned invalid JSON evidence"
            ) from exc
        if not isinstance(payload, dict):
            raise DrOrchestrationError(f"orchestration command {step} evidence is not an object")
        if payload.get("operation_id") != plan.operation_id:
            raise DrOrchestrationError(f"orchestration command {step} evidence has wrong operation")
        return payload

    async def source_fenced(self, plan: FailoverPlan) -> dict[str, Any]:
        return await self._run("source_fenced", plan)

    async def target_ready(self, plan: FailoverPlan) -> dict[str, Any]:
        result = await self._run("target_ready", plan)
        if result.get("readiness_hash") != plan.readiness_hash:
            raise DrOrchestrationError("target readiness hash differs from the approved plan")
        return result

    async def target_term_acquired(self, plan: FailoverPlan) -> dict[str, Any]:
        return await self._run("target_term_acquired", plan)

    async def source_connections_drained(self, plan: FailoverPlan) -> dict[str, Any]:
        return await self._run("source_connections_drained", plan)

    async def route_switched(self, plan: FailoverPlan) -> dict[str, Any]:
        return await self._run("route_switched", plan)

    async def public_route_verified(self, plan: FailoverPlan) -> dict[str, Any]:
        return await self._run("public_route_verified", plan)

    async def rollback(
        self,
        plan: FailoverPlan,
        *,
        failed_step: str,
        completed_steps: tuple[str, ...],
    ) -> dict[str, Any]:
        # Dynamic failure state is discovered by the approved rollback argv and
        # is accepted only through the operation-bound evidence contract.
        del failed_step, completed_steps
        return await self._run("rollback", plan)
