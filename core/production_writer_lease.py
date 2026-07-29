"""Root-only local lease parser for the legacy 2c08 Writer Witness agent.

The separate host-level agent owns this file and controls only the isolated
WebApp app/sync services.  The parser deliberately has no application,
database, route, volume, or Object Storage dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


LEASE_SCHEMA = "production-writer-lease-v1"
WEBAPP_SITES = frozenset({"webapp_fi", "webapp_ir"})
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_LEASE_BYTES = 64 * 1024


class ProductionWriterLeaseError(RuntimeError):
    """Raised when local writer authority cannot be proved."""


@dataclass(frozen=True)
class ProductionWriterLease:
    holder_site: str
    writer_epoch: int
    lease_id: str
    issued_at: datetime
    expires_at: datetime
    witness_transition_id: str
    proof_sha256: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ProductionWriterLeaseError("writer lease timestamp lacks a timezone")
    return value.astimezone(timezone.utc)


def _parse_time(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ProductionWriterLeaseError(f"writer lease {field} is missing")
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise ProductionWriterLeaseError(f"writer lease {field} is invalid") from exc


def _secure_read(path: Path, *, owner_uid: int | None = None) -> bytes:
    expected_uid = os.geteuid() if owner_uid is None else int(owner_uid)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductionWriterLeaseError("writer lease cannot be opened securely") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != expected_uid
            or stat.S_IMODE(before.st_mode) & 0o077
            or before.st_nlink != 1
            or before.st_size < 0
            or before.st_size > MAX_LEASE_BYTES
        ):
            raise ProductionWriterLeaseError("writer lease file permissions are unsafe")
        payload = bytearray()
        while len(payload) <= MAX_LEASE_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_LEASE_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > MAX_LEASE_BYTES:
            raise ProductionWriterLeaseError("writer lease file is oversized")
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, name) != getattr(after, name) for name in stable_fields):
            raise ProductionWriterLeaseError("writer lease changed while being read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProductionWriterLeaseError("writer lease contains duplicate JSON keys")
        result[key] = value
    return result


def load_production_writer_lease(
    path: Path,
    *,
    owner_uid: int | None = None,
) -> ProductionWriterLease:
    """Load exactly one root-only Witness lease without trusting path metadata."""

    try:
        payload = json.loads(_secure_read(path, owner_uid=owner_uid), object_pairs_hook=_strict_object)
    except ProductionWriterLeaseError:
        raise
    except Exception as exc:
        raise ProductionWriterLeaseError("writer lease JSON is invalid") from exc
    expected = {
        "schema",
        "holder_site",
        "writer_epoch",
        "lease_id",
        "issued_at",
        "expires_at",
        "witness_transition_id",
        "proof_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != expected or payload.get("schema") != LEASE_SCHEMA:
        raise ProductionWriterLeaseError("writer lease schema is invalid")
    holder = payload.get("holder_site")
    if holder not in WEBAPP_SITES:
        raise ProductionWriterLeaseError("writer lease holder site is invalid")
    epoch = payload.get("writer_epoch")
    if type(epoch) is not int or epoch < 1:
        raise ProductionWriterLeaseError("writer lease epoch is invalid")
    lease_id = payload.get("lease_id")
    transition_id = payload.get("witness_transition_id")
    if (
        not isinstance(lease_id, str)
        or not lease_id
        or lease_id != lease_id.strip()
        or len(lease_id) > 128
        or not isinstance(transition_id, str)
        or not transition_id
        or transition_id != transition_id.strip()
        or len(transition_id) > 128
    ):
        raise ProductionWriterLeaseError("writer lease identifiers are invalid")
    proof_hash = str(payload.get("proof_sha256") or "").lower()
    if not HASH_RE.fullmatch(proof_hash):
        raise ProductionWriterLeaseError("writer lease proof hash is invalid")
    issued_at = _parse_time(payload.get("issued_at"), field="issued_at")
    expires_at = _parse_time(payload.get("expires_at"), field="expires_at")
    if expires_at <= issued_at:
        raise ProductionWriterLeaseError("writer lease expiry is invalid")
    return ProductionWriterLease(
        holder_site=holder,
        writer_epoch=epoch,
        lease_id=lease_id,
        issued_at=issued_at,
        expires_at=expires_at,
        witness_transition_id=transition_id,
        proof_sha256=proof_hash,
    )
