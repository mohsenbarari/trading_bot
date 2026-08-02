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
import hmac
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile


REPO_ROOT = Path(__file__).resolve().parents[1]
SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
SOURCE_RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
PACKAGE_ROOT = "emergency-ir-standalone"
PACKAGE_PATHS = (
    "deploy/emergency-ir",
    "scripts/emergency_ir_object_storage_manifest.py",
    "scripts/emergency_ir_object_storage_receiver.py",
    "scripts/emergency_ir_standalone_activate.py",
    "scripts/run_emergency_ir_object_storage_receive.py",
    "scripts/render_emergency_ir_standalone_env.py",
    "scripts/preflight_emergency_ir_host_isolation.py",
    "scripts/validate_emergency_ir_compose_contract.py",
    "scripts/verify_emergency_ir_image_provenance.py",
    "scripts/verify_emergency_ir_sms_egress_image.py",
    "scripts/verify_emergency_ir_standalone.py",
)
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
PACKAGE_BUILDER_SOURCE_PATH = "scripts/build_emergency_ir_release_package.py"


class EmergencyPackageError(RuntimeError):
    pass


def _git_environment() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "LC_ALL": "C",
            "LANG": "C",
            "PATH": os.defpath,
        }
    )
    return environment


def _fixed_git(
    *arguments: str, text: bool = False
) -> subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [
                "/usr/bin/git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-c",
                "core.preloadIndex=false",
                "-c",
                f"core.worktree={REPO_ROOT}",
                "-C",
                str(REPO_ROOT),
                *arguments,
            ],
            check=False,
            capture_output=True,
            text=text,
            env=_git_environment(),
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyPackageError("cannot inspect the fixed Emergency package source") from exc


def _fixed_source_revision() -> str:
    top = _fixed_git("rev-parse", "--show-toplevel", text=True)
    try:
        if top.returncode != 0 or Path(str(top.stdout).strip()).resolve() != REPO_ROOT:
            raise EmergencyPackageError("Emergency package Git worktree differs from the executing checkout")
    except OSError as exc:
        raise EmergencyPackageError("Emergency package Git worktree cannot be resolved") from exc
    head = _fixed_git("rev-parse", "--verify", "HEAD^{commit}", text=True)
    revision = str(head.stdout).strip()
    if head.returncode != 0 or SHA_RE.fullmatch(revision) is None:
        raise EmergencyPackageError("Emergency package source revision is unsafe")
    return revision


def _read_builder_worktree_blob() -> bytes:
    """Read the executable package builder itself without links or races."""

    path = REPO_ROOT / PACKAGE_BUILDER_SOURCE_PATH
    try:
        before = path.lstat()
    except OSError as exc:
        raise EmergencyPackageError("Emergency package builder source cannot be inspected") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or not 1 <= before.st_size <= MAX_FILE_BYTES
    ):
        raise EmergencyPackageError("Emergency package builder source is not one bounded owner-controlled regular file")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(opened, field) for field in fields):
            raise EmergencyPackageError("Emergency package builder source changed while being opened")
        payload = bytearray()
        while len(payload) <= MAX_FILE_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(payload) != opened.st_size
            or len(payload) > MAX_FILE_BYTES
            or any(getattr(opened, field) != getattr(after, field) for field in fields)
        ):
            raise EmergencyPackageError("Emergency package builder source changed while being read")
        return bytes(payload)
    except OSError as exc:
        raise EmergencyPackageError("Emergency package builder source cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _assert_fixed_builder_source(*, revision: str) -> None:
    """Require the running builder bytes to equal its captured Git revision."""

    expected = _read_head_blob(revision=revision, relative=PACKAGE_BUILDER_SOURCE_PATH)
    actual = _read_builder_worktree_blob()
    if not hmac.compare_digest(actual, expected):
        raise EmergencyPackageError("Emergency package builder source differs from its fixed revision")


def _tracked_files(*, revision: str) -> list[str]:
    result = _fixed_git("ls-tree", "-r", "-z", "--name-only", revision, "--", *PACKAGE_PATHS)
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


def _read_head_blob(*, revision: str, relative: str) -> bytes:
    """Read exactly the committed blob, never a mutable worktree pathname."""

    result = _fixed_git("show", f"{revision}:{relative}")
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


def build_package(*, source_release_sha: str, emergency_patch_sha: str, output: Path) -> tuple[str, int]:
    if SHA_RE.fullmatch(source_release_sha) is None or SHA_RE.fullmatch(emergency_patch_sha) is None:
        raise EmergencyPackageError("release identities must be exact lowercase Git SHAs")
    if source_release_sha != SOURCE_RELEASE_SHA:
        raise EmergencyPackageError("source release SHA is not the fixed Emergency base release")
    revision = _fixed_source_revision()
    if revision != emergency_patch_sha:
        raise EmergencyPackageError("Emergency patch SHA is not the package worktree HEAD")
    _assert_fixed_builder_source(revision=revision)
    entries = [
        (relative, _read_head_blob(revision=revision, relative=relative))
        for relative in _tracked_files(revision=revision)
    ]
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-release-sha", required=True)
    parser.add_argument("--emergency-patch-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def _require_isolated_cli() -> None:
    if not sys.flags.isolated or not sys.flags.dont_write_bytecode:
        raise EmergencyPackageError("Emergency release package builder must be launched with python3 -I -B")


def main(argv: list[str] | None = None) -> int:
    try:
        _require_isolated_cli()
        args = parse_args(argv)
        digest, size = build_package(
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
