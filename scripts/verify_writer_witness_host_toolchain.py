#!/usr/bin/env python3
"""Bind the complete privileged Writer Witness host toolchain to one digest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess


class ToolchainError(RuntimeError):
    """The immutable host toolchain is incomplete, unsafe, or drifted."""


TOOL_NAMES = (
    "age",
    "awk",
    "basename",
    "bash",
    "cat",
    "chmod",
    "chown",
    "cmp",
    "cp",
    "createdb",
    "curl",
    "cut",
    "date",
    "dd",
    "df",
    "dirname",
    "dpkg-query",
    "dropdb",
    "env",
    "find",
    "flock",
    "getent",
    "grep",
    "head",
    "id",
    "install",
    "initdb",
    "journalctl",
    "ldd",
    "ln",
    "mktemp",
    "mount",
    "mountpoint",
    "mv",
    "nft",
    "nginx",
    "openssl",
    "pg_config",
    "pg_ctl",
    "pg_dump",
    "pg_restore",
    "pgrep",
    "psql",
    "postgres",
    "python3",
    "python3.12",
    "readlink",
    "realpath",
    "rm",
    "runuser",
    "scp",
    "sed",
    "seq",
    "sha256sum",
    "sh",
    "sleep",
    "sort",
    "ss",
    "ssh",
    "sshd",
    "stat",
    "sync",
    "systemctl",
    "timedatectl",
    "timeout",
    "tr",
    "ufw",
    "umount",
    "wc",
)
EXTRA_PACKAGES = ("ca-certificates", "libfaketime", "python3-venv")
SEARCH_PATHS = (Path("/usr/sbin"), Path("/usr/bin"), Path("/sbin"), Path("/bin"))
HEX64_RE = re.compile(r"[0-9a-f]{64}")
POSTGRESQL_SERVER_BINARIES = frozenset({"initdb", "pg_ctl", "postgres"})
POSTGRESQL_WRAPPED_BINARIES = frozenset(
    {"createdb", "dropdb", "pg_dump", "pg_restore", "psql"}
)

# This is the reviewed, reachable privileged host command surface.  Each tool
# has an exact source marker outside TOOL_NAMES (except ldd/pg_config, whose
# subprocess invocations live in this verifier).  The source gate requires
# exact set equality with TOOL_NAMES: adding an invocation or an inventory
# entry is therefore one explicit, reviewable contract change rather than a
# hand-picked subset assertion.
COMMAND_SURFACE = {
    "age": ("deploy/writer-witness/writer-witness-offsite-backup.sh", "age --encrypt"),
    "awk": ("deploy/writer-witness/writer-witness-state-manifest.sh", "| awk '{print $1}'"),
    "basename": ("scripts/plan_writer_witness_real_host_matrix.py", "basename \"$latest\""),
    "bash": ("scripts/provision_writer_witness_host.sh", "#!/bin/bash"),
    "cat": ("scripts/provision_writer_witness_host.sh", 'cat >"$ssh_hardening_candidate"'),
    "chmod": ("deploy/writer-witness/writer-witness-live-restore.sh", "chmod 0600"),
    "chown": ("deploy/writer-witness/writer-witness-matrix-host-faults.sh", "chown postgres:postgres"),
    "cmp": ("deploy/writer-witness/writer-witness-matrix-host-faults.sh", "cmp --silent"),
    "cp": ("scripts/provision_writer_witness_host.sh", "cp -a"),
    "createdb": ("deploy/writer-witness/writer-witness-restore-drill.sh", "createdb"),
    "curl": ("deploy/writer-witness/writer-witness-live-restore.sh", "curl --fail"),
    "cut": ("scripts/provision_writer_witness_host.sh", "| cut -d: -f6"),
    "date": ("deploy/writer-witness/writer-witness-backup.sh", "date -u"),
    "dd": ("deploy/writer-witness/writer-witness-live-restore.sh", "dd iflag=fullblock bs=1048576 count=64 status=none"),
    "df": ("scripts/plan_writer_witness_real_host_matrix.py", "used=$(df -P /"),
    "dirname": ("deploy/writer-witness/writer-witness-live-restore.sh", "dirname -- \"$path\""),
    "dpkg-query": ("deploy/writer-witness/writer-witness-matrix-host-faults.sh", "dpkg-query"),
    "dropdb": ("deploy/writer-witness/writer-witness-restore-drill.sh", "dropdb"),
    "env": ("scripts/provision_writer_witness_host.sh", "/usr/bin/env -i"),
    "find": ("deploy/writer-witness/writer-witness-offsite-backup.sh", "find \"$BACKUP_DIR\""),
    "flock": ("deploy/writer-witness/writer-witness-live-restore.sh", "flock --exclusive --nonblock"),
    "getent": ("scripts/provision_writer_witness_host.sh", "getent passwd"),
    "grep": ("deploy/writer-witness/writer-witness-live-restore.sh", "| grep -qx 1"),
    "head": ("deploy/writer-witness/writer-witness-matrix-host-faults.sh", "head -1"),
    "id": ("deploy/writer-witness/writer-witness-live-restore.sh", "id -u"),
    "install": ("deploy/writer-witness/writer-witness-backup.sh", "install -d"),
    "initdb": ("deploy/writer-witness/writer-witness-matrix-host-faults.sh", "initdb"),
    "journalctl": ("scripts/provision_writer_witness_host.sh", "journalctl"),
    "ldd": ("scripts/verify_writer_witness_host_toolchain.py", '_find_tool("ldd")'),
    "ln": ("deploy/writer-witness/writer-witness-live-restore.sh", "ln -T"),
    "mktemp": ("deploy/writer-witness/writer-witness-offsite-backup.sh", "mktemp"),
    "mount": ("deploy/writer-witness/writer-witness-matrix-host-faults.sh", "mount -t tmpfs"),
    "mountpoint": ("scripts/run_writer_witness_real_host_matrix.py", "mountpoint -q"),
    "mv": ("deploy/writer-witness/writer-witness-offsite-backup.sh", "mv -f"),
    "nft": ("scripts/provision_writer_witness_host.sh", "nft -j list ruleset"),
    "nginx": ("scripts/provision_writer_witness_host.sh", "nginx -t"),
    "openssl": ("scripts/provision_writer_witness_host.sh", "openssl"),
    "pg_config": ("scripts/verify_writer_witness_host_toolchain.py", '_find_tool("pg_config")'),
    "pg_ctl": ("deploy/writer-witness/writer-witness-matrix-host-faults.sh", "pg_ctl"),
    "pg_dump": ("deploy/writer-witness/writer-witness-backup.sh", "pg_dump"),
    "pg_restore": ("deploy/writer-witness/writer-witness-live-restore.sh", "pg_restore"),
    "pgrep": ("scripts/run_writer_witness_real_host_matrix.py", "pgrep -f"),
    "psql": ("deploy/writer-witness/writer-witness-state-manifest.sh", "psql -XAt"),
    "postgres": ("deploy/writer-witness/writer-witness-matrix-host-faults.sh", "postgres"),
    "python3": ("scripts/run_writer_witness_real_host_matrix.py", '"python3",'),
    "python3.12": ("deploy/writer-witness/writer-witness-activation-watchdog.sh", "/usr/bin/python3.12"),
    "readlink": ("scripts/provision_writer_witness_host.sh", "readlink -f"),
    "realpath": ("deploy/writer-witness/writer-witness-matrix-host-faults.sh", "realpath -e"),
    "rm": ("deploy/writer-witness/writer-witness-live-restore.sh", "rm -f"),
    "runuser": ("deploy/writer-witness/writer-witness-backup.sh", "runuser -u postgres"),
    "scp": ("scripts/run_writer_witness_real_host_matrix.py", '"scp", "-q"'),
    "sed": ("scripts/provision_writer_witness_host.sh", "sed -n"),
    "seq": ("deploy/writer-witness/writer-witness-live-restore.sh", "seq 1 30"),
    "sha256sum": ("deploy/writer-witness/writer-witness-backup.sh", "sha256sum"),
    "sh": ("deploy/writer-witness/writer-witness-matrix-host-faults.sh", "#!/bin/sh"),
    "sleep": ("deploy/writer-witness/writer-witness-live-restore.sh", "sleep 1"),
    "sort": ("deploy/writer-witness/writer-witness-offsite-backup.sh", "sort -nr"),
    "ss": ("deploy/writer-witness/writer-witness-matrix-host-faults.sh", "ss -H -ltn"),
    "ssh": ("scripts/run_writer_witness_real_host_matrix.py", '"ssh", "-o"'),
    "sshd": ("scripts/provision_writer_witness_host.sh", "sshd -t"),
    "stat": ("deploy/writer-witness/writer-witness-offsite-backup.sh", "stat -c"),
    "sync": ("deploy/writer-witness/writer-witness-live-restore.sh", "sync -f"),
    "systemctl": ("deploy/writer-witness/writer-witness-live-restore.sh", "systemctl stop"),
    "timedatectl": ("scripts/plan_writer_witness_real_host_matrix.py", "timedatectl show"),
    "timeout": ("scripts/plan_writer_witness_real_host_matrix.py", "timeout 5 bash -c"),
    "tr": ("scripts/provision_writer_witness_host.sh", "tr -d"),
    "ufw": ("scripts/plan_writer_witness_real_host_matrix.py", "ufw status verbose"),
    "umount": ("deploy/writer-witness/writer-witness-matrix-host-fault-state.py", '"umount"'),
    "wc": ("deploy/writer-witness/writer-witness-live-restore.sh", "dd bs=1 count=1 status=none | wc -c"),
}


def verify_command_surface(source_root: Path) -> dict[str, object]:
    if not source_root.is_absolute() or not source_root.is_dir():
        raise ToolchainError("command source root must be an absolute directory")
    inventory = set(TOOL_NAMES)
    declared = set(COMMAND_SURFACE)
    missing = sorted(declared - inventory)
    unbound = sorted(inventory - declared)
    if missing or unbound:
        raise ToolchainError(
            "host command surface and immutable inventory differ: "
            f"missing_from_inventory={missing}; missing_source_binding={unbound}"
        )
    covered: list[dict[str, str]] = []
    for command in sorted(COMMAND_SURFACE):
        relative, marker = COMMAND_SURFACE[command]
        path = source_root / relative
        try:
            payload = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ToolchainError(f"cannot read command source: {relative}") from exc
        if marker not in payload:
            raise ToolchainError(
                f"host command source marker drifted: {relative}:{command}"
            )
        covered.append({"command": command, "source": relative})
    return {
        "command_surface_attested": "yes",
        "entries": covered,
        "entry_count": len(covered),
    }


def _clean_run(arguments: list[str]) -> str:
    completed = subprocess.run(
        arguments,
        env={"LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ToolchainError(f"host package query failed: {detail}")
    return completed.stdout


def _postgresql_bindir() -> Path:
    pg_config = _find_tool("pg_config")
    bindir = Path(_clean_run([str(pg_config), "--bindir"]).strip())
    if (
        not bindir.is_absolute()
        or len(bindir.parts) != 6
        or bindir.parts[:4] != ("/", "usr", "lib", "postgresql")
        or not bindir.parts[4].isdigit()
        or bindir.parts[5] != "bin"
    ):
        raise ToolchainError("pg_config returned an unsafe PostgreSQL binary directory")
    return bindir


def _find_tool(name: str) -> Path:
    if name in POSTGRESQL_SERVER_BINARIES:
        bindir = _postgresql_bindir()
        candidate = bindir / name
        if os.access(candidate, os.X_OK):
            return candidate
        raise ToolchainError(f"required PostgreSQL executable is missing: {name}")
    for directory in SEARCH_PATHS:
        candidate = directory / name
        if os.access(candidate, os.X_OK):
            return candidate
    raise ToolchainError(f"required host executable is missing: {name}")


def _read_executable(path: Path) -> tuple[bytes, os.stat_result]:
    resolved = path.resolve(strict=True)
    postgresql_binary = (
        len(resolved.parts) == 7
        and resolved.parts[:4] == ("/", "usr", "lib", "postgresql")
        and resolved.parts[4].isdigit()
        and resolved.parts[5] == "bin"
    )
    postgresql_wrapper = resolved == Path("/usr/share/postgresql-common/pg_wrapper")
    if (
        resolved.parent not in SEARCH_PATHS
        and not postgresql_binary
        and not postgresql_wrapper
    ):
        raise ToolchainError(f"host executable resolves outside approved roots: {path}")
    descriptor = os.open(
        resolved,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_mode & 0o022
            or not before.st_mode & 0o111
            or before.st_nlink < 1
            or before.st_size < 1
        ):
            raise ToolchainError(f"host executable metadata is unsafe: {resolved}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_uid,
            value.st_gid,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if identity(before) != identity(after):
            raise ToolchainError(f"host executable changed during attestation: {resolved}")
        return b"".join(chunks), before
    finally:
        os.close(descriptor)


def _read_native_object(path: Path) -> tuple[bytes, os.stat_result]:
    resolved = path.resolve(strict=True)
    if not (
        resolved.is_relative_to("/usr/lib")
        or resolved.is_relative_to("/lib")
        or resolved.is_relative_to("/usr/lib64")
        or resolved.is_relative_to("/lib64")
    ):
        raise ToolchainError(f"native dependency resolves outside approved roots: {path}")
    descriptor = os.open(
        resolved,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_mode & 0o022
            or before.st_nlink < 1
            or before.st_size < 1
        ):
            raise ToolchainError(f"native dependency metadata is unsafe: {resolved}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_uid,
            value.st_gid,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if identity(before) != identity(after):
            raise ToolchainError(f"native dependency changed during attestation: {resolved}")
        return b"".join(chunks), before
    finally:
        os.close(descriptor)


def _linked_native_paths(executable: Path, payload: bytes) -> set[Path]:
    if not payload.startswith(b"\x7fELF"):
        return set()
    output = _clean_run([str(_find_tool("ldd")), str(executable)])
    paths: set[Path] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("linux-vdso"):
            continue
        if "=> not found" in line:
            raise ToolchainError(f"host executable has a missing native dependency: {executable}")
        if "=>" in line:
            candidate = line.split("=>", 1)[1].strip().split(" ", 1)[0]
        else:
            candidate = line.split(" ", 1)[0]
        if not candidate.startswith("/"):
            raise ToolchainError(f"host executable has an unbound native dependency: {line}")
        paths.add(Path(candidate).resolve(strict=True))
    if not paths:
        raise ToolchainError(f"ELF host executable has no observable native closure: {executable}")
    return paths


def _package_owner(path: Path) -> str:
    output = _clean_run(["/usr/bin/dpkg-query", "-S", str(path)])
    owners = {
        line.split(":", 1)[0].split(",", 1)[0]
        for line in output.splitlines()
        if ":" in line
    }
    if len(owners) != 1:
        raise ToolchainError(f"host executable has ambiguous package ownership: {path}")
    return owners.pop()


def _package_inventory(packages: set[str]) -> list[dict[str, str]]:
    inventory: list[dict[str, str]] = []
    for package in sorted(packages):
        output = _clean_run(
            [
                "/usr/bin/dpkg-query",
                "-W",
                "-f=${binary:Package}\\t${Version}\\t${Architecture}\\t${db:Status-Abbrev}\\n",
                package,
            ]
        ).rstrip("\n")
        parts = output.split("\t")
        if len(parts) != 4 or parts[3] != "ii ":
            raise ToolchainError(f"required host package is not fully installed: {package}")
        inventory.append(
            {"package": parts[0], "version": parts[1], "architecture": parts[2], "status": parts[3]}
        )
    return inventory


def build_inventory() -> dict[str, object]:
    tools: list[dict[str, object]] = []
    postgresql_binaries: list[dict[str, object]] = []
    native_paths: set[Path] = set()
    packages = set(EXTRA_PACKAGES)
    for name in TOOL_NAMES:
        requested = _find_tool(name)
        resolved = requested.resolve(strict=True)
        payload, metadata = _read_executable(requested)
        package = _package_owner(resolved)
        packages.add(package)
        native_paths.update(_linked_native_paths(resolved, payload))
        tools.append(
            {
                "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
                "name": name,
                "package": package,
                "requested_path": str(requested),
                "resolved_path": str(resolved),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": metadata.st_size,
                "uid": metadata.st_uid,
                "gid": metadata.st_gid,
            }
        )
    bindir = _postgresql_bindir()
    for name in sorted(POSTGRESQL_WRAPPED_BINARIES):
        resolved = (bindir / name).resolve(strict=True)
        payload, metadata = _read_executable(resolved)
        package = _package_owner(resolved)
        packages.add(package)
        native_paths.update(_linked_native_paths(resolved, payload))
        postgresql_binaries.append(
            {
                "gid": metadata.st_gid,
                "mode": format(stat.S_IMODE(metadata.st_mode), "04o"),
                "name": name,
                "package": package,
                "path": str(resolved),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": metadata.st_size,
                "uid": metadata.st_uid,
            }
        )
    native_objects: list[dict[str, object]] = []
    for resolved in sorted(native_paths):
        payload, metadata = _read_native_object(resolved)
        package = _package_owner(resolved)
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
    return {
        "native_objects": native_objects,
        "packages": _package_inventory(packages),
        "postgresql_selected_binaries": postgresql_binaries,
        "schema_version": "writer_witness_host_toolchain_v1",
        "tools": tools,
    }


def canonical_bytes(inventory: dict[str, object]) -> bytes:
    return (json.dumps(inventory, separators=(",", ":"), sort_keys=True) + "\n").encode()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--emit-inventory", action="store_true")
    mode.add_argument("--expected-inventory-sha256")
    mode.add_argument("--verify-command-surface", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.verify_command_surface is not None:
        print(
            json.dumps(
                verify_command_surface(args.verify_command_surface),
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return
    payload = canonical_bytes(build_inventory())
    if args.emit_inventory:
        os.write(1, payload)
        return
    expected = args.expected_inventory_sha256
    if not isinstance(expected, str) or HEX64_RE.fullmatch(expected) is None:
        raise ToolchainError("expected host toolchain digest must be 64 lowercase hex")
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ToolchainError("immutable host toolchain differs from its approved pin")
    print("host_toolchain_attested=yes")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ToolchainError, ValueError) as exc:
        raise SystemExit(f"Writer Witness host toolchain verification failed: {exc}") from exc
