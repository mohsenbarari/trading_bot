#!/usr/bin/env python3
"""Create the tiny, pinned-key bootstrap bundle for Emergency WA-IR receive.

The bundle deliberately contains only the manifest verifier, bounded receiver,
small Python entry point, and the non-secret Ed25519 public key.  It has no
application source, image, database, settings, credentials, or Object Storage
client configuration.  It is fetched by WA-IR directly from Object Storage
before the sealed artifact manifest can be verified.
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
import sys
import tarfile
import types
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


class ReceiverBundleError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise ReceiverBundleError(message)


def _assert_preimport_scripts_surface(*, repo_root: Path = REPO_ROOT) -> Path:
    """Return the one safe implicit ``scripts`` directory before importing it.

    ``scripts`` deliberately has no ``__init__.py`` in the Emergency
    bootstrap.  A bare ``sys.path.insert(0, repo_root)`` is not sufficient:
    Python can prefer a later *regular* ``scripts`` package from a system site
    directory over an earlier implicit namespace.  Validate the local surface
    first, then :func:`_install_pinned_scripts_namespace` below makes that
    bypass impossible.
    """

    scripts_root = repo_root / "scripts"
    try:
        directory = scripts_root.lstat()
    except OSError as exc:
        raise ReceiverBundleError("receiver bundle scripts directory cannot be inspected") from exc
    if (
        stat.S_ISLNK(directory.st_mode)
        or not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != os.geteuid()
        or stat.S_IMODE(directory.st_mode) & 0o022
    ):
        _fail("receiver bundle scripts directory is not a safe import surface")
    try:
        (scripts_root / "__init__.py").lstat()
    except FileNotFoundError:
        return scripts_root
    except OSError as exc:
        raise ReceiverBundleError("receiver bundle scripts package initializer cannot be inspected") from exc
    _fail("receiver bundle source contains an unsupported scripts package initializer")


def _install_pinned_scripts_namespace(*, repo_root: Path = REPO_ROOT) -> None:
    """Pin imports to this exact implicit namespace, never ambient site paths."""

    scripts_root = _assert_preimport_scripts_surface(repo_root=repo_root)
    expected = str(scripts_root)
    present = sys.modules.get("scripts")
    if present is not None:
        paths = getattr(present, "__path__", None)
        if (
            getattr(present, "__file__", None) is not None
            or paths is None
            or [str(item) for item in paths] != [expected]
        ):
            _fail("receiver bundle scripts namespace was preloaded from an ambient path")
        return
    namespace = types.ModuleType("scripts")
    namespace.__package__ = "scripts"
    namespace.__path__ = [expected]  # type: ignore[attr-defined]
    sys.modules["scripts"] = namespace


def _require_isolated_cli() -> None:
    if not sys.flags.isolated or not sys.flags.dont_write_bytecode:
        _fail("Emergency receiver bundle builder must be launched with python3 -I -B")


# This runs before an import from ``scripts``.  In CLI mode reject an ambient
# interpreter before argparse can process any real work arguments.
_install_pinned_scripts_namespace()
if __name__ == "__main__":
    try:
        _require_isolated_cli()
    except ReceiverBundleError as exc:
        sys.stderr.write(f"blocked: {exc}\n")
        raise SystemExit(2) from exc

from scripts import emergency_ir_object_storage_manifest as manifest


MAX_MEMBER_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 4 * 1024 * 1024
GIT_REVISION_RE = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$", re.ASCII)
BUNDLE_MEMBERS = (
    ("deploy/emergency-ir/run_object_storage_receiver.py", "run_receiver.py"),
    ("scripts/emergency_ir_object_storage_manifest.py", "scripts/emergency_ir_object_storage_manifest.py"),
    ("scripts/emergency_ir_object_storage_receiver.py", "scripts/emergency_ir_object_storage_receiver.py"),
    # The activator remains in the pinned bootstrap bundle so package tar
    # extraction is not a bootstrap trust gap.  It only operates on already
    # received local files; it has no network or Object Storage client.
    ("scripts/emergency_ir_standalone_activate.py", "scripts/emergency_ir_standalone_activate.py"),
)


def _git_environment() -> dict[str, str]:
    """Return a deterministic Git environment for immutable source reads."""

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


def _source_git(
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
            text=text,
            capture_output=True,
            check=False,
            env=_git_environment(),
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReceiverBundleError("receiver bundle source cannot be inspected") from exc


def _fixed_source_revision() -> str:
    completed = _source_git("rev-parse", "--verify", "HEAD^{commit}", text=True)
    revision = str(completed.stdout).strip()
    if completed.returncode != 0 or GIT_REVISION_RE.fullmatch(revision) is None:
        _fail("receiver bundle source revision is unsafe")
    return revision


def _head_blob(*, source_revision: str, relative: str) -> bytes:
    """Read one exact bundle member from the already captured Git revision."""

    completed = _source_git("show", f"{source_revision}:{relative}")
    payload = bytes(completed.stdout)
    if completed.returncode != 0 or not 1 <= len(payload) <= MAX_MEMBER_BYTES:
        _fail(f"receiver bundle source is unavailable at its fixed revision: {relative}")
    return payload


def _read_regular(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    try:
        state = path.lstat()
    except OSError as exc:
        raise ReceiverBundleError(f"{label} cannot be inspected") from exc
    if (
        stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or state.st_uid != os.geteuid()
        or state.st_nlink != 1
        or stat.S_IMODE(state.st_mode) & 0o022
        or not 1 <= state.st_size <= maximum_bytes
    ):
        raise ReceiverBundleError(f"{label} is not one bounded owner-controlled regular file")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(descriptor)
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if len(payload) != before.st_size or len(payload) > maximum_bytes or any(
            getattr(before, field) != getattr(after, field) for field in fields
        ):
            raise ReceiverBundleError(f"{label} changed while being read")
        return bytes(payload)
    except OSError as exc:
        raise ReceiverBundleError(f"{label} cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_create_only(path: Path, payload: bytes) -> None:
    if not path.is_absolute():
        raise ReceiverBundleError("receiver bundle output must be an absolute path")
    try:
        parent = path.parent.lstat()
    except OSError as exc:
        raise ReceiverBundleError("receiver bundle output directory cannot be inspected") from exc
    if (
        stat.S_ISLNK(parent.st_mode)
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise ReceiverBundleError("receiver bundle output directory is not owner-controlled")
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
        raise ReceiverBundleError("refusing to overwrite an existing receiver bundle") from exc
    except OSError as exc:
        raise ReceiverBundleError("receiver bundle cannot be created") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def render_bundle(*, signing_public_key: Path, source_revision: str | None = None) -> bytes:
    """Return the deterministic receiver bundle bytes without writing an output.

    The publisher uses this in its no-network planning phase to bind the
    exact executable bootstrap bytes into the human confirmation and sealed
    manifest.  Keeping rendering separate from the create-only write makes a
    dry run unable to leave a bundle behind.
    """

    try:
        manifest.load_public_key(signing_public_key)
    except Exception as exc:
        raise ReceiverBundleError("Emergency signing public key is unavailable or invalid") from exc
    revision = source_revision if source_revision is not None else _fixed_source_revision()
    if GIT_REVISION_RE.fullmatch(revision) is None:
        _fail("receiver bundle source revision is unsafe")
    # Do not reread mutable worktree paths after provenance captured the
    # revision.  Every executable bundle member is rendered from this exact
    # immutable Git object snapshot.
    files: list[tuple[str, bytes]] = [
        (target, _head_blob(source_revision=revision, relative=source))
        for source, target in BUNDLE_MEMBERS
    ]
    files.append(("signing-public.key", _read_regular(signing_public_key, label="Emergency signing public key", maximum_bytes=1024)))
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for name, payload in files:
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mode = 0o600
                info.uid = 0
                info.gid = 0
                info.uname = "root"
                info.gname = "root"
                info.mtime = 0
                archive.addfile(info, io.BytesIO(payload))
    payload = raw.getvalue()
    if not 1 <= len(payload) <= MAX_BUNDLE_BYTES:
        raise ReceiverBundleError("receiver bootstrap bundle exceeds its fixed size bound")
    return payload


def bundle_digest(
    *, signing_public_key: Path, source_revision: str | None = None
) -> tuple[str, int]:
    """Return the exact digest/size of the bundle that would be created."""

    payload = render_bundle(signing_public_key=signing_public_key, source_revision=source_revision)
    return hashlib.sha256(payload).hexdigest(), len(payload)


def build_bundle(
    *, signing_public_key: Path, output: Path, source_revision: str | None = None
) -> tuple[str, int]:
    """Build one deterministic gzip tar and return its ciphertext-free digest."""

    payload = render_bundle(signing_public_key=signing_public_key, source_revision=source_revision)
    _write_create_only(output, payload)
    return hashlib.sha256(payload).hexdigest(), len(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signing-public-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        _require_isolated_cli()
        digest, size = build_bundle(
            signing_public_key=args.signing_public_key, output=args.output
        )
    except ReceiverBundleError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps({"status": "built-local-only", "sha256": digest, "bytes": size, "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
