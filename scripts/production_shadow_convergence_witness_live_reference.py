#!/usr/bin/env python3
"""Root-only create-only publication for a redacted Witness-live observation.

This is deliberately an evidence installer, not a Witness client.  It neither
contacts a host nor changes the convergence gate/source-set state.  A caller
must supply an already validated, redacted signed-lease observation.  The
installer writes one digest-addressed immutable copy under the source-set's
canonical incoming location and immediately reopens it with the exact same
contract validation.
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

from scripts import production_shadow_convergence_witness_live as WITNESS


REFERENCE_FIELDS = frozenset({"path", "sha256"})
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
MAX_OBSERVATION_BYTES = WITNESS.MAX_INPUT_BYTES
_PATH_COMPONENTS = (
    "convergence-gate",
    "observation-inputs",
    "incoming",
    "pure-observations",
)


class WitnessLiveObservationReferenceError(RuntimeError):
    """A Witness-live observation cannot be installed or read safely."""


@dataclass(frozen=True)
class WitnessLiveObservationReference:
    """The only supported local pointer to a published Witness observation."""

    path: Path
    sha256: str


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise WitnessLiveObservationReferenceError("Witness observation has duplicate JSON fields")
        document[key] = value
    return document


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
        raise WitnessLiveObservationReferenceError("Witness observation is not canonical JSON") from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _digest(value: Any) -> str:
    try:
        return WITNESS._nonzero_sha256(value, label="Witness observation reference")  # noqa: SLF001
    except WITNESS.WitnessLiveContractError as exc:
        raise WitnessLiveObservationReferenceError("Witness observation reference digest is invalid") from exc


def _evidence_root(value: Path) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        raise WitnessLiveObservationReferenceError("controller evidence root is invalid")
    return value


def _require_root() -> None:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise WitnessLiveObservationReferenceError("Witness observation publication requires root:root")


def canonical_observation_path(*, evidence_root: Path, sha256: str) -> Path:
    """Return the one accepted digest-addressed source-set input path."""

    return _evidence_root(evidence_root).joinpath(
        *_PATH_COMPONENTS,
        f"witness_live.{_digest(sha256)}.json",
    )


def reference_document(reference: WitnessLiveObservationReference) -> dict[str, str]:
    if not isinstance(reference, WitnessLiveObservationReference):
        raise WitnessLiveObservationReferenceError("Witness observation reference is invalid")
    path = reference.path
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise WitnessLiveObservationReferenceError("Witness observation reference path is invalid")
    return {"path": str(path), "sha256": _digest(reference.sha256)}


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
        raise WitnessLiveObservationReferenceError(f"{label} is not root:root mode 0700")


def _open_private_root(root: Path) -> int:
    try:
        descriptor = os.open(root, _directory_flags())
    except OSError as exc:
        raise WitnessLiveObservationReferenceError("controller evidence root is unavailable") from exc
    try:
        _assert_private_directory(descriptor, label="controller evidence root")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_private_output(root: Path, *, create: bool) -> None:
    descriptor = _open_private_root(root)
    try:
        for component in _PATH_COMPONENTS:
            if create:
                try:
                    os.mkdir(component, DIRECTORY_MODE, dir_fd=descriptor)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise WitnessLiveObservationReferenceError(
                        "Witness observation directory cannot be created"
                    ) from exc
            try:
                child = os.open(component, _directory_flags(), dir_fd=descriptor)
            except OSError as exc:
                raise WitnessLiveObservationReferenceError(
                    "Witness observation directory is unavailable"
                ) from exc
            try:
                _assert_private_directory(child, label="Witness observation directory")
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
        or stat.S_IMODE(metadata.st_mode) != FILE_MODE
        or not 1 <= metadata.st_size <= MAX_OBSERVATION_BYTES
    ):
        raise WitnessLiveObservationReferenceError(f"{label} is not a root-only observation file")


def _read_private_bytes(path: Path, *, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise WitnessLiveObservationReferenceError(f"{label} is unavailable") from exc
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
            raise WitnessLiveObservationReferenceError(f"{label} exceeds its size limit")
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise WitnessLiveObservationReferenceError(f"{label} changed while being read")
        return payload
    finally:
        os.close(descriptor)


def _write_private_new_bytes(path: Path, payload: bytes) -> None:
    if not 1 <= len(payload) <= MAX_OBSERVATION_BYTES:
        raise WitnessLiveObservationReferenceError("Witness observation payload is invalid or oversized")
    try:
        directory = os.open(path.parent, _directory_flags())
    except OSError as exc:
        raise WitnessLiveObservationReferenceError("Witness observation directory is unavailable") from exc
    temporary_fd = -1
    temporary_name = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    published = False
    try:
        _assert_private_directory(directory, label="Witness observation directory")
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            FILE_MODE,
            dir_fd=directory,
        )
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(temporary_fd, view[offset:])
            if written <= 0:
                raise WitnessLiveObservationReferenceError("Witness observation write made no progress")
            offset += written
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
            raise WitnessLiveObservationReferenceError("Witness observation path already exists") from exc
        published = True
        os.unlink(temporary_name, dir_fd=directory)
        os.fsync(directory)
    except OSError as exc:
        raise WitnessLiveObservationReferenceError("Witness observation cannot be published safely") from exc
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        try:
            os.unlink(temporary_name, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)
    if not published:
        raise WitnessLiveObservationReferenceError("Witness observation was not published")


def _decode_payload(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WitnessLiveObservationReferenceError(f"{label} is not canonical JSON") from exc
    if not isinstance(document, dict) or payload != _canonical_json(document) + b"\n":
        raise WitnessLiveObservationReferenceError(f"{label} is not canonical JSON")
    return document


def validate_reference(
    reference: Mapping[str, Any],
    *,
    evidence_root: Path,
    identity: Mapping[str, Any],
    journal_started_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    """Securely reopen and validate exactly one installed Witness observation."""

    _require_root()
    root = _evidence_root(evidence_root)
    if not isinstance(reference, Mapping) or set(reference) != REFERENCE_FIELDS:
        raise WitnessLiveObservationReferenceError("Witness observation reference fields differ")
    digest = _digest(reference.get("sha256"))
    path = canonical_observation_path(evidence_root=root, sha256=digest)
    if reference.get("path") != str(path):
        raise WitnessLiveObservationReferenceError("Witness observation reference path differs")
    _open_private_output(root, create=False)
    payload = _read_private_bytes(path, label="installed Witness observation")
    if _sha256(payload) != digest:
        raise WitnessLiveObservationReferenceError("installed Witness observation digest differs")
    document = _decode_payload(payload, label="installed Witness observation")
    try:
        checked = WITNESS.validate_observation(
            document,
            identity=identity,
            journal_started_at=journal_started_at,
            now=now,
        )
    except WITNESS.WitnessLiveContractError as exc:
        raise WitnessLiveObservationReferenceError("installed Witness observation is invalid") from exc
    if checked != document:
        raise WitnessLiveObservationReferenceError("installed Witness observation normalization differs")
    return document


def install_observation(
    observation: Mapping[str, Any],
    *,
    evidence_root: Path,
    identity: Mapping[str, Any],
    journal_started_at: datetime,
    now: datetime,
) -> tuple[WitnessLiveObservationReference, str]:
    """Create and read back one canonical immutable Witness-live observation.

    The only idempotent case is byte-for-byte reuse of the same digest-addressed
    object.  This does not create an available source set or satisfy any gate.
    """

    _require_root()
    root = _evidence_root(evidence_root)
    try:
        document = WITNESS.validate_observation(
            observation,
            identity=identity,
            journal_started_at=journal_started_at,
            now=now,
        )
    except WITNESS.WitnessLiveContractError as exc:
        raise WitnessLiveObservationReferenceError("Witness observation is invalid") from exc
    if document != dict(observation):
        raise WitnessLiveObservationReferenceError("Witness observation normalization differs")
    payload = _canonical_json(document) + b"\n"
    digest = _sha256(payload)
    path = canonical_observation_path(evidence_root=root, sha256=digest)
    _open_private_output(root, create=True)
    try:
        _write_private_new_bytes(path, payload)
        outcome = "created"
    except WitnessLiveObservationReferenceError as exc:
        try:
            existing = _read_private_bytes(path, label="existing Witness observation")
        except WitnessLiveObservationReferenceError as read_exc:
            raise WitnessLiveObservationReferenceError("Witness observation cannot be published safely") from read_exc
        if existing != payload:
            raise WitnessLiveObservationReferenceError(
                "existing Witness observation differs and will not be replaced"
            ) from exc
        outcome = "reused"
    reference = WitnessLiveObservationReference(path=path, sha256=digest)
    readback = validate_reference(
        reference_document(reference),
        evidence_root=root,
        identity=identity,
        journal_started_at=journal_started_at,
        now=now,
    )
    if readback != document:
        raise WitnessLiveObservationReferenceError("Witness observation readback differs")
    return reference, outcome
