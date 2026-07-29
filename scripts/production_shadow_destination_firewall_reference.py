#!/usr/bin/env python3
"""Private create-only references for destination-firewall observations.

This is intentionally narrower than the convergence gate.  It can install
and re-read one already-reduced, redacted ``destination_firewall`` observation
at the gate's canonical observation path.  It neither assembles a source set
nor contacts a provider, host, container runtime, or network.
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
from scripts import production_shadow_destination_firewall_observation as FIREWALL


OBSERVATION_LABEL = "destination_firewall"
REFERENCE_FIELDS = frozenset({"path", "sha256"})
MAX_OBSERVATION_BYTES = 256 * 1024
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600


class DestinationFirewallReferenceError(RuntimeError):
    """A firewall observation reference cannot be proven private and stable."""


@dataclass(frozen=True)
class DestinationFirewallObservationReference:
    """The sole accepted local reference to one published observation."""

    path: Path
    sha256: str

    def document(self) -> dict[str, str]:
        return {"path": os.fspath(self.path), "sha256": self.sha256}


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise DestinationFirewallReferenceError("observation JSON has duplicate fields")
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
        raise DestinationFirewallReferenceError("observation is not canonical JSON") from exc


def _canonical_payload(document: Mapping[str, Any]) -> bytes:
    return _canonical_json(dict(document)) + b"\n"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    try:
        return FIREWALL._hash(value, label=label)  # noqa: SLF001
    except FIREWALL.DestinationFirewallObservationError as exc:
        raise DestinationFirewallReferenceError(f"{label} is invalid") from exc


def _absolute_path(value: Path | str, *, label: str) -> Path:
    try:
        path = Path(value)
    except TypeError as exc:
        raise DestinationFirewallReferenceError(f"{label} path is invalid") from exc
    if not path.is_absolute() or ".." in path.parts or path != Path(os.path.abspath(path)):
        raise DestinationFirewallReferenceError(f"{label} path is not canonical absolute")
    return path


def _controller_evidence_root(manifest: Mapping[str, Any]) -> Path:
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("deployment"), Mapping):
        raise DestinationFirewallReferenceError("firewall reference manifest is invalid")
    deployment = manifest["deployment"]
    return _absolute_path(
        deployment.get("controller_evidence_root"),
        label="controller evidence root",
    )


def _require_root() -> None:
    if os.geteuid() != 0:
        raise DestinationFirewallReferenceError("destination firewall reference requires root")


def _private_directory(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DestinationFirewallReferenceError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != DIRECTORY_MODE
    ):
        raise DestinationFirewallReferenceError(f"{label} must be root:root mode 0700")


def _ensure_private_child(parent: Path, name: str, *, label: str) -> Path:
    _private_directory(parent, label=f"{label} parent")
    child = parent / name
    try:
        child.mkdir(mode=DIRECTORY_MODE)
    except FileExistsError:
        pass
    except OSError as exc:
        raise DestinationFirewallReferenceError(f"{label} cannot be created") from exc
    _private_directory(child, label=label)
    return child


def _observation_root(manifest: Mapping[str, Any], *, create: bool) -> Path:
    root = _controller_evidence_root(manifest)
    if not create:
        _private_directory(root, label="controller evidence root")
        gate = root / "convergence-gate"
        _private_directory(gate, label="convergence gate root")
        inputs = gate / "observation-inputs"
        _private_directory(inputs, label="convergence observation inputs")
        observations = inputs / "observations"
        _private_directory(observations, label="convergence observations")
        return observations
    gate = _ensure_private_child(root, "convergence-gate", label="convergence gate root")
    inputs = _ensure_private_child(gate, "observation-inputs", label="convergence observation inputs")
    return _ensure_private_child(inputs, "observations", label="convergence observations")


def canonical_destination_firewall_observation_path(
    manifest: Mapping[str, Any], *, digest: str
) -> Path:
    """Return the one digest-addressed gate path for firewall evidence."""

    checked = _nonzero_sha256(digest, label="destination firewall reference digest")
    root = _controller_evidence_root(manifest)
    return (
        root
        / "convergence-gate"
        / "observation-inputs"
        / "observations"
        / f"{OBSERVATION_LABEL}.{checked}.json"
    )


def _reference(value: DestinationFirewallObservationReference | Mapping[str, Any]) -> DestinationFirewallObservationReference:
    if isinstance(value, DestinationFirewallObservationReference):
        path = _absolute_path(value.path, label="destination firewall reference")
        digest = _nonzero_sha256(value.sha256, label="destination firewall reference digest")
        return DestinationFirewallObservationReference(path, digest)
    if not isinstance(value, Mapping) or set(value) != REFERENCE_FIELDS:
        raise DestinationFirewallReferenceError("destination firewall reference fields differ")
    return DestinationFirewallObservationReference(
        _absolute_path(value["path"], label="destination firewall reference"),
        _nonzero_sha256(value["sha256"], label="destination firewall reference digest"),
    )


def _read_private_payload(path: Path, *, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DestinationFirewallReferenceError(f"{label} is unavailable") from exc
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
            raise DestinationFirewallReferenceError(f"{label} is not a root-only observation file")
        chunks: list[bytes] = []
        remaining = MAX_OBSERVATION_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_OBSERVATION_BYTES:
            raise DestinationFirewallReferenceError(f"{label} exceeds its size limit")
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise DestinationFirewallReferenceError(f"{label} changed while being read")
        return payload
    finally:
        os.close(descriptor)


def _decode_canonical_payload(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DestinationFirewallReferenceError(f"{label} is not strict ASCII JSON") from exc
    if not isinstance(document, dict) or payload != _canonical_payload(document):
        raise DestinationFirewallReferenceError(f"{label} is not canonical JSON")
    return document


def validate_destination_firewall_observation_reference(
    reference: DestinationFirewallObservationReference | Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    identity: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Read and validate the exact canonical firewall observation reference."""

    _require_root()
    checked = _reference(reference)
    expected_path = canonical_destination_firewall_observation_path(manifest, digest=checked.sha256)
    if checked.path != expected_path:
        raise DestinationFirewallReferenceError("destination firewall reference path differs")
    _observation_root(manifest, create=False)
    payload = _read_private_payload(checked.path, label="destination firewall observation")
    if _sha256(payload) != checked.sha256:
        raise DestinationFirewallReferenceError("destination firewall reference digest differs")
    document = _decode_canonical_payload(payload, label="destination firewall observation")
    try:
        validated = FIREWALL.validate_published_destination_firewall_observation(
            document,
            identity=identity,
            now=now,
        )
    except FIREWALL.DestinationFirewallObservationError as exc:
        raise DestinationFirewallReferenceError("destination firewall observation validation differs") from exc
    if validated != document:
        raise DestinationFirewallReferenceError("destination firewall observation normalization differs")
    return document


def install_destination_firewall_observation(
    document: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    identity: Mapping[str, Any],
    now: datetime,
) -> DestinationFirewallObservationReference:
    """Create-only publish one validated redacted observation and read it back."""

    _require_root()
    try:
        validated = FIREWALL.validate_published_destination_firewall_observation(
            document,
            identity=identity,
            now=now,
        )
    except FIREWALL.DestinationFirewallObservationError as exc:
        raise DestinationFirewallReferenceError("destination firewall observation validation differs") from exc
    if validated != document:
        raise DestinationFirewallReferenceError("destination firewall observation normalization differs")
    payload = _canonical_payload(validated)
    if len(payload) > MAX_OBSERVATION_BYTES:
        raise DestinationFirewallReferenceError("destination firewall observation exceeds its size limit")
    digest = _sha256(payload)
    root = _observation_root(manifest, create=True)
    path = root / f"{OBSERVATION_LABEL}.{digest}.json"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="destination firewall observation",
            mode=FILE_MODE,
            max_size=MAX_OBSERVATION_BYTES,
        )
    except SecureFileError as exc:
        if not os.path.lexists(path):
            raise DestinationFirewallReferenceError("destination firewall observation cannot be published") from exc
        existing = _read_private_payload(path, label="existing destination firewall observation")
        if existing != payload:
            raise DestinationFirewallReferenceError("destination firewall observation collision differs") from exc
    reference = DestinationFirewallObservationReference(path=path, sha256=digest)
    readback = validate_destination_firewall_observation_reference(
        reference,
        manifest=manifest,
        identity=identity,
        now=now,
    )
    if readback != validated:
        raise DestinationFirewallReferenceError("destination firewall observation readback differs")
    return reference
