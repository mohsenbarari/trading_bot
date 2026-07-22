#!/usr/bin/python3.12
"""Build and verify the Matrix controller's separate executable inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import verify_writer_witness_host_toolchain as host_toolchain


SCHEMA = "writer_witness_matrix_controller_toolchain_v1"
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
# These are controller-local executables.  Remote shell tools belong to the
# role on which the SSH command runs and are deliberately not claimed here.
CONTROLLER_TOOL_PATHS = {
    "age": Path("/usr/bin/age"),
    "awk": Path("/usr/bin/awk"),
    "bash": Path("/bin/bash"),
    "cp": Path("/usr/bin/cp"),
    "curl": Path("/usr/bin/curl"),
    "docker": Path("/usr/bin/docker"),
    "env": Path("/usr/bin/env"),
    "findmnt": Path("/usr/bin/findmnt"),
    "flock": Path("/usr/bin/flock"),
    "git": Path("/usr/bin/git"),
    "grep": Path("/usr/bin/grep"),
    "ln": Path("/usr/bin/ln"),
    "mv": Path("/usr/bin/mv"),
    "openssl": Path("/usr/bin/openssl"),
    "python3": Path("/usr/bin/python3"),
    "python3.12": Path("/usr/bin/python3.12"),
    "readlink": Path("/usr/bin/readlink"),
    "realpath": Path("/usr/bin/realpath"),
    "rm": Path("/usr/bin/rm"),
    "runuser": Path("/usr/sbin/runuser"),
    "scp": Path("/usr/bin/scp"),
    "sed": Path("/usr/bin/sed"),
    "sha256sum": Path("/usr/bin/sha256sum"),
    "sleep": Path("/usr/bin/sleep"),
    "ssh": Path("/usr/bin/ssh"),
    "ssh-keygen": Path("/usr/bin/ssh-keygen"),
    "stat": Path("/usr/bin/stat"),
    "systemd-run": Path("/usr/bin/systemd-run"),
    "systemctl": Path("/usr/bin/systemctl"),
}


class ControllerToolchainError(RuntimeError):
    """Controller executable closure is absent, unsafe, or drifted."""


def build_inventory() -> dict[str, object]:
    tools: list[dict[str, object]] = []
    native_paths: set[Path] = set()
    packages: set[str] = set()
    for name, requested in sorted(CONTROLLER_TOOL_PATHS.items()):
        if not requested.is_absolute():
            raise ControllerToolchainError(f"controller tool path is not absolute: {name}")
        try:
            resolved = requested.resolve(strict=True)
            payload, metadata = host_toolchain._read_executable(requested)
            package = host_toolchain._package_owner(resolved)
            native_paths.update(host_toolchain._linked_native_paths(resolved, payload))
        except (OSError, host_toolchain.ToolchainError) as exc:
            raise ControllerToolchainError(
                f"controller tool attestation failed: {name}"
            ) from exc
        packages.add(package)
        tools.append(
            {
                "gid": metadata.st_gid,
                "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
                "name": name,
                "package": package,
                "requested_path": str(requested),
                "resolved_path": str(resolved),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": metadata.st_size,
                "uid": metadata.st_uid,
            }
        )
    native_objects: list[dict[str, object]] = []
    for resolved in sorted(native_paths):
        try:
            payload, metadata = host_toolchain._read_native_object(resolved)
            package = host_toolchain._package_owner(resolved)
        except (OSError, host_toolchain.ToolchainError) as exc:
            raise ControllerToolchainError(
                f"controller native dependency attestation failed: {resolved}"
            ) from exc
        packages.add(package)
        native_objects.append(
            {
                "gid": metadata.st_gid,
                "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
                "package": package,
                "path": str(resolved),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": metadata.st_size,
                "uid": metadata.st_uid,
            }
        )
    try:
        package_inventory = host_toolchain._package_inventory(packages)
    except (OSError, host_toolchain.ToolchainError) as exc:
        raise ControllerToolchainError("controller package inventory failed") from exc
    return {
        "native_objects": native_objects,
        "packages": package_inventory,
        "schema_version": SCHEMA,
        "tools": tools,
    }


def canonical_bytes(inventory: dict[str, object]) -> bytes:
    return (json.dumps(inventory, separators=(",", ":"), sort_keys=True) + "\n").encode()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--emit-inventory", action="store_true")
    mode.add_argument("--expected-inventory-sha256")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = canonical_bytes(build_inventory())
    if args.emit_inventory:
        os.write(1, payload)
        return
    expected = str(args.expected_inventory_sha256 or "")
    if HEX64_RE.fullmatch(expected) is None:
        raise ControllerToolchainError(
            "expected controller toolchain digest must be 64 lowercase hex"
        )
    observed = hashlib.sha256(payload).hexdigest()
    if observed != expected:
        raise ControllerToolchainError(
            "Matrix controller toolchain differs from its approved pin"
        )
    print("controller_toolchain_attested=yes")
    print(f"controller_toolchain_inventory_sha256={observed}")


if __name__ == "__main__":
    try:
        main()
    except (ControllerToolchainError, OSError, ValueError) as exc:
        raise SystemExit(f"Matrix controller toolchain verification failed: {exc}") from exc
