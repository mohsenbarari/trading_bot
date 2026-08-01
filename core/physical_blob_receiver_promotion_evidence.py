"""Promotion-evidence admission for a receiver-verified Blob mapping.

The physical Blob spool's v1 inventory receipt proves an immutable source
publication only.  It is deliberately insufficient to represent a
receiver-restorable Blob frontier.  This local-only, default-disabled adapter
accepts the separate v2 receiver mapping capability and its exact pinned
mapping Object-Storage receipt, revalidates all signed v2 descriptors under a
locally fresh, unexpired Writer-Witness proof, and mints a narrower opaque
evidence capability.

It does not fetch Object Storage, decrypt an age payload, materialize a Blob,
persist an acceptance record, query Witness for a successor/revocation,
replay PostgreSQL, issue an acknowledgement, or authorize a promotion.  Its
replay LSN is a *mapping scope ceiling* only: it is pinned to the mapping
baseline and must never be represented as a receiver replay receipt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import re
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import core.physical_blob_object_storage_uploader as _storage
import core.physical_blob_receiver_inventory_mapping as _mapping
from core.append_only_sync_delta_batch import SHA256_RE
from core.physical_blob_object_storage_uploader import (
    PhysicalBlobInventoryShardObjectStorageReceipt,
    PhysicalBlobObjectStorageReceipt,
    VerifiedPhysicalBlobObjectStorageBinding,
    verify_physical_blob_object_storage_receipt,
)
from core.physical_blob_receiver_inventory_mapping import (
    MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES,
    PhysicalBlobReceiverInventoryMappingReceipt,
    VerifiedPhysicalBlobReceiverInventoryMapping,
    verify_physical_blob_receiver_inventory_mapping_receipt,
)


__all__ = (
    "PHYSICAL_BLOB_RECEIVER_PROMOTION_EVIDENCE_DEFAULT_ENABLED",
    "PHYSICAL_BLOB_RECEIVER_PROMOTION_EVIDENCE_SCHEMA",
    "PHYSICAL_WAL_PROMOTION_V2_BLOB_REQUIREMENT_SCHEMA",
    "PhysicalBlobReceiverPromotionBlobObject",
    "PhysicalBlobReceiverPromotionEvidenceConfig",
    "PhysicalBlobReceiverPromotionEvidenceError",
    "VerifiedPhysicalBlobReceiverPromotionEvidence",
    "VerifiedPhysicalWalPromotionV2BlobRequirement",
    "build_physical_wal_promotion_v2_blob_requirement",
    "require_physical_wal_promotion_v2_blob_requirement",
    "require_verified_physical_blob_receiver_promotion_evidence",
    "verify_physical_blob_receiver_promotion_evidence",
)


PHYSICAL_BLOB_RECEIVER_PROMOTION_EVIDENCE_SCHEMA = (
    "gold-trade-physical-blob-receiver-promotion-evidence-v1"
)
PHYSICAL_WAL_PROMOTION_V2_BLOB_REQUIREMENT_SCHEMA = (
    "gold-trade-physical-wal-promotion-v2-blob-requirement-v1"
)
PHYSICAL_BLOB_RECEIVER_PROMOTION_EVIDENCE_DEFAULT_ENABLED = False

_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
_VERIFIED_PROMOTION_EVIDENCE_CAPABILITY = object()
_VERIFIED_PROMOTION_V2_BLOB_REQUIREMENT_CAPABILITY = object()


class PhysicalBlobReceiverPromotionEvidenceError(ValueError):
    """A Blob mapping cannot safely become promotion-evidence input."""


@dataclass(frozen=True)
class PhysicalBlobReceiverPromotionEvidenceConfig:
    """Explicit pins for this pure default-disabled evidence adapter."""

    mapping_signer_public_key: bytes = b""
    blob_receipt_signer_public_key: bytes = b""
    enabled: bool = PHYSICAL_BLOB_RECEIVER_PROMOTION_EVIDENCE_DEFAULT_ENABLED


@dataclass(frozen=True)
class PhysicalBlobReceiverPromotionBlobObject:
    """One ordered v2 Blob Object identity committed by the mapping."""

    ordinal: int
    source_record_id: str
    plaintext_sha256: str
    plaintext_bytes: int
    handoff_descriptor_sha256: str
    blob_receipt_sha256: str
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int


@dataclass(frozen=True)
class VerifiedPhysicalBlobReceiverPromotionEvidence:
    """Opaque mapping-scoped input for a future Blob-frontier evidence signer.

    This is intentionally less than an Object Storage fetch/decrypt result and
    less than a PostgreSQL replay/remote-ack/promotion capability.  It carries
    enough exact receipt identities for a later coordinator to require this
    v2 bridge rather than accepting a raw v1 inventory receipt alone.
    """

    schema: str
    canonical_mapping_plaintext: bytes
    mapping_receipt: PhysicalBlobReceiverInventoryMappingReceipt
    original_v1_inventory_receipt: PhysicalBlobInventoryShardObjectStorageReceipt
    blob_object_receipts: tuple[PhysicalBlobObjectStorageReceipt, ...]
    blob_objects: tuple[PhysicalBlobReceiverPromotionBlobObject, ...]
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    destination_age_recipient: str
    timeline_id: int
    mapping_plaintext_sha256: str
    mapping_plaintext_bytes: int
    mapping_receipt_sha256: str
    original_v1_inventory_sha256: str
    original_v1_inventory_bytes: int
    original_v1_inventory_receipt_sha256: str
    shard_ordinal: int
    entry_count: int
    blob_receipts_sha256: str
    mapping_eligible_replay_wal_lsn: str
    mapping_signer_public_key: bytes
    blob_receipt_signer_public_key: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalWalPromotionV2BlobRequirement:
    """Opaque v2-only Blob prerequisite for a future promotion coordinator.

    Legacy v1 inventory/frontier receipts have no path to mint this type.  A
    future physical-WAL promotion-v2 signer must require this capability
    before it can consider asserting a Blob-complete frontier.
    """

    schema: str
    receiver_promotion_evidence: VerifiedPhysicalBlobReceiverPromotionEvidence
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    timeline_id: int
    mapping_plaintext_sha256: str
    mapping_receipt_sha256: str
    mapping_object_key: str
    mapping_object_version_id: str
    mapping_ciphertext_sha256: str
    mapping_ciphertext_bytes: int
    original_v1_inventory_receipt_sha256: str
    blob_receipts_sha256: str
    entry_count: int
    mapping_eligible_replay_wal_lsn: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _EvidenceFacts:
    binding: Any
    mapping: Any
    mapping_receipt: PhysicalBlobReceiverInventoryMappingReceipt
    original_v1_inventory_receipt: PhysicalBlobInventoryShardObjectStorageReceipt
    blob_object_receipts: tuple[PhysicalBlobObjectStorageReceipt, ...]
    blob_objects: tuple[PhysicalBlobReceiverPromotionBlobObject, ...]


def _error_from_mapping(
    exc: Exception, *, label: str
) -> PhysicalBlobReceiverPromotionEvidenceError:
    return PhysicalBlobReceiverPromotionEvidenceError(label)


def _public_key(value: object, *, label: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32 or value == b"\x00" * 32:
        raise PhysicalBlobReceiverPromotionEvidenceError(f"{label} is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError as exc:
        raise PhysicalBlobReceiverPromotionEvidenceError(f"{label} is invalid") from exc
    return value


def _sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        raise PhysicalBlobReceiverPromotionEvidenceError(f"{label} is invalid")
    return value


def _lsn(value: object, *, label: str) -> tuple[str, int]:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        raise PhysicalBlobReceiverPromotionEvidenceError(f"{label} is invalid")
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _normalise_config(value: object) -> tuple[bytes, bytes]:
    if type(value) is not PhysicalBlobReceiverPromotionEvidenceConfig:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "receiver Blob promotion-evidence config is invalid"
        )
    if value.enabled is not True:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "receiver Blob promotion-evidence adapter is disabled"
        )
    return (
        _public_key(value.mapping_signer_public_key, label="receiver mapping signer public key"),
        _public_key(
            value.blob_receipt_signer_public_key,
            label="receiver Blob receipt signer public key",
        ),
    )


def _binding(
    value: VerifiedPhysicalBlobObjectStorageBinding, *, now: datetime
) -> Any:
    try:
        return _mapping._binding_facts(value, now=now)
    except _mapping.PhysicalBlobReceiverInventoryMappingError as exc:
        raise _error_from_mapping(
            exc, label="receiver Blob promotion-evidence binding is not live and authorized"
        ) from exc


def _require_mapping_capability(value: object) -> VerifiedPhysicalBlobReceiverInventoryMapping:
    if (
        type(value) is not VerifiedPhysicalBlobReceiverInventoryMapping
        or value._capability is not _mapping._VERIFIED_MAPPING_CAPABILITY
    ):
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified receiver Blob mapping capability is required"
        )
    return value


def _positive_int(value: object, *, label: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise PhysicalBlobReceiverPromotionEvidenceError(f"{label} is invalid")
    return value


def _validate_verified_mapping_wrapper(
    *,
    value: object,
    mapping_signer_public_key: bytes,
) -> tuple[
    VerifiedPhysicalBlobReceiverInventoryMapping,
    PhysicalBlobReceiverInventoryMappingReceipt,
]:
    """Validate all capability projection scalars before any ``==`` use.

    A mapping capability is a dataclass wrapper around canonical bytes.  Its
    opaque marker prevents ordinary construction, but an in-process mutation
    must still not exploit Python's ``True == 1`` behavior for a shard,
    timeline, epoch, entry count, or byte count.
    """

    mapping = _require_mapping_capability(value)
    if not isinstance(mapping.canonical_plaintext, bytes) or not mapping.canonical_plaintext:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified receiver Blob mapping plaintext is invalid"
        )
    for field_name in (
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "baseline_generation_id",
        "baseline_manifest_sha256",
        "baseline_wal_lsn",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
        "destination_age_recipient",
        "original_v1_inventory_sha256",
        "blob_receipts_sha256",
    ):
        if not isinstance(getattr(mapping, field_name), str) or not getattr(mapping, field_name):
            raise PhysicalBlobReceiverPromotionEvidenceError(
                f"verified receiver Blob mapping {field_name} is invalid"
            )
    _positive_int(
        mapping.writer_epoch,
        label="verified receiver Blob mapping writer epoch",
        maximum=2**63 - 1,
    )
    _positive_int(
        mapping.timeline_id,
        label="verified receiver Blob mapping timeline",
        maximum=0xFFFFFFFF,
    )
    _positive_int(
        mapping.original_v1_inventory_bytes,
        label="verified receiver Blob mapping original v1 inventory bytes",
        maximum=32 * 1024 * 1024,
    )
    _positive_int(
        mapping.shard_ordinal,
        label="verified receiver Blob mapping shard ordinal",
        maximum=2**63 - 1,
    )
    _positive_int(
        mapping.entry_count,
        label="verified receiver Blob mapping entry count",
        maximum=16_384,
    )
    if type(mapping.mapping_receipt) is not PhysicalBlobReceiverInventoryMappingReceipt:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified receiver Blob mapping receipt is invalid"
        )
    try:
        normalized_receipt = verify_physical_blob_receiver_inventory_mapping_receipt(
            receipt=mapping.mapping_receipt,
            mapping_signer_public_key=mapping_signer_public_key,
        )
    except _mapping.PhysicalBlobReceiverInventoryMappingError as exc:
        raise _error_from_mapping(
            exc, label="verified receiver Blob mapping receipt is invalid"
        ) from exc
    if normalized_receipt != mapping.mapping_receipt:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified receiver Blob mapping receipt wrapper was tampered"
        )
    return mapping, normalized_receipt


def _require_pinned_mapping_receipt(
    *,
    receipt: object,
    expected_public_key: bytes,
    verified_mapping_receipt: PhysicalBlobReceiverInventoryMappingReceipt,
) -> PhysicalBlobReceiverInventoryMappingReceipt:
    if type(receipt) is not PhysicalBlobReceiverInventoryMappingReceipt:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "exact typed receiver mapping Object-Storage receipt is required; v1 Blob receipts are insufficient"
        )
    try:
        normalized = verify_physical_blob_receiver_inventory_mapping_receipt(
            receipt=receipt,
            mapping_signer_public_key=expected_public_key,
        )
    except _mapping.PhysicalBlobReceiverInventoryMappingError as exc:
        raise _error_from_mapping(
            exc, label="pinned receiver mapping Object-Storage receipt is invalid"
        ) from exc
    if normalized != verified_mapping_receipt:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "pinned receiver mapping Object-Storage receipt does not match verified mapping"
        )
    return normalized


def _mapping_facts(
    *,
    verified_mapping: VerifiedPhysicalBlobReceiverInventoryMapping,
    mapping_signer_public_key: bytes,
    blob_receipt_signer_public_key: bytes,
    binding: Any,
) -> Any:
    try:
        facts = _mapping._parse_mapping_plaintext(
            raw=verified_mapping.canonical_plaintext,
            mapping_signer_public_key=mapping_signer_public_key,
            blob_receipt_signer_public_key=blob_receipt_signer_public_key,
            binding=binding,
            original_v1_inventory=None,
        )
    except _mapping.PhysicalBlobReceiverInventoryMappingError as exc:
        raise _error_from_mapping(exc, label="verified receiver Blob mapping is invalid") from exc
    if (
        facts.raw != verified_mapping.canonical_plaintext
        or facts.source_site != verified_mapping.source_site
        or facts.destination_site != verified_mapping.destination_site
        or facts.campaign_id != verified_mapping.campaign_id
        or facts.release_sha != verified_mapping.release_sha
        or facts.baseline_generation_id != verified_mapping.baseline_generation_id
        or facts.baseline_manifest_sha256 != verified_mapping.baseline_manifest_sha256
        or facts.baseline_wal_lsn != verified_mapping.baseline_wal_lsn
        or facts.writer_epoch != verified_mapping.writer_epoch
        or facts.writer_lease_id != verified_mapping.writer_lease_id
        or facts.witnessed_term_proof_sha256
        != verified_mapping.witnessed_term_proof_sha256
        or facts.destination_age_recipient != verified_mapping.destination_age_recipient
        or facts.timeline_id != verified_mapping.timeline_id
        or facts.original_v1_inventory_sha256
        != verified_mapping.original_v1_inventory_sha256
        or facts.original_v1_inventory_bytes
        != verified_mapping.original_v1_inventory_bytes
        or facts.shard_ordinal != verified_mapping.shard_ordinal
        or facts.entry_count != verified_mapping.entry_count
        or facts.blob_receipts_sha256 != verified_mapping.blob_receipts_sha256
    ):
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified receiver Blob mapping capability was tampered"
        )
    return facts


def _typed_original_v1_receipt(
    *, raw: bytes, blob_receipt_signer_public_key: bytes
) -> PhysicalBlobInventoryShardObjectStorageReceipt:
    try:
        receipt = verify_physical_blob_object_storage_receipt(
            receipt=raw,
            receipt_signer_public_key=blob_receipt_signer_public_key,
        )
    except _storage.PhysicalBlobObjectStorageUploaderError as exc:
        raise _error_from_mapping(
            exc, label="receiver mapping original v1 inventory receipt is invalid"
        ) from exc
    if type(receipt) is not PhysicalBlobInventoryShardObjectStorageReceipt:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "receiver mapping original receipt is not a typed v1 inventory Object-Storage receipt"
        )
    return receipt


def _typed_blob_object(
    *,
    mapping_entry: Any,
    blob_receipt_signer_public_key: bytes,
) -> tuple[PhysicalBlobObjectStorageReceipt, PhysicalBlobReceiverPromotionBlobObject]:
    try:
        receipt = verify_physical_blob_object_storage_receipt(
            receipt=mapping_entry.blob_receipt_raw,
            receipt_signer_public_key=blob_receipt_signer_public_key,
        )
    except _storage.PhysicalBlobObjectStorageUploaderError as exc:
        raise _error_from_mapping(
            exc, label="receiver mapping Blob Object receipt is invalid"
        ) from exc
    if type(receipt) is not PhysicalBlobObjectStorageReceipt:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "receiver mapping entry is not a typed v2 Blob Object-Storage receipt"
        )
    if (
        receipt.receipt_sha256 != mapping_entry.blob_receipt_sha256
        or receipt.source_record_id != mapping_entry.source_record_id
        or receipt.plaintext_sha256 != mapping_entry.content_sha256
        or receipt.plaintext_bytes != mapping_entry.content_bytes
        or receipt.handoff_descriptor_sha256 != mapping_entry.handoff_descriptor_sha256
        or receipt.object_key != mapping_entry.object_key
        or receipt.version_id != mapping_entry.version_id
        or receipt.ciphertext_sha256 != mapping_entry.ciphertext_sha256
        or receipt.ciphertext_bytes != mapping_entry.ciphertext_bytes
    ):
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "receiver mapping Blob Object receipt does not match its pinned descriptor"
        )
    return (
        receipt,
        PhysicalBlobReceiverPromotionBlobObject(
            ordinal=mapping_entry.ordinal,
            source_record_id=receipt.source_record_id,
            plaintext_sha256=receipt.plaintext_sha256,
            plaintext_bytes=receipt.plaintext_bytes,
            handoff_descriptor_sha256=receipt.handoff_descriptor_sha256,
            blob_receipt_sha256=receipt.receipt_sha256,
            object_key=receipt.object_key,
            version_id=receipt.version_id,
            ciphertext_sha256=receipt.ciphertext_sha256,
            ciphertext_bytes=receipt.ciphertext_bytes,
        ),
    )


def _evidence_facts(
    *,
    verified_mapping: VerifiedPhysicalBlobReceiverInventoryMapping,
    pinned_mapping_receipt: object,
    mapping_signer_public_key: bytes,
    blob_receipt_signer_public_key: bytes,
    verified_binding: VerifiedPhysicalBlobObjectStorageBinding,
    now: datetime,
) -> _EvidenceFacts:
    mapping_capability, normalized_mapping_receipt = _validate_verified_mapping_wrapper(
        value=verified_mapping,
        mapping_signer_public_key=mapping_signer_public_key,
    )
    binding = _binding(verified_binding, now=now)
    pinned_receipt = _require_pinned_mapping_receipt(
        receipt=pinned_mapping_receipt,
        expected_public_key=mapping_signer_public_key,
        verified_mapping_receipt=normalized_mapping_receipt,
    )
    mapping = _mapping_facts(
        verified_mapping=mapping_capability,
        mapping_signer_public_key=mapping_signer_public_key,
        blob_receipt_signer_public_key=blob_receipt_signer_public_key,
        binding=binding,
    )
    try:
        receipt_facts = _mapping._parse_mapping_receipt(
            pinned_receipt.signed_receipt,
            mapping_signer_public_key=mapping_signer_public_key,
        )
        _mapping._require_mapping_binding(receipt_facts, binding=binding)
        expected_key = _mapping._mapping_object_key(
            binding=binding,
            original_v1_inventory_sha256=mapping.original_v1_inventory_sha256,
            mapping_plaintext_sha256=hashlib.sha256(mapping.raw).hexdigest(),
        )
    except _mapping.PhysicalBlobReceiverInventoryMappingError as exc:
        raise _error_from_mapping(
            exc, label="pinned receiver mapping Object-Storage receipt is invalid"
        ) from exc
    if (
        receipt_facts.mapping_plaintext_sha256 != hashlib.sha256(mapping.raw).hexdigest()
        or receipt_facts.mapping_plaintext_bytes != len(mapping.raw)
        or receipt_facts.original_v1_inventory_sha256
        != mapping.original_v1_inventory_sha256
        or receipt_facts.original_v1_inventory_bytes
        != mapping.original_v1_inventory_bytes
        or receipt_facts.shard_ordinal != mapping.shard_ordinal
        or receipt_facts.entry_count != mapping.entry_count
        or receipt_facts.blob_receipts_sha256 != mapping.blob_receipts_sha256
        or pinned_receipt.object_key != expected_key
    ):
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "pinned receiver mapping receipt does not match mapping plaintext or descriptor"
        )
    original_v1_receipt = _typed_original_v1_receipt(
        raw=mapping.original_v1_inventory_receipt_raw,
        blob_receipt_signer_public_key=blob_receipt_signer_public_key,
    )
    if (
        original_v1_receipt.receipt_sha256
        != hashlib.sha256(mapping.original_v1_inventory_receipt_raw).hexdigest()
        or original_v1_receipt.plaintext_sha256 != mapping.original_v1_inventory_sha256
        or original_v1_receipt.plaintext_bytes != mapping.original_v1_inventory_bytes
        or original_v1_receipt.shard_ordinal != mapping.shard_ordinal
        or original_v1_receipt.entry_count != mapping.entry_count
        or original_v1_receipt.blob_receipts_sha256 != mapping.blob_receipts_sha256
        or original_v1_receipt.timeline_id != mapping.timeline_id
        or original_v1_receipt.route_binding_sha256 != mapping.route_binding_sha256
    ):
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "receiver mapping original v1 inventory receipt does not match pinned mapping"
        )
    blob_pairs = tuple(
        _typed_blob_object(
            mapping_entry=entry,
            blob_receipt_signer_public_key=blob_receipt_signer_public_key,
        )
        for entry in mapping.entries
    )
    blob_object_receipts = tuple(item[0] for item in blob_pairs)
    blob_objects = tuple(item[1] for item in blob_pairs)
    if (
        len(blob_objects) != mapping.entry_count
        or tuple(item.ordinal for item in blob_objects)
        != tuple(range(1, mapping.entry_count + 1))
    ):
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "receiver mapping Blob Object receipt ordering is invalid"
        )
    return _EvidenceFacts(
        binding=binding,
        mapping=mapping,
        mapping_receipt=pinned_receipt,
        original_v1_inventory_receipt=original_v1_receipt,
        blob_object_receipts=blob_object_receipts,
        blob_objects=blob_objects,
    )


def _validate_verified_wrapper(
    value: VerifiedPhysicalBlobReceiverPromotionEvidence,
) -> None:
    """Reject Python equality coercions before comparing a stale capability.

    The later mapping reparse validates canonical bytes, but this wrapper also
    carries a projection that a future coordinator might inspect.  In
    particular, ``True == 1`` must never let a forged shard ordinal, entry
    count, epoch, or byte count survive the capability recheck.
    """

    if not isinstance(value.canonical_mapping_plaintext, bytes) or not value.canonical_mapping_plaintext:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified mapping plaintext is invalid"
        )
    _sha256(value.mapping_plaintext_sha256, label="verified mapping plaintext SHA-256")
    _sha256(value.mapping_receipt_sha256, label="verified mapping receipt SHA-256")
    _sha256(value.original_v1_inventory_sha256, label="verified original v1 inventory SHA-256")
    _sha256(
        value.original_v1_inventory_receipt_sha256,
        label="verified original v1 inventory receipt SHA-256",
    )
    _sha256(value.route_binding_sha256, label="verified route binding SHA-256")
    _sha256(value.witnessed_term_proof_sha256, label="verified witnessed term proof SHA-256")
    _sha256(value.blob_receipts_sha256, label="verified Blob receipt-set SHA-256")
    _positive_int(
        value.mapping_plaintext_bytes,
        label="verified mapping plaintext byte count",
        maximum=MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES,
    )
    _positive_int(
        value.original_v1_inventory_bytes,
        label="verified original v1 inventory byte count",
        maximum=32 * 1024 * 1024,
    )
    _positive_int(value.writer_epoch, label="verified writer epoch", maximum=2**63 - 1)
    _positive_int(value.timeline_id, label="verified timeline", maximum=0xFFFFFFFF)
    _positive_int(value.shard_ordinal, label="verified shard ordinal", maximum=2**63 - 1)
    entry_count = _positive_int(value.entry_count, label="verified entry count", maximum=16_384)
    if type(value.mapping_receipt) is not PhysicalBlobReceiverInventoryMappingReceipt:
        raise PhysicalBlobReceiverPromotionEvidenceError("verified mapping receipt is invalid")
    if type(value.original_v1_inventory_receipt) is not PhysicalBlobInventoryShardObjectStorageReceipt:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified original v1 inventory receipt is invalid"
        )
    if (
        type(value.blob_object_receipts) is not tuple
        or len(value.blob_object_receipts) != entry_count
        or type(value.blob_objects) is not tuple
        or len(value.blob_objects) != entry_count
    ):
        raise PhysicalBlobReceiverPromotionEvidenceError("verified Blob Object list is invalid")
    for ordinal, (receipt, item) in enumerate(
        zip(value.blob_object_receipts, value.blob_objects, strict=True), start=1
    ):
        if type(receipt) is not PhysicalBlobObjectStorageReceipt:
            raise PhysicalBlobReceiverPromotionEvidenceError(
                "verified Blob Object receipt is invalid"
            )
        if type(item) is not PhysicalBlobReceiverPromotionBlobObject:
            raise PhysicalBlobReceiverPromotionEvidenceError(
                "verified Blob Object item is invalid"
            )
        if _positive_int(item.ordinal, label="verified Blob Object ordinal", maximum=16_384) != ordinal:
            raise PhysicalBlobReceiverPromotionEvidenceError(
                "verified Blob Object ordering is invalid"
            )
        if not isinstance(item.source_record_id, str) or not item.source_record_id:
            raise PhysicalBlobReceiverPromotionEvidenceError(
                "verified Blob Object source record is invalid"
            )
        _sha256(item.plaintext_sha256, label="verified Blob Object plaintext SHA-256")
        _positive_int(
            item.plaintext_bytes,
            label="verified Blob Object plaintext byte count",
            maximum=_storage.MAX_PHYSICAL_BLOB_BYTES,
        )
        _sha256(item.handoff_descriptor_sha256, label="verified Blob Object handoff SHA-256")
        _sha256(item.blob_receipt_sha256, label="verified Blob Object receipt SHA-256")
        if not isinstance(item.object_key, str) or not item.object_key:
            raise PhysicalBlobReceiverPromotionEvidenceError(
                "verified Blob Object key is invalid"
            )
        if not isinstance(item.version_id, str) or not item.version_id or item.version_id == "null":
            raise PhysicalBlobReceiverPromotionEvidenceError(
                "verified Blob Object version is invalid"
            )
        _sha256(item.ciphertext_sha256, label="verified Blob Object ciphertext SHA-256")
        _positive_int(
            item.ciphertext_bytes,
            label="verified Blob Object ciphertext byte count",
            maximum=_storage.MAX_PHYSICAL_BLOB_BYTES + 32 * 1024 * 1024,
        )
    for field_name in (
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "baseline_generation_id",
        "baseline_manifest_sha256",
        "baseline_wal_lsn",
        "writer_lease_id",
        "destination_age_recipient",
    ):
        if not isinstance(getattr(value, field_name), str) or not getattr(value, field_name):
            raise PhysicalBlobReceiverPromotionEvidenceError(
                f"verified {field_name} is invalid"
            )


def _verify_projected_blob_receipt_wrappers(
    *,
    value: VerifiedPhysicalBlobReceiverPromotionEvidence,
    blob_receipt_signer_public_key: bytes,
) -> None:
    """Verify typed projected receipt wrappers before equality comparison."""

    try:
        original = verify_physical_blob_object_storage_receipt(
            receipt=value.original_v1_inventory_receipt,
            receipt_signer_public_key=blob_receipt_signer_public_key,
        )
    except _storage.PhysicalBlobObjectStorageUploaderError as exc:
        raise _error_from_mapping(
            exc, label="verified original v1 inventory receipt wrapper is invalid"
        ) from exc
    if (
        type(original) is not PhysicalBlobInventoryShardObjectStorageReceipt
        or original != value.original_v1_inventory_receipt
    ):
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified original v1 inventory receipt wrapper was tampered"
        )
    for receipt in value.blob_object_receipts:
        try:
            normalized = verify_physical_blob_object_storage_receipt(
                receipt=receipt,
                receipt_signer_public_key=blob_receipt_signer_public_key,
            )
        except _storage.PhysicalBlobObjectStorageUploaderError as exc:
            raise _error_from_mapping(
                exc, label="verified Blob Object receipt wrapper is invalid"
            ) from exc
        if type(normalized) is not PhysicalBlobObjectStorageReceipt or normalized != receipt:
            raise PhysicalBlobReceiverPromotionEvidenceError(
                "verified Blob Object receipt wrapper was tampered"
            )


def verify_physical_blob_receiver_promotion_evidence(
    *,
    config: PhysicalBlobReceiverPromotionEvidenceConfig,
    verified_mapping: VerifiedPhysicalBlobReceiverInventoryMapping,
    pinned_mapping_receipt: PhysicalBlobReceiverInventoryMappingReceipt,
    requested_replay_wal_lsn: str,
    verified_binding: VerifiedPhysicalBlobObjectStorageBinding,
    now: datetime,
) -> VerifiedPhysicalBlobReceiverPromotionEvidence:
    """Mint a mapping-scoped, non-authorizing promotion-evidence capability.

    ``requested_replay_wal_lsn`` must be exactly the mapping's pinned
    baseline LSN.  A v2 mapping does not prove Blob coverage beyond that
    point, so this function rejects both a later and an earlier substitute.
    It also intentionally requires the *typed v2 mapping receipt*; a raw or
    typed v1 Blob inventory receipt is rejected before any promotion-evidence
    capability can be minted.
    """

    mapping_key, blob_key = _normalise_config(config)
    replay_text, _replay_value = _lsn(
        requested_replay_wal_lsn, label="requested Blob mapping replay WAL LSN"
    )
    facts = _evidence_facts(
        verified_mapping=verified_mapping,
        pinned_mapping_receipt=pinned_mapping_receipt,
        mapping_signer_public_key=mapping_key,
        blob_receipt_signer_public_key=blob_key,
        verified_binding=verified_binding,
        now=now,
    )
    if replay_text != facts.mapping.baseline_wal_lsn:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "requested replay WAL LSN is outside the receiver mapping baseline scope"
        )
    result = VerifiedPhysicalBlobReceiverPromotionEvidence(
        schema=PHYSICAL_BLOB_RECEIVER_PROMOTION_EVIDENCE_SCHEMA,
        canonical_mapping_plaintext=facts.mapping.raw,
        mapping_receipt=facts.mapping_receipt,
        original_v1_inventory_receipt=facts.original_v1_inventory_receipt,
        blob_object_receipts=facts.blob_object_receipts,
        blob_objects=facts.blob_objects,
        source_site=facts.mapping.source_site,
        destination_site=facts.mapping.destination_site,
        campaign_id=facts.mapping.campaign_id,
        release_sha=facts.mapping.release_sha,
        baseline_generation_id=facts.mapping.baseline_generation_id,
        baseline_manifest_sha256=facts.mapping.baseline_manifest_sha256,
        baseline_wal_lsn=facts.mapping.baseline_wal_lsn,
        route_binding_sha256=facts.mapping.route_binding_sha256,
        writer_epoch=facts.mapping.writer_epoch,
        writer_lease_id=facts.mapping.writer_lease_id,
        witnessed_term_proof_sha256=facts.mapping.witnessed_term_proof_sha256,
        destination_age_recipient=facts.mapping.destination_age_recipient,
        timeline_id=facts.mapping.timeline_id,
        mapping_plaintext_sha256=facts.mapping_receipt.mapping_plaintext_sha256,
        mapping_plaintext_bytes=facts.mapping_receipt.mapping_plaintext_bytes,
        mapping_receipt_sha256=facts.mapping_receipt.receipt_sha256,
        original_v1_inventory_sha256=facts.mapping.original_v1_inventory_sha256,
        original_v1_inventory_bytes=facts.mapping.original_v1_inventory_bytes,
        original_v1_inventory_receipt_sha256=facts.original_v1_inventory_receipt.receipt_sha256,
        shard_ordinal=facts.mapping.shard_ordinal,
        entry_count=facts.mapping.entry_count,
        blob_receipts_sha256=facts.mapping.blob_receipts_sha256,
        mapping_eligible_replay_wal_lsn=replay_text,
        mapping_signer_public_key=mapping_key,
        blob_receipt_signer_public_key=blob_key,
    )
    object.__setattr__(result, "_capability", _VERIFIED_PROMOTION_EVIDENCE_CAPABILITY)
    require_verified_physical_blob_receiver_promotion_evidence(
        result,
        config=config,
        verified_binding=verified_binding,
        now=now,
    )
    return result


def require_verified_physical_blob_receiver_promotion_evidence(
    value: object,
    *,
    config: PhysicalBlobReceiverPromotionEvidenceConfig,
    verified_binding: VerifiedPhysicalBlobObjectStorageBinding,
    now: datetime,
) -> VerifiedPhysicalBlobReceiverPromotionEvidence:
    """Revalidate a mapping-scoped evidence capability before later use.

    This is the narrow required input for a future Blob-frontier/promotion
    evidence coordinator.  It remains deliberately unusable as a replay or
    promotion action itself.
    """

    if (
        type(value) is not VerifiedPhysicalBlobReceiverPromotionEvidence
        or value._capability is not _VERIFIED_PROMOTION_EVIDENCE_CAPABILITY
    ):
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified receiver Blob promotion-evidence capability is required"
        )
    _validate_verified_wrapper(value)
    mapping_key, blob_key = _normalise_config(config)
    if (
        value.mapping_signer_public_key != mapping_key
        or value.blob_receipt_signer_public_key != blob_key
    ):
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified receiver Blob promotion-evidence signer pins do not match configuration"
        )
    if value.schema != PHYSICAL_BLOB_RECEIVER_PROMOTION_EVIDENCE_SCHEMA:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified receiver Blob promotion-evidence schema is invalid"
        )
    _verify_projected_blob_receipt_wrappers(
        value=value, blob_receipt_signer_public_key=blob_key
    )
    _sha256(value.mapping_plaintext_sha256, label="verified mapping plaintext SHA-256")
    if (
        type(value.mapping_plaintext_bytes) is not int
        or not 1 <= value.mapping_plaintext_bytes <= MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES
    ):
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified mapping plaintext byte count is invalid"
        )
    facts = _evidence_facts(
        verified_mapping=_mapping_capability_from_promotion_evidence(value),
        pinned_mapping_receipt=value.mapping_receipt,
        mapping_signer_public_key=mapping_key,
        blob_receipt_signer_public_key=blob_key,
        verified_binding=verified_binding,
        now=now,
    )
    replay_text, _replay_value = _lsn(
        value.mapping_eligible_replay_wal_lsn,
        label="verified Blob mapping replay WAL LSN",
    )
    if replay_text != facts.mapping.baseline_wal_lsn:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified Blob mapping replay WAL LSN is outside baseline scope"
        )
    if (
        value.canonical_mapping_plaintext != facts.mapping.raw
        or value.mapping_receipt != facts.mapping_receipt
        or value.original_v1_inventory_receipt != facts.original_v1_inventory_receipt
        or value.blob_object_receipts != facts.blob_object_receipts
        or value.blob_objects != facts.blob_objects
        or value.source_site != facts.mapping.source_site
        or value.destination_site != facts.mapping.destination_site
        or value.campaign_id != facts.mapping.campaign_id
        or value.release_sha != facts.mapping.release_sha
        or value.baseline_generation_id != facts.mapping.baseline_generation_id
        or value.baseline_manifest_sha256 != facts.mapping.baseline_manifest_sha256
        or value.baseline_wal_lsn != facts.mapping.baseline_wal_lsn
        or value.route_binding_sha256 != facts.mapping.route_binding_sha256
        or value.writer_epoch != facts.mapping.writer_epoch
        or value.writer_lease_id != facts.mapping.writer_lease_id
        or value.witnessed_term_proof_sha256
        != facts.mapping.witnessed_term_proof_sha256
        or value.destination_age_recipient != facts.mapping.destination_age_recipient
        or value.timeline_id != facts.mapping.timeline_id
        or value.mapping_plaintext_sha256
        != facts.mapping_receipt.mapping_plaintext_sha256
        or value.mapping_plaintext_bytes
        != facts.mapping_receipt.mapping_plaintext_bytes
        or value.mapping_receipt_sha256 != facts.mapping_receipt.receipt_sha256
        or value.original_v1_inventory_sha256
        != facts.mapping.original_v1_inventory_sha256
        or value.original_v1_inventory_bytes
        != facts.mapping.original_v1_inventory_bytes
        or value.original_v1_inventory_receipt_sha256
        != facts.original_v1_inventory_receipt.receipt_sha256
        or value.shard_ordinal != facts.mapping.shard_ordinal
        or value.entry_count != facts.mapping.entry_count
        or value.blob_receipts_sha256 != facts.mapping.blob_receipts_sha256
    ):
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified receiver Blob promotion-evidence capability was tampered"
        )
    return value


def build_physical_wal_promotion_v2_blob_requirement(
    *,
    receiver_promotion_evidence: VerifiedPhysicalBlobReceiverPromotionEvidence,
    config: PhysicalBlobReceiverPromotionEvidenceConfig,
    verified_binding: VerifiedPhysicalBlobObjectStorageBinding,
    now: datetime,
) -> VerifiedPhysicalWalPromotionV2BlobRequirement:
    """Mint the v2-only Blob prerequisite for a future promotion coordinator.

    This deliberately takes no v1 inventory receipt, legacy frontier mapping,
    raw JSON, or generic Object-Storage descriptor.  The only admission route
    is the opaque v2 receiver-promotion evidence capability above.
    """

    if type(receiver_promotion_evidence) is not VerifiedPhysicalBlobReceiverPromotionEvidence:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "physical-WAL promotion-v2 Blob requirement requires verified v2 receiver mapping evidence"
        )
    evidence = require_verified_physical_blob_receiver_promotion_evidence(
        receiver_promotion_evidence,
        config=config,
        verified_binding=verified_binding,
        now=now,
    )
    result = VerifiedPhysicalWalPromotionV2BlobRequirement(
        schema=PHYSICAL_WAL_PROMOTION_V2_BLOB_REQUIREMENT_SCHEMA,
        receiver_promotion_evidence=evidence,
        source_site=evidence.source_site,
        destination_site=evidence.destination_site,
        campaign_id=evidence.campaign_id,
        release_sha=evidence.release_sha,
        baseline_generation_id=evidence.baseline_generation_id,
        baseline_manifest_sha256=evidence.baseline_manifest_sha256,
        baseline_wal_lsn=evidence.baseline_wal_lsn,
        route_binding_sha256=evidence.route_binding_sha256,
        writer_epoch=evidence.writer_epoch,
        writer_lease_id=evidence.writer_lease_id,
        witnessed_term_proof_sha256=evidence.witnessed_term_proof_sha256,
        timeline_id=evidence.timeline_id,
        mapping_plaintext_sha256=evidence.mapping_plaintext_sha256,
        mapping_receipt_sha256=evidence.mapping_receipt_sha256,
        mapping_object_key=evidence.mapping_receipt.object_key,
        mapping_object_version_id=evidence.mapping_receipt.version_id,
        mapping_ciphertext_sha256=evidence.mapping_receipt.ciphertext_sha256,
        mapping_ciphertext_bytes=evidence.mapping_receipt.ciphertext_bytes,
        original_v1_inventory_receipt_sha256=evidence.original_v1_inventory_receipt_sha256,
        blob_receipts_sha256=evidence.blob_receipts_sha256,
        entry_count=evidence.entry_count,
        mapping_eligible_replay_wal_lsn=evidence.mapping_eligible_replay_wal_lsn,
    )
    object.__setattr__(result, "_capability", _VERIFIED_PROMOTION_V2_BLOB_REQUIREMENT_CAPABILITY)
    require_physical_wal_promotion_v2_blob_requirement(
        result,
        config=config,
        verified_binding=verified_binding,
        now=now,
    )
    return result


def require_physical_wal_promotion_v2_blob_requirement(
    value: object,
    *,
    config: PhysicalBlobReceiverPromotionEvidenceConfig,
    verified_binding: VerifiedPhysicalBlobObjectStorageBinding,
    now: datetime,
) -> VerifiedPhysicalWalPromotionV2BlobRequirement:
    """Revalidate the v2-only prerequisite; v1 receipts cannot enter here."""

    if (
        type(value) is not VerifiedPhysicalWalPromotionV2BlobRequirement
        or value._capability is not _VERIFIED_PROMOTION_V2_BLOB_REQUIREMENT_CAPABILITY
    ):
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "verified physical-WAL promotion-v2 Blob requirement is required"
        )
    if value.schema != PHYSICAL_WAL_PROMOTION_V2_BLOB_REQUIREMENT_SCHEMA:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "physical-WAL promotion-v2 Blob requirement schema is invalid"
        )
    if type(value.receiver_promotion_evidence) is not VerifiedPhysicalBlobReceiverPromotionEvidence:
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "physical-WAL promotion-v2 receiver Blob evidence is invalid"
        )
    evidence = require_verified_physical_blob_receiver_promotion_evidence(
        value.receiver_promotion_evidence,
        config=config,
        verified_binding=verified_binding,
        now=now,
    )
    for field_name in (
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "baseline_generation_id",
        "baseline_manifest_sha256",
        "baseline_wal_lsn",
        "route_binding_sha256",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
        "mapping_plaintext_sha256",
        "mapping_receipt_sha256",
        "mapping_object_key",
        "mapping_object_version_id",
        "mapping_ciphertext_sha256",
        "original_v1_inventory_receipt_sha256",
        "blob_receipts_sha256",
        "mapping_eligible_replay_wal_lsn",
    ):
        if not isinstance(getattr(value, field_name), str) or not getattr(value, field_name):
            raise PhysicalBlobReceiverPromotionEvidenceError(
                f"physical-WAL promotion-v2 Blob requirement {field_name} is invalid"
            )
    _sha256(
        value.baseline_manifest_sha256,
        label="physical-WAL promotion-v2 Blob requirement baseline SHA-256",
    )
    _sha256(
        value.route_binding_sha256,
        label="physical-WAL promotion-v2 Blob requirement route SHA-256",
    )
    _sha256(
        value.witnessed_term_proof_sha256,
        label="physical-WAL promotion-v2 Blob requirement Witness proof SHA-256",
    )
    _sha256(
        value.mapping_plaintext_sha256,
        label="physical-WAL promotion-v2 Blob requirement mapping SHA-256",
    )
    _sha256(
        value.mapping_receipt_sha256,
        label="physical-WAL promotion-v2 Blob requirement mapping receipt SHA-256",
    )
    _sha256(
        value.mapping_ciphertext_sha256,
        label="physical-WAL promotion-v2 Blob requirement mapping ciphertext SHA-256",
    )
    _sha256(
        value.original_v1_inventory_receipt_sha256,
        label="physical-WAL promotion-v2 Blob requirement v1 receipt SHA-256",
    )
    _sha256(
        value.blob_receipts_sha256,
        label="physical-WAL promotion-v2 Blob requirement Blob receipt-set SHA-256",
    )
    _positive_int(
        value.writer_epoch,
        label="physical-WAL promotion-v2 Blob requirement writer epoch",
        maximum=2**63 - 1,
    )
    _positive_int(
        value.timeline_id,
        label="physical-WAL promotion-v2 Blob requirement timeline",
        maximum=0xFFFFFFFF,
    )
    _positive_int(
        value.mapping_ciphertext_bytes,
        label="physical-WAL promotion-v2 Blob requirement mapping ciphertext bytes",
        maximum=MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES + 4 * 1024 * 1024,
    )
    _positive_int(
        value.entry_count,
        label="physical-WAL promotion-v2 Blob requirement entry count",
        maximum=16_384,
    )
    _lsn(
        value.mapping_eligible_replay_wal_lsn,
        label="physical-WAL promotion-v2 Blob requirement replay WAL LSN",
    )
    if (
        value.receiver_promotion_evidence is not evidence
        or value.source_site != evidence.source_site
        or value.destination_site != evidence.destination_site
        or value.campaign_id != evidence.campaign_id
        or value.release_sha != evidence.release_sha
        or value.baseline_generation_id != evidence.baseline_generation_id
        or value.baseline_manifest_sha256 != evidence.baseline_manifest_sha256
        or value.baseline_wal_lsn != evidence.baseline_wal_lsn
        or value.route_binding_sha256 != evidence.route_binding_sha256
        or value.writer_epoch != evidence.writer_epoch
        or value.writer_lease_id != evidence.writer_lease_id
        or value.witnessed_term_proof_sha256 != evidence.witnessed_term_proof_sha256
        or value.timeline_id != evidence.timeline_id
        or value.mapping_plaintext_sha256 != evidence.mapping_plaintext_sha256
        or value.mapping_receipt_sha256 != evidence.mapping_receipt_sha256
        or value.mapping_object_key != evidence.mapping_receipt.object_key
        or value.mapping_object_version_id != evidence.mapping_receipt.version_id
        or value.mapping_ciphertext_sha256 != evidence.mapping_receipt.ciphertext_sha256
        or value.mapping_ciphertext_bytes != evidence.mapping_receipt.ciphertext_bytes
        or value.original_v1_inventory_receipt_sha256
        != evidence.original_v1_inventory_receipt_sha256
        or value.blob_receipts_sha256 != evidence.blob_receipts_sha256
        or value.entry_count != evidence.entry_count
        or value.mapping_eligible_replay_wal_lsn
        != evidence.mapping_eligible_replay_wal_lsn
    ):
        raise PhysicalBlobReceiverPromotionEvidenceError(
            "physical-WAL promotion-v2 Blob requirement was tampered"
        )
    return value


def _mapping_capability_from_promotion_evidence(
    value: VerifiedPhysicalBlobReceiverPromotionEvidence,
) -> VerifiedPhysicalBlobReceiverInventoryMapping:
    """Reconstruct only the preverified mapping wrapper for private reparse.

    Its private capability is set only after the outer promotion-evidence
    capability check above.  The subsequent mapping parser and all signed
    receipt checks still validate canonical bytes, descriptor order, and the
    live binding; this reconstruction never mints a public mapping capability.
    """

    result = VerifiedPhysicalBlobReceiverInventoryMapping(
        canonical_plaintext=value.canonical_mapping_plaintext,
        mapping_receipt=value.mapping_receipt,
        source_site=value.source_site,
        destination_site=value.destination_site,
        campaign_id=value.campaign_id,
        release_sha=value.release_sha,
        baseline_generation_id=value.baseline_generation_id,
        baseline_manifest_sha256=value.baseline_manifest_sha256,
        baseline_wal_lsn=value.baseline_wal_lsn,
        writer_epoch=value.writer_epoch,
        writer_lease_id=value.writer_lease_id,
        witnessed_term_proof_sha256=value.witnessed_term_proof_sha256,
        destination_age_recipient=value.destination_age_recipient,
        timeline_id=value.timeline_id,
        original_v1_inventory_sha256=value.original_v1_inventory_sha256,
        original_v1_inventory_bytes=value.original_v1_inventory_bytes,
        shard_ordinal=value.shard_ordinal,
        entry_count=value.entry_count,
        blob_receipts_sha256=value.blob_receipts_sha256,
    )
    object.__setattr__(result, "_capability", _mapping._VERIFIED_MAPPING_CAPABILITY)
    return result
