#!/usr/bin/env python3
"""Build both sealed-driver runtime and command-backend configs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import read_secure_bytes, write_secure_atomic_bytes  # noqa: E402
from core.three_site_execution_safety import (  # noqa: E402
    EXECUTION_CLASSES,
    SHARED_HOST_SAFE,
)
from core.three_site_full_matrix_campaign import scenarios_for_execution_class  # noqa: E402
from core.three_site_full_matrix_command_backend import (  # noqa: E402
    CONFIG_SCHEMA,
    RUNTIME_CONFIG_SCHEMA,
)


class FullMatrixBackendBuildError(RuntimeError):
    pass


TIMEOUTS = {
    "preflight": 1800,
    "recovery": 1800,
    "scenario": 7200,
    "endurance": 90000,
    "cleanup": 1800,
    "finalize": 1800,
}


def _binding(path: Path, *, relative: bool) -> dict[str, str]:
    if path.is_symlink():
        raise FullMatrixBackendBuildError("backend source binding is unsafe")
    resolved = path.resolve()
    if not resolved.is_file():
        raise FullMatrixBackendBuildError("backend source binding is unsafe")
    if relative:
        metadata = resolved.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or metadata.st_nlink != 1
            or not 1 <= metadata.st_size <= 4 * 1024 * 1024
        ):
            raise FullMatrixBackendBuildError("tracked backend source is unsafe")
        raw = resolved.read_bytes()
    else:
        try:
            raw = read_secure_bytes(
                resolved,
                label="Full Matrix backend runtime plan",
                max_size=16 * 1024 * 1024,
            )
        except Exception as exc:
            raise FullMatrixBackendBuildError(
                "backend runtime plan is unsafe"
            ) from exc
    value = str(resolved.relative_to(REPO_ROOT)) if relative else str(resolved)
    return {"path": value, "sha256": hashlib.sha256(raw).hexdigest()}


def _identity(
    *,
    campaign_id: str,
    gate_group_id: str,
    execution_class: str,
    release_sha: str,
) -> tuple[str, str, dict[str, list[str]]]:
    try:
        campaign = str(UUID(campaign_id))
        group = str(UUID(gate_group_id))
    except ValueError as exc:
        raise FullMatrixBackendBuildError("backend UUID identity is invalid") from exc
    if execution_class not in EXECUTION_CLASSES:
        raise FullMatrixBackendBuildError("backend execution class is invalid")
    if len(release_sha) != 40 or any(char not in "0123456789abcdef" for char in release_sha):
        raise FullMatrixBackendBuildError("backend release SHA is invalid")
    catalog = {
        phase: list(scenarios)
        for phase, scenarios in scenarios_for_execution_class(execution_class).items()
    }
    return campaign, group, catalog


def build_documents(
    *,
    campaign_id: str,
    gate_group_id: str,
    execution_class: str,
    release_sha: str,
    live_plan: Path,
    runtime_output: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    campaign, group, catalog = _identity(
        campaign_id=campaign_id,
        gate_group_id=gate_group_id,
        execution_class=execution_class,
        release_sha=release_sha,
    )
    live_driver_config = {
        "schema": "three-site-staging-full-matrix-live-driver-v1",
        "runner": _binding(
            REPO_ROOT / "scripts/full_matrix_live/runner.py",
            relative=True,
        ),
        "oracle": _binding(
            REPO_ROOT / "scripts/full_matrix_live/oracle.py",
            relative=True,
        ),
        "runtime_plan": _binding(live_plan, relative=False),
        "timeouts_seconds": dict(TIMEOUTS),
    }
    runtime = {
        "schema": RUNTIME_CONFIG_SCHEMA,
        "campaign_id": campaign,
        "gate_group_id": group,
        "execution_class": execution_class,
        "release_sha": release_sha,
        "production_forbidden": True,
        "host_mutation_policy": (
            "forbidden"
            if execution_class == SHARED_HOST_SAFE
            else "dedicated-staging-only"
        ),
        "supported_scenarios": catalog,
        "driver_config": live_driver_config,
    }
    runtime_raw = (
        json.dumps(runtime, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    backend = {
        "schema": CONFIG_SCHEMA,
        "campaign_id": campaign,
        "gate_group_id": group,
        "execution_class": execution_class,
        "release_sha": release_sha,
        "production_forbidden": True,
        "driver": _binding(
            REPO_ROOT / "scripts/full_matrix_drivers/driver.py",
            relative=True,
        ),
        "runtime_config": {
            "path": str(runtime_output.resolve()),
            "sha256": hashlib.sha256(runtime_raw).hexdigest(),
        },
        "supported_scenarios": catalog,
        "timeouts_seconds": dict(TIMEOUTS),
    }
    return runtime, backend


def _raw(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--gate-group-id", required=True)
    parser.add_argument("--execution-class", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--live-plan", required=True, type=Path)
    parser.add_argument("--runtime-output", required=True, type=Path)
    parser.add_argument("--backend-output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        runtime, backend = build_documents(
            campaign_id=args.campaign_id,
            gate_group_id=args.gate_group_id,
            execution_class=args.execution_class,
            release_sha=args.release_sha,
            live_plan=args.live_plan,
            runtime_output=args.runtime_output,
        )
        runtime_raw = _raw(runtime)
        backend_raw = _raw(backend)
        write_secure_atomic_bytes(
            args.runtime_output,
            runtime_raw,
            label="Full Matrix sealed-driver runtime",
            mode=0o600,
            max_size=16 * 1024 * 1024,
        )
        write_secure_atomic_bytes(
            args.backend_output,
            backend_raw,
            label="Full Matrix command backend config",
            mode=0o600,
            max_size=16 * 1024 * 1024,
        )
        print(
            json.dumps(
                {
                    "status": "built",
                    "runtime_output": str(args.runtime_output),
                    "runtime_sha256": hashlib.sha256(runtime_raw).hexdigest(),
                    "backend_output": str(args.backend_output),
                    "backend_sha256": hashlib.sha256(backend_raw).hexdigest(),
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
