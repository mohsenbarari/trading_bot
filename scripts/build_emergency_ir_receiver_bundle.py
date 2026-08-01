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
import stat
import sys
import tarfile
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
# This tool is deliberately supported as ``python3 scripts/...py`` as well as
# ``python3 -m scripts...``.  The former puts ``scripts/`` rather than the
# repository root on sys.path, so establish the bounded local import root
# before loading the manifest verifier.  The bootstrap never relies on a
# caller-provided PYTHONPATH.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import emergency_ir_object_storage_manifest as manifest


MAX_MEMBER_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 4 * 1024 * 1024
BUNDLE_MEMBERS = (
    ("deploy/emergency-ir/run_object_storage_receiver.py", "run_receiver.py"),
    ("scripts/emergency_ir_object_storage_manifest.py", "scripts/emergency_ir_object_storage_manifest.py"),
    ("scripts/emergency_ir_object_storage_receiver.py", "scripts/emergency_ir_object_storage_receiver.py"),
)


class ReceiverBundleError(RuntimeError):
    pass


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


def build_bundle(*, repo: Path, signing_public_key: Path, output: Path) -> tuple[str, int]:
    """Build one deterministic gzip tar and return its ciphertext-free digest."""

    try:
        manifest.load_public_key(signing_public_key)
    except Exception as exc:
        raise ReceiverBundleError("Emergency signing public key is unavailable or invalid") from exc
    files: list[tuple[str, bytes]] = [
        (target, _read_regular(repo / source, label=f"receiver bundle {source}", maximum_bytes=MAX_MEMBER_BYTES))
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
    _write_create_only(output, payload)
    return hashlib.sha256(payload).hexdigest(), len(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--signing-public-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        digest, size = build_bundle(
            repo=args.repo.resolve(), signing_public_key=args.signing_public_key, output=args.output
        )
    except ReceiverBundleError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps({"status": "built-local-only", "sha256": digest, "bytes": size, "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
