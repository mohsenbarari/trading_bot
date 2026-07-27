"""Pure-stdlib contract shared by the WA-IR publisher and bootstrap receiver."""

from __future__ import annotations

from pathlib import Path
import re
import uuid


ARVAN_ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
ARVAN_HOST = "s3.ir-thr-at1.arvanstorage.ir"
ARVAN_REGION = "ir-thr-at1"
PRODUCTION_BUCKET = "production-sync-coin"
PRODUCTION_PREFIX_ROOT = "dark-standby"
AGE_EXECUTABLE = Path("/usr/bin/age")
WA_IR_AGE_IDENTITY_FILE = Path(
    "/root/secure-envs/trading-bot/wa-ir-object-storage-age-identity.txt"
)
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024 * 1024
MAX_READBACK_BYTES = MAX_PAYLOAD_BYTES + 1024 * 1024
TRANSPORT_SCHEMA = "wa-ir-production-age-transport-v1"

ARTIFACT_KIND_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
PREFIX_PART_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ProductionTransportError(RuntimeError):
    """Raised when any production transport invariant cannot be proven."""


def validate_operation_id(raw: str) -> str:
    try:
        operation_id = uuid.UUID(raw)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProductionTransportError(
            "production WA-IR operation id must be a canonical UUID"
        ) from exc
    canonical = str(operation_id)
    if raw != canonical or operation_id.int == 0:
        raise ProductionTransportError(
            "production WA-IR operation id must be a nonzero canonical UUID"
        )
    return canonical


def validate_prefix(raw: str) -> str:
    if not 5 <= len(raw) <= 240 or raw != raw.strip("/") or "//" in raw:
        raise ProductionTransportError("production WA-IR object prefix is invalid")
    parts = raw.split("/")
    if (
        len(parts) < 2
        or parts[0] != PRODUCTION_PREFIX_ROOT
        or any(not PREFIX_PART_RE.fullmatch(part) for part in parts)
        or any(part in {".", ".."} for part in parts)
    ):
        raise ProductionTransportError(
            "production WA-IR object prefix must be isolated below dark-standby"
        )
    return raw


def validate_object_key_binding(
    object_key: str,
    *,
    operation_id: str,
    artifact_kind: str,
    ciphertext_sha256: str,
) -> None:
    """Prove an object key is inside the requested operation and artifact scope."""

    try:
        key_parts = object_key.split("/")
        if len(key_parts) < 5:
            raise ValueError
        validate_prefix("/".join(key_parts[:-3]))
        if (
            validate_operation_id(key_parts[-3]) != operation_id
            or key_parts[-2] != artifact_kind
            or not ARTIFACT_KIND_RE.fullmatch(artifact_kind)
        ):
            raise ValueError
        filename = re.fullmatch(
            r"(?P<nonce>[0-9a-f]{32})-(?P<sha256>[0-9a-f]{64})\.age",
            key_parts[-1],
        )
        if filename is None or filename.group("sha256") != ciphertext_sha256:
            raise ValueError
    except (AttributeError, TypeError, ValueError, ProductionTransportError) as exc:
        raise ProductionTransportError(
            "production WA-IR object key binding is invalid"
        ) from exc
