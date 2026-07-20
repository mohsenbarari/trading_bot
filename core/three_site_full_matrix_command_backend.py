"""Closed, hash-pinned execution backend for the three-site staging Matrix.

The signed campaign binds the backend configuration.  That configuration in
turn binds one tracked driver by path and SHA-256 and declares the complete
catalog.  The backend never invokes a shell and supplies the immutable
campaign/scenario identity as fixed argv fields.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

from core.three_site_full_matrix_campaign import PHASES, PHASE_SCENARIOS
from core.three_site_full_matrix_runner import CampaignIdentity, FullMatrixRunnerError


CONFIG_SCHEMA = "three-site-staging-full-matrix-command-backend-v1"
PYTHON = "/usr/bin/python3"
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
}


class CommandFullMatrixBackend:
    """Execute the complete closed catalog through one reviewed driver."""

    def __init__(
        self,
        *,
        config: dict[str, Any],
        repo_root: Path,
        artifact_root: Path,
        campaign_id: str,
        release_sha: str,
    ) -> None:
        fields = {
            "schema", "campaign_id", "release_sha", "production_forbidden",
            "driver", "supported_scenarios", "timeouts_seconds",
        }
        if (
            not isinstance(config, dict)
            or set(config) != fields
            or config.get("schema") != CONFIG_SCHEMA
            or config.get("campaign_id") != campaign_id
            or config.get("release_sha") != release_sha
            or config.get("production_forbidden") is not True
            or config.get("supported_scenarios")
            != {phase: list(PHASE_SCENARIOS[phase]) for phase in PHASES}
        ):
            raise FullMatrixRunnerError("Full Matrix command backend config is invalid")
        driver = config.get("driver")
        if not isinstance(driver, dict) or set(driver) != {"path", "sha256"}:
            raise FullMatrixRunnerError("Full Matrix command backend driver is invalid")
        relative = Path(str(driver["path"]))
        approved_parent = (repo_root / "scripts" / "full_matrix_drivers").resolve()
        resolved = (repo_root / relative).resolve()
        if (
            relative.is_absolute()
            or resolved.parent != approved_parent
            or not resolved.is_file()
            or resolved.is_symlink()
        ):
            raise FullMatrixRunnerError("Full Matrix driver path is outside the closed directory")
        metadata = resolved.stat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise FullMatrixRunnerError("Full Matrix driver ownership/mode is unsafe")
        try:
            source_fd = os.open(
                resolved,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as exc:
            raise FullMatrixRunnerError("Full Matrix driver could not be opened safely") from exc
        try:
            opened = os.fstat(source_fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != metadata.st_dev
                or opened.st_ino != metadata.st_ino
                or opened.st_size != metadata.st_size
            ):
                raise FullMatrixRunnerError("Full Matrix driver changed while being opened")
            driver_bytes = os.pread(source_fd, opened.st_size + 1, 0)
        finally:
            os.close(source_fd)
        if len(driver_bytes) != metadata.st_size:
            raise FullMatrixRunnerError("Full Matrix driver read is incomplete")
        digest = hashlib.sha256(driver_bytes).hexdigest()
        if digest != driver["sha256"]:
            raise FullMatrixRunnerError("Full Matrix driver hash differs from signed config")
        try:
            immutable_fd = os.memfd_create(
                "three-site-full-matrix-driver",
                os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
            )
            written = os.write(immutable_fd, driver_bytes)
            if written != len(driver_bytes):
                raise OSError("short write")
            fcntl.fcntl(
                immutable_fd,
                fcntl.F_ADD_SEALS,
                fcntl.F_SEAL_SEAL
                | fcntl.F_SEAL_SHRINK
                | fcntl.F_SEAL_GROW
                | fcntl.F_SEAL_WRITE,
            )
        except (AttributeError, OSError) as exc:
            if "immutable_fd" in locals():
                os.close(immutable_fd)
            raise FullMatrixRunnerError(
                "Full Matrix driver could not be pinned into sealed memory"
            ) from exc
        timeouts = config.get("timeouts_seconds")
        if not isinstance(timeouts, dict) or set(timeouts) != {
            "preflight", "recovery", "scenario", "endurance", "cleanup", "finalize"
        }:
            raise FullMatrixRunnerError("Full Matrix command backend timeouts are invalid")
        for name, value in timeouts.items():
            minimum = 86400 if name == "endurance" else 1
            maximum = 90000 if name == "endurance" else 7200
            if type(value) is not int or not minimum <= value <= maximum:
                raise FullMatrixRunnerError("Full Matrix command backend timeout is unsafe")
        self.driver = resolved
        self._driver_fd = immutable_fd
        self.driver_sha256 = digest
        self.repo_root = repo_root.resolve()
        self.artifact_root = artifact_root.resolve()
        self.timeouts = dict(timeouts)

    async def _invoke(
        self,
        identity: CampaignIdentity,
        *,
        operation: str,
        operation_id: str,
        phase: str | None = None,
        scenario_id: str | None = None,
        iteration: int | None = None,
        attempt: int | None = None,
        failed: bool | None = None,
    ) -> dict[str, Any]:
        timeout_name = operation
        if operation == "scenario":
            timeout_name = (
                "endurance"
                if scenario_id == "twenty_four_hour_endurance_no_growth"
                else "scenario"
            )
        command = [
            PYTHON, "-I", "-B", f"/proc/self/fd/{self._driver_fd}",
            "--operation", operation,
            "--operation-id", operation_id,
            "--campaign-id", identity.campaign_id,
            "--campaign-hash", identity.campaign_hash,
            "--release-sha", identity.release_sha,
            "--activation-sha", identity.activation_sha,
            "--artifact-root", str(self.artifact_root),
        ]
        if phase is not None:
            command.extend(["--phase", phase])
        if scenario_id is not None:
            command.extend(["--scenario-id", scenario_id])
        if iteration is not None:
            command.extend(["--iteration", str(iteration)])
        if attempt is not None:
            command.extend(["--attempt", str(attempt)])
        if failed is not None:
            command.extend(["--failed", "true" if failed else "false"])

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=self.repo_root,
                env=SAFE_ENV,
                pass_fds=(self._driver_fd,),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeouts[timeout_name]
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise
        except (OSError, asyncio.TimeoutError) as exc:
            raise FullMatrixRunnerError(
                f"Full Matrix {operation} driver failed closed"
            ) from exc
        if process.returncode != 0 or len(stdout) > 1024 * 1024:
            raise FullMatrixRunnerError(
                f"Full Matrix {operation} driver returned a failure"
            )
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FullMatrixRunnerError(
                f"Full Matrix {operation} driver output is invalid"
            ) from exc
        if not isinstance(payload, dict):
            raise FullMatrixRunnerError(f"Full Matrix {operation} driver output is not an object")
        return payload

    def close(self) -> None:
        descriptor = getattr(self, "_driver_fd", -1)
        if descriptor >= 0:
            os.close(descriptor)
            self._driver_fd = -1

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass

    async def preflight(
        self, identity: CampaignIdentity, *, operation_id: str
    ) -> dict[str, Any]:
        return await self._invoke(
            identity, operation="preflight", operation_id=operation_id
        )

    async def recover_interrupted(
        self,
        identity: CampaignIdentity,
        *,
        phase: str,
        scenario_id: str,
        iteration: int,
        attempt: int,
        operation_id: str,
    ) -> dict[str, Any]:
        return await self._invoke(
            identity, operation="recovery", operation_id=operation_id, phase=phase,
            scenario_id=scenario_id, iteration=iteration, attempt=attempt,
        )

    async def execute_scenario(
        self,
        identity: CampaignIdentity,
        *,
        phase: str,
        scenario_id: str,
        iteration: int,
        attempt: int,
        operation_id: str,
    ) -> dict[str, Any]:
        if phase not in PHASE_SCENARIOS or scenario_id not in PHASE_SCENARIOS[phase]:
            raise FullMatrixRunnerError("Full Matrix requested an unknown scenario")
        return await self._invoke(
            identity, operation="scenario", operation_id=operation_id, phase=phase,
            scenario_id=scenario_id, iteration=iteration, attempt=attempt,
        )

    async def cleanup_phase(
        self,
        identity: CampaignIdentity,
        *,
        phase: str,
        iteration: int,
        failed: bool,
        operation_id: str,
    ) -> dict[str, Any]:
        return await self._invoke(
            identity, operation="cleanup", operation_id=operation_id, phase=phase,
            iteration=iteration, failed=failed,
        )

    async def finalize(
        self, identity: CampaignIdentity, *, operation_id: str
    ) -> dict[str, Any]:
        return await self._invoke(
            identity, operation="finalize", operation_id=operation_id
        )
