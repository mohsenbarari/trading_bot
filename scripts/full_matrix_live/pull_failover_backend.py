"""Typed JIT failover backend with a pull-only WA-IR operation boundary.

The legacy staging backend is retained for non-Matrix callers.  This adapter
changes only Full Matrix's WA-IR execution path: all local Iran mutations are
sent to the signed Object-Storage pull agent, while FI-local, Witness and
Arvan operations remain on their reviewed local routes.  It deliberately has
no SSH fallback for WA-IR.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Any

from core.dr_command_orchestration_adapter import TYPED_OPERATIONS
from core.dr_event_protocol import canonical_json_bytes
from core.dr_failover_orchestrator import FailoverPlan, parse_plan
from core.dr_staging_operation_backend import (
    StagingBackendConfig,
    StagingOperationBackendError,
    StagingTypedOperationBackend,
)


PullOperation = Callable[
    [
        FailoverPlan,
        dict[str, Any],
        str,
        dict[str, Any] | None,
        dict[str, Any] | None,
        str | None,
    ],
    dict[str, Any],
]

_SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
}


class PullFailoverBackend(StagingTypedOperationBackend):
    """Use Object Storage for every WA-IR site mutation in one JIT saga."""

    def __init__(
        self,
        config: StagingBackendConfig,
        *,
        plan_document: dict[str, Any],
        pull_operation: PullOperation,
    ) -> None:
        super().__init__(config)
        self._plan_document = dict(plan_document)
        self._pull_operation = pull_operation

    @staticmethod
    def _write_private(path: Path, payload: dict[str, Any]) -> None:
        raw = canonical_json_bytes(payload) + b"\n"
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600,
        )
        try:
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written <= 0:
                    raise StagingOperationBackendError("JIT failover file write was incomplete")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _assert_private_regular(path: Path) -> None:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise StagingOperationBackendError("JIT failover local artifact is unsafe")

    def _copy_to_webapp_fi(self, source: Path, *, destination: str) -> None:
        """Copy only one approved JIT artifact to Finland over pinned SSH.

        WA-IR is deliberately absent from this path.  Its plan is supplied
        only in the signed encrypted Object-Storage pull envelope.
        """

        host = self.config.hosts["webapp_fi"]
        self._assert_private_regular(source)
        result = subprocess.run(
            [
                "/usr/bin/scp",
                "-q",
                "-p",
                "-P",
                str(host.ssh_port),
                "-i",
                str(host.ssh_identity_file),
                "-o",
                "BatchMode=yes",
                "-o",
                f"UserKnownHostsFile={host.ssh_known_hosts_file}",
                "-o",
                "StrictHostKeyChecking=yes",
                "--",
                str(source),
                f"{host.ssh_user}@{host.host_ip}:{destination}",
            ],
            env=_SAFE_ENV,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=90,
        )
        if (
            result.returncode != 0
            or len(result.stdout) > 64 * 1024
            or len(result.stderr) > 64 * 1024
        ):
            raise StagingOperationBackendError("JIT failover artifact transfer to WA-FI failed")

    def materialize_webapp_fi_inputs(self, plan: FailoverPlan) -> None:
        """Stage the exact final plan/manifest only on the direct FI route."""

        try:
            staged_plan = parse_plan(self._plan_document)
        except Exception as exc:
            raise StagingOperationBackendError("JIT failover document is invalid") from exc
        if (
            staged_plan.operation_id != plan.operation_id
            or staged_plan.plan_hash != plan.plan_hash
        ):
            raise StagingOperationBackendError("JIT failover document differs from parsed plan")
        manifest = {
            "schema": "three-site-typed-operation-adapter-v1",
            "operation_id": plan.operation_id,
            "operations": dict(TYPED_OPERATIONS),
        }
        with tempfile.TemporaryDirectory(prefix="full-matrix-failover-fi-") as raw:
            root = Path(raw)
            os.chmod(root, 0o700)
            plan_path = root / "plan.json"
            manifest_path = root / "typed-operation-manifest.json"
            self._write_private(plan_path, self._plan_document)
            self._write_private(manifest_path, manifest)
            host = self.config.hosts["webapp_fi"]
            self._copy_to_webapp_fi(plan_path, destination=host.plan_path)
            self._copy_to_webapp_fi(
                manifest_path,
                destination=host.command_manifest_path,
            )

    def _pull(
        self,
        plan: FailoverPlan,
        *,
        action: str,
        source_tail_boundary: dict[str, Any] | None = None,
        readiness_evidence: dict[str, Any] | None = None,
        previous_proof_hash: str | None = None,
    ) -> dict[str, Any]:
        result = self._pull_operation(
            plan,
            self._plan_document,
            action,
            source_tail_boundary,
            readiness_evidence,
            previous_proof_hash,
        )
        if (
            not isinstance(result, dict)
            or result.get("status") != "ok"
            or result.get("operation_id") != plan.operation_id
        ):
            raise StagingOperationBackendError(
                "WA-IR pull operation did not attest its approved plan"
            )
        return result

    def _site_agent(
        self,
        plan: FailoverPlan,
        *,
        role: str,
        action: str,
        source_tail: dict[str, Any] | None = None,
        previous_proof_hash: str | None = None,
    ) -> dict[str, Any]:
        if role != "webapp_ir":
            return super()._site_agent(
                plan,
                role=role,
                action=action,
                source_tail=source_tail,
                previous_proof_hash=previous_proof_hash,
            )
        boundary = (
            source_tail.get("source_tail_boundary")
            if isinstance(source_tail, dict)
            else None
        )
        return self._pull(
            plan,
            action=action,
            source_tail_boundary=boundary,
            previous_proof_hash=previous_proof_hash,
        )

    def _inspect_witness(self, plan: FailoverPlan, *, role: str) -> dict[str, Any]:
        # The controller's FI identity is sufficient for the authenticated
        # Witness read.  Do not open a controller-to-Iran connection merely to
        # inspect a Witness lease during rollback.
        return super()._inspect_witness(
            plan,
            role="webapp_fi" if role == "webapp_ir" else role,
        )

    async def source_fenced(self, plan: FailoverPlan) -> dict[str, Any]:
        self.validate_plan_scope(plan)
        if plan.source_site != "webapp_ir":
            return await super().source_fenced(plan)
        return await asyncio.to_thread(
            self._pull,
            plan,
            action="source-drained-and-fenced",
        )

    async def target_term_acquired_with_readiness(
        self,
        plan: FailoverPlan,
        *,
        target_readiness: dict[str, Any] | None,
    ) -> dict[str, Any]:
        self.validate_plan_scope(plan)
        if plan.target_site != "webapp_ir":
            return await super().target_term_acquired(plan)
        if not isinstance(target_readiness, dict):
            raise StagingOperationBackendError(
                "WA-IR pull acquisition requires retained target-readiness evidence"
            )
        return await asyncio.to_thread(
            self._pull,
            plan,
            action="target-term-acquired",
            readiness_evidence=target_readiness,
        )

    async def target_term_acquired(self, plan: FailoverPlan) -> dict[str, Any]:
        if plan.target_site == "webapp_ir":
            raise StagingOperationBackendError(
                "WA-IR pull acquisition requires journaled target-readiness evidence"
            )
        return await super().target_term_acquired(plan)
