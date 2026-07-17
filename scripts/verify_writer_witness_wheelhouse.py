#!/usr/bin/env python3
"""Fail closed unless an offline Writer Witness wheelhouse matches its manifest."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Mapping, Sequence


MAXIMUM_MANIFEST_BYTES = 16 * 1024 * 1024
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
SAFE_WHEEL_BASENAME_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._+!-]*\.whl\Z", re.ASCII
)


class WheelhouseAttestationError(RuntimeError):
    """The wheelhouse cannot be proven to match its release-bound manifest."""


@dataclass(frozen=True)
class ManifestInventory:
    entries: Mapping[str, str]
    sha256: str


def _metadata_signature(metadata: os.stat_result) -> tuple[int, ...]:
    """Return security-relevant metadata, deliberately excluding access time."""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _assert_stable(
    before: os.stat_result, after: os.stat_result, *, label: str
) -> None:
    if _metadata_signature(before) != _metadata_signature(after):
        raise WheelhouseAttestationError(f"{label} changed during attestation")


def _validate_expected_uid(expected_uid: int | None) -> None:
    if expected_uid is not None and expected_uid < 0:
        raise WheelhouseAttestationError("expected owner uid must be non-negative")


def _validate_regular_file(
    metadata: os.stat_result, *, label: str, expected_uid: int | None
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise WheelhouseAttestationError(f"{label} is not a regular file")
    if metadata.st_nlink != 1:
        raise WheelhouseAttestationError(f"{label} must have exactly one hard link")
    if expected_uid is not None and metadata.st_uid != expected_uid:
        raise WheelhouseAttestationError(f"{label} has an unexpected owner")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise WheelhouseAttestationError(f"{label} is group/world writable")


def _open_secure_path(
    path: Path, *, label: str, expected_uid: int | None
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WheelhouseAttestationError(f"cannot safely open {label}") from exc
    try:
        metadata = os.fstat(descriptor)
        _validate_regular_file(metadata, label=label, expected_uid=expected_uid)
        return descriptor, metadata
    except Exception:
        os.close(descriptor)
        raise


def _open_secure_entry(
    directory_descriptor: int,
    basename: str,
    *,
    label: str,
    expected_uid: int | None,
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(basename, flags, dir_fd=directory_descriptor)
    except OSError as exc:
        raise WheelhouseAttestationError(f"cannot safely open {label}") from exc
    try:
        metadata = os.fstat(descriptor)
        _validate_regular_file(metadata, label=label, expected_uid=expected_uid)
        return descriptor, metadata
    except Exception:
        os.close(descriptor)
        raise


def _read_descriptor(
    descriptor: int,
    before: os.stat_result,
    *,
    label: str,
    maximum_bytes: int,
) -> bytes:
    if before.st_size > maximum_bytes:
        raise WheelhouseAttestationError(f"{label} exceeds its safe size limit")
    chunks: list[bytes] = []
    remaining = maximum_bytes + 1
    try:
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) > maximum_bytes:
            raise WheelhouseAttestationError(f"{label} exceeds its safe size limit")
        _assert_stable(before, os.fstat(descriptor), label=label)
        return value
    except WheelhouseAttestationError:
        raise
    except OSError as exc:
        raise WheelhouseAttestationError(f"cannot safely read {label}") from exc


def _hash_descriptor(
    descriptor: int, before: os.stat_result, *, label: str
) -> str:
    digest = hashlib.sha256()
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        _assert_stable(before, os.fstat(descriptor), label=label)
        return digest.hexdigest()
    except WheelhouseAttestationError:
        raise
    except OSError as exc:
        raise WheelhouseAttestationError(f"cannot safely hash {label}") from exc


def _read_manifest_open(
    path: Path, *, expected_uid: int | None
) -> tuple[int, os.stat_result, bytes]:
    descriptor, before = _open_secure_path(
        path, label="wheelhouse manifest", expected_uid=expected_uid
    )
    try:
        value = _read_descriptor(
            descriptor,
            before,
            label="wheelhouse manifest",
            maximum_bytes=MAXIMUM_MANIFEST_BYTES,
        )
        return descriptor, before, value
    except Exception:
        os.close(descriptor)
        raise


def _safe_wheel_basename(value: str) -> bool:
    return SAFE_WHEEL_BASENAME_PATTERN.fullmatch(value) is not None


def _parse_manifest(value: bytes) -> ManifestInventory:
    if not value:
        raise WheelhouseAttestationError("wheelhouse manifest is empty")
    if not value.endswith(b"\n") or value.endswith(b"\n\n"):
        raise WheelhouseAttestationError(
            "wheelhouse manifest must have exactly one final newline"
        )
    if b"\r" in value or value.startswith(b"\xef\xbb\xbf"):
        raise WheelhouseAttestationError(
            "wheelhouse manifest is not canonical UTF-8 text"
        )
    try:
        decoded = value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise WheelhouseAttestationError(
            "wheelhouse manifest is not canonical ASCII/UTF-8 text"
        ) from exc

    entries: dict[str, str] = {}
    ordered_basenames: list[str] = []
    for line_number, line in enumerate(decoded[:-1].split("\n"), start=1):
        if len(line) < 67 or line[64:66] != "  ":
            raise WheelhouseAttestationError(
                f"wheelhouse manifest line {line_number} is not canonical SHA256SUMS syntax"
            )
        digest = line[:64]
        basename = line[66:]
        if not SHA256_PATTERN.fullmatch(digest):
            raise WheelhouseAttestationError(
                f"wheelhouse manifest line {line_number} has an invalid SHA-256"
            )
        if not _safe_wheel_basename(basename):
            raise WheelhouseAttestationError(
                f"wheelhouse manifest line {line_number} has an unsafe wheel basename"
            )
        if basename in entries:
            raise WheelhouseAttestationError(
                "wheelhouse manifest contains a duplicate wheel basename"
            )
        entries[basename] = digest
        ordered_basenames.append(basename)

    if not entries:
        raise WheelhouseAttestationError("wheelhouse manifest contains no wheel entries")
    if ordered_basenames != sorted(ordered_basenames):
        raise WheelhouseAttestationError(
            "wheelhouse manifest entries are not sorted by wheel basename"
        )
    return ManifestInventory(
        entries=entries,
        sha256=hashlib.sha256(value).hexdigest(),
    )


def _open_wheelhouse_directory(
    path: Path, *, expected_uid: int | None
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WheelhouseAttestationError(
            "cannot safely open wheelhouse directory"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise WheelhouseAttestationError("wheelhouse root is not a directory")
        if expected_uid is not None and metadata.st_uid != expected_uid:
            raise WheelhouseAttestationError("wheelhouse directory has an unexpected owner")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise WheelhouseAttestationError(
                "wheelhouse directory is group/world writable"
            )
        return descriptor, metadata
    except Exception:
        os.close(descriptor)
        raise


def _scan_directory(
    directory_descriptor: int,
) -> dict[str, tuple[int, ...]]:
    try:
        basenames = os.listdir(directory_descriptor)
    except OSError as exc:
        raise WheelhouseAttestationError("cannot safely enumerate wheelhouse") from exc

    result: dict[str, tuple[int, ...]] = {}
    for basename in basenames:
        if not isinstance(basename, str):
            raise WheelhouseAttestationError(
                "wheelhouse contains a non-canonical directory entry"
            )
        try:
            basename.encode("ascii")
        except UnicodeEncodeError as exc:
            raise WheelhouseAttestationError(
                "wheelhouse contains a non-canonical directory entry"
            ) from exc
        try:
            metadata = os.stat(
                basename, dir_fd=directory_descriptor, follow_symlinks=False
            )
        except OSError as exc:
            raise WheelhouseAttestationError(
                "wheelhouse changed during directory enumeration"
            ) from exc
        result[basename] = _metadata_signature(metadata)
    return result


def _mode_from_signature(signature: tuple[int, ...]) -> int:
    return signature[2]


def _validate_scanned_regular(
    signature: tuple[int, ...], *, label: str, expected_uid: int | None
) -> None:
    mode = _mode_from_signature(signature)
    if stat.S_ISLNK(mode):
        raise WheelhouseAttestationError(f"wheelhouse contains a symlink: {label}")
    if stat.S_ISDIR(mode):
        raise WheelhouseAttestationError(f"wheelhouse contains a subdirectory: {label}")
    if not stat.S_ISREG(mode):
        raise WheelhouseAttestationError(f"wheelhouse contains a special node: {label}")
    if signature[3] != 1:
        raise WheelhouseAttestationError(f"{label} must have exactly one hard link")
    if expected_uid is not None and signature[4] != expected_uid:
        raise WheelhouseAttestationError(f"{label} has an unexpected owner")
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise WheelhouseAttestationError(f"{label} is group/world writable")


def _aggregate_digest(entries: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    digest.update(b"writer-witness-wheelhouse-v1\0")
    for basename in sorted(entries):
        digest.update(basename.encode("ascii"))
        digest.update(b"\0")
        digest.update(entries[basename].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _attest_wheelhouse(
    wheelhouse: Path, manifest: Path, *, expected_uid: int | None = 0
) -> dict[str, object]:
    _validate_expected_uid(expected_uid)
    manifest_descriptor, manifest_before, manifest_bytes = _read_manifest_open(
        manifest, expected_uid=expected_uid
    )
    try:
        inventory = _parse_manifest(manifest_bytes)
        directory_descriptor, directory_before = _open_wheelhouse_directory(
            wheelhouse, expected_uid=expected_uid
        )
        try:
            scanned_before = _scan_directory(directory_descriptor)
            manifest_identity = (manifest_before.st_dev, manifest_before.st_ino)
            internal_manifest_names = [
                basename
                for basename, signature in scanned_before.items()
                if (signature[0], signature[1]) == manifest_identity
            ]
            if len(internal_manifest_names) > 1:
                raise WheelhouseAttestationError(
                    "wheelhouse manifest has multiple directory entries"
                )
            internal_manifest_name = (
                internal_manifest_names[0] if internal_manifest_names else None
            )

            observed_wheels: set[str] = set()
            for basename, signature in scanned_before.items():
                if basename.startswith("."):
                    raise WheelhouseAttestationError(
                        "wheelhouse contains a hidden directory entry"
                    )
                label = (
                    "wheelhouse manifest"
                    if basename == internal_manifest_name
                    else "wheelhouse entry"
                )
                _validate_scanned_regular(
                    signature, label=label, expected_uid=expected_uid
                )
                if basename == internal_manifest_name:
                    if signature != _metadata_signature(manifest_before):
                        raise WheelhouseAttestationError(
                            "wheelhouse manifest changed before directory attestation"
                        )
                    continue
                if not _safe_wheel_basename(basename):
                    raise WheelhouseAttestationError(
                        "wheelhouse contains an unmanifested non-wheel file"
                    )
                observed_wheels.add(basename)

            expected_wheels = set(inventory.entries)
            missing = sorted(expected_wheels - observed_wheels)
            extra = sorted(observed_wheels - expected_wheels)
            if missing:
                raise WheelhouseAttestationError(
                    "wheelhouse is missing one or more manifested wheels"
                )
            if extra:
                raise WheelhouseAttestationError(
                    "wheelhouse contains one or more unmanifested wheels"
                )

            for basename in sorted(inventory.entries):
                descriptor, before = _open_secure_entry(
                    directory_descriptor,
                    basename,
                    label="wheelhouse wheel",
                    expected_uid=expected_uid,
                )
                try:
                    if _metadata_signature(before) != scanned_before[basename]:
                        raise WheelhouseAttestationError(
                            "wheelhouse wheel changed before hashing"
                        )
                    observed_digest = _hash_descriptor(
                        descriptor, before, label="wheelhouse wheel"
                    )
                finally:
                    os.close(descriptor)
                if observed_digest != inventory.entries[basename]:
                    raise WheelhouseAttestationError(
                        "wheelhouse wheel digest does not match its manifest"
                    )

            _assert_stable(
                manifest_before,
                os.fstat(manifest_descriptor),
                label="wheelhouse manifest",
            )
            scanned_after = _scan_directory(directory_descriptor)
            if scanned_after != scanned_before:
                raise WheelhouseAttestationError(
                    "wheelhouse directory entries changed during attestation"
                )
            _assert_stable(
                directory_before,
                os.fstat(directory_descriptor),
                label="wheelhouse directory",
            )
        finally:
            os.close(directory_descriptor)
    finally:
        os.close(manifest_descriptor)

    return {
        "aggregate_sha256": _aggregate_digest(inventory.entries),
        "file_count": len(inventory.entries),
        "manifest_sha256": inventory.sha256,
        "wheelhouse_attested": "yes",
    }


def attest_wheelhouse(
    wheelhouse: Path, manifest: Path, *, expected_uid: int | None = 0
) -> dict[str, object]:
    """Attest a wheelhouse while converting raw operating-system failures safely."""

    try:
        return _attest_wheelhouse(
            wheelhouse, manifest, expected_uid=expected_uid
        )
    except WheelhouseAttestationError:
        raise
    except OSError as exc:
        raise WheelhouseAttestationError(
            "operating-system failure during wheelhouse attestation"
        ) from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-uid", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = attest_wheelhouse(
            args.wheelhouse, args.manifest, expected_uid=args.expected_uid
        )
    except WheelhouseAttestationError as exc:
        print(f"wheelhouse attestation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
