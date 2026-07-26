#!/usr/bin/env python3
"""Install one hash-manifested WA-IR pull-agent bundle without deletion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any


SCHEMA = "three-site-full-matrix-object-storage-agent-bundle-v1"
EXPECTED_FILES = {
    "agent-config.json": ("/etc/trading-bot-full-matrix/object-storage-agent.json", 0o600),
    "agent-ed25519.pem": ("/etc/trading-bot-full-matrix/agent-ed25519.pem", 0o600),
    "agent-age-identity.txt": (
        "/etc/trading-bot-full-matrix/agent-age-identity.txt",
        0o600,
    ),
    "object_storage_agent.py": (
        "/usr/local/lib/trading-bot-full-matrix/object_storage_agent.py",
        0o700,
    ),
    "object_storage_protocol.py": (
        "/usr/local/lib/trading-bot-full-matrix/object_storage_protocol.py",
        0o600,
    ),
    "site_agent.py": (
        "/usr/local/lib/trading-bot-full-matrix/site_agent.py",
        0o700,
    ),
    "full-matrix-object-storage-agent.service": (
        "/etc/systemd/system/full-matrix-object-storage-agent.service",
        0o644,
    ),
}
MAX_BUNDLE_BYTES = 4 * 1024 * 1024
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


class AgentInstallError(RuntimeError):
    """The WA-IR pull-agent bundle failed closed validation."""


def _hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            size += len(chunk)
            if size > MAX_BUNDLE_BYTES:
                raise AgentInstallError("agent bundle/file exceeds its fixed bound")
            digest.update(chunk)
    return digest.hexdigest(), size


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise AgentInstallError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _bundle(path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    if not path.is_absolute() or path.is_symlink():
        raise AgentInstallError("agent bundle path is unsafe")
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or not 2 <= metadata.st_size <= MAX_BUNDLE_BYTES
    ):
        raise AgentInstallError("agent bundle is not owner-only")
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [item.name for item in members]
        if (
            len(names) != len(set(names))
            or set(names) != set(EXPECTED_FILES) | {"manifest.json"}
        ):
            raise AgentInstallError("agent bundle member set is invalid")
        payloads: dict[str, bytes] = {}
        for member in members:
            member_path = Path(member.name)
            if (
                not member.isfile()
                or member_path.is_absolute()
                or len(member_path.parts) != 1
                or member.issym()
                or member.islnk()
                or member.size < 1
                or member.size > MAX_BUNDLE_BYTES
            ):
                raise AgentInstallError("agent bundle contains an unsafe member")
            handle = archive.extractfile(member)
            if handle is None:
                raise AgentInstallError("agent bundle member cannot be read")
            payload = handle.read(MAX_BUNDLE_BYTES + 1)
            if len(payload) != member.size:
                raise AgentInstallError("agent bundle member size differs")
            payloads[member.name] = payload
    try:
        manifest = json.loads(
            payloads.pop("manifest.json"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentInstallError("agent bundle manifest is invalid") from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "release_sha", "campaign_id", "files"}
        or manifest.get("schema") != SCHEMA
        or not isinstance(manifest.get("files"), dict)
        or set(manifest["files"]) != set(EXPECTED_FILES)
    ):
        raise AgentInstallError("agent bundle manifest fields are invalid")
    for name, payload in payloads.items():
        expected = manifest["files"][name]
        if (
            not isinstance(expected, dict)
            or set(expected) != {"sha256", "bytes", "mode"}
            or expected["sha256"] != hashlib.sha256(payload).hexdigest()
            or expected["bytes"] != len(payload)
            or expected["mode"] != EXPECTED_FILES[name][1]
        ):
            raise AgentInstallError(f"agent bundle member differs: {name}")
    return manifest, payloads


def _install(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(mode=0o700 if mode != 0o644 else 0o755, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise AgentInstallError("agent installation path is unsafe")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        mode,
    )
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise AgentInstallError("agent installation write was incomplete")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def install(bundle: Path) -> dict[str, Any]:
    manifest, payloads = _bundle(bundle)
    for name, (target, mode) in EXPECTED_FILES.items():
        _install(Path(target), payloads[name], mode=mode)
    state_root = Path("/var/lib/trading-bot-full-matrix/object-storage-agent")
    state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state_root.parent, 0o700)
    os.chmod(state_root, 0o700)
    for argv in (
        ["/usr/bin/systemctl", "daemon-reload"],
        ["/usr/bin/systemctl", "enable", "--now", "full-matrix-object-storage-agent.service"],
        ["/usr/bin/systemctl", "is-active", "--quiet", "full-matrix-object-storage-agent.service"],
    ):
        result = subprocess.run(
            argv,
            env=SAFE_ENV,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            raise AgentInstallError("Object Storage agent service activation failed")
    return {
        "status": "installed",
        "schema": SCHEMA,
        "release_sha": manifest["release_sha"],
        "campaign_id": manifest["campaign_id"],
        "service": "full-matrix-object-storage-agent.service",
        "transport": "private-versioned-object-storage-pull",
        "delete_operation_available": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(install(args.bundle), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AgentInstallError, OSError, RuntimeError):
        raise SystemExit(1)
