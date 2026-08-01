#!/usr/bin/env python3
"""Seal four already-validated Emergency IR artifacts without network I/O.

This build-host helper deliberately has no S3, SSH, Docker, DNS, or deployment
surface.  It reads a fixed set of local plaintext artifacts through stable,
owner-controlled descriptors, encrypts each one to the fixed WA-IR age
recipient, and creates the exact four-entry publish plan consumed by
``publish_emergency_ir_object_storage.py``.  Every output is create-only;
failed encryption leaves its ``.part`` file in the otherwise empty output
directory for forensic inspection rather than overwriting anything.
"""

from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, BinaryIO, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT_TEXT = str(REPO_ROOT)
sys.path[:] = [entry for entry in sys.path if entry != _REPO_ROOT_TEXT]
sys.path.insert(0, _REPO_ROOT_TEXT)

from scripts import emergency_ir_object_storage_manifest as manifest
from scripts import publish_emergency_ir_object_storage as publisher


SHA_RE = re.compile(r"^[a-f0-9]{40}$", re.ASCII)
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{10,200}$", re.ASCII)
AGE_HEADER = b"age-encryption.org/v1\n"
HASH_CHUNK_BYTES = 1024 * 1024
DEFAULT_AGE_BINARY = Path("/usr/bin/age")
WA_IR_AGE_RECIPIENT = "age1hxt7paq6kp3cr4ey6tp0ne2dpvmz7az9h7jh09vfr9gpsm30fa7qa8zmkt"
OUTPUT_FILENAMES = {
    "image_bundle": "images.tar.age",
    "package_tar": "package.tar.age",
    "snapshot": "snapshot.dump.age",
    "settings": "settings.tar.age",
}


class EmergencyArtifactSealError(RuntimeError):
    """A local, fail-closed artifact sealing precondition failed."""


@dataclasses.dataclass(frozen=True)
class FileDigest:
    path: Path
    sha256: str
    bytes: int


def _fail(message: str) -> None:
    raise EmergencyArtifactSealError(message)


def _canonical_created_at(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail("created_at must be canonical RFC3339 UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EmergencyArtifactSealError("created_at must be canonical RFC3339 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _fail("created_at must be canonical RFC3339 UTC")
    canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if canonical != value:
        _fail("created_at must be canonical RFC3339 UTC")
    return value


def _secure_directory(path: Path, *, label: str, empty: bool | None = None) -> None:
    try:
        state = path.lstat()
    except OSError as exc:
        raise EmergencyArtifactSealError(f"{label} cannot be inspected") from exc
    if (
        not path.is_absolute()
        or stat.S_ISLNK(state.st_mode)
        or not stat.S_ISDIR(state.st_mode)
        or state.st_uid != os.geteuid()
        or stat.S_IMODE(state.st_mode) & 0o022
    ):
        _fail(f"{label} must be an owner-controlled absolute directory")
    if empty is True:
        try:
            if any(path.iterdir()):
                _fail(f"{label} must be empty for create-only artifact sealing")
        except OSError as exc:
            raise EmergencyArtifactSealError(f"{label} cannot be inspected") from exc


def _open_stable_regular(path: Path, *, label: str) -> tuple[BinaryIO, os.stat_result]:
    if not path.is_absolute():
        _fail(f"{label} path must be absolute")
    try:
        before = path.lstat()
    except OSError as exc:
        raise EmergencyArtifactSealError(f"{label} cannot be inspected") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or not 1 <= before.st_size <= manifest.MAX_ARTIFACT_BYTES
    ):
        _fail(f"{label} must be one bounded owner-controlled regular file")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise EmergencyArtifactSealError(f"{label} cannot be opened") from exc
    opened = os.fstat(fd)
    fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
    if any(getattr(before, field) != getattr(opened, field) for field in fields):
        os.close(fd)
        _fail(f"{label} changed while being opened")
    return os.fdopen(fd, "rb", closefd=True), opened


def _digest_open_file(
    source: BinaryIO,
    opened: os.stat_result,
    *,
    path: Path,
    label: str,
) -> FileDigest:
    observed = hashlib.sha256()
    total = 0
    while True:
        chunk = source.read(HASH_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > manifest.MAX_ARTIFACT_BYTES:
            _fail(f"{label} exceeds the Emergency artifact size bound")
        observed.update(chunk)
    after = os.fstat(source.fileno())
    fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
    if total != opened.st_size or any(getattr(opened, field) != getattr(after, field) for field in fields):
        _fail(f"{label} changed while being read")
    try:
        source.seek(0)
    except OSError as exc:
        raise EmergencyArtifactSealError(f"{label} cannot be rewound") from exc
    return FileDigest(path=path, sha256=observed.hexdigest(), bytes=total)


def _write_plan_create_only(path: Path, payload: bytes) -> None:
    if not path.is_absolute():
        _fail("publish plan output must be absolute")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        offset = 0
        view = memoryview(payload)
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError("short output write")
            offset += written
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise EmergencyArtifactSealError("refusing to overwrite an existing Emergency publish plan") from exc
    except OSError as exc:
        raise EmergencyArtifactSealError("Emergency publish plan cannot be written") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _digest_path(path: Path, *, label: str) -> FileDigest:
    source, opened = _open_stable_regular(path, label=label)
    with source:
        return _digest_open_file(source, opened, path=path, label=label)


def _require_fixed_age_binary() -> Path:
    """Accept only the host's pinned, root-owned age executable.

    This is intentionally not a CLI parameter: a caller-selectable program
    could claim to create age ciphertext while copying plaintext elsewhere.
    """

    try:
        state = DEFAULT_AGE_BINARY.lstat()
    except OSError as exc:
        raise EmergencyArtifactSealError("pinned age binary cannot be inspected") from exc
    if (
        stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or state.st_uid != 0
        or stat.S_IMODE(state.st_mode) & 0o022
        or not bool(state.st_mode & stat.S_IXUSR)
        or state.st_size < 1
    ):
        _fail("pinned age binary is not one root-owned non-writable executable")
    return DEFAULT_AGE_BINARY


def _finalize_create_only(*, temporary: Path, output_path: Path, label: str) -> None:
    """Atomically publish a same-directory temporary without replacement."""

    try:
        os.link(temporary, output_path, follow_symlinks=False)
    except FileExistsError as exc:
        raise EmergencyArtifactSealError(f"refusing to overwrite existing {label} ciphertext") from exc
    except OSError as exc:
        raise EmergencyArtifactSealError(f"{label} ciphertext cannot be finalized") from exc
    directory_fd: int | None = None
    try:
        directory_fd = os.open(output_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        os.fsync(directory_fd)
        os.unlink(temporary)
        os.fsync(directory_fd)
    except OSError as exc:
        raise EmergencyArtifactSealError(f"{label} ciphertext finalization could not be completed") from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def _seal_one(
    *,
    source_path: Path,
    output_path: Path,
    recipient: str,
    age_binary: Path,
    label: str,
) -> tuple[FileDigest, FileDigest]:
    """Encrypt one stable descriptor to a create-only output through stdin/stdout."""

    if output_path.exists() or output_path.is_symlink():
        _fail(f"refusing to overwrite existing {label} ciphertext")
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.part")
    if temporary.exists() or temporary.is_symlink():
        _fail(f"refusing to overwrite existing partial {label} ciphertext")
    source, opened = _open_stable_regular(source_path, label=f"{label} plaintext")
    try:
        plaintext = _digest_open_file(
            source,
            opened,
            path=source_path,
            label=f"{label} plaintext",
        )
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                try:
                    completed = subprocess.run(
                        [str(age_binary), "-r", recipient],
                        stdin=source,
                        stdout=output,
                        stderr=subprocess.PIPE,
                        check=False,
                        timeout=7200,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    raise EmergencyArtifactSealError(f"{label} age encryption failed") from exc
                output.flush()
                os.fsync(output.fileno())
        finally:
            descriptor = -1
        if completed.returncode != 0:
            _fail(f"{label} age encryption failed")
        after = os.fstat(source.fileno())
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(opened, field) != getattr(after, field) for field in fields):
            _fail(f"{label} plaintext changed during encryption")
    finally:
        source.close()
    ciphertext = _digest_path(temporary, label=f"{label} ciphertext")
    if ciphertext.bytes <= plaintext.bytes or ciphertext.sha256 == plaintext.sha256:
        _fail(f"{label} ciphertext does not satisfy the age artifact contract")
    with temporary.open("rb") as handle:
        if handle.read(len(AGE_HEADER)) != AGE_HEADER:
            _fail(f"{label} ciphertext is not an age-v1 stream")
    _finalize_create_only(temporary=temporary, output_path=output_path, label=label)
    return plaintext, FileDigest(path=output_path, sha256=ciphertext.sha256, bytes=ciphertext.bytes)


def _wa_ir_recipient_key_id() -> str:
    if AGE_RECIPIENT_RE.fullmatch(WA_IR_AGE_RECIPIENT) is None:
        _fail("pinned WA-IR age recipient is invalid")
    return "age-recipient-sha256:" + hashlib.sha256(WA_IR_AGE_RECIPIENT.encode("ascii")).hexdigest()


def build_publish_plan(
    *,
    campaign_id: str,
    bucket: str,
    prefix: str,
    created_at: str,
    recipient_key_id: str,
    artifacts: Mapping[str, tuple[FileDigest, FileDigest]],
) -> dict[str, Any]:
    """Return the strict publisher input, reusing its validation contract."""

    if set(artifacts) != set(manifest.ARTIFACT_ORDER):
        _fail("sealed artifacts must cover exactly the fixed Emergency set")
    values: list[dict[str, Any]] = []
    for kind in manifest.ARTIFACT_ORDER:
        plaintext, ciphertext = artifacts[kind]
        values.append(
            {
                "kind": kind,
                "ciphertext_path": str(ciphertext.path),
                "plaintext_sha256": plaintext.sha256,
                "plaintext_bytes": plaintext.bytes,
                "ciphertext_sha256": ciphertext.sha256,
                "ciphertext_bytes": ciphertext.bytes,
            }
        )
    plan = {
        "schema": publisher.PUBLISH_PLAN_SCHEMA,
        "campaign_id": campaign_id,
        "bucket": bucket,
        "prefix": prefix,
        "created_at": created_at,
        "destination_age_recipient_key_id": recipient_key_id,
        "artifacts": values,
    }
    # ``seal_artifacts`` writes this canonical value create-only and immediately
    # invokes the publisher's file loader.  Keeping this function pure makes
    # it impossible for a dry inspection to create an output file.
    return plan


def seal_artifacts(
    *,
    campaign_id: str,
    bucket: str,
    prefix: str,
    created_at: str | None,
    plaintext_paths: Mapping[str, Path],
    output_directory: Path,
) -> dict[str, Any]:
    """Seal exactly four ready plaintexts and emit a create-only publish plan."""

    if set(plaintext_paths) != set(manifest.ARTIFACT_ORDER):
        _fail("plaintext inputs must cover exactly the fixed Emergency set")
    _secure_directory(output_directory, label="Emergency artifact output directory", empty=True)
    age_binary = _require_fixed_age_binary()
    recipient_key_id = _wa_ir_recipient_key_id()
    timestamp = _canonical_created_at(created_at)
    sealed: dict[str, tuple[FileDigest, FileDigest]] = {}
    for kind in manifest.ARTIFACT_ORDER:
        sealed[kind] = _seal_one(
            source_path=plaintext_paths[kind],
            output_path=output_directory / OUTPUT_FILENAMES[kind],
            recipient=WA_IR_AGE_RECIPIENT,
            age_binary=age_binary,
            label=kind,
        )
    plan = build_publish_plan(
        campaign_id=campaign_id,
        bucket=bucket,
        prefix=prefix,
        created_at=timestamp,
        recipient_key_id=recipient_key_id,
        artifacts=sealed,
    )
    plan_path = output_directory / "publish-plan.json"
    _write_plan_create_only(plan_path, manifest.canonical_json_bytes(plan))
    # The publisher parses exactly this file before any external client is made.
    try:
        publisher.load_publish_plan(plan_path)
    except Exception as exc:
        raise EmergencyArtifactSealError("written Emergency publish plan failed local verification") from exc
    return {
        "status": "sealed-local-only",
        "campaign_id": campaign_id,
        "destination_age_recipient": WA_IR_AGE_RECIPIENT,
        "destination_age_recipient_key_id": recipient_key_id,
        "publish_plan": str(plan_path),
        "artifacts": [
            {
                "kind": kind,
                "plaintext_sha256": sealed[kind][0].sha256,
                "plaintext_bytes": sealed[kind][0].bytes,
                "ciphertext_sha256": sealed[kind][1].sha256,
                "ciphertext_bytes": sealed[kind][1].bytes,
            }
            for kind in manifest.ARTIFACT_ORDER
        ],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--created-at")
    parser.add_argument("--image-bundle", type=Path, required=True)
    parser.add_argument("--package-tar", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        if not sys.flags.isolated:
            _fail("Emergency artifact sealer must be launched with python3 -I -B")
        args = parse_args(argv)
        result = seal_artifacts(
            campaign_id=args.campaign_id,
            bucket=args.bucket,
            prefix=args.prefix,
            created_at=args.created_at,
            plaintext_paths={
                "image_bundle": args.image_bundle,
                "package_tar": args.package_tar,
                "snapshot": args.snapshot,
                "settings": args.settings,
            },
            output_directory=args.output_directory,
        )
    except EmergencyArtifactSealError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
