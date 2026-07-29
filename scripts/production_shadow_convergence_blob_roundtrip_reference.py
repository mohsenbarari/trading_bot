#!/usr/bin/env python3
"""Create-only local references for redacted blob-roundtrip observations.

This deliberately publishes only one already-reduced observation into the
digest-addressed, root-only incoming location.  It has no Object Storage,
network, SSH, Docker, source-set, or convergence-gate dependency, and cannot
make a source set or gate ready.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Mapping

from scripts import production_shadow_convergence_blob_roundtrip as BLOB


OBSERVATION_LABEL = "blob_roundtrip"
REFERENCE_FIELDS = frozenset({"path", "sha256"})
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
MAX_OBSERVATION_BYTES = BLOB.MAX_PROOF_BYTES
_PATH_COMPONENTS = (
    "convergence-gate",
    "observation-inputs",
    "incoming",
    "pure-observations",
)


class BlobRoundtripObservationReferenceError(RuntimeError):
    """The local immutable observation reference is unavailable or unsafe."""


@dataclass(frozen=True)
class BlobRoundtripObservationReference:
    """Path and digest of the one create-only, redacted local observation."""

    path: Path
    sha256: str


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise BlobRoundtripObservationReferenceError("blob observation has duplicate JSON fields")
        document[key] = value
    return document


def _canonical_payload(document: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                dict(document),
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise BlobRoundtripObservationReferenceError("blob observation is not canonical JSON") from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_root() -> None:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise BlobRoundtripObservationReferenceError("blob observation publication requires root:root")


def _root(value: Path) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise BlobRoundtripObservationReferenceError("controller evidence root is invalid")
    return value


def _digest(value: Any) -> str:
    try:
        return BLOB._nonzero_sha256(value, label="blob observation reference")  # noqa: SLF001
    except BLOB.BlobRoundtripContractError as exc:
        raise BlobRoundtripObservationReferenceError("blob observation reference digest is invalid") from exc


def canonical_observation_path(*, evidence_root: Path, sha256: str) -> Path:
    """Return the sole digest-addressed local input path for blob evidence."""

    return _root(evidence_root).joinpath(*_PATH_COMPONENTS, f"{OBSERVATION_LABEL}.{_digest(sha256)}.json")


def reference_document(reference: BlobRoundtripObservationReference) -> dict[str, str]:
    if not isinstance(reference, BlobRoundtripObservationReference):
        raise BlobRoundtripObservationReferenceError("blob observation reference is invalid")
    return {"path": str(reference.path), "sha256": _digest(reference.sha256)}


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _assert_private_directory(descriptor: int, *, label: str) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != DIRECTORY_MODE
    ):
        raise BlobRoundtripObservationReferenceError(f"{label} is not root:root mode 0700")


def _open_private_root(root: Path) -> int:
    try:
        descriptor = os.open(root, _directory_flags())
    except OSError as exc:
        raise BlobRoundtripObservationReferenceError("controller evidence root is unavailable") from exc
    try:
        _assert_private_directory(descriptor, label="controller evidence root")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _walk_output_directory(root: Path, *, create: bool) -> None:
    descriptor = _open_private_root(root)
    try:
        for component in _PATH_COMPONENTS:
            if create:
                try:
                    os.mkdir(component, DIRECTORY_MODE, dir_fd=descriptor)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise BlobRoundtripObservationReferenceError(
                        "blob observation directory cannot be created"
                    ) from exc
            try:
                child = os.open(component, _directory_flags(), dir_fd=descriptor)
            except OSError as exc:
                raise BlobRoundtripObservationReferenceError(
                    "blob observation directory is unavailable"
                ) from exc
            try:
                _assert_private_directory(child, label="blob observation directory")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def _assert_private_file(metadata: os.stat_result, *, label: str) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != FILE_MODE
        or not 1 <= metadata.st_size <= MAX_OBSERVATION_BYTES
    ):
        raise BlobRoundtripObservationReferenceError(f"{label} is not a root-only observation file")


def _read_private_bytes(path: Path, *, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BlobRoundtripObservationReferenceError(f"{label} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        _assert_private_file(before, label=label)
        chunks: list[bytes] = []
        remaining = MAX_OBSERVATION_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_OBSERVATION_BYTES:
            raise BlobRoundtripObservationReferenceError(f"{label} exceeds the approved size")
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise BlobRoundtripObservationReferenceError(f"{label} changed while being read")
        return payload
    finally:
        os.close(descriptor)


def _write_new(path: Path, payload: bytes) -> None:
    if not 1 <= len(payload) <= MAX_OBSERVATION_BYTES:
        raise BlobRoundtripObservationReferenceError("blob observation payload is invalid or oversized")
    try:
        directory = os.open(path.parent, _directory_flags())
    except OSError as exc:
        raise BlobRoundtripObservationReferenceError("blob observation directory is unavailable") from exc
    temporary_fd = -1
    temporary_name = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    try:
        _assert_private_directory(directory, label="blob observation directory")
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            FILE_MODE,
            dir_fd=directory,
        )
        view = memoryview(payload)
        while view:
            written = os.write(temporary_fd, view)
            if written <= 0:
                raise BlobRoundtripObservationReferenceError("blob observation write made no progress")
            view = view[written:]
        os.fchmod(temporary_fd, FILE_MODE)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = -1
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise BlobRoundtripObservationReferenceError("blob observation path already exists") from exc
        os.unlink(temporary_name, dir_fd=directory)
        os.fsync(directory)
    except OSError as exc:
        raise BlobRoundtripObservationReferenceError("blob observation cannot be published safely") from exc
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def _decode(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BlobRoundtripObservationReferenceError(f"{label} is not canonical JSON") from exc
    if not isinstance(document, dict) or payload != _canonical_payload(document):
        raise BlobRoundtripObservationReferenceError(f"{label} is not canonical JSON")
    return document


def validate_reference(
    reference: Mapping[str, Any], *, evidence_root: Path, identity: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    """Securely re-read and validate one installed immutable observation."""

    _require_root()
    root = _root(evidence_root)
    if not isinstance(reference, Mapping) or set(reference) != REFERENCE_FIELDS:
        raise BlobRoundtripObservationReferenceError("blob observation reference fields differ")
    digest = _digest(reference.get("sha256"))
    path = canonical_observation_path(evidence_root=root, sha256=digest)
    if reference.get("path") != str(path):
        raise BlobRoundtripObservationReferenceError("blob observation reference path differs")
    _walk_output_directory(root, create=False)
    payload = _read_private_bytes(path, label="installed blob observation")
    if _sha256(payload) != digest:
        raise BlobRoundtripObservationReferenceError("installed blob observation digest differs")
    document = _decode(payload, label="installed blob observation")
    try:
        checked = BLOB.validate_observation(document, identity=identity, now=now)
    except BLOB.BlobRoundtripContractError as exc:
        raise BlobRoundtripObservationReferenceError("installed blob observation is invalid") from exc
    if checked != document:
        raise BlobRoundtripObservationReferenceError("installed blob observation normalization differs")
    return document


def install_observation(
    observation: Mapping[str, Any], *, evidence_root: Path, identity: Mapping[str, Any], now: datetime
) -> tuple[BlobRoundtripObservationReference, str]:
    """Create-only publish and immediately revalidate one redacted observation."""

    _require_root()
    root = _root(evidence_root)
    try:
        document = BLOB.validate_observation(observation, identity=identity, now=now)
    except BLOB.BlobRoundtripContractError as exc:
        raise BlobRoundtripObservationReferenceError("blob observation is invalid") from exc
    if document != dict(observation):
        raise BlobRoundtripObservationReferenceError("blob observation normalization differs")
    payload = _canonical_payload(document)
    digest = _sha256(payload)
    path = canonical_observation_path(evidence_root=root, sha256=digest)
    _walk_output_directory(root, create=True)
    try:
        _write_new(path, payload)
        outcome = "created"
    except BlobRoundtripObservationReferenceError:
        existing = _read_private_bytes(path, label="existing blob observation")
        if existing != payload:
            raise BlobRoundtripObservationReferenceError(
                "existing blob observation differs and will not be replaced"
            )
        outcome = "reused"
    reference = BlobRoundtripObservationReference(path=path, sha256=digest)
    if validate_reference(reference_document(reference), evidence_root=root, identity=identity, now=now) != document:
        raise BlobRoundtripObservationReferenceError("blob observation readback differs")
    return reference, outcome
