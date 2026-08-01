"""Pure signed evidence contract for a future physical-WAL pull-plane ack.

This module deliberately models only portable, signed evidence: a source
request and a destination durable/replay receipt for one exact physical-WAL
continuity point.  It has no transport implementation and never opens a
socket, Object Storage, database, filesystem, ``age`` process, shell, Docker,
SSH connection, restore, recovery, route change, or promotion path.

The verifier can mint an opaque capability only after it validates both
Ed25519 signatures, the exact two-site route, bounded canonical JSON,
freshness, replay inputs, and the complete manifest/Object-version set.  That
capability remains evidence, not a database commit permit, a synchronous
remote-apply fact, a Writer permit, or a promotion permit.  A runtime still
needs a durable replay ledger plus real transport and database integration.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE


PHYSICAL_WAL_REMOTE_ACK_VERSION = 1
PHYSICAL_WAL_REMOTE_ACK_REQUEST_SCHEMA = "gold-trade-physical-wal-remote-ack-request-v1"
PHYSICAL_WAL_REMOTE_ACK_RECEIPT_SCHEMA = "gold-trade-physical-wal-remote-ack-receipt-v1"
PHYSICAL_WAL_REMOTE_ACK_SIGNATURE_ALGORITHM = "ed25519"
PHYSICAL_WAL_REMOTE_ACK_DEFAULT_ENABLED = False
MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES = 64 * 1024
MAX_PHYSICAL_WAL_REMOTE_ACK_AGE_SECONDS = 60
MAX_PHYSICAL_WAL_REMOTE_ACK_FUTURE_SKEW_SECONDS = 5
MAX_PHYSICAL_WAL_REMOTE_ACK_MANIFESTS = 4096
MAX_PHYSICAL_WAL_REMOTE_ACK_OBJECT_VERSIONS = 131_072

_REQUEST_DOMAIN = b"gold-trade-physical-wal-remote-ack-request-v1\x00"
_RECEIPT_DOMAIN = b"gold-trade-physical-wal-remote-ack-receipt-v1\x00"
_VERIFIED_CAPABILITY = object()
_VERIFIED_REQUEST_CAPABILITY = object()
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$", re.ASCII)
_MUTABLE_ALIAS_COMPONENTS = frozenset({"alias", "current", "head", "latest", "pointer"})

_TERM_FIELDS = frozenset(
    {"writer_holder_site", "writer_epoch", "writer_lease_id", "witnessed_term_proof_sha256"}
)
_OBJECT_VERSION_FIELDS = frozenset({"object_key", "version_id"})
_BINDING_FIELDS = frozenset(
    {
        "source_site",
        "destination_site",
        "destination_age_recipient",
        "campaign_id",
        "release_sha",
        "stream_generation_id",
        "baseline_generation_id",
        "baseline_manifest_sha256",
        "writer_term",
        "target_acknowledged_wal_lsn",
        "blob_object_frontier_wal_lsn",
        "objects_complete",
        "manifest_sha256es",
        "object_versions",
    }
)
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "binding",
        "request_id",
        "request_nonce",
        "issued_at",
        "source_signer",
        "source_signature",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "binding",
        "request_id",
        "request_nonce",
        "request_issued_at",
        "source_request_sha256",
        "receipt_id",
        "receipt_nonce",
        "acknowledged_at",
        "destination_signer",
        "destination_signature",
    }
)


class PhysicalWalRemoteAckError(ValueError):
    """Physical-WAL remote acknowledgement evidence is unsafe or unbound."""


@dataclass(frozen=True)
class PhysicalWalRemoteAckTermProjection:
    """Signed projection of the live Writer term; not a live Witness check."""

    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str


@dataclass(frozen=True)
class PhysicalWalRemoteAckObjectVersion:
    """One immutable Object Storage identity bound by signed manifests."""

    object_key: str
    version_id: str


@dataclass(frozen=True)
class PhysicalWalRemoteAckBinding:
    """The exact route/lineage/frontier the source asks the target to attest."""

    source_site: str
    destination_site: str
    destination_age_recipient: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    writer_term: PhysicalWalRemoteAckTermProjection
    target_acknowledged_wal_lsn: str
    blob_object_frontier_wal_lsn: str
    objects_complete: bool
    manifest_sha256es: tuple[str, ...]
    object_versions: tuple[PhysicalWalRemoteAckObjectVersion, ...]


@dataclass(frozen=True)
class VerifiedPhysicalWalRemoteAckEvidence:
    """Opaque signature-verified evidence, explicitly not execution authority."""

    source_request: bytes
    destination_receipt: bytes
    source_public_key: bytes
    destination_public_key: bytes
    binding: PhysicalWalRemoteAckBinding
    request_id: str
    request_nonce: str
    receipt_id: str
    receipt_nonce: str
    issued_at: datetime
    acknowledged_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalWalRemoteAckRequest:
    """Opaque, source-verified request for a destination receipt boundary.

    This is deliberately less than a receipt and less than execution
    authority.  A destination runtime can use it to reject a foreign or stale
    request *before* it considers local replay state or signs anything.  It
    still needs a durable replay/nonce ledger and its own live-term checks.
    """

    source_request: bytes
    source_public_key: bytes
    binding: PhysicalWalRemoteAckBinding
    request_id: str
    request_nonce: str
    issued_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _RequestFacts:
    raw: bytes
    binding: PhysicalWalRemoteAckBinding
    request_id: str
    request_nonce: str
    issued_at: datetime
    source_public_key: bytes


@dataclass(frozen=True)
class _ReceiptFacts:
    raw: bytes
    binding: PhysicalWalRemoteAckBinding
    request_id: str
    request_nonce: str
    request_issued_at: datetime
    source_request_sha256: str
    receipt_id: str
    receipt_nonce: str
    acknowledged_at: datetime
    destination_public_key: bytes


def _canonical(value: object, *, label: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalRemoteAckError(f"{label} is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PhysicalWalRemoteAckError("remote acknowledgement JSON has duplicate fields")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise PhysicalWalRemoteAckError("remote acknowledgement JSON has a forbidden constant")


def _exact_mapping(value: object, *, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise PhysicalWalRemoteAckError(f"{label} fields are invalid")
    return dict(value)


def _text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PhysicalWalRemoteAckError(f"{label} is invalid")
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        raise PhysicalWalRemoteAckError(f"{label} is invalid") from exc
    return value


def _sha256(value: object, *, label: str) -> str:
    return _text(value, label=label, pattern=SHA256_RE)


def _nonzero_sha256(value: object, *, label: str) -> str:
    digest = _sha256(value, label=label)
    if digest == "0" * 64:
        raise PhysicalWalRemoteAckError(f"{label} is invalid")
    return digest


def _site(value: object, *, label: str) -> str:
    if not isinstance(value, str) or value not in WEBAPP_SITES:
        raise PhysicalWalRemoteAckError(f"{label} is invalid")
    return value


def _id(value: object, *, label: str) -> str:
    return _text(value, label=label, pattern=_ID_RE)


def _nonce(value: object, *, label: str) -> str:
    return _text(value, label=label, pattern=_NONCE_RE)


def _lsn(value: object, *, label: str) -> tuple[str, int]:
    text = _text(value, label=label, pattern=_LSN_RE)
    high, low = text.split("/", 1)
    return text, (int(high, 16) << 32) | int(low, 16)


def _utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PhysicalWalRemoteAckError(f"{label} is invalid")
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PhysicalWalRemoteAckError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PhysicalWalRemoteAckError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise PhysicalWalRemoteAckError(f"{label} is invalid")
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        raise PhysicalWalRemoteAckError(f"{label} is not canonical UTC")
    return normalized


def _timestamp_text(value: object, *, label: str) -> str:
    return _utc(value, label=label).isoformat()


def _public_key(value: object, *, label: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        raise PhysicalWalRemoteAckError(f"{label} is invalid")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError) as exc:
        raise PhysicalWalRemoteAckError(f"{label} is invalid") from exc
    return value


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _decode_base64(value: object, *, label: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str):
        raise PhysicalWalRemoteAckError(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise PhysicalWalRemoteAckError(f"{label} is invalid") from exc
    if len(decoded) != expected_bytes:
        raise PhysicalWalRemoteAckError(f"{label} is invalid")
    return decoded


def _signer(value: object, *, label: str) -> bytes:
    signer = _exact_mapping(value, fields=_SIGNER_FIELDS, label=f"{label} signer")
    if signer["algorithm"] != PHYSICAL_WAL_REMOTE_ACK_SIGNATURE_ALGORITHM:
        raise PhysicalWalRemoteAckError(f"{label} signer algorithm is invalid")
    public_key = _public_key(
        _decode_base64(signer["public_key_base64"], label=f"{label} signer public key", expected_bytes=32),
        label=f"{label} signer public key",
    )
    if _text(signer["key_id"], label=f"{label} signer key ID", pattern=_KEY_ID_RE) != _key_id(public_key):
        raise PhysicalWalRemoteAckError(f"{label} signer key ID does not match public key")
    return public_key


def _signature(value: object, *, label: str) -> bytes:
    signature = _exact_mapping(value, fields=_SIGNATURE_FIELDS, label=f"{label} signature")
    if signature["algorithm"] != PHYSICAL_WAL_REMOTE_ACK_SIGNATURE_ALGORITHM:
        raise PhysicalWalRemoteAckError(f"{label} signature algorithm is invalid")
    return _decode_base64(signature["signature_base64"], label=f"{label} signature", expected_bytes=64)


def _term_from_mapping(value: object, *, label: str) -> PhysicalWalRemoteAckTermProjection:
    term = _exact_mapping(value, fields=_TERM_FIELDS, label=f"{label} term")
    if type(term["writer_epoch"]) is not int or term["writer_epoch"] < 1:
        raise PhysicalWalRemoteAckError(f"{label} term epoch is invalid")
    return PhysicalWalRemoteAckTermProjection(
        writer_holder_site=_site(term["writer_holder_site"], label=f"{label} term holder"),
        writer_epoch=term["writer_epoch"],
        writer_lease_id=_text(term["writer_lease_id"], label=f"{label} term lease", pattern=LEASE_ID_RE),
        witnessed_term_proof_sha256=_nonzero_sha256(
            term["witnessed_term_proof_sha256"], label=f"{label} term proof"
        ),
    )


def _term_mapping(value: PhysicalWalRemoteAckTermProjection) -> dict[str, Any]:
    return {
        "writer_holder_site": value.writer_holder_site,
        "writer_epoch": value.writer_epoch,
        "writer_lease_id": value.writer_lease_id,
        "witnessed_term_proof_sha256": value.witnessed_term_proof_sha256,
    }


def _object_key(value: object, *, label: str) -> str:
    key = _text(value, label=label, pattern=OBJECT_KEY_RE)
    components = key.split("/")
    if (
        not key.endswith(".age")
        or ".." in components
        or any(
            component.casefold() in _MUTABLE_ALIAS_COMPONENTS
            or component.split(".", 1)[0].casefold() in _MUTABLE_ALIAS_COMPONENTS
            for component in components
        )
    ):
        raise PhysicalWalRemoteAckError(f"{label} is a mutable alias")
    return key


def _version_id(value: object, *, label: str) -> str:
    version = _text(value, label=label, pattern=VERSION_ID_RE)
    if version.casefold() in _MUTABLE_ALIAS_COMPONENTS | {"null", "none"}:
        raise PhysicalWalRemoteAckError(f"{label} is a mutable alias")
    return version


def _object_version_from_mapping(value: object, *, label: str) -> PhysicalWalRemoteAckObjectVersion:
    item = _exact_mapping(value, fields=_OBJECT_VERSION_FIELDS, label=label)
    return PhysicalWalRemoteAckObjectVersion(
        object_key=_object_key(item["object_key"], label=f"{label} object key"),
        version_id=_version_id(item["version_id"], label=f"{label} version ID"),
    )


def _object_version_mapping(value: PhysicalWalRemoteAckObjectVersion) -> dict[str, str]:
    return {"object_key": value.object_key, "version_id": value.version_id}


def _normalise_binding(value: object, *, label: str) -> PhysicalWalRemoteAckBinding:
    if type(value) is not PhysicalWalRemoteAckBinding:
        raise PhysicalWalRemoteAckError(f"{label} is invalid")
    source = _site(value.source_site, label=f"{label} source site")
    destination = _site(value.destination_site, label=f"{label} destination site")
    if source == destination:
        raise PhysicalWalRemoteAckError(f"{label} source and destination overlap")
    recipient = _text(
        value.destination_age_recipient,
        label=f"{label} destination age recipient",
        pattern=AGE_RECIPIENT_RE,
    )
    campaign = _text(value.campaign_id, label=f"{label} campaign", pattern=CAMPAIGN_ID_RE)
    release = _text(value.release_sha, label=f"{label} release", pattern=RELEASE_SHA_RE)
    stream = _text(value.stream_generation_id, label=f"{label} stream", pattern=STREAM_GENERATION_ID_RE)
    baseline_generation = _text(
        value.baseline_generation_id,
        label=f"{label} baseline generation",
        pattern=STREAM_GENERATION_ID_RE,
    )
    baseline = _nonzero_sha256(value.baseline_manifest_sha256, label=f"{label} baseline manifest")
    if type(value.writer_term) is not PhysicalWalRemoteAckTermProjection:
        raise PhysicalWalRemoteAckError(f"{label} writer term is invalid")
    term = _term_from_mapping(_term_mapping(value.writer_term), label=label)
    if term.writer_holder_site != source:
        raise PhysicalWalRemoteAckError(f"{label} term holder does not match source site")
    target, target_value = _lsn(value.target_acknowledged_wal_lsn, label=f"{label} target WAL LSN")
    blob_frontier, blob_value = _lsn(
        value.blob_object_frontier_wal_lsn, label=f"{label} blob frontier WAL LSN"
    )
    if type(value.objects_complete) is not bool or not value.objects_complete:
        raise PhysicalWalRemoteAckError(f"{label} blob objects are incomplete")
    if blob_value < target_value:
        raise PhysicalWalRemoteAckError(f"{label} blob frontier is behind target WAL LSN")
    if isinstance(value.manifest_sha256es, (str, bytes)) or not isinstance(value.manifest_sha256es, Sequence):
        raise PhysicalWalRemoteAckError(f"{label} manifest set is invalid")
    if not 1 <= len(value.manifest_sha256es) <= MAX_PHYSICAL_WAL_REMOTE_ACK_MANIFESTS:
        raise PhysicalWalRemoteAckError(f"{label} manifest set is invalid")
    manifests = tuple(
        sorted(_nonzero_sha256(item, label=f"{label} manifest hash") for item in value.manifest_sha256es)
    )
    if len(set(manifests)) != len(manifests) or baseline not in manifests:
        raise PhysicalWalRemoteAckError(f"{label} manifest set is incomplete or replayed")
    if isinstance(value.object_versions, (str, bytes)) or not isinstance(value.object_versions, Sequence):
        raise PhysicalWalRemoteAckError(f"{label} Object-version set is invalid")
    if not 1 <= len(value.object_versions) <= MAX_PHYSICAL_WAL_REMOTE_ACK_OBJECT_VERSIONS:
        raise PhysicalWalRemoteAckError(f"{label} Object-version set is invalid")
    objects: list[PhysicalWalRemoteAckObjectVersion] = []
    for index, item in enumerate(value.object_versions, start=1):
        if type(item) is not PhysicalWalRemoteAckObjectVersion:
            raise PhysicalWalRemoteAckError(f"{label} Object-version {index} is invalid")
        objects.append(
            _object_version_from_mapping(_object_version_mapping(item), label=f"{label} Object-version {index}")
        )
    ordered_objects = tuple(sorted(objects, key=lambda item: (item.object_key, item.version_id)))
    if len(set((item.object_key, item.version_id) for item in ordered_objects)) != len(ordered_objects):
        raise PhysicalWalRemoteAckError(f"{label} Object-version set is replayed")
    return PhysicalWalRemoteAckBinding(
        source_site=source,
        destination_site=destination,
        destination_age_recipient=recipient,
        campaign_id=campaign,
        release_sha=release,
        stream_generation_id=stream,
        baseline_generation_id=baseline_generation,
        baseline_manifest_sha256=baseline,
        writer_term=term,
        target_acknowledged_wal_lsn=target,
        blob_object_frontier_wal_lsn=blob_frontier,
        objects_complete=True,
        manifest_sha256es=manifests,
        object_versions=ordered_objects,
    )


def _binding_from_mapping(value: object, *, label: str) -> PhysicalWalRemoteAckBinding:
    binding = _exact_mapping(value, fields=_BINDING_FIELDS, label=f"{label} binding")
    if not isinstance(binding["manifest_sha256es"], list) or not isinstance(binding["object_versions"], list):
        raise PhysicalWalRemoteAckError(f"{label} binding set is invalid")
    objects = tuple(
        _object_version_from_mapping(item, label=f"{label} Object-version {index}")
        for index, item in enumerate(binding["object_versions"], start=1)
    )
    raw_binding = PhysicalWalRemoteAckBinding(
        source_site=binding["source_site"],
        destination_site=binding["destination_site"],
        destination_age_recipient=binding["destination_age_recipient"],
        campaign_id=binding["campaign_id"],
        release_sha=binding["release_sha"],
        stream_generation_id=binding["stream_generation_id"],
        baseline_generation_id=binding["baseline_generation_id"],
        baseline_manifest_sha256=binding["baseline_manifest_sha256"],
        writer_term=_term_from_mapping(binding["writer_term"], label=f"{label} binding"),
        target_acknowledged_wal_lsn=binding["target_acknowledged_wal_lsn"],
        blob_object_frontier_wal_lsn=binding["blob_object_frontier_wal_lsn"],
        objects_complete=binding["objects_complete"],
        manifest_sha256es=tuple(binding["manifest_sha256es"]),
        object_versions=objects,
    )
    normalised = _normalise_binding(raw_binding, label=f"{label} binding")
    if _binding_mapping(normalised) != binding:
        raise PhysicalWalRemoteAckError(f"{label} binding is not canonical")
    return normalised


def _binding_mapping(value: PhysicalWalRemoteAckBinding) -> dict[str, Any]:
    return {
        "source_site": value.source_site,
        "destination_site": value.destination_site,
        "destination_age_recipient": value.destination_age_recipient,
        "campaign_id": value.campaign_id,
        "release_sha": value.release_sha,
        "stream_generation_id": value.stream_generation_id,
        "baseline_generation_id": value.baseline_generation_id,
        "baseline_manifest_sha256": value.baseline_manifest_sha256,
        "writer_term": _term_mapping(value.writer_term),
        "target_acknowledged_wal_lsn": value.target_acknowledged_wal_lsn,
        "blob_object_frontier_wal_lsn": value.blob_object_frontier_wal_lsn,
        "objects_complete": True,
        "manifest_sha256es": list(value.manifest_sha256es),
        "object_versions": [_object_version_mapping(item) for item in value.object_versions],
    }


def build_physical_wal_remote_ack_binding(
    *,
    source_site: str,
    destination_site: str,
    destination_age_recipient: str,
    campaign_id: str,
    release_sha: str,
    stream_generation_id: str,
    baseline_generation_id: str,
    baseline_manifest_sha256: str,
    writer_epoch: int,
    writer_holder_site: str,
    writer_lease_id: str,
    witnessed_term_proof_sha256: str,
    target_acknowledged_wal_lsn: str,
    blob_object_frontier_wal_lsn: str,
    manifest_sha256es: Sequence[str],
    object_versions: Sequence[tuple[str, str] | PhysicalWalRemoteAckObjectVersion],
    objects_complete: bool = True,
) -> PhysicalWalRemoteAckBinding:
    """Build a normalized exact evidence binding without I/O or clock access."""

    if isinstance(manifest_sha256es, (str, bytes)) or not isinstance(manifest_sha256es, Sequence):
        raise PhysicalWalRemoteAckError("remote acknowledgement manifest set is invalid")
    objects: list[PhysicalWalRemoteAckObjectVersion] = []
    if isinstance(object_versions, (str, bytes)) or not isinstance(object_versions, Sequence):
        raise PhysicalWalRemoteAckError("remote acknowledgement Object-version set is invalid")
    for index, item in enumerate(object_versions, start=1):
        if type(item) is PhysicalWalRemoteAckObjectVersion:
            objects.append(item)
        elif isinstance(item, tuple) and len(item) == 2:
            objects.append(PhysicalWalRemoteAckObjectVersion(item[0], item[1]))
        else:
            raise PhysicalWalRemoteAckError(
                f"remote acknowledgement Object-version {index} is invalid"
            )
    return _normalise_binding(
        PhysicalWalRemoteAckBinding(
            source_site=source_site,
            destination_site=destination_site,
            destination_age_recipient=destination_age_recipient,
            campaign_id=campaign_id,
            release_sha=release_sha,
            stream_generation_id=stream_generation_id,
            baseline_generation_id=baseline_generation_id,
            baseline_manifest_sha256=baseline_manifest_sha256,
            writer_term=PhysicalWalRemoteAckTermProjection(
                writer_holder_site=writer_holder_site,
                writer_epoch=writer_epoch,
                writer_lease_id=writer_lease_id,
                witnessed_term_proof_sha256=witnessed_term_proof_sha256,
            ),
            target_acknowledged_wal_lsn=target_acknowledged_wal_lsn,
            blob_object_frontier_wal_lsn=blob_object_frontier_wal_lsn,
            objects_complete=objects_complete,
            manifest_sha256es=tuple(manifest_sha256es),
            object_versions=tuple(objects),
        ),
        label="remote acknowledgement binding",
    )


def _signer_from_private(value: object, *, label: str) -> tuple[object, bytes, dict[str, str]]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover - environment dependency.
        raise PhysicalWalRemoteAckError(f"{label} signing is unavailable") from exc
    if not isinstance(value, Ed25519PrivateKey):
        raise PhysicalWalRemoteAckError(f"{label} signer is invalid")
    try:
        public_key = value.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except ValueError as exc:
        raise PhysicalWalRemoteAckError(f"{label} signer is invalid") from exc
    public_key = _public_key(public_key, label=f"{label} signer public key")
    return (
        value,
        public_key,
        {
            "algorithm": PHYSICAL_WAL_REMOTE_ACK_SIGNATURE_ALGORITHM,
            "public_key_base64": base64.b64encode(public_key).decode("ascii"),
            "key_id": _key_id(public_key),
        },
    )


def _sign(unsigned: Mapping[str, Any], *, domain: bytes, signer: object, label: str) -> dict[str, str]:
    private, _public, _mapping = _signer_from_private(signer, label=label)
    try:
        signature = private.sign(domain + _canonical(dict(unsigned), label=label))
    except ValueError as exc:
        raise PhysicalWalRemoteAckError(f"{label} signer failed") from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise PhysicalWalRemoteAckError(f"{label} signer produced an invalid signature")
    return {
        "algorithm": PHYSICAL_WAL_REMOTE_ACK_SIGNATURE_ALGORITHM,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def _parse_canonical(value: object, *, label: str) -> tuple[dict[str, Any], bytes]:
    if isinstance(value, Mapping):
        try:
            payload = dict(value)
            raw = _canonical(payload, label=label)
        except (TypeError, ValueError) as exc:
            raise PhysicalWalRemoteAckError(f"{label} is invalid") from exc
    elif isinstance(value, bytes):
        raw = value
        if not raw or len(raw) > MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES:
            raise PhysicalWalRemoteAckError(f"{label} byte size is invalid")
        try:
            payload = json.loads(
                raw.decode("ascii", "strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
        except PhysicalWalRemoteAckError:
            raise
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise PhysicalWalRemoteAckError(f"{label} is invalid JSON") from exc
        if not isinstance(payload, dict) or _canonical(payload, label=label) != raw:
            raise PhysicalWalRemoteAckError(f"{label} is not canonical JSON")
    else:
        raise PhysicalWalRemoteAckError(f"{label} is invalid")
    if not raw or len(raw) > MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES:
        raise PhysicalWalRemoteAckError(f"{label} byte size is invalid")
    return payload, raw


def _verify_signature(
    payload: Mapping[str, Any], *, signature_field: str, signer_field: str, domain: bytes, label: str, expected_key: bytes | None
) -> bytes:
    public_key = _signer(payload.get(signer_field), label=label)
    if expected_key is not None and public_key != expected_key:
        raise PhysicalWalRemoteAckError(f"{label} signer does not match expected route key")
    signature = _signature(payload.get(signature_field), label=label)
    unsigned = {key: item for key, item in payload.items() if key != signature_field}
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, domain + _canonical(unsigned, label=label)
        )
    except (InvalidSignature, ValueError) as exc:
        raise PhysicalWalRemoteAckError(f"{label} signature is invalid") from exc
    return public_key


def _parse_request(value: object, *, expected_source_public_key: bytes | None = None) -> _RequestFacts:
    payload, raw = _parse_canonical(value, label="remote acknowledgement request")
    request = _exact_mapping(payload, fields=_REQUEST_FIELDS, label="remote acknowledgement request")
    if (
        request["schema"] != PHYSICAL_WAL_REMOTE_ACK_REQUEST_SCHEMA
        or request["version"] != PHYSICAL_WAL_REMOTE_ACK_VERSION
        or request["kind"] != "physical_wal_pull_plane_durable_replay_ack_request"
    ):
        raise PhysicalWalRemoteAckError("remote acknowledgement request schema is invalid")
    binding = _binding_from_mapping(request["binding"], label="remote acknowledgement request")
    source_key = _verify_signature(
        request,
        signature_field="source_signature",
        signer_field="source_signer",
        domain=_REQUEST_DOMAIN,
        label="remote acknowledgement request",
        expected_key=expected_source_public_key,
    )
    request_id = _id(request["request_id"], label="remote acknowledgement request ID")
    request_nonce = _nonce(request["request_nonce"], label="remote acknowledgement request nonce")
    if request_id == request_nonce:
        raise PhysicalWalRemoteAckError("remote acknowledgement request identity reuses its nonce")
    return _RequestFacts(
        raw=raw,
        binding=binding,
        request_id=request_id,
        request_nonce=request_nonce,
        issued_at=_timestamp(request["issued_at"], label="remote acknowledgement request issued_at"),
        source_public_key=source_key,
    )


def _parse_receipt(value: object, *, expected_destination_public_key: bytes | None = None) -> _ReceiptFacts:
    payload, raw = _parse_canonical(value, label="remote acknowledgement receipt")
    receipt = _exact_mapping(payload, fields=_RECEIPT_FIELDS, label="remote acknowledgement receipt")
    if (
        receipt["schema"] != PHYSICAL_WAL_REMOTE_ACK_RECEIPT_SCHEMA
        or receipt["version"] != PHYSICAL_WAL_REMOTE_ACK_VERSION
        or receipt["kind"] != "physical_wal_pull_plane_durable_replay_ack_receipt"
    ):
        raise PhysicalWalRemoteAckError("remote acknowledgement receipt schema is invalid")
    binding = _binding_from_mapping(receipt["binding"], label="remote acknowledgement receipt")
    destination_key = _verify_signature(
        receipt,
        signature_field="destination_signature",
        signer_field="destination_signer",
        domain=_RECEIPT_DOMAIN,
        label="remote acknowledgement receipt",
        expected_key=expected_destination_public_key,
    )
    return _ReceiptFacts(
        raw=raw,
        binding=binding,
        request_id=_id(receipt["request_id"], label="remote acknowledgement receipt request ID"),
        request_nonce=_nonce(receipt["request_nonce"], label="remote acknowledgement receipt request nonce"),
        request_issued_at=_timestamp(
            receipt["request_issued_at"], label="remote acknowledgement receipt request issued_at"
        ),
        source_request_sha256=_sha256(
            receipt["source_request_sha256"], label="remote acknowledgement receipt request hash"
        ),
        receipt_id=_id(receipt["receipt_id"], label="remote acknowledgement receipt ID"),
        receipt_nonce=_nonce(receipt["receipt_nonce"], label="remote acknowledgement receipt nonce"),
        acknowledged_at=_timestamp(
            receipt["acknowledged_at"], label="remote acknowledgement receipt acknowledged_at"
        ),
        destination_public_key=destination_key,
    )


def build_physical_wal_remote_ack_request(
    *,
    binding: PhysicalWalRemoteAckBinding,
    request_id: str,
    request_nonce: str,
    issued_at: datetime,
    source_signer: object,
) -> dict[str, Any]:
    """Build one canonical source request; it does not send it anywhere."""

    normalized = _normalise_binding(binding, label="remote acknowledgement binding")
    request_identity = _id(request_id, label="remote acknowledgement request ID")
    nonce = _nonce(request_nonce, label="remote acknowledgement request nonce")
    if request_identity == nonce:
        raise PhysicalWalRemoteAckError("remote acknowledgement request identity reuses its nonce")
    issued_text = _timestamp_text(issued_at, label="remote acknowledgement request issued_at")
    _private, _public, signer = _signer_from_private(source_signer, label="remote acknowledgement request")
    unsigned = {
        "schema": PHYSICAL_WAL_REMOTE_ACK_REQUEST_SCHEMA,
        "version": PHYSICAL_WAL_REMOTE_ACK_VERSION,
        "kind": "physical_wal_pull_plane_durable_replay_ack_request",
        "binding": _binding_mapping(normalized),
        "request_id": request_identity,
        "request_nonce": nonce,
        "issued_at": issued_text,
        "source_signer": signer,
    }
    return {
        **unsigned,
        "source_signature": _sign(
            unsigned,
            domain=_REQUEST_DOMAIN,
            signer=source_signer,
            label="remote acknowledgement request",
        ),
    }


def build_physical_wal_remote_ack_receipt(
    *,
    source_request: Mapping[str, Any] | bytes,
    receipt_id: str,
    receipt_nonce: str,
    acknowledged_at: datetime,
    destination_signer: object,
) -> dict[str, Any]:
    """Build a destination receipt from a valid source request, with no transport."""

    request = _parse_request(source_request)
    receipt_identity = _id(receipt_id, label="remote acknowledgement receipt ID")
    nonce = _nonce(receipt_nonce, label="remote acknowledgement receipt nonce")
    if len({request.request_id, request.request_nonce, receipt_identity, nonce}) != 4:
        raise PhysicalWalRemoteAckError("remote acknowledgement receipt identity reuses request identity")
    acknowledged_text = _timestamp_text(
        acknowledged_at, label="remote acknowledgement receipt acknowledged_at"
    )
    acknowledged = _timestamp(acknowledged_text, label="remote acknowledgement receipt acknowledged_at")
    if acknowledged < request.issued_at:
        raise PhysicalWalRemoteAckError("remote acknowledgement receipt predates its request")
    _private, _public, signer = _signer_from_private(destination_signer, label="remote acknowledgement receipt")
    unsigned = {
        "schema": PHYSICAL_WAL_REMOTE_ACK_RECEIPT_SCHEMA,
        "version": PHYSICAL_WAL_REMOTE_ACK_VERSION,
        "kind": "physical_wal_pull_plane_durable_replay_ack_receipt",
        "binding": _binding_mapping(request.binding),
        "request_id": request.request_id,
        "request_nonce": request.request_nonce,
        "request_issued_at": request.issued_at.isoformat(),
        "source_request_sha256": hashlib.sha256(request.raw).hexdigest(),
        "receipt_id": receipt_identity,
        "receipt_nonce": nonce,
        "acknowledged_at": acknowledged_text,
        "destination_signer": signer,
    }
    return {
        **unsigned,
        "destination_signature": _sign(
            unsigned,
            domain=_RECEIPT_DOMAIN,
            signer=destination_signer,
            label="remote acknowledgement receipt",
        ),
    }


def _fresh(value: datetime, *, now: datetime, label: str) -> None:
    if value > now + timedelta(seconds=MAX_PHYSICAL_WAL_REMOTE_ACK_FUTURE_SKEW_SECONDS):
        raise PhysicalWalRemoteAckError(f"{label} is from the future")
    if value < now - timedelta(seconds=MAX_PHYSICAL_WAL_REMOTE_ACK_AGE_SECONDS):
        raise PhysicalWalRemoteAckError(f"{label} is stale")


def _consumed(value: object, *, label: str, validator) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Collection):
        raise PhysicalWalRemoteAckError(f"{label} replay set is invalid")
    return frozenset(validator(item, label=f"{label} replay value") for item in value)


def _verify_request_replay_inputs(
    request: _RequestFacts,
    *,
    consumed_request_ids: Collection[str],
    consumed_request_nonces: Collection[str],
) -> None:
    consumed_ids = _consumed(
        consumed_request_ids, label="request ID", validator=_id
    )
    consumed_nonces = _consumed(
        consumed_request_nonces, label="request nonce", validator=_nonce
    )
    if request.request_id in consumed_ids:
        raise PhysicalWalRemoteAckError("remote acknowledgement request ID was replayed")
    if request.request_nonce in consumed_nonces:
        raise PhysicalWalRemoteAckError("remote acknowledgement request nonce was replayed")


def verify_physical_wal_remote_ack_request(
    *,
    source_request: Mapping[str, Any] | bytes,
    expected_binding: PhysicalWalRemoteAckBinding,
    expected_source_public_key: bytes,
    now: datetime,
    consumed_request_ids: Collection[str] = (),
    consumed_request_nonces: Collection[str] = (),
) -> VerifiedPhysicalWalRemoteAckRequest:
    """Verify one incoming source request before a destination considers it.

    The result is intentionally not a replay proof, receipt, writer permit,
    or acknowledgement.  The supplied replay sets are observations from the
    destination's future durable ledger; this pure verifier never persists
    them.  It exists so a runtime never has to sign an unpinned raw request
    merely to discover whether it was foreign.
    """

    binding = _normalise_binding(
        expected_binding, label="expected remote acknowledgement binding"
    )
    source_key = _public_key(
        expected_source_public_key, label="expected source public key"
    )
    observed_now = _utc(now, label="remote acknowledgement verification clock")
    request = _parse_request(source_request, expected_source_public_key=source_key)
    if request.binding != binding:
        raise PhysicalWalRemoteAckError(
            "remote acknowledgement request route, term, recipient, or lineage is foreign"
        )
    _fresh(request.issued_at, now=observed_now, label="remote acknowledgement request")
    _verify_request_replay_inputs(
        request,
        consumed_request_ids=consumed_request_ids,
        consumed_request_nonces=consumed_request_nonces,
    )
    result = VerifiedPhysicalWalRemoteAckRequest(
        source_request=request.raw,
        source_public_key=source_key,
        binding=binding,
        request_id=request.request_id,
        request_nonce=request.request_nonce,
        issued_at=request.issued_at,
    )
    object.__setattr__(result, "_capability", _VERIFIED_REQUEST_CAPABILITY)
    return result


def require_verified_physical_wal_remote_ack_request(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalWalRemoteAckRequest:
    """Recheck an opaque source request without consuming it or signing it."""

    if (
        type(value) is not VerifiedPhysicalWalRemoteAckRequest
        or value._capability is not _VERIFIED_REQUEST_CAPABILITY
    ):
        raise PhysicalWalRemoteAckError(
            "verified remote acknowledgement request capability is required"
        )
    binding = _normalise_binding(
        value.binding, label="verified remote acknowledgement binding"
    )
    source_key = _public_key(value.source_public_key, label="verified source public key")
    request = _parse_request(value.source_request, expected_source_public_key=source_key)
    observed_now = _utc(now, label="remote acknowledgement verification clock")
    _fresh(request.issued_at, now=observed_now, label="remote acknowledgement request")
    if (
        request.binding != binding
        or request.request_id != value.request_id
        or request.request_nonce != value.request_nonce
        or request.issued_at != value.issued_at
    ):
        raise PhysicalWalRemoteAckError("verified remote acknowledgement request was tampered")
    return value


def _verify_pair(
    request: _RequestFacts,
    receipt: _ReceiptFacts,
    *,
    expected_binding: PhysicalWalRemoteAckBinding,
    now: datetime,
    consumed_request_ids: Collection[str],
    consumed_request_nonces: Collection[str],
    consumed_receipt_ids: Collection[str],
    consumed_receipt_nonces: Collection[str],
    minimum_acknowledged_wal_lsn: str | None,
) -> None:
    if request.binding != expected_binding or receipt.binding != expected_binding:
        raise PhysicalWalRemoteAckError("remote acknowledgement route, term, recipient, or lineage is foreign")
    if (
        receipt.request_id != request.request_id
        or receipt.request_nonce != request.request_nonce
        or receipt.request_issued_at != request.issued_at
        or receipt.source_request_sha256 != hashlib.sha256(request.raw).hexdigest()
    ):
        raise PhysicalWalRemoteAckError("remote acknowledgement receipt does not bind exact source request")
    if len(
        {
            request.request_id,
            request.request_nonce,
            receipt.receipt_id,
            receipt.receipt_nonce,
        }
    ) != 4:
        raise PhysicalWalRemoteAckError("remote acknowledgement receipt identity reuses request identity")
    if receipt.acknowledged_at < request.issued_at:
        raise PhysicalWalRemoteAckError("remote acknowledgement receipt predates its request")
    _fresh(request.issued_at, now=now, label="remote acknowledgement request")
    _fresh(receipt.acknowledged_at, now=now, label="remote acknowledgement receipt")
    consumed_request_id_values = _consumed(
        consumed_request_ids, label="request ID", validator=_id
    )
    consumed_request_nonce_values = _consumed(
        consumed_request_nonces, label="request nonce", validator=_nonce
    )
    consumed_receipt_id_values = _consumed(
        consumed_receipt_ids, label="receipt ID", validator=_id
    )
    consumed_receipt_nonce_values = _consumed(
        consumed_receipt_nonces, label="receipt nonce", validator=_nonce
    )
    if request.request_id in consumed_request_id_values | consumed_receipt_id_values:
        raise PhysicalWalRemoteAckError("remote acknowledgement request ID was replayed")
    if request.request_nonce in consumed_request_nonce_values | consumed_receipt_nonce_values:
        raise PhysicalWalRemoteAckError("remote acknowledgement request nonce was replayed")
    if receipt.receipt_id in consumed_request_id_values | consumed_receipt_id_values:
        raise PhysicalWalRemoteAckError("remote acknowledgement receipt ID was replayed")
    if receipt.receipt_nonce in consumed_request_nonce_values | consumed_receipt_nonce_values:
        raise PhysicalWalRemoteAckError("remote acknowledgement receipt nonce was replayed")
    if minimum_acknowledged_wal_lsn is not None:
        _minimum, minimum_value = _lsn(
            minimum_acknowledged_wal_lsn, label="minimum acknowledged WAL LSN"
        )
        _target, target_value = _lsn(
            expected_binding.target_acknowledged_wal_lsn, label="target acknowledged WAL LSN"
        )
        if target_value < minimum_value:
            raise PhysicalWalRemoteAckError("remote acknowledgement WAL LSN regresses")


def verify_physical_wal_remote_ack_evidence(
    *,
    source_request: Mapping[str, Any] | bytes,
    destination_receipt: Mapping[str, Any] | bytes,
    expected_binding: PhysicalWalRemoteAckBinding,
    expected_source_public_key: bytes,
    expected_destination_public_key: bytes,
    now: datetime,
    consumed_request_ids: Collection[str] = (),
    consumed_request_nonces: Collection[str] = (),
    consumed_receipt_ids: Collection[str] = (),
    consumed_receipt_nonces: Collection[str] = (),
    minimum_acknowledged_wal_lsn: str | None = None,
) -> VerifiedPhysicalWalRemoteAckEvidence:
    """Verify pure signed evidence and mint no authority beyond that capability.

    Replay collections are caller-provided observations of a future durable
    ledger.  This pure function never persists them and cannot replace that
    ledger, real pull transport, destination commit verification, or a live
    Witness/Writer transition check.
    """

    binding = _normalise_binding(expected_binding, label="expected remote acknowledgement binding")
    source_key = _public_key(expected_source_public_key, label="expected source public key")
    destination_key = _public_key(
        expected_destination_public_key, label="expected destination public key"
    )
    if source_key == destination_key:
        raise PhysicalWalRemoteAckError("remote acknowledgement route keys must differ")
    observed_now = _utc(now, label="remote acknowledgement verification clock")
    request = _parse_request(source_request, expected_source_public_key=source_key)
    receipt = _parse_receipt(destination_receipt, expected_destination_public_key=destination_key)
    _verify_pair(
        request,
        receipt,
        expected_binding=binding,
        now=observed_now,
        consumed_request_ids=consumed_request_ids,
        consumed_request_nonces=consumed_request_nonces,
        consumed_receipt_ids=consumed_receipt_ids,
        consumed_receipt_nonces=consumed_receipt_nonces,
        minimum_acknowledged_wal_lsn=minimum_acknowledged_wal_lsn,
    )
    evidence = VerifiedPhysicalWalRemoteAckEvidence(
        source_request=request.raw,
        destination_receipt=receipt.raw,
        source_public_key=source_key,
        destination_public_key=destination_key,
        binding=binding,
        request_id=request.request_id,
        request_nonce=request.request_nonce,
        receipt_id=receipt.receipt_id,
        receipt_nonce=receipt.receipt_nonce,
        issued_at=request.issued_at,
        acknowledged_at=receipt.acknowledged_at,
    )
    object.__setattr__(evidence, "_capability", _VERIFIED_CAPABILITY)
    return evidence


def require_verified_physical_wal_remote_ack_evidence(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalWalRemoteAckEvidence:
    """Recheck opaque evidence signatures/freshness, but never authorize work."""

    if type(value) is not VerifiedPhysicalWalRemoteAckEvidence or value._capability is not _VERIFIED_CAPABILITY:
        raise PhysicalWalRemoteAckError("verified remote acknowledgement evidence capability is required")
    binding = _normalise_binding(value.binding, label="verified remote acknowledgement binding")
    source_key = _public_key(value.source_public_key, label="verified source public key")
    destination_key = _public_key(value.destination_public_key, label="verified destination public key")
    request = _parse_request(value.source_request, expected_source_public_key=source_key)
    receipt = _parse_receipt(value.destination_receipt, expected_destination_public_key=destination_key)
    observed_now = _utc(now, label="remote acknowledgement verification clock")
    _verify_pair(
        request,
        receipt,
        expected_binding=binding,
        now=observed_now,
        consumed_request_ids=(),
        consumed_request_nonces=(),
        consumed_receipt_ids=(),
        consumed_receipt_nonces=(),
        minimum_acknowledged_wal_lsn=None,
    )
    if (
        request.request_id != value.request_id
        or request.request_nonce != value.request_nonce
        or receipt.receipt_id != value.receipt_id
        or receipt.receipt_nonce != value.receipt_nonce
        or request.issued_at != value.issued_at
        or receipt.acknowledged_at != value.acknowledged_at
        or binding != value.binding
    ):
        raise PhysicalWalRemoteAckError("verified remote acknowledgement evidence was tampered")
    return value


def canonical_physical_wal_remote_ack_request_bytes(request: Mapping[str, Any]) -> bytes:
    """Return canonical request bytes after self-contained signature validation."""

    return _parse_request(request).raw


def canonical_physical_wal_remote_ack_receipt_bytes(receipt: Mapping[str, Any]) -> bytes:
    """Return canonical receipt bytes after self-contained signature validation."""

    return _parse_receipt(receipt).raw


__all__ = (
    "MAX_PHYSICAL_WAL_REMOTE_ACK_AGE_SECONDS",
    "MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES",
    "MAX_PHYSICAL_WAL_REMOTE_ACK_FUTURE_SKEW_SECONDS",
    "PHYSICAL_WAL_REMOTE_ACK_DEFAULT_ENABLED",
    "PHYSICAL_WAL_REMOTE_ACK_RECEIPT_SCHEMA",
    "PHYSICAL_WAL_REMOTE_ACK_REQUEST_SCHEMA",
    "PHYSICAL_WAL_REMOTE_ACK_SIGNATURE_ALGORITHM",
    "PHYSICAL_WAL_REMOTE_ACK_VERSION",
    "PhysicalWalRemoteAckBinding",
    "PhysicalWalRemoteAckError",
    "PhysicalWalRemoteAckObjectVersion",
    "PhysicalWalRemoteAckTermProjection",
    "VerifiedPhysicalWalRemoteAckEvidence",
    "VerifiedPhysicalWalRemoteAckRequest",
    "build_physical_wal_remote_ack_binding",
    "build_physical_wal_remote_ack_receipt",
    "build_physical_wal_remote_ack_request",
    "canonical_physical_wal_remote_ack_receipt_bytes",
    "canonical_physical_wal_remote_ack_request_bytes",
    "require_verified_physical_wal_remote_ack_evidence",
    "require_verified_physical_wal_remote_ack_request",
    "verify_physical_wal_remote_ack_evidence",
    "verify_physical_wal_remote_ack_request",
)
