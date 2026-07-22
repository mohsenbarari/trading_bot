"""Fail-closed runtime capability shared by Matrix preflight and executor."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys

from scripts import hold_writer_witness_package_locks as package_locks
from scripts import verify_writer_witness_controller_toolchain as controller_toolchain


TRUSTED_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
CONTROLLER_EXECUTABLES = {
    name: str(path) for name, path in controller_toolchain.CONTROLLER_TOOL_PATHS.items()
}
ALLOWED_ENVIRONMENT = frozenset(
    {
        "HOME",
        "INVOCATION_ID",
        "LANG",
        "LC_ALL",
        "LOGNAME",
        "PATH",
        "SYSTEMD_EXEC_PID",
        "USER",
        "WRITER_WITNESS_CONTROLLER_TRANSACTION_UNIT",
        "WRITER_WITNESS_PACKAGE_LOCK_OWNER_PID",
        "WRITER_WITNESS_REAL_HOST_MATRIX_CONFIRM",
        "WRITER_WITNESS_REAL_HOST_MATRIX_APPROVAL_REQUEST_CONFIRM",
        "WRITER_WITNESS_REAL_HOST_MATRIX_SCENARIO",
    }
)


class ControllerRuntimeError(RuntimeError):
    """The Matrix controller lacks its exact runtime capability."""


def clean_environment() -> dict[str, str]:
    environment = {
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LOGNAME": "root",
        "PATH": TRUSTED_PATH,
        "USER": "root",
    }
    for name in sorted(ALLOWED_ENVIRONMENT):
        if name in os.environ and name not in environment:
            environment[name] = os.environ[name]
    return environment


def executable(name: str) -> str:
    try:
        return CONTROLLER_EXECUTABLES[name]
    except KeyError as exc:
        raise ControllerRuntimeError(
            f"undeclared controller executable requested: {name}"
        ) from exc


def assert_command(arguments: list[str] | tuple[str, ...]) -> None:
    if not arguments or arguments[0] not in set(CONTROLLER_EXECUTABLES.values()):
        raise ControllerRuntimeError(
            "controller command does not use an absolute inventoried executable"
        )


def assert_runtime(expected_inventory_sha256: str) -> str:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise ControllerRuntimeError("Matrix controller must run as root")
    if (
        sys.executable != "/usr/bin/python3.12"
        or not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.flags.dont_write_bytecode
        or not sys.flags.utf8_mode
    ):
        raise ControllerRuntimeError(
            "Matrix controller requires exact isolated /usr/bin/python3.12"
        )
    unexpected = sorted(set(os.environ) - ALLOWED_ENVIRONMENT)
    if unexpected:
        raise ControllerRuntimeError(
            "Matrix controller inherited an unapproved environment: "
            + ",".join(unexpected)
        )
    if os.environ.get("PATH") != TRUSTED_PATH:
        raise ControllerRuntimeError("Matrix controller PATH is not its fixed clean value")
    unit = os.environ.get("WRITER_WITNESS_CONTROLLER_TRANSACTION_UNIT", "")
    owner_raw = os.environ.get("WRITER_WITNESS_PACKAGE_LOCK_OWNER_PID", "")
    if (
        re.fullmatch(r"writer-witness-matrix-controller-[0-9a-f]{20}\.service", unit)
        is None
        or not owner_raw.isdigit()
    ):
        raise ControllerRuntimeError("Matrix controller transaction identity is absent")
    owner_pid = int(owner_raw)
    if owner_pid not in {os.getpid(), os.getppid()}:
        raise ControllerRuntimeError("Matrix controller lock owner is not this process lineage")
    try:
        package_locks.assert_package_locks_owned_by(
            package_locks.PACKAGE_LOCK_PATHS,
            owner_pid=owner_pid,
        )
    except (OSError, package_locks.PackageLockError) as exc:
        raise ControllerRuntimeError(
            "Matrix controller native package-lock capability is absent"
        ) from exc
    if owner_pid == os.getpid():
        completed = subprocess.run(
            [
                "/usr/bin/systemctl",
                "show",
                "-p",
                "MainPID",
                "-p",
                "KillMode",
                "-p",
                "Type",
                unit,
            ],
            env=clean_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        properties: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            name, separator, value = line.partition("=")
            if separator and name in {"MainPID", "KillMode", "Type"}:
                properties[name] = value
        if completed.returncode != 0 or properties != {
            "MainPID": str(os.getpid()),
            "KillMode": "control-group",
            "Type": "exec",
        }:
            raise ControllerRuntimeError(
                "Matrix controller is not the exact systemd cgroup main process"
            )
    if re.fullmatch(r"[0-9a-f]{64}", expected_inventory_sha256) is None:
        raise ControllerRuntimeError("controller toolchain approval digest is invalid")
    payload = controller_toolchain.canonical_bytes(controller_toolchain.build_inventory())
    observed = hashlib.sha256(payload).hexdigest()
    if observed != expected_inventory_sha256:
        raise ControllerRuntimeError(
            "Matrix controller toolchain differs from its approved digest"
        )
    return observed
