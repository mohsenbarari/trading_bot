#!/usr/bin/env python3
"""Private create-only references for reduced queue-state observations.

This module is intentionally a narrow local publication boundary.  It accepts
one already-reduced, redacted ``queue_state`` observation, writes it only to
the digest-addressed pure-observation input location, and immediately reopens
and validates it.  It does not contact a database, Redis, Docker, a network,
or any convergence/source-set readiness path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping

from core.secure_file_io import SecureFileError, write_secure_new_bytes
from scripts import production_shadow_queue_state_observation as QUEUE


OBSERVATION_LABEL = "queue_state"
REFERENCE_FIELDS = frozenset({"path", "sha256"})
MAX_OBSERVATION_BYTES = 256 * 1024
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
_PATH_COMPONENTS = (
    "convergence-gate",
    "observation-inputs",
    "incoming",
    "pure-observations",
)


class QueueStateReferenceError(RuntimeError):
    """A queue-state observation reference is not private and stable."""


@dataclass(frozen=True)
class QueueStateObservationReference:
    """The sole accepted local reference to one queue-state observation."""

    path: Path
    sha256: str

    def document(self) -> dict[str, str]:
        return {"path": os.fspath(self.path), "sha256": self.sha256}


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise QueueStateReferenceError("queue-state JSON has duplicate fields")
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
        raise QueueStateReferenceError("queue-state observation is not canonical JSON") from exc


def _canonical_payload(document: Mapping[str, Any]) -> bytes:
    return _canonical_json(dict(document)) + b"\n"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    try:
        return QUEUE._sha256_nonzero(value, label=label)  # noqa: SLF001
    except QUEUE.QueueStateObservationError as exc:
        raise QueueStateReferenceError(f"{label} is invalid") from exc


def _absolute_path(value: Path | str, *, label: str) -> Path:
    try:
        path = Path(value)
    except TypeError as exc:
        raise QueueStateReferenceError(f"{label} path is invalid") from exc
    if not path.is_absolute() or ".." in path.parts or path != Path(os.path.abspath(path)):
        raise QueueStateReferenceError(f"{label} path is not canonical absolute")
    return path


def _controller_evidence_root(manifest: Mapping[str, Any]) -> Path:
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("deployment"), Mapping):
        raise QueueStateReferenceError("queue-state reference manifest is invalid")
    return _absolute_path(
        manifest["deployment"].get("controller_evidence_root"),
        label="controller evidence root",
    )


def _require_root() -> None:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise QueueStateReferenceError("queue-state reference requires root:root")


def _private_directory(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise QueueStateReferenceError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != DIRECTORY_MODE
    ):
        raise QueueStateReferenceError(f"{label} must be root:root mode 0700")


def _ensure_private_child(parent: Path, name: str, *, label: str) -> Path:
    _private_directory(parent, label=f"{label} parent")
    child = parent / name
    try:
        child.mkdir(mode=DIRECTORY_MODE)
    except FileExistsError:
        pass
    except OSError as exc:
        raise QueueStateReferenceError(f"{label} cannot be created") from exc
    _private_directory(child, label=label)
    return child


def _observation_root(manifest: Mapping[str, Any], *, create: bool) -> Path:
    root = _controller_evidence_root(manifest)
    if not create:
        _private_directory(root, label="controller evidence root")
        current = root
        for component in _PATH_COMPONENTS:
            current = current / component
            _private_directory(current, label=f"queue-state {component} root")
        return current
    current = root
    for component in _PATH_COMPONENTS:
        current = _ensure_private_child(
            current,
            component,
            label=f"queue-state {component} root",
        )
    return current


def canonical_queue_state_observation_path(
    manifest: Mapping[str, Any], *, digest: str
) -> Path:
    """Return the only digest-addressed pure-input path for queue evidence."""

    checked = _nonzero_sha256(digest, label="queue-state reference digest")
    return _controller_evidence_root(manifest).joinpath(
        *_PATH_COMPONENTS,
        f"{OBSERVATION_LABEL}.{checked}.json",
    )


def _reference(
    value: QueueStateObservationReference | Mapping[str, Any],
) -> QueueStateObservationReference:
    if isinstance(value, QueueStateObservationReference):
        return QueueStateObservationReference(
            _absolute_path(value.path, label="queue-state reference"),
            _nonzero_sha256(value.sha256, label="queue-state reference digest"),
        )
    if not isinstance(value, Mapping) or set(value) != REFERENCE_FIELDS:
        raise QueueStateReferenceError("queue-state reference fields differ")
    return QueueStateObservationReference(
        _absolute_path(value["path"], label="queue-state reference"),
        _nonzero_sha256(value["sha256"], label="queue-state reference digest"),
    )


def _read_private_payload(path: Path, *, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise QueueStateReferenceError(f"{label} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != FILE_MODE
            or before.st_size < 1
            or before.st_size > MAX_OBSERVATION_BYTES
        ):
            raise QueueStateReferenceError(f"{label} is not a root-only observation file")
        chunks: list[bytes] = []
        remaining = MAX_OBSERVATION_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_OBSERVATION_BYTES:
            raise QueueStateReferenceError(f"{label} exceeds its size limit")
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise QueueStateReferenceError(f"{label} changed while being read")
        return payload
    finally:
        os.close(descriptor)


def _decode_canonical_payload(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QueueStateReferenceError(f"{label} is not strict ASCII JSON") from exc
    if not isinstance(document, dict) or payload != _canonical_payload(document):
        raise QueueStateReferenceError(f"{label} is not canonical JSON")
    return document


def validate_queue_state_observation_reference(
    reference: QueueStateObservationReference | Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    identity: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Read and validate exactly one installed reduced queue observation."""

    _require_root()
    checked = _reference(reference)
    expected = canonical_queue_state_observation_path(manifest, digest=checked.sha256)
    if checked.path != expected:
        raise QueueStateReferenceError("queue-state reference path differs")
    _observation_root(manifest, create=False)
    payload = _read_private_payload(checked.path, label="queue-state observation")
    if _sha256(payload) != checked.sha256:
        raise QueueStateReferenceError("queue-state reference digest differs")
    document = _decode_canonical_payload(payload, label="queue-state observation")
    try:
        validated = QUEUE.validate_published_queue_observation(
            document,
            identity=identity,
            now=now,
        )
    except QUEUE.QueueStateObservationError as exc:
        raise QueueStateReferenceError("queue-state observation validation differs") from exc
    if validated != document:
        raise QueueStateReferenceError("queue-state observation normalization differs")
    return document


def install_queue_state_observation(
    document: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    identity: Mapping[str, Any],
    now: datetime,
) -> QueueStateObservationReference:
    """Create-only publish a validated queue observation and revalidate it."""

    _require_root()
    try:
        validated = QUEUE.validate_published_queue_observation(
            document,
            identity=identity,
            now=now,
        )
    except QUEUE.QueueStateObservationError as exc:
        raise QueueStateReferenceError("queue-state observation validation differs") from exc
    if validated != document:
        raise QueueStateReferenceError("queue-state observation normalization differs")
    payload = _canonical_payload(validated)
    if len(payload) > MAX_OBSERVATION_BYTES:
        raise QueueStateReferenceError("queue-state observation exceeds its size limit")
    digest = _sha256(payload)
    root = _observation_root(manifest, create=True)
    path = root / f"{OBSERVATION_LABEL}.{digest}.json"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="queue-state observation",
            mode=FILE_MODE,
            max_size=MAX_OBSERVATION_BYTES,
        )
    except SecureFileError as exc:
        if not os.path.lexists(path):
            raise QueueStateReferenceError("queue-state observation cannot be published") from exc
        existing = _read_private_payload(path, label="existing queue-state observation")
        if existing != payload:
            raise QueueStateReferenceError("queue-state observation collision differs") from exc
    reference = QueueStateObservationReference(path=path, sha256=digest)
    readback = validate_queue_state_observation_reference(
        reference,
        manifest=manifest,
        identity=identity,
        now=now,
    )
    if readback != validated:
        raise QueueStateReferenceError("queue-state observation readback differs")
    return reference
