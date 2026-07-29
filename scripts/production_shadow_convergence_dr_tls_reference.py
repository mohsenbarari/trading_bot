#!/usr/bin/env python3
"""Root-only create-only local publication for redacted DR TLS observations.

This module is deliberately limited to the ``dr_tls`` observation contract.
It never contacts a runtime or imports the convergence gate/source-set layers.
The digest-addressed output path matches the canonical convergence-gate input
location, and every publication is immediately reopened and revalidated.
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

from scripts import production_shadow_convergence_dr_tls as DR_TLS


REFERENCE_FIELDS = frozenset({"path", "sha256"})
OUTPUT_DIRECTORY_MODE = 0o700
OUTPUT_FILE_MODE = 0o600
MAX_OBSERVATION_BYTES = DR_TLS.MAX_PROOF_BYTES
_PATH_COMPONENTS = (
    "convergence-gate",
    "observation-inputs",
    "incoming",
    "pure-observations",
)


class DrTlsObservationReferenceError(RuntimeError):
    """A local DR TLS observation publication or readback is unsafe."""


@dataclass(frozen=True)
class DrTlsObservationReference:
    path: Path
    sha256: str


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise DrTlsObservationReferenceError("DR TLS observation is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DrTlsObservationReferenceError("DR TLS observation has duplicate JSON fields")
        result[key] = value
    return result


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_root() -> None:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise DrTlsObservationReferenceError("DR TLS observation publication requires root:root")


def _validate_evidence_root(value: Path) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise DrTlsObservationReferenceError("controller evidence root is invalid")
    return value


def _validate_digest(value: Any) -> str:
    try:
        return DR_TLS._nonzero_sha256(value, label="DR TLS observation reference")  # noqa: SLF001
    except DR_TLS.DrTlsContractError as exc:
        raise DrTlsObservationReferenceError("DR TLS observation reference digest is invalid") from exc


def canonical_observation_path(*, evidence_root: Path, sha256: str) -> Path:
    """Return the sole canonical convergence-gate input path for ``dr_tls``."""

    root = _validate_evidence_root(evidence_root)
    digest = _validate_digest(sha256)
    return root.joinpath(*_PATH_COMPONENTS, f"dr_tls.{digest}.json")


def reference_document(reference: DrTlsObservationReference) -> dict[str, str]:
    if not isinstance(reference, DrTlsObservationReference):
        raise DrTlsObservationReferenceError("DR TLS observation reference is invalid")
    return {"path": str(reference.path), "sha256": _validate_digest(reference.sha256)}


def _assert_private_directory(descriptor: int, *, label: str) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise DrTlsObservationReferenceError(f"{label} is not a root-only directory")


def _open_private_root(root: Path) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise DrTlsObservationReferenceError("controller evidence root is unavailable") from exc
    try:
        _assert_private_directory(descriptor, label="controller evidence root")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_or_create_private_output(root: Path) -> None:
    descriptor = _open_private_root(root)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        for component in _PATH_COMPONENTS:
            try:
                os.mkdir(component, OUTPUT_DIRECTORY_MODE, dir_fd=descriptor)
            except FileExistsError:
                pass
            except OSError as exc:
                raise DrTlsObservationReferenceError(
                    "DR TLS observation directory cannot be created"
                ) from exc
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except OSError as exc:
                raise DrTlsObservationReferenceError(
                    "DR TLS observation directory is unavailable"
                ) from exc
            try:
                _assert_private_directory(child, label="DR TLS observation directory")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def _assert_private_output_exists(root: Path) -> None:
    descriptor = _open_private_root(root)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        for component in _PATH_COMPONENTS:
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except OSError as exc:
                raise DrTlsObservationReferenceError(
                    "DR TLS observation directory is unavailable"
                ) from exc
            try:
                _assert_private_directory(child, label="DR TLS observation directory")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def _assert_private_regular_file(metadata: os.stat_result, *, label: str) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != OUTPUT_FILE_MODE
        or metadata.st_size < 1
        or metadata.st_size > MAX_OBSERVATION_BYTES
    ):
        raise DrTlsObservationReferenceError(f"{label} is not a root-only observation file")


def _read_private_bytes(path: Path, *, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DrTlsObservationReferenceError(f"{label} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        _assert_private_regular_file(before, label=label)
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
            raise DrTlsObservationReferenceError(f"{label} exceeds the approved size")
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise DrTlsObservationReferenceError(f"{label} changed while being read")
        return payload
    finally:
        os.close(descriptor)


def _write_private_new_bytes(path: Path, payload: bytes) -> None:
    if not 1 <= len(payload) <= MAX_OBSERVATION_BYTES:
        raise DrTlsObservationReferenceError("DR TLS observation payload is invalid or oversized")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        directory = os.open(path.parent, flags)
    except OSError as exc:
        raise DrTlsObservationReferenceError("DR TLS observation directory is unavailable") from exc
    temporary_fd = -1
    temporary_name = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    published = False
    try:
        _assert_private_directory(directory, label="DR TLS observation directory")
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            OUTPUT_FILE_MODE,
            dir_fd=directory,
        )
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(temporary_fd, view[offset:])
            if written <= 0:
                raise DrTlsObservationReferenceError("DR TLS observation write made no progress")
            offset += written
        os.fchmod(temporary_fd, OUTPUT_FILE_MODE)
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
            raise DrTlsObservationReferenceError("DR TLS observation path already exists") from exc
        published = True
        os.unlink(temporary_name, dir_fd=directory)
        os.fsync(directory)
    except OSError as exc:
        raise DrTlsObservationReferenceError("DR TLS observation cannot be published safely") from exc
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)
    if not published:
        raise DrTlsObservationReferenceError("DR TLS observation was not published")


def _decode_payload(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DrTlsObservationReferenceError(f"{label} is not canonical JSON") from exc
    if not isinstance(document, dict) or payload != _canonical_json(document) + b"\n":
        raise DrTlsObservationReferenceError(f"{label} is not canonical JSON")
    return document


def validate_reference(
    reference: Mapping[str, Any],
    *,
    evidence_root: Path,
    identity: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Securely read and validate one installed DR TLS observation reference."""

    _require_root()
    root = _validate_evidence_root(evidence_root)
    if not isinstance(reference, Mapping) or set(reference) != REFERENCE_FIELDS:
        raise DrTlsObservationReferenceError("DR TLS observation reference fields differ")
    digest = _validate_digest(reference.get("sha256"))
    path = canonical_observation_path(evidence_root=root, sha256=digest)
    if reference.get("path") != str(path):
        raise DrTlsObservationReferenceError("DR TLS observation reference path differs")
    _assert_private_output_exists(root)
    payload = _read_private_bytes(path, label="installed DR TLS observation")
    if _sha256(payload) != digest:
        raise DrTlsObservationReferenceError("installed DR TLS observation digest differs")
    document = _decode_payload(payload, label="installed DR TLS observation")
    try:
        checked = DR_TLS.validate_observation(document, identity=identity, now=now)
    except DR_TLS.DrTlsContractError as exc:
        raise DrTlsObservationReferenceError("installed DR TLS observation is invalid") from exc
    if checked != document:
        raise DrTlsObservationReferenceError("installed DR TLS observation normalization differs")
    return document


def install_observation(
    observation: Mapping[str, Any],
    *,
    evidence_root: Path,
    identity: Mapping[str, Any],
    now: datetime,
) -> tuple[DrTlsObservationReference, str]:
    """Create and read back one canonical redacted ``dr_tls`` observation."""

    _require_root()
    root = _validate_evidence_root(evidence_root)
    try:
        document = DR_TLS.validate_observation(observation, identity=identity, now=now)
    except DR_TLS.DrTlsContractError as exc:
        raise DrTlsObservationReferenceError("DR TLS observation is invalid") from exc
    if document != dict(observation):
        raise DrTlsObservationReferenceError("DR TLS observation normalization differs")
    payload = _canonical_json(document) + b"\n"
    digest = _sha256(payload)
    path = canonical_observation_path(evidence_root=root, sha256=digest)
    _open_or_create_private_output(root)
    try:
        _write_private_new_bytes(path, payload)
        outcome = "created"
    except DrTlsObservationReferenceError as exc:
        try:
            existing = _read_private_bytes(path, label="existing DR TLS observation")
        except DrTlsObservationReferenceError as read_exc:
            raise DrTlsObservationReferenceError("DR TLS observation cannot be published safely") from read_exc
        if existing != payload:
            raise DrTlsObservationReferenceError(
                "existing DR TLS observation differs and will not be replaced"
            ) from exc
        outcome = "reused"
    reference = DrTlsObservationReference(path=path, sha256=digest)
    readback = validate_reference(
        reference_document(reference),
        evidence_root=root,
        identity=identity,
        now=now,
    )
    if readback != document:
        raise DrTlsObservationReferenceError("DR TLS observation readback differs")
    return reference, outcome
