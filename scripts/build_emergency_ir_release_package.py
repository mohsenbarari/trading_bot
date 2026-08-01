#!/usr/bin/env python3
"""Build a minimal, deterministic Emergency IR standalone release package.

The package deliberately excludes application source, Docker images, database
content, production settings, and every credential.  It contains only the
isolated Compose/Nginx/verification/receive controls needed on WA-IR after
the separately encrypted artifacts have arrived.  A signed Object Storage
manifest subsequently binds this package tar to the exact campaign.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile


REPO_ROOT = Path(__file__).resolve().parents[1]
SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
PACKAGE_ROOT = "emergency-ir-standalone"
PACKAGE_PATHS = (
    "deploy/emergency-ir",
    "scripts/emergency_ir_object_storage_manifest.py",
    "scripts/emergency_ir_object_storage_receiver.py",
    "scripts/emergency_ir_standalone_activate.py",
    "scripts/run_emergency_ir_object_storage_receive.py",
    "scripts/render_emergency_ir_standalone_env.py",
    "scripts/verify_emergency_ir_image_provenance.py",
    "scripts/verify_emergency_ir_sms_egress_image.py",
    "scripts/verify_emergency_ir_standalone.py",
)
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_PACKAGE_BYTES = 32 * 1024 * 1024


class EmergencyPackageError(RuntimeError):
    pass


def _tracked_files(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", *PACKAGE_PATHS],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise EmergencyPackageError("cannot list the tracked Emergency package files")
    files = sorted(item for item in result.stdout.decode("utf-8").split("\0") if item)
    if not files or not any(item.startswith("deploy/emergency-ir/") for item in files):
        raise EmergencyPackageError("Emergency package file set is incomplete")
    unexpected = [
        item
        for item in files
        if not any(item == allowed or item.startswith(allowed.rstrip("/") + "/") for allowed in PACKAGE_PATHS)
    ]
    if unexpected:
        raise EmergencyPackageError("Emergency package file set escaped its allowlist")
    return files


def _read_head_blob(repo: Path, relative: str) -> bytes:
    """Read exactly the committed blob, never a mutable worktree pathname."""

    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"HEAD:{relative}"],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0 or not 1 <= len(result.stdout) <= MAX_FILE_BYTES:
        raise EmergencyPackageError(f"Emergency package HEAD blob is unavailable or invalid: {relative}")
    return bytes(result.stdout)


def _write_create_only(path: Path, payload: bytes) -> None:
    if not path.is_absolute():
        raise EmergencyPackageError("package output must be absolute")
    try:
        parent = path.parent.lstat()
    except OSError as exc:
        raise EmergencyPackageError("package output directory cannot be inspected") from exc
    if (
        stat.S_ISLNK(parent.st_mode)
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise EmergencyPackageError("package output directory is not owner-controlled")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError("short output write")
            offset += written
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise EmergencyPackageError("refusing to overwrite an existing Emergency package") from exc
    except OSError as exc:
        raise EmergencyPackageError("Emergency package cannot be created") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def build_package(
    *, repo: Path, source_release_sha: str, emergency_patch_sha: str, output: Path
) -> tuple[str, int]:
    if SHA_RE.fullmatch(source_release_sha) is None or SHA_RE.fullmatch(emergency_patch_sha) is None:
        raise EmergencyPackageError("release identities must be exact lowercase Git SHAs")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=False, capture_output=True, text=True, timeout=30
    )
    if head.returncode != 0 or head.stdout.strip() != emergency_patch_sha:
        raise EmergencyPackageError("Emergency patch SHA is not the package worktree HEAD")
    entries = [(relative, _read_head_blob(repo, relative)) for relative in _tracked_files(repo)]
    release = {
        "schema": "gold-trade-emergency-ir-release-package-v1",
        "source_release_sha": source_release_sha,
        "emergency_patch_sha": emergency_patch_sha,
        "files": [
            {"path": relative, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
            for relative, payload in entries
        ],
    }
    release_payload = (json.dumps(release, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for relative, payload in [("RELEASE.json", release_payload), *entries]:
                info = tarfile.TarInfo(f"{PACKAGE_ROOT}/{relative}")
                info.size = len(payload)
                info.mode = 0o600
                info.uid = 0
                info.gid = 0
                info.uname = "root"
                info.gname = "root"
                info.mtime = 0
                archive.addfile(info, io.BytesIO(payload))
    package = raw.getvalue()
    if not 1 <= len(package) <= MAX_PACKAGE_BYTES:
        raise EmergencyPackageError("Emergency package exceeds its fixed size bound")
    _write_create_only(output, package)
    return hashlib.sha256(package).hexdigest(), len(package)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--source-release-sha", required=True)
    parser.add_argument("--emergency-patch-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        digest, size = build_package(
            repo=args.repo.resolve(),
            source_release_sha=args.source_release_sha,
            emergency_patch_sha=args.emergency_patch_sha,
            output=args.output,
        )
    except EmergencyPackageError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps({"status": "built-local-only", "sha256": digest, "bytes": size, "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
