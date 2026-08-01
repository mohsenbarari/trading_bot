"""Receiver-ready encrypted v2 mapping for finalized Blob inventory shards.

The v1 Blob spool inventory deliberately names a local/spool-era Object key.
That is useful as a frozen source record, but it does not contain the v2
timeline- and term-pinned Object descriptor ultimately needed by a receiver.
This module derives a separate, signed, encrypted mapping artifact from one
parsed v1 inventory, its signed v1 inventory upload receipt, and the exact
ordered v2 Blob upload receipts.  It never changes the v1 inventory or its
publication; both remain available for audit and receiver-side cross-checks.

No import-time I/O occurs.  Object Storage and age are injected only into the
explicit default-disabled publisher.  This module does not fetch, decrypt,
restore, replay, or promote anything at a receiver.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import core.physical_blob_object_storage_uploader as _storage
from core.append_only_sync_delta_batch import (
    OBJECT_KEY_RE,
    SHA256_RE,
    VERSION_ID_RE,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_blob_artifact_spool import (
    MAX_BLOBS_PER_INVENTORY_SHARD,
    MAX_INVENTORY_SHARD_PLAINTEXT_BYTES,
    MAX_PHYSICAL_BLOB_BYTES,
    PHYSICAL_BLOB_INVENTORY_SHARD_PLAINTEXT_SCHEMA,
    PhysicalBlobArtifactManifestBinding,
    PhysicalBlobInventoryShardPlaintext,
    derive_physical_blob_artifact_object_key,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA,
    PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION,
    PHYSICAL_WAL_OBJECT_IMMUTABILITY,
    PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
)


__all__ = (
    "MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES",
    "PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_DEFAULT_ENABLED",
    "PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_RECEIPT_SCHEMA",
    "PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_SCHEMA",
    "PhysicalBlobReceiverInventoryMappingArtifact",
    "PhysicalBlobReceiverInventoryMappingConfig",
    "PhysicalBlobReceiverInventoryMappingError",
    "PhysicalBlobReceiverInventoryMappingPublisher",
    "PhysicalBlobReceiverInventoryMappingReceipt",
    "VerifiedPhysicalBlobReceiverInventoryMapping",
    "build_physical_wal_blob_inventory_shard_from_receiver_mapping",
    "derive_physical_blob_receiver_inventory_mapping_object_key",
    "require_verified_physical_blob_receiver_inventory_mapping",
    "verify_physical_blob_receiver_inventory_mapping_plaintext",
    "verify_physical_blob_receiver_inventory_mapping_receipt",
)


PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_SCHEMA = (
    "gold-trade-physical-blob-receiver-inventory-mapping-v2"
)
PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_RECEIPT_SCHEMA = (
    "gold-trade-physical-blob-receiver-inventory-mapping-receipt-v1"
)
PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_DEFAULT_ENABLED = False
MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES = 96 * 1024 * 1024

_MAX_MAPPING_CIPHERTEXT_OVERHEAD_BYTES = 4 * 1024 * 1024
_MAX_BLOB_CIPHERTEXT_OVERHEAD_BYTES = 32 * 1024 * 1024
_MAX_RECEIPT_BYTES = 128 * 1024
_MAPPING_SIGNATURE_DOMAIN = (
    b"gold-trade-physical-blob-receiver-inventory-mapping-v2\x00"
)
_RECEIPT_SIGNATURE_DOMAIN = (
    b"gold-trade-physical-blob-receiver-inventory-mapping-receipt-v1\x00"
)
_KEY_ID_PREFIX = "ed25519-sha256:"
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_B64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$", re.ASCII)
_VERIFIED_MAPPING_CAPABILITY = object()


class PhysicalBlobReceiverInventoryMappingError(ValueError):
    """The v2 receiver mapping, receipt, or publication boundary is unsafe."""


@dataclass(frozen=True)
class PhysicalBlobReceiverInventoryMappingConfig:
    """Default-disabled publisher configuration for one ordered FI↔IR route."""

    source_site: str = ""
    destination_site: str = ""
    workspace: Path | None = None
    spool_root: Path | None = None
    bucket: str = ""
    region: str = ""
    destination_age_recipient: str = ""
    mapping_signer_public_key: bytes = b""
    blob_receipt_signer_public_key: bytes = b""
    enabled: bool = PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_DEFAULT_ENABLED
    maximum_blob_plaintext_bytes: int = MAX_PHYSICAL_BLOB_BYTES
    maximum_mapping_plaintext_bytes: int = MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class PhysicalBlobReceiverInventoryMappingArtifact:
    """Canonical source-signed mapping plaintext, prior to encryption/upload."""

    canonical_plaintext: bytes
    plaintext_sha256: str
    plaintext_bytes: int
    original_v1_inventory_sha256: str
    original_v1_inventory_bytes: int
    shard_ordinal: int
    entry_count: int
    blob_receipts_sha256: str
    object_key: str
    timeline_id: int
    route_binding_sha256: str


@dataclass(frozen=True)
class PhysicalBlobReceiverInventoryMappingReceipt:
    """Typed signed receipt for the encrypted receiver-ready mapping object."""

    signed_receipt: bytes
    receipt_sha256: str
    mapping_plaintext_sha256: str
    mapping_plaintext_bytes: int
    original_v1_inventory_sha256: str
    original_v1_inventory_bytes: int
    shard_ordinal: int
    entry_count: int
    blob_receipts_sha256: str
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    timeline_id: int
    route_binding_sha256: str


@dataclass(frozen=True)
class VerifiedPhysicalBlobReceiverInventoryMapping:
    """Opaque receiver-validated plaintext plus its pinned mapping receipt."""

    canonical_plaintext: bytes
    mapping_receipt: PhysicalBlobReceiverInventoryMappingReceipt
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    destination_age_recipient: str
    timeline_id: int
    original_v1_inventory_sha256: str
    original_v1_inventory_bytes: int
    shard_ordinal: int
    entry_count: int
    blob_receipts_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _ConfigFacts:
    storage_config: Any
    mapping_signer_public_key: bytes
    blob_receipt_signer_public_key: bytes
    maximum_mapping_plaintext_bytes: int


@dataclass(frozen=True)
class _V1InventoryEntry:
    ordinal: int
    source_record_id: str
    content_sha256: str
    content_bytes: int
    handoff_descriptor_sha256: str
    spool_object_key: str


@dataclass(frozen=True)
class _V1Inventory:
    plaintext_sha256: str
    plaintext_bytes: int
    shard_ordinal: int
    entries: tuple[_V1InventoryEntry, ...]


@dataclass(frozen=True)
class _MappingEntry:
    ordinal: int
    source_record_id: str
    content_sha256: str
    content_bytes: int
    handoff_descriptor_sha256: str
    spool_object_key: str
    blob_receipt_sha256: str
    blob_receipt_raw: bytes
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int


@dataclass(frozen=True)
class _MappingFacts:
    raw: bytes
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
    original_v1_inventory_sha256: str
    original_v1_inventory_bytes: int
    original_v1_inventory_receipt_raw: bytes
    shard_ordinal: int
    entry_count: int
    blob_receipts_sha256: str
    entries: tuple[_MappingEntry, ...]


@dataclass(frozen=True)
class _ReceiptFacts:
    raw: bytes
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
    original_v1_inventory_sha256: str
    original_v1_inventory_bytes: int
    shard_ordinal: int
    entry_count: int
    blob_receipts_sha256: str
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int


def _error_from_storage(exc: Exception, *, label: str) -> PhysicalBlobReceiverInventoryMappingError:
    return PhysicalBlobReceiverInventoryMappingError(label)


def _canonical(value: Mapping[str, Any], *, label: str) -> bytes:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PhysicalBlobReceiverInventoryMappingError("receiver mapping JSON has duplicate fields")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise PhysicalBlobReceiverInventoryMappingError(
        f"receiver mapping JSON constant is forbidden: {value}"
    )


def _parse_canonical_json(raw: object, *, label: str, maximum_bytes: int) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > maximum_bytes:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} byte size is invalid")
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalBlobReceiverInventoryMappingError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict) or _canonical(value, label=label) != raw:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} is not canonical")
    return value


def _exact_mapping(value: object, *, label: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} fields are invalid")
    return dict(value)


def _sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} is invalid")
    return value


def _positive_int(value: object, *, label: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} is invalid")
    return value


def _timeline_id(value: object, *, label: str) -> int:
    return _positive_int(value, label=label, maximum=0xFFFFFFFF)


def _public_key(value: object, *, label: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32 or value == b"\x00" * 32:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError as exc:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} is invalid") from exc
    return value


def _key_id(value: bytes) -> str:
    return _KEY_ID_PREFIX + hashlib.sha256(value).hexdigest()


def _decode_b64(value: object, *, label: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str) or not value or _B64_RE.fullmatch(value) is None:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} is invalid") from exc
    if len(decoded) != expected_bytes or base64.b64encode(decoded).decode("ascii") != value:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} is invalid")
    return decoded


def _signer_mapping(public_key: bytes) -> dict[str, str]:
    return {
        "algorithm": "ed25519",
        "public_key_base64": base64.b64encode(public_key).decode("ascii"),
        "key_id": _key_id(public_key),
    }


def _signature_placeholder() -> dict[str, str]:
    return {
        "algorithm": "ed25519",
        "signature_base64": base64.b64encode(b"\x00" * 64).decode("ascii"),
    }


def _sign_canonical(
    *,
    value: Mapping[str, Any],
    signature_field: str,
    signature_domain: bytes,
    signer_factory: Callable[[], _storage.PhysicalBlobReceiptSigner] | None,
    expected_public_key: bytes,
    label: str,
) -> bytes:
    if signer_factory is None or not callable(signer_factory):
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} signer factory is required")
    unsigned = dict(value)
    unsigned[signature_field] = _signature_placeholder()
    signing_bytes = signature_domain + _canonical(unsigned, label=label)
    try:
        signer = signer_factory()
    except Exception as exc:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} signer factory failed") from exc
    if not callable(getattr(signer, "sign", None)):
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} signer is invalid")
    try:
        signature = signer.sign(signing_bytes)
    except Exception as exc:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} signing failed") from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} signature is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(expected_public_key).verify(signature, signing_bytes)
    except (ValueError, InvalidSignature) as exc:
        raise PhysicalBlobReceiverInventoryMappingError(
            f"{label} signature does not match the pinned public key"
        ) from exc
    signed = dict(value)
    signed[signature_field] = {
        "algorithm": "ed25519",
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }
    return _canonical(signed, label=label)


def _verify_signature(
    *,
    value: Mapping[str, Any],
    signer_field: str,
    signature_field: str,
    signature_domain: bytes,
    expected_public_key: bytes,
    label: str,
) -> None:
    signer = _exact_mapping(
        value[signer_field],
        label=f"{label} signer",
        fields={"algorithm", "public_key_base64", "key_id"},
    )
    if signer["algorithm"] != "ed25519":
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} signer algorithm is invalid")
    signer_public_key = _decode_b64(
        signer["public_key_base64"], label=f"{label} signer public key", expected_bytes=32
    )
    _public_key(signer_public_key, label=f"{label} signer public key")
    if signer_public_key != expected_public_key or signer.get("key_id") != _key_id(expected_public_key):
        raise PhysicalBlobReceiverInventoryMappingError(
            f"{label} signer does not match the pinned public key"
        )
    signature_value = _exact_mapping(
        value[signature_field],
        label=f"{label} signature",
        fields={"algorithm", "signature_base64"},
    )
    if signature_value["algorithm"] != "ed25519":
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} signature algorithm is invalid")
    signature = _decode_b64(
        signature_value["signature_base64"], label=f"{label} signature", expected_bytes=64
    )
    unsigned = dict(value)
    unsigned[signature_field] = _signature_placeholder()
    try:
        Ed25519PublicKey.from_public_bytes(expected_public_key).verify(
            signature, signature_domain + _canonical(unsigned, label=label)
        )
    except (ValueError, InvalidSignature) as exc:
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} signature is invalid") from exc


def _normalise_config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalBlobReceiverInventoryMappingConfig:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping config is invalid")
    if value.enabled is not True:
        raise PhysicalBlobReceiverInventoryMappingError(
            "receiver mapping Object-Storage publisher is disabled"
        )
    mapping_signer_public_key = _public_key(
        value.mapping_signer_public_key, label="receiver mapping signer public key"
    )
    blob_receipt_signer_public_key = _public_key(
        value.blob_receipt_signer_public_key,
        label="receiver mapping Blob receipt signer public key",
    )
    if type(value.maximum_mapping_plaintext_bytes) is not int or not (
        1 <= value.maximum_mapping_plaintext_bytes <= MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES
    ):
        raise PhysicalBlobReceiverInventoryMappingError(
            "receiver mapping maximum plaintext bytes is invalid"
        )
    transport_config = _storage.PhysicalBlobObjectStorageUploaderConfig(
        source_site=value.source_site,
        destination_site=value.destination_site,
        workspace=value.workspace,
        spool_root=value.spool_root,
        bucket=value.bucket,
        region=value.region,
        destination_age_recipient=value.destination_age_recipient,
        receipt_signer_public_key=mapping_signer_public_key,
        enabled=value.enabled,
        maximum_blob_plaintext_bytes=value.maximum_blob_plaintext_bytes,
        direct_site_control=value.direct_site_control,
        destination_object_ingest=value.destination_object_ingest,
    )
    try:
        storage_config = _storage._normalise_config(transport_config)
    except _storage.PhysicalBlobObjectStorageUploaderError as exc:
        raise _error_from_storage(exc, label="receiver mapping storage configuration is invalid") from exc
    return _ConfigFacts(
        storage_config=storage_config,
        mapping_signer_public_key=mapping_signer_public_key,
        blob_receipt_signer_public_key=blob_receipt_signer_public_key,
        maximum_mapping_plaintext_bytes=value.maximum_mapping_plaintext_bytes,
    )


def _binding_facts(
    value: _storage.VerifiedPhysicalBlobObjectStorageBinding,
    *,
    now: datetime,
):
    try:
        return _storage._binding_facts(value, now=now)
    except _storage.PhysicalBlobObjectStorageUploaderError as exc:
        raise _error_from_storage(exc, label="receiver mapping binding is not live and authorized") from exc


def _require_config_binding(config: _ConfigFacts, binding: Any) -> None:
    try:
        _storage._require_config_binding_match(config.storage_config, binding)
    except _storage.PhysicalBlobObjectStorageUploaderError as exc:
        raise _error_from_storage(exc, label="receiver mapping route binding is invalid") from exc


def _artifact_manifest(binding: Any) -> PhysicalBlobArtifactManifestBinding:
    manifest = binding.manifest
    return PhysicalBlobArtifactManifestBinding(
        source_site=manifest.source_site,
        destination_site=manifest.destination_site,
        campaign_id=manifest.campaign_id,
        release_sha=manifest.release_sha,
        baseline_generation_id=manifest.baseline_generation_id,
        baseline_manifest_sha256=manifest.baseline_manifest_sha256,
        baseline_wal_lsn=manifest.baseline_wal_lsn,
        destination_age_recipient=manifest.destination_age_recipient,
    )


def _v1_inventory_from_raw(*, raw: bytes, binding: Any, maximum_blob_bytes: int) -> _V1Inventory:
    try:
        item = _storage._exact_mapping(
            _storage._parse_canonical_json(
                raw,
                label="physical Blob v1 inventory plaintext",
                maximum_bytes=MAX_INVENTORY_SHARD_PLAINTEXT_BYTES,
            ),
            label="physical Blob v1 inventory plaintext",
            fields=_storage._INVENTORY_FIELDS,
        )
        if (
            item["schema"] != PHYSICAL_BLOB_INVENTORY_SHARD_PLAINTEXT_SCHEMA
            or item["kind"] != "finalized_database_visible_blob_inventory_shard_plaintext"
        ):
            raise _storage.PhysicalBlobObjectStorageUploaderError("v1 inventory schema is invalid")
        _storage._require_descriptor_binding(
            item, binding=binding, label="physical Blob v1 inventory plaintext"
        )
        _storage._sha256(item["uploads_root_identity_sha256"], label="v1 uploads root identity")
        shard_ordinal = _storage._positive_int(
            item["shard_ordinal"], label="v1 inventory shard ordinal", maximum=2**63 - 1
        )
        if not all(
            item[name] is True
            for name in (
                "not_a_database_snapshot_consistency_proof",
                "not_a_blob_frontier_manifest",
                "not_a_remote_apply_proof",
                "not_a_strict_acknowledgement_proof",
            )
        ):
            raise _storage.PhysicalBlobObjectStorageUploaderError("v1 inventory disclaimers are invalid")
        raw_entries = item["entries"]
        if isinstance(raw_entries, (str, bytes)) or not isinstance(raw_entries, Sequence):
            raise _storage.PhysicalBlobObjectStorageUploaderError("v1 inventory entries are invalid")
        if not raw_entries or len(raw_entries) > MAX_BLOBS_PER_INVENTORY_SHARD:
            raise _storage.PhysicalBlobObjectStorageUploaderError("v1 inventory entry count is invalid")
        entries: list[_V1InventoryEntry] = []
        seen: set[str] = set()
        for ordinal, raw_entry in enumerate(raw_entries, start=1):
            entry = _storage._exact_mapping(
                raw_entry,
                label=f"v1 inventory entry {ordinal}",
                fields={
                    "ordinal",
                    "source_record_id",
                    "content_sha256",
                    "content_bytes",
                    "handoff_descriptor_sha256",
                    "object_key",
                },
            )
            if _storage._positive_int(
                entry["ordinal"], label=f"v1 inventory entry {ordinal} ordinal", maximum=MAX_BLOBS_PER_INVENTORY_SHARD
            ) != ordinal:
                raise _storage.PhysicalBlobObjectStorageUploaderError("v1 inventory entries are reordered")
            source_record_id = _storage._safe_text(
                entry["source_record_id"],
                label=f"v1 inventory entry {ordinal} source record ID",
                pattern=_storage._SYSTEM_ID_RE,
            )
            if source_record_id in seen:
                raise _storage.PhysicalBlobObjectStorageUploaderError("v1 inventory repeats a source record")
            seen.add(source_record_id)
            content_sha256 = _storage._sha256(
                entry["content_sha256"], label=f"v1 inventory entry {ordinal} content SHA-256"
            )
            content_bytes = _storage._positive_int(
                entry["content_bytes"],
                label=f"v1 inventory entry {ordinal} content bytes",
                maximum=maximum_blob_bytes,
            )
            handoff_descriptor_sha256 = _storage._sha256(
                entry["handoff_descriptor_sha256"], label=f"v1 inventory entry {ordinal} handoff SHA-256"
            )
            spool_object_key = _storage._safe_text(
                entry["object_key"],
                label=f"v1 inventory entry {ordinal} Object key",
                pattern=OBJECT_KEY_RE,
            )
            expected_spool_object_key = derive_physical_blob_artifact_object_key(
                manifest_binding=_artifact_manifest(binding),
                source_record_id=source_record_id,
                declared_content_sha256=content_sha256,
            )
            if spool_object_key != expected_spool_object_key:
                raise _storage.PhysicalBlobObjectStorageUploaderError(
                    "v1 inventory Object key is not deterministic"
                )
            entries.append(
                _V1InventoryEntry(
                    ordinal=ordinal,
                    source_record_id=source_record_id,
                    content_sha256=content_sha256,
                    content_bytes=content_bytes,
                    handoff_descriptor_sha256=handoff_descriptor_sha256,
                    spool_object_key=spool_object_key,
                )
            )
    except _storage.PhysicalBlobObjectStorageUploaderError as exc:
        raise _error_from_storage(exc, label="receiver mapping v1 inventory is invalid") from exc
    if len(raw) > MAX_INVENTORY_SHARD_PLAINTEXT_BYTES:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping v1 inventory exceeds its bound")
    return _V1Inventory(
        plaintext_sha256=hashlib.sha256(raw).hexdigest(),
        plaintext_bytes=len(raw),
        shard_ordinal=shard_ordinal,
        entries=tuple(entries),
    )


def _typed_v1_inventory_from_spool(
    *,
    inventory_shard: PhysicalBlobInventoryShardPlaintext,
    binding: Any,
    config: _ConfigFacts,
) -> tuple[_V1Inventory, bytes]:
    if type(inventory_shard) is not PhysicalBlobInventoryShardPlaintext:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping v1 inventory input is invalid")
    try:
        parsed = _storage._parse_inventory(
            inventory=inventory_shard,
            binding=binding,
            config=config.storage_config,
        )
        raw = _storage._read_regular_file(
            parsed.plaintext_path,
            label="receiver mapping v1 inventory plaintext",
            maximum_bytes=MAX_INVENTORY_SHARD_PLAINTEXT_BYTES,
        )
    except _storage.PhysicalBlobObjectStorageUploaderError as exc:
        raise _error_from_storage(exc, label="receiver mapping v1 inventory input is invalid") from exc
    v1 = _v1_inventory_from_raw(
        raw=raw,
        binding=binding,
        maximum_blob_bytes=config.storage_config.maximum_blob_plaintext_bytes,
    )
    if (
        v1.plaintext_sha256 != parsed.plaintext_sha256
        or v1.plaintext_bytes != parsed.plaintext_bytes
        or v1.shard_ordinal != parsed.shard_ordinal
        or len(v1.entries) != parsed.entry_count
    ):
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping v1 inventory changed during parse")
    return v1, raw


def _parse_typed_blob_receipt(
    *,
    receipt: _storage.PhysicalBlobObjectStorageReceipt,
    blob_receipt_signer_public_key: bytes,
    binding: Any,
) -> Any:
    if type(receipt) is not _storage.PhysicalBlobObjectStorageReceipt:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping requires typed Blob receipts only")
    try:
        normalized = _storage.verify_physical_blob_object_storage_receipt(
            receipt=receipt,
            receipt_signer_public_key=blob_receipt_signer_public_key,
        )
        if type(normalized) is not _storage.PhysicalBlobObjectStorageReceipt:
            raise _storage.PhysicalBlobObjectStorageUploaderError("receipt type is invalid")
        facts = _storage._parse_receipt(
            normalized.signed_receipt,
            receipt_signer_public_key=blob_receipt_signer_public_key,
        )
        _storage._require_receipt_binding(facts, binding=binding)
    except _storage.PhysicalBlobObjectStorageUploaderError as exc:
        raise _error_from_storage(exc, label="receiver mapping Blob receipt is invalid") from exc
    if facts.kind != "finalized_blob_object" or facts.source_record_id is None or facts.handoff_descriptor_sha256 is None:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping Blob receipt kind is invalid")
    return facts


def _parse_typed_v1_inventory_receipt(
    *,
    receipt: _storage.PhysicalBlobInventoryShardObjectStorageReceipt,
    blob_receipt_signer_public_key: bytes,
    binding: Any,
) -> Any:
    if type(receipt) is not _storage.PhysicalBlobInventoryShardObjectStorageReceipt:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping v1 inventory receipt is invalid")
    try:
        normalized = _storage.verify_physical_blob_object_storage_receipt(
            receipt=receipt,
            receipt_signer_public_key=blob_receipt_signer_public_key,
        )
        if type(normalized) is not _storage.PhysicalBlobInventoryShardObjectStorageReceipt:
            raise _storage.PhysicalBlobObjectStorageUploaderError("receipt type is invalid")
        facts = _storage._parse_receipt(
            normalized.signed_receipt,
            receipt_signer_public_key=blob_receipt_signer_public_key,
        )
        _storage._require_receipt_binding(facts, binding=binding)
    except _storage.PhysicalBlobObjectStorageUploaderError as exc:
        raise _error_from_storage(exc, label="receiver mapping v1 inventory receipt is invalid") from exc
    if (
        facts.kind != "blob_inventory_shard_object"
        or facts.shard_ordinal is None
        or facts.entry_count is None
        or facts.blob_receipts_sha256 is None
    ):
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping v1 inventory receipt kind is invalid")
    return facts


def _receipt_raw_object(raw: bytes, *, label: str) -> dict[str, Any]:
    return _parse_canonical_json(raw, label=label, maximum_bytes=_MAX_RECEIPT_BYTES)


def _object_descriptor(*, facts: Any, expected_kind: str, recipient: str) -> dict[str, Any]:
    if facts.object_key is None or facts.version_id is None:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping Object descriptor is incomplete")
    return {
        "schema": PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA,
        "version": PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION,
        "object_kind": expected_kind,
        "object_key": facts.object_key,
        "version_id": facts.version_id,
        "ciphertext_sha256": facts.ciphertext_sha256,
        "ciphertext_bytes": facts.ciphertext_bytes,
        "encryption": PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
        "age_recipient": recipient,
        "immutability": PHYSICAL_WAL_OBJECT_IMMUTABILITY,
    }


def _mapping_object_key(*, binding: Any, original_v1_inventory_sha256: str, mapping_plaintext_sha256: str) -> str:
    original_hash = _sha256(original_v1_inventory_sha256, label="receiver mapping original v1 inventory SHA-256")
    mapping_hash = _sha256(mapping_plaintext_sha256, label="receiver mapping plaintext SHA-256")
    term_component = "-".join(
        (
            f"term-{binding.writer_epoch:020d}",
            hashlib.sha256(binding.writer_lease_id.encode("utf-8")).hexdigest(),
            binding.witnessed_term_proof_sha256,
        )
    )
    manifest = binding.manifest
    key = "/".join(
        (
            "physical-blob-receiver-mappings-v2",
            manifest.campaign_id,
            manifest.release_sha,
            manifest.baseline_generation_id,
            f"{manifest.source_site}-to-{manifest.destination_site}",
            f"timeline-{binding.timeline_id:08X}",
            f"route-{binding.route_binding_sha256}",
            term_component,
            f"v1-inventory-{original_hash}",
            f"mapping-{mapping_hash}.age",
        )
    )
    if OBJECT_KEY_RE.fullmatch(key) is None or any(part in {"", ".", ".."} for part in key.split("/")):
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping Object key is invalid")
    return key


def derive_physical_blob_receiver_inventory_mapping_object_key(
    *,
    verified_binding: _storage.VerifiedPhysicalBlobObjectStorageBinding,
    original_v1_inventory_sha256: str,
    mapping_plaintext_sha256: str,
    now: datetime,
) -> str:
    """Derive the non-colliding immutable key for one receiver v2 mapping."""

    return _mapping_object_key(
        binding=_binding_facts(verified_binding, now=now),
        original_v1_inventory_sha256=original_v1_inventory_sha256,
        mapping_plaintext_sha256=mapping_plaintext_sha256,
    )


_MAPPING_FIELDS = {
    "schema",
    "version",
    "kind",
    "source_site",
    "destination_site",
    "campaign_id",
    "release_sha",
    "baseline_generation_id",
    "baseline_manifest_sha256",
    "baseline_wal_lsn",
    "route_binding_sha256",
    "writer_term",
    "timeline_id",
    "destination_age_recipient",
    "original_v1_inventory",
    "blob_receipts_sha256",
    "entries",
    "source_mapping_signer",
    "source_mapping_signature",
    "not_a_database_snapshot_consistency_proof",
    "not_a_blob_frontier_manifest",
    "not_a_remote_apply_proof",
    "not_a_strict_acknowledgement_proof",
}


def _mapping_unsigned(
    *,
    binding: Any,
    v1: _V1Inventory,
    v1_inventory_receipt_facts: Any,
    blob_receipt_facts: Sequence[Any],
    mapping_signer_public_key: bytes,
) -> dict[str, Any]:
    if len(blob_receipt_facts) != len(v1.entries):
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping Blob receipt coverage is invalid")
    entries: list[dict[str, Any]] = []
    for v1_entry, receipt_facts in zip(v1.entries, blob_receipt_facts, strict=True):
        if (
            receipt_facts.source_record_id != v1_entry.source_record_id
            or receipt_facts.handoff_descriptor_sha256 != v1_entry.handoff_descriptor_sha256
            or receipt_facts.plaintext_sha256 != v1_entry.content_sha256
            or receipt_facts.plaintext_bytes != v1_entry.content_bytes
            or receipt_facts.object_key
            != _storage._derive_blob_object_key(
                binding=binding,
                source_record_id=v1_entry.source_record_id,
                plaintext_sha256=v1_entry.content_sha256,
            )
        ):
            raise PhysicalBlobReceiverInventoryMappingError(
                "receiver mapping Blob receipt does not exactly cover its v1 inventory entry"
            )
        raw_receipt = receipt_facts.raw
        entries.append(
            {
                "ordinal": v1_entry.ordinal,
                "source_record": {
                    "record_id": v1_entry.source_record_id,
                    "record_id_sha256": hashlib.sha256(
                        v1_entry.source_record_id.encode("utf-8")
                    ).hexdigest(),
                },
                "plaintext": {
                    "sha256": v1_entry.content_sha256,
                    "bytes": v1_entry.content_bytes,
                },
                "v1_inventory_entry": {
                    "handoff_descriptor_sha256": v1_entry.handoff_descriptor_sha256,
                    "spool_object_key": v1_entry.spool_object_key,
                },
                "blob_receipt_sha256": hashlib.sha256(raw_receipt).hexdigest(),
                "blob_receipt": _receipt_raw_object(raw_receipt, label="receiver mapping Blob receipt"),
                "final_object": _object_descriptor(
                    facts=receipt_facts,
                    expected_kind="blob",
                    recipient=binding.manifest.destination_age_recipient,
                ),
            }
        )
    manifest = binding.manifest
    return {
        "schema": PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_SCHEMA,
        "version": 2,
        "kind": "receiver_ready_blob_inventory_mapping",
        "source_site": manifest.source_site,
        "destination_site": manifest.destination_site,
        "campaign_id": manifest.campaign_id,
        "release_sha": manifest.release_sha,
        "baseline_generation_id": manifest.baseline_generation_id,
        "baseline_manifest_sha256": manifest.baseline_manifest_sha256,
        "baseline_wal_lsn": manifest.baseline_wal_lsn,
        "route_binding_sha256": binding.route_binding_sha256,
        "writer_term": {
            "holder_site": manifest.source_site,
            "writer_epoch": binding.writer_epoch,
            "writer_lease_id": binding.writer_lease_id,
            "witnessed_term_proof_sha256": binding.witnessed_term_proof_sha256,
        },
        "timeline_id": binding.timeline_id,
        "destination_age_recipient": manifest.destination_age_recipient,
        "original_v1_inventory": {
            "schema": PHYSICAL_BLOB_INVENTORY_SHARD_PLAINTEXT_SCHEMA,
            "plaintext_sha256": v1.plaintext_sha256,
            "plaintext_bytes": v1.plaintext_bytes,
            "shard_ordinal": v1.shard_ordinal,
            "entry_count": len(v1.entries),
            "inventory_upload_receipt_sha256": hashlib.sha256(
                v1_inventory_receipt_facts.raw
            ).hexdigest(),
            "inventory_upload_receipt": _receipt_raw_object(
                v1_inventory_receipt_facts.raw,
                label="receiver mapping v1 inventory receipt",
            ),
        },
        "blob_receipts_sha256": _storage._blob_receipts_digest(tuple(blob_receipt_facts)),
        "entries": entries,
        "source_mapping_signer": _signer_mapping(mapping_signer_public_key),
        "not_a_database_snapshot_consistency_proof": True,
        "not_a_blob_frontier_manifest": True,
        "not_a_remote_apply_proof": True,
        "not_a_strict_acknowledgement_proof": True,
    }


def _mapping_artifact_from_inputs(
    *,
    config: _ConfigFacts,
    inventory_shard: PhysicalBlobInventoryShardPlaintext,
    v1_inventory_receipt: _storage.PhysicalBlobInventoryShardObjectStorageReceipt,
    blob_receipts: Sequence[_storage.PhysicalBlobObjectStorageReceipt],
    verified_binding: _storage.VerifiedPhysicalBlobObjectStorageBinding,
    now: datetime,
    mapping_signer_factory: Callable[[], _storage.PhysicalBlobReceiptSigner] | None,
) -> PhysicalBlobReceiverInventoryMappingArtifact:
    binding = _binding_facts(verified_binding, now=now)
    _require_config_binding(config, binding)
    v1, _raw_v1 = _typed_v1_inventory_from_spool(
        inventory_shard=inventory_shard, binding=binding, config=config
    )
    v1_receipt_facts = _parse_typed_v1_inventory_receipt(
        receipt=v1_inventory_receipt,
        blob_receipt_signer_public_key=config.blob_receipt_signer_public_key,
        binding=binding,
    )
    if (
        v1_receipt_facts.shard_ordinal != v1.shard_ordinal
        or v1_receipt_facts.entry_count != len(v1.entries)
        or v1_receipt_facts.plaintext_sha256 != v1.plaintext_sha256
        or v1_receipt_facts.plaintext_bytes != v1.plaintext_bytes
    ):
        raise PhysicalBlobReceiverInventoryMappingError(
            "receiver mapping v1 inventory receipt does not match its plaintext"
        )
    if isinstance(blob_receipts, (str, bytes)) or not isinstance(blob_receipts, Sequence):
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping Blob receipts are invalid")
    if len(blob_receipts) != len(v1.entries):
        raise PhysicalBlobReceiverInventoryMappingError(
            "receiver mapping Blob receipts do not cover the v1 inventory exactly"
        )
    parsed_blob_receipts = tuple(
        _parse_typed_blob_receipt(
            receipt=receipt,
            blob_receipt_signer_public_key=config.blob_receipt_signer_public_key,
            binding=binding,
        )
        for receipt in blob_receipts
    )
    if v1_receipt_facts.blob_receipts_sha256 != _storage._blob_receipts_digest(parsed_blob_receipts):
        raise PhysicalBlobReceiverInventoryMappingError(
            "receiver mapping Blob receipt set does not match its v1 inventory receipt"
        )
    unsigned = _mapping_unsigned(
        binding=binding,
        v1=v1,
        v1_inventory_receipt_facts=v1_receipt_facts,
        blob_receipt_facts=parsed_blob_receipts,
        mapping_signer_public_key=config.mapping_signer_public_key,
    )
    canonical_plaintext = _sign_canonical(
        value=unsigned,
        signature_field="source_mapping_signature",
        signature_domain=_MAPPING_SIGNATURE_DOMAIN,
        signer_factory=mapping_signer_factory,
        expected_public_key=config.mapping_signer_public_key,
        label="receiver mapping plaintext",
    )
    if len(canonical_plaintext) > config.maximum_mapping_plaintext_bytes:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping plaintext exceeds its bound")
    plaintext_sha256 = hashlib.sha256(canonical_plaintext).hexdigest()
    return PhysicalBlobReceiverInventoryMappingArtifact(
        canonical_plaintext=canonical_plaintext,
        plaintext_sha256=plaintext_sha256,
        plaintext_bytes=len(canonical_plaintext),
        original_v1_inventory_sha256=v1.plaintext_sha256,
        original_v1_inventory_bytes=v1.plaintext_bytes,
        shard_ordinal=v1.shard_ordinal,
        entry_count=len(v1.entries),
        blob_receipts_sha256=v1_receipt_facts.blob_receipts_sha256,
        object_key=_mapping_object_key(
            binding=binding,
            original_v1_inventory_sha256=v1.plaintext_sha256,
            mapping_plaintext_sha256=plaintext_sha256,
        ),
        timeline_id=binding.timeline_id,
        route_binding_sha256=binding.route_binding_sha256,
    )


_RECEIPT_FIELDS = {
    "schema",
    "version",
    "kind",
    "source_site",
    "destination_site",
    "campaign_id",
    "release_sha",
    "baseline_generation_id",
    "baseline_manifest_sha256",
    "baseline_wal_lsn",
    "route_binding_sha256",
    "writer_term",
    "timeline_id",
    "destination_age_recipient",
    "mapping_plaintext",
    "original_v1_inventory",
    "blob_receipts_sha256",
    "object",
    "mapping_receipt_signer",
    "mapping_receipt_signature",
    "readback_verified",
    "not_a_database_snapshot_consistency_proof",
    "not_a_blob_frontier_manifest",
    "not_a_remote_apply_proof",
    "not_a_strict_acknowledgement_proof",
}


def _mapping_receipt_unsigned(
    *,
    binding: Any,
    artifact: PhysicalBlobReceiverInventoryMappingArtifact,
    mapping_signer_public_key: bytes,
    version_id: str,
    ciphertext_sha256: str,
    ciphertext_bytes: int,
) -> dict[str, Any]:
    manifest = binding.manifest
    return {
        "schema": PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_RECEIPT_SCHEMA,
        "version": 1,
        "kind": "receiver_inventory_mapping_object",
        "source_site": manifest.source_site,
        "destination_site": manifest.destination_site,
        "campaign_id": manifest.campaign_id,
        "release_sha": manifest.release_sha,
        "baseline_generation_id": manifest.baseline_generation_id,
        "baseline_manifest_sha256": manifest.baseline_manifest_sha256,
        "baseline_wal_lsn": manifest.baseline_wal_lsn,
        "route_binding_sha256": binding.route_binding_sha256,
        "writer_term": {
            "holder_site": manifest.source_site,
            "writer_epoch": binding.writer_epoch,
            "writer_lease_id": binding.writer_lease_id,
            "witnessed_term_proof_sha256": binding.witnessed_term_proof_sha256,
        },
        "timeline_id": binding.timeline_id,
        "destination_age_recipient": manifest.destination_age_recipient,
        "mapping_plaintext": {
            "sha256": artifact.plaintext_sha256,
            "bytes": artifact.plaintext_bytes,
        },
        "original_v1_inventory": {
            "sha256": artifact.original_v1_inventory_sha256,
            "bytes": artifact.original_v1_inventory_bytes,
            "shard_ordinal": artifact.shard_ordinal,
            "entry_count": artifact.entry_count,
        },
        "blob_receipts_sha256": artifact.blob_receipts_sha256,
        "object": {
            "schema": PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA,
            "version": PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION,
            "object_kind": "blob_inventory_shard",
            "object_key": artifact.object_key,
            "version_id": version_id,
            "ciphertext_sha256": ciphertext_sha256,
            "ciphertext_bytes": ciphertext_bytes,
            "encryption": PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
            "age_recipient": manifest.destination_age_recipient,
            "immutability": PHYSICAL_WAL_OBJECT_IMMUTABILITY,
        },
        "mapping_receipt_signer": _signer_mapping(mapping_signer_public_key),
        "readback_verified": True,
        "not_a_database_snapshot_consistency_proof": True,
        "not_a_blob_frontier_manifest": True,
        "not_a_remote_apply_proof": True,
        "not_a_strict_acknowledgement_proof": True,
    }


def _parse_mapping_receipt(raw: object, *, mapping_signer_public_key: bytes) -> _ReceiptFacts:
    item = _exact_mapping(
        _parse_canonical_json(raw, label="receiver mapping signed receipt", maximum_bytes=_MAX_RECEIPT_BYTES),
        label="receiver mapping signed receipt",
        fields=_RECEIPT_FIELDS,
    )
    if (
        item["schema"] != PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_RECEIPT_SCHEMA
        or type(item["version"]) is not int
        or item["version"] != 1
        or item["kind"] != "receiver_inventory_mapping_object"
    ):
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping receipt schema is invalid")
    _verify_signature(
        value=item,
        signer_field="mapping_receipt_signer",
        signature_field="mapping_receipt_signature",
        signature_domain=_RECEIPT_SIGNATURE_DOMAIN,
        expected_public_key=mapping_signer_public_key,
        label="receiver mapping receipt",
    )
    return _receipt_facts_from_value(item, raw=raw)


def _common_binding_facts(value: Mapping[str, Any], *, label: str) -> tuple[str, str, str, str, str, str, str, str, int, str, str, int, str]:
    source_site = value["source_site"]
    destination_site = value["destination_site"]
    try:
        if (
            not isinstance(source_site, str)
            or not isinstance(destination_site, str)
            or source_site not in _storage.WEBAPP_SITES
            or destination_site not in _storage.WEBAPP_SITES
            or source_site == destination_site
        ):
            raise _storage.PhysicalBlobObjectStorageUploaderError("route is invalid")
        campaign = _storage._safe_text(value["campaign_id"], label=f"{label} campaign", pattern=_storage.CAMPAIGN_ID_RE)
        release = _storage._safe_text(value["release_sha"], label=f"{label} release", pattern=_storage.RELEASE_SHA_RE)
        baseline_generation_id = _storage._safe_text(
            value["baseline_generation_id"],
            label=f"{label} baseline generation",
            pattern=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII),
        )
        baseline_manifest_sha256 = _storage._sha256(value["baseline_manifest_sha256"], label=f"{label} baseline hash")
        baseline_wal_lsn = _storage._lsn(value["baseline_wal_lsn"], label=f"{label} baseline LSN")
        route_binding_sha256 = _storage._sha256(value["route_binding_sha256"], label=f"{label} route hash")
        term = _storage._exact_mapping(
            value["writer_term"],
            label=f"{label} writer term",
            fields={"holder_site", "writer_epoch", "writer_lease_id", "witnessed_term_proof_sha256"},
        )
        if term["holder_site"] != source_site:
            raise _storage.PhysicalBlobObjectStorageUploaderError("term holder is invalid")
        writer_epoch = _storage._positive_int(term["writer_epoch"], label=f"{label} writer epoch", maximum=2**63 - 1)
        writer_lease_id = _storage._safe_text(term["writer_lease_id"], label=f"{label} writer lease", pattern=_storage.LEASE_ID_RE)
        witnessed_term_proof_sha256 = _storage._sha256(term["witnessed_term_proof_sha256"], label=f"{label} Witness proof")
        timeline_id = _storage._timeline_id(value["timeline_id"], label=f"{label} timeline")
        destination_age_recipient = _storage._safe_text(
            value["destination_age_recipient"], label=f"{label} recipient", pattern=AGE_RECIPIENT_RE
        )
    except _storage.PhysicalBlobObjectStorageUploaderError as exc:
        raise _error_from_storage(exc, label=f"{label} binding is invalid") from exc
    return (
        source_site,
        destination_site,
        campaign,
        release,
        baseline_generation_id,
        baseline_manifest_sha256,
        baseline_wal_lsn,
        route_binding_sha256,
        writer_epoch,
        writer_lease_id,
        witnessed_term_proof_sha256,
        timeline_id,
        destination_age_recipient,
    )


def _object_facts(value: object, *, label: str, expected_kind: str, recipient: str) -> tuple[str, str, str, int]:
    item = _exact_mapping(
        value,
        label=label,
        fields={
            "schema",
            "version",
            "object_kind",
            "object_key",
            "version_id",
            "ciphertext_sha256",
            "ciphertext_bytes",
            "encryption",
            "age_recipient",
            "immutability",
        },
    )
    if (
        item["schema"] != PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA
        or type(item["version"]) is not int
        or item["version"] != PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION
        or item["object_kind"] != expected_kind
        or item["encryption"] != PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION
        or item["immutability"] != PHYSICAL_WAL_OBJECT_IMMUTABILITY
        or item["age_recipient"] != recipient
    ):
        raise PhysicalBlobReceiverInventoryMappingError(f"{label} policy is invalid")
    try:
        object_key = _storage._safe_text(item["object_key"], label=f"{label} Object key", pattern=OBJECT_KEY_RE)
        if any(part in {"", ".", ".."} for part in object_key.split("/")):
            raise _storage.PhysicalBlobObjectStorageUploaderError("Object key is invalid")
        version_id = item["version_id"]
        if not isinstance(version_id, str) or not version_id or version_id == "null" or VERSION_ID_RE.fullmatch(version_id) is None:
            raise _storage.PhysicalBlobObjectStorageUploaderError("VersionId is invalid")
        ciphertext_sha256 = _storage._sha256(item["ciphertext_sha256"], label=f"{label} ciphertext hash")
        ciphertext_bytes = _storage._positive_int(
            item["ciphertext_bytes"],
            label=f"{label} ciphertext bytes",
            maximum=(
                MAX_PHYSICAL_BLOB_BYTES + _MAX_BLOB_CIPHERTEXT_OVERHEAD_BYTES
                if expected_kind == "blob"
                else MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES
                + _MAX_MAPPING_CIPHERTEXT_OVERHEAD_BYTES
            ),
        )
    except _storage.PhysicalBlobObjectStorageUploaderError as exc:
        raise _error_from_storage(exc, label=f"{label} is invalid") from exc
    return object_key, version_id, ciphertext_sha256, ciphertext_bytes


def _receipt_facts_from_value(item: Mapping[str, Any], *, raw: bytes) -> _ReceiptFacts:
    (
        source_site,
        destination_site,
        campaign,
        release,
        baseline_generation_id,
        baseline_manifest_sha256,
        baseline_wal_lsn,
        route_binding_sha256,
        writer_epoch,
        writer_lease_id,
        witnessed_term_proof_sha256,
        timeline_id,
        destination_age_recipient,
    ) = _common_binding_facts(item, label="receiver mapping receipt")
    mapping_plaintext = _exact_mapping(
        item["mapping_plaintext"], label="receiver mapping receipt plaintext", fields={"sha256", "bytes"}
    )
    original_v1_inventory = _exact_mapping(
        item["original_v1_inventory"],
        label="receiver mapping receipt original v1 inventory",
        fields={"sha256", "bytes", "shard_ordinal", "entry_count"},
    )
    mapping_plaintext_sha256 = _sha256(mapping_plaintext["sha256"], label="receiver mapping receipt plaintext SHA-256")
    mapping_plaintext_bytes = _positive_int(
        mapping_plaintext["bytes"], label="receiver mapping receipt plaintext bytes", maximum=MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES
    )
    original_v1_inventory_sha256 = _sha256(original_v1_inventory["sha256"], label="receiver mapping receipt v1 SHA-256")
    original_v1_inventory_bytes = _positive_int(
        original_v1_inventory["bytes"], label="receiver mapping receipt v1 bytes", maximum=MAX_INVENTORY_SHARD_PLAINTEXT_BYTES
    )
    shard_ordinal = _positive_int(original_v1_inventory["shard_ordinal"], label="receiver mapping receipt shard ordinal", maximum=2**63 - 1)
    entry_count = _positive_int(original_v1_inventory["entry_count"], label="receiver mapping receipt entry count", maximum=MAX_BLOBS_PER_INVENTORY_SHARD)
    blob_receipts_sha256 = _sha256(item["blob_receipts_sha256"], label="receiver mapping receipt Blob receipts SHA-256")
    object_key, version_id, ciphertext_sha256, ciphertext_bytes = _object_facts(
        item["object"],
        label="receiver mapping receipt Object",
        expected_kind="blob_inventory_shard",
        recipient=destination_age_recipient,
    )
    if not all(
        item[name] is True
        for name in (
            "readback_verified",
            "not_a_database_snapshot_consistency_proof",
            "not_a_blob_frontier_manifest",
            "not_a_remote_apply_proof",
            "not_a_strict_acknowledgement_proof",
        )
    ):
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping receipt proof flags are invalid")
    return _ReceiptFacts(
        raw=raw,
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=campaign,
        release_sha=release,
        baseline_generation_id=baseline_generation_id,
        baseline_manifest_sha256=baseline_manifest_sha256,
        baseline_wal_lsn=baseline_wal_lsn,
        route_binding_sha256=route_binding_sha256,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
        witnessed_term_proof_sha256=witnessed_term_proof_sha256,
        destination_age_recipient=destination_age_recipient,
        timeline_id=timeline_id,
        mapping_plaintext_sha256=mapping_plaintext_sha256,
        mapping_plaintext_bytes=mapping_plaintext_bytes,
        original_v1_inventory_sha256=original_v1_inventory_sha256,
        original_v1_inventory_bytes=original_v1_inventory_bytes,
        shard_ordinal=shard_ordinal,
        entry_count=entry_count,
        blob_receipts_sha256=blob_receipts_sha256,
        object_key=object_key,
        version_id=version_id,
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=ciphertext_bytes,
    )


def _typed_mapping_receipt(facts: _ReceiptFacts) -> PhysicalBlobReceiverInventoryMappingReceipt:
    return PhysicalBlobReceiverInventoryMappingReceipt(
        signed_receipt=facts.raw,
        receipt_sha256=hashlib.sha256(facts.raw).hexdigest(),
        mapping_plaintext_sha256=facts.mapping_plaintext_sha256,
        mapping_plaintext_bytes=facts.mapping_plaintext_bytes,
        original_v1_inventory_sha256=facts.original_v1_inventory_sha256,
        original_v1_inventory_bytes=facts.original_v1_inventory_bytes,
        shard_ordinal=facts.shard_ordinal,
        entry_count=facts.entry_count,
        blob_receipts_sha256=facts.blob_receipts_sha256,
        object_key=facts.object_key,
        version_id=facts.version_id,
        ciphertext_sha256=facts.ciphertext_sha256,
        ciphertext_bytes=facts.ciphertext_bytes,
        timeline_id=facts.timeline_id,
        route_binding_sha256=facts.route_binding_sha256,
    )


def _validate_mapping_receipt_wrapper(value: PhysicalBlobReceiverInventoryMappingReceipt) -> None:
    if type(value) is not PhysicalBlobReceiverInventoryMappingReceipt or type(value.signed_receipt) is not bytes:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping receipt wrapper is invalid")
    _sha256(value.receipt_sha256, label="receiver mapping receipt wrapper SHA-256")
    _sha256(value.mapping_plaintext_sha256, label="receiver mapping receipt wrapper plaintext SHA-256")
    _positive_int(value.mapping_plaintext_bytes, label="receiver mapping receipt wrapper plaintext bytes", maximum=MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES)
    _sha256(value.original_v1_inventory_sha256, label="receiver mapping receipt wrapper v1 SHA-256")
    _positive_int(value.original_v1_inventory_bytes, label="receiver mapping receipt wrapper v1 bytes", maximum=MAX_INVENTORY_SHARD_PLAINTEXT_BYTES)
    _positive_int(value.shard_ordinal, label="receiver mapping receipt wrapper shard ordinal", maximum=2**63 - 1)
    _positive_int(value.entry_count, label="receiver mapping receipt wrapper entry count", maximum=MAX_BLOBS_PER_INVENTORY_SHARD)
    _sha256(value.blob_receipts_sha256, label="receiver mapping receipt wrapper Blob receipt SHA-256")
    try:
        _storage._safe_text(
            value.object_key,
            label="receiver mapping receipt wrapper Object key",
            pattern=OBJECT_KEY_RE,
        )
    except _storage.PhysicalBlobObjectStorageUploaderError as exc:
        raise _error_from_storage(
            exc, label="receiver mapping receipt wrapper Object key is invalid"
        ) from exc
    if not isinstance(value.version_id, str) or not value.version_id or value.version_id == "null" or VERSION_ID_RE.fullmatch(value.version_id) is None:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping receipt wrapper VersionId is invalid")
    _sha256(value.ciphertext_sha256, label="receiver mapping receipt wrapper ciphertext SHA-256")
    _positive_int(value.ciphertext_bytes, label="receiver mapping receipt wrapper ciphertext bytes", maximum=MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES + _MAX_MAPPING_CIPHERTEXT_OVERHEAD_BYTES)
    _timeline_id(value.timeline_id, label="receiver mapping receipt wrapper timeline")
    _sha256(value.route_binding_sha256, label="receiver mapping receipt wrapper route binding")


def verify_physical_blob_receiver_inventory_mapping_receipt(
    *,
    receipt: bytes | PhysicalBlobReceiverInventoryMappingReceipt,
    mapping_signer_public_key: bytes,
) -> PhysicalBlobReceiverInventoryMappingReceipt:
    """Verify and normalize the signed immutable mapping upload receipt."""

    expected_public_key = _public_key(mapping_signer_public_key, label="receiver mapping signer public key")
    if type(receipt) is PhysicalBlobReceiverInventoryMappingReceipt:
        _validate_mapping_receipt_wrapper(receipt)
        raw = receipt.signed_receipt
    elif isinstance(receipt, bytes):
        raw = receipt
    else:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping receipt is invalid")
    facts = _parse_mapping_receipt(raw, mapping_signer_public_key=expected_public_key)
    typed = _typed_mapping_receipt(facts)
    if type(receipt) is PhysicalBlobReceiverInventoryMappingReceipt and receipt != typed:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping receipt wrapper was tampered")
    return typed


class PhysicalBlobReceiverInventoryMappingPublisher:
    """Build and publish a v2 mapping while preserving v1 inventory publication."""

    def __init__(
        self,
        *,
        config: PhysicalBlobReceiverInventoryMappingConfig,
        age_encryptor_factory: Callable[[], _storage.PhysicalBlobAgeEncryptor] | None,
        client_factory: Callable[[], _storage.PhysicalBlobObjectStorageClient] | None,
        mapping_signer_factory: Callable[[], _storage.PhysicalBlobReceiptSigner] | None,
    ) -> None:
        self._config = config
        self._age_encryptor_factory = age_encryptor_factory
        self._client_factory = client_factory
        self._mapping_signer_factory = mapping_signer_factory

    def build_artifact(
        self,
        *,
        inventory_shard: PhysicalBlobInventoryShardPlaintext,
        v1_inventory_receipt: _storage.PhysicalBlobInventoryShardObjectStorageReceipt,
        blob_receipts: Sequence[_storage.PhysicalBlobObjectStorageReceipt],
        verified_binding: _storage.VerifiedPhysicalBlobObjectStorageBinding,
        now: datetime,
    ) -> PhysicalBlobReceiverInventoryMappingArtifact:
        """Validate v1+v2 inputs and create a canonical source-signed mapping."""

        return _mapping_artifact_from_inputs(
            config=_normalise_config(self._config),
            inventory_shard=inventory_shard,
            v1_inventory_receipt=v1_inventory_receipt,
            blob_receipts=blob_receipts,
            verified_binding=verified_binding,
            now=now,
            mapping_signer_factory=self._mapping_signer_factory,
        )

    def publish_artifact(
        self,
        *,
        artifact: PhysicalBlobReceiverInventoryMappingArtifact,
        verified_binding: _storage.VerifiedPhysicalBlobObjectStorageBinding,
        now: datetime,
        term_recheck_clock: Callable[[], datetime] | None,
    ) -> PhysicalBlobReceiverInventoryMappingReceipt:
        """Encrypt/create-only-upload one previously signed canonical mapping."""

        if type(artifact) is not PhysicalBlobReceiverInventoryMappingArtifact:
            raise PhysicalBlobReceiverInventoryMappingError("receiver mapping artifact is invalid")
        if term_recheck_clock is None or not callable(term_recheck_clock):
            raise PhysicalBlobReceiverInventoryMappingError("receiver mapping term recheck clock is required")
        observed_now = _storage._utc(now, label="receiver mapping publication clock")
        config = _normalise_config(self._config)
        binding = _binding_facts(verified_binding, now=observed_now)
        _require_config_binding(config, binding)
        mapping = _parse_mapping_plaintext(
            raw=artifact.canonical_plaintext,
            mapping_signer_public_key=config.mapping_signer_public_key,
            blob_receipt_signer_public_key=config.blob_receipt_signer_public_key,
            binding=binding,
            original_v1_inventory=None,
        )
        _validate_artifact_against_mapping(artifact=artifact, mapping=mapping, binding=binding)
        with tempfile.TemporaryDirectory(
            prefix="physical-blob-receiver-mapping-", dir=str(config.storage_config.workspace)
        ) as raw_directory:
            directory = Path(raw_directory)
            try:
                os.chmod(directory, 0o700)
                metadata = os.lstat(directory)
            except OSError as exc:
                raise PhysicalBlobReceiverInventoryMappingError("receiver mapping workspace is unsafe") from exc
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise PhysicalBlobReceiverInventoryMappingError("receiver mapping workspace is unsafe")
            plaintext_path = directory / "receiver-ready-inventory-mapping.json"
            _write_private_plaintext(plaintext_path, artifact.canonical_plaintext)
            try:
                version_id, ciphertext_sha256, ciphertext_bytes = _storage._publish_encrypted_create_only(
                    config=config.storage_config,
                    binding=binding,
                    plaintext_path=plaintext_path,
                    plaintext_sha256=artifact.plaintext_sha256,
                    plaintext_bytes=artifact.plaintext_bytes,
                    maximum_overhead_bytes=_MAX_MAPPING_CIPHERTEXT_OVERHEAD_BYTES,
                    object_key=artifact.object_key,
                    artifact_kind="receiver_inventory_mapping",
                    descriptor_or_inventory_sha256=artifact.original_v1_inventory_sha256,
                    age_encryptor_factory=self._age_encryptor_factory,
                    client_factory=self._client_factory,
                    transport_schema=PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_SCHEMA,
                )
            except _storage.PhysicalBlobObjectStorageUploaderError as exc:
                raise _error_from_storage(exc, label="receiver mapping Object Storage publication failed") from exc
        completion_now = _storage._utc(
            term_recheck_clock(), label="receiver mapping publication completion clock"
        )
        if completion_now < observed_now:
            raise PhysicalBlobReceiverInventoryMappingError(
                "receiver mapping publication completion clock moved backwards"
            )
        final_binding = _binding_facts(verified_binding, now=completion_now)
        if final_binding != binding:
            raise PhysicalBlobReceiverInventoryMappingError(
                "receiver mapping live binding changed during publication"
            )
        raw_receipt = _sign_canonical(
            value=_mapping_receipt_unsigned(
                binding=binding,
                artifact=artifact,
                mapping_signer_public_key=config.mapping_signer_public_key,
                version_id=version_id,
                ciphertext_sha256=ciphertext_sha256,
                ciphertext_bytes=ciphertext_bytes,
            ),
            signature_field="mapping_receipt_signature",
            signature_domain=_RECEIPT_SIGNATURE_DOMAIN,
            signer_factory=self._mapping_signer_factory,
            expected_public_key=config.mapping_signer_public_key,
            label="receiver mapping receipt",
        )
        return verify_physical_blob_receiver_inventory_mapping_receipt(
            receipt=raw_receipt,
            mapping_signer_public_key=config.mapping_signer_public_key,
        )

    def publish(
        self,
        *,
        inventory_shard: PhysicalBlobInventoryShardPlaintext,
        v1_inventory_receipt: _storage.PhysicalBlobInventoryShardObjectStorageReceipt,
        blob_receipts: Sequence[_storage.PhysicalBlobObjectStorageReceipt],
        verified_binding: _storage.VerifiedPhysicalBlobObjectStorageBinding,
        now: datetime,
        term_recheck_clock: Callable[[], datetime] | None,
    ) -> PhysicalBlobReceiverInventoryMappingReceipt:
        """Build then publish a mapping without changing the v1 publication."""

        artifact = self.build_artifact(
            inventory_shard=inventory_shard,
            v1_inventory_receipt=v1_inventory_receipt,
            blob_receipts=blob_receipts,
            verified_binding=verified_binding,
            now=now,
        )
        return self.publish_artifact(
            artifact=artifact,
            verified_binding=verified_binding,
            now=now,
            term_recheck_clock=term_recheck_clock,
        )


def _require_mapping_binding(mapping: _MappingFacts | _ReceiptFacts, *, binding: Any) -> None:
    manifest = binding.manifest
    if (
        mapping.source_site != manifest.source_site
        or mapping.destination_site != manifest.destination_site
        or mapping.campaign_id != manifest.campaign_id
        or mapping.release_sha != manifest.release_sha
        or mapping.baseline_generation_id != manifest.baseline_generation_id
        or mapping.baseline_manifest_sha256 != manifest.baseline_manifest_sha256
        or mapping.baseline_wal_lsn != manifest.baseline_wal_lsn
        or mapping.route_binding_sha256 != binding.route_binding_sha256
        or mapping.writer_epoch != binding.writer_epoch
        or mapping.writer_lease_id != binding.writer_lease_id
        or mapping.witnessed_term_proof_sha256 != binding.witnessed_term_proof_sha256
        or mapping.destination_age_recipient != manifest.destination_age_recipient
        or mapping.timeline_id != binding.timeline_id
    ):
        raise PhysicalBlobReceiverInventoryMappingError(
            "receiver mapping does not match the live route, baseline, term, or recipient"
        )


def _parse_mapping_plaintext(
    *,
    raw: object,
    mapping_signer_public_key: bytes,
    blob_receipt_signer_public_key: bytes,
    binding: Any,
    original_v1_inventory: _V1Inventory | None,
) -> _MappingFacts:
    item = _exact_mapping(
        _parse_canonical_json(
            raw,
            label="receiver-ready Blob inventory mapping",
            maximum_bytes=MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES,
        ),
        label="receiver-ready Blob inventory mapping",
        fields=_MAPPING_FIELDS,
    )
    if (
        item["schema"] != PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_SCHEMA
        or type(item["version"]) is not int
        or item["version"] != 2
        or item["kind"] != "receiver_ready_blob_inventory_mapping"
    ):
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping schema is invalid")
    _verify_signature(
        value=item,
        signer_field="source_mapping_signer",
        signature_field="source_mapping_signature",
        signature_domain=_MAPPING_SIGNATURE_DOMAIN,
        expected_public_key=mapping_signer_public_key,
        label="receiver mapping plaintext",
    )
    (
        source_site,
        destination_site,
        campaign,
        release,
        baseline_generation_id,
        baseline_manifest_sha256,
        baseline_wal_lsn,
        route_binding_sha256,
        writer_epoch,
        writer_lease_id,
        witnessed_term_proof_sha256,
        timeline_id,
        destination_age_recipient,
    ) = _common_binding_facts(item, label="receiver mapping plaintext")
    original = _exact_mapping(
        item["original_v1_inventory"],
        label="receiver mapping original v1 inventory",
        fields={
            "schema",
            "plaintext_sha256",
            "plaintext_bytes",
            "shard_ordinal",
            "entry_count",
            "inventory_upload_receipt_sha256",
            "inventory_upload_receipt",
        },
    )
    if original["schema"] != PHYSICAL_BLOB_INVENTORY_SHARD_PLAINTEXT_SCHEMA:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping original v1 inventory schema is invalid")
    original_v1_inventory_sha256 = _sha256(
        original["plaintext_sha256"], label="receiver mapping original v1 inventory SHA-256"
    )
    original_v1_inventory_bytes = _positive_int(
        original["plaintext_bytes"],
        label="receiver mapping original v1 inventory bytes",
        maximum=MAX_INVENTORY_SHARD_PLAINTEXT_BYTES,
    )
    shard_ordinal = _positive_int(
        original["shard_ordinal"],
        label="receiver mapping original v1 inventory shard ordinal",
        maximum=2**63 - 1,
    )
    entry_count = _positive_int(
        original["entry_count"],
        label="receiver mapping original v1 inventory entry count",
        maximum=MAX_BLOBS_PER_INVENTORY_SHARD,
    )
    if not isinstance(original["inventory_upload_receipt"], Mapping):
        raise PhysicalBlobReceiverInventoryMappingError(
            "receiver mapping original v1 inventory receipt is invalid"
        )
    original_receipt_raw = _canonical(
        dict(original["inventory_upload_receipt"]),
        label="receiver mapping original v1 inventory receipt",
    )
    if hashlib.sha256(original_receipt_raw).hexdigest() != _sha256(
        original["inventory_upload_receipt_sha256"],
        label="receiver mapping original v1 inventory receipt SHA-256",
    ):
        raise PhysicalBlobReceiverInventoryMappingError(
            "receiver mapping original v1 inventory receipt identity is invalid"
        )
    try:
        original_typed_receipt = _storage.verify_physical_blob_object_storage_receipt(
            receipt=original_receipt_raw,
            receipt_signer_public_key=blob_receipt_signer_public_key,
        )
    except _storage.PhysicalBlobObjectStorageUploaderError as exc:
        raise _error_from_storage(
            exc, label="receiver mapping original v1 inventory receipt is invalid"
        ) from exc
    v1_receipt_facts = _parse_typed_v1_inventory_receipt(
        receipt=original_typed_receipt,
        blob_receipt_signer_public_key=blob_receipt_signer_public_key,
        binding=binding,
    )
    if (
        v1_receipt_facts.plaintext_sha256 != original_v1_inventory_sha256
        or v1_receipt_facts.plaintext_bytes != original_v1_inventory_bytes
        or v1_receipt_facts.shard_ordinal != shard_ordinal
        or v1_receipt_facts.entry_count != entry_count
    ):
        raise PhysicalBlobReceiverInventoryMappingError(
            "receiver mapping original v1 inventory receipt does not match its identity"
        )
    raw_entries = item["entries"]
    if isinstance(raw_entries, (str, bytes)) or not isinstance(raw_entries, Sequence):
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping entries are invalid")
    if len(raw_entries) != entry_count or not raw_entries:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping entries omit or add v1 records")
    entries: list[_MappingEntry] = []
    parsed_blob_receipts: list[Any] = []
    seen_source_record_ids: set[str] = set()
    for ordinal, raw_entry in enumerate(raw_entries, start=1):
        entry = _exact_mapping(
            raw_entry,
            label=f"receiver mapping entry {ordinal}",
            fields={
                "ordinal",
                "source_record",
                "plaintext",
                "v1_inventory_entry",
                "blob_receipt_sha256",
                "blob_receipt",
                "final_object",
            },
        )
        if _positive_int(
            entry["ordinal"], label=f"receiver mapping entry {ordinal} ordinal", maximum=MAX_BLOBS_PER_INVENTORY_SHARD
        ) != ordinal:
            raise PhysicalBlobReceiverInventoryMappingError("receiver mapping entries are reordered")
        source_record = _exact_mapping(
            entry["source_record"],
            label=f"receiver mapping entry {ordinal} source record",
            fields={"record_id", "record_id_sha256"},
        )
        try:
            source_record_id = _storage._safe_text(
                source_record["record_id"],
                label=f"receiver mapping entry {ordinal} source record ID",
                pattern=_storage._SYSTEM_ID_RE,
            )
        except _storage.PhysicalBlobObjectStorageUploaderError as exc:
            raise _error_from_storage(exc, label="receiver mapping source record is invalid") from exc
        if source_record_id in seen_source_record_ids:
            raise PhysicalBlobReceiverInventoryMappingError("receiver mapping repeats a source record")
        seen_source_record_ids.add(source_record_id)
        if _sha256(
            source_record["record_id_sha256"],
            label=f"receiver mapping entry {ordinal} source record SHA-256",
        ) != hashlib.sha256(source_record_id.encode("utf-8")).hexdigest():
            raise PhysicalBlobReceiverInventoryMappingError("receiver mapping source record identity is invalid")
        plaintext = _exact_mapping(
            entry["plaintext"],
            label=f"receiver mapping entry {ordinal} plaintext",
            fields={"sha256", "bytes"},
        )
        content_sha256 = _sha256(
            plaintext["sha256"], label=f"receiver mapping entry {ordinal} plaintext SHA-256"
        )
        content_bytes = _positive_int(
            plaintext["bytes"],
            label=f"receiver mapping entry {ordinal} plaintext bytes",
            maximum=MAX_PHYSICAL_BLOB_BYTES,
        )
        v1_entry = _exact_mapping(
            entry["v1_inventory_entry"],
            label=f"receiver mapping entry {ordinal} v1 inventory anchor",
            fields={"handoff_descriptor_sha256", "spool_object_key"},
        )
        handoff_descriptor_sha256 = _sha256(
            v1_entry["handoff_descriptor_sha256"],
            label=f"receiver mapping entry {ordinal} handoff SHA-256",
        )
        try:
            spool_object_key = _storage._safe_text(
                v1_entry["spool_object_key"],
                label=f"receiver mapping entry {ordinal} v1 Object key",
                pattern=OBJECT_KEY_RE,
            )
        except _storage.PhysicalBlobObjectStorageUploaderError as exc:
            raise _error_from_storage(exc, label="receiver mapping v1 Object key is invalid") from exc
        expected_spool_object_key = derive_physical_blob_artifact_object_key(
            manifest_binding=_artifact_manifest(binding),
            source_record_id=source_record_id,
            declared_content_sha256=content_sha256,
        )
        if spool_object_key != expected_spool_object_key:
            raise PhysicalBlobReceiverInventoryMappingError(
                "receiver mapping v1 Object key is not deterministic"
            )
        if not isinstance(entry["blob_receipt"], Mapping):
            raise PhysicalBlobReceiverInventoryMappingError(
                f"receiver mapping entry {ordinal} Blob receipt is invalid"
            )
        blob_receipt_raw = _canonical(
            dict(entry["blob_receipt"]),
            label=f"receiver mapping entry {ordinal} Blob receipt",
        )
        blob_receipt_sha256 = _sha256(
            entry["blob_receipt_sha256"],
            label=f"receiver mapping entry {ordinal} Blob receipt SHA-256",
        )
        if hashlib.sha256(blob_receipt_raw).hexdigest() != blob_receipt_sha256:
            raise PhysicalBlobReceiverInventoryMappingError(
                "receiver mapping Blob receipt identity is invalid"
            )
        try:
            blob_receipt = _storage.verify_physical_blob_object_storage_receipt(
                receipt=blob_receipt_raw,
                receipt_signer_public_key=blob_receipt_signer_public_key,
            )
        except _storage.PhysicalBlobObjectStorageUploaderError as exc:
            raise _error_from_storage(
                exc, label=f"receiver mapping entry {ordinal} Blob receipt is invalid"
            ) from exc
        blob_receipt_facts = _parse_typed_blob_receipt(
            receipt=blob_receipt,
            blob_receipt_signer_public_key=blob_receipt_signer_public_key,
            binding=binding,
        )
        if (
            blob_receipt_facts.source_record_id != source_record_id
            or blob_receipt_facts.handoff_descriptor_sha256 != handoff_descriptor_sha256
            or blob_receipt_facts.plaintext_sha256 != content_sha256
            or blob_receipt_facts.plaintext_bytes != content_bytes
            or blob_receipt_facts.object_key
            != _storage._derive_blob_object_key(
                binding=binding,
                source_record_id=source_record_id,
                plaintext_sha256=content_sha256,
            )
        ):
            raise PhysicalBlobReceiverInventoryMappingError(
                "receiver mapping Blob receipt does not match its entry"
            )
        object_key, version_id, ciphertext_sha256, ciphertext_bytes = _object_facts(
            entry["final_object"],
            label=f"receiver mapping entry {ordinal} final Object",
            expected_kind="blob",
            recipient=binding.manifest.destination_age_recipient,
        )
        expected_object = _object_descriptor(
            facts=blob_receipt_facts,
            expected_kind="blob",
            recipient=binding.manifest.destination_age_recipient,
        )
        if dict(entry["final_object"]) != expected_object:
            raise PhysicalBlobReceiverInventoryMappingError(
                "receiver mapping final Object descriptor does not match its Blob receipt"
            )
        entries.append(
            _MappingEntry(
                ordinal=ordinal,
                source_record_id=source_record_id,
                content_sha256=content_sha256,
                content_bytes=content_bytes,
                handoff_descriptor_sha256=handoff_descriptor_sha256,
                spool_object_key=spool_object_key,
                blob_receipt_sha256=blob_receipt_sha256,
                blob_receipt_raw=blob_receipt_raw,
                object_key=object_key,
                version_id=version_id,
                ciphertext_sha256=ciphertext_sha256,
                ciphertext_bytes=ciphertext_bytes,
            )
        )
        parsed_blob_receipts.append(blob_receipt_facts)
    blob_receipts_sha256 = _sha256(
        item["blob_receipts_sha256"], label="receiver mapping Blob receipt-set SHA-256"
    )
    if (
        blob_receipts_sha256 != _storage._blob_receipts_digest(tuple(parsed_blob_receipts))
        or v1_receipt_facts.blob_receipts_sha256 != blob_receipts_sha256
    ):
        raise PhysicalBlobReceiverInventoryMappingError(
            "receiver mapping Blob receipt set does not match its pinned inventory receipt"
        )
    facts = _MappingFacts(
        raw=raw,
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=campaign,
        release_sha=release,
        baseline_generation_id=baseline_generation_id,
        baseline_manifest_sha256=baseline_manifest_sha256,
        baseline_wal_lsn=baseline_wal_lsn,
        route_binding_sha256=route_binding_sha256,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
        witnessed_term_proof_sha256=witnessed_term_proof_sha256,
        destination_age_recipient=destination_age_recipient,
        timeline_id=timeline_id,
        original_v1_inventory_sha256=original_v1_inventory_sha256,
        original_v1_inventory_bytes=original_v1_inventory_bytes,
        original_v1_inventory_receipt_raw=original_receipt_raw,
        shard_ordinal=shard_ordinal,
        entry_count=entry_count,
        blob_receipts_sha256=blob_receipts_sha256,
        entries=tuple(entries),
    )
    _require_mapping_binding(facts, binding=binding)
    if original_v1_inventory is not None:
        if (
            facts.original_v1_inventory_sha256 != original_v1_inventory.plaintext_sha256
            or facts.original_v1_inventory_bytes != original_v1_inventory.plaintext_bytes
            or facts.shard_ordinal != original_v1_inventory.shard_ordinal
            or facts.entry_count != len(original_v1_inventory.entries)
        ):
            raise PhysicalBlobReceiverInventoryMappingError(
                "receiver mapping does not match the pinned original v1 inventory"
            )
        for mapping_entry, v1_entry in zip(facts.entries, original_v1_inventory.entries, strict=True):
            if (
                mapping_entry.ordinal != v1_entry.ordinal
                or mapping_entry.source_record_id != v1_entry.source_record_id
                or mapping_entry.content_sha256 != v1_entry.content_sha256
                or mapping_entry.content_bytes != v1_entry.content_bytes
                or mapping_entry.handoff_descriptor_sha256 != v1_entry.handoff_descriptor_sha256
                or mapping_entry.spool_object_key != v1_entry.spool_object_key
            ):
                raise PhysicalBlobReceiverInventoryMappingError(
                    "receiver mapping omits, reorders, or alters an original v1 inventory entry"
                )
    return facts


def _validate_artifact_against_mapping(
    *,
    artifact: PhysicalBlobReceiverInventoryMappingArtifact,
    mapping: _MappingFacts,
    binding: Any,
) -> None:
    if type(artifact.canonical_plaintext) is not bytes:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping artifact plaintext is invalid")
    plaintext_sha256 = _sha256(
        artifact.plaintext_sha256, label="receiver mapping artifact plaintext SHA-256"
    )
    plaintext_bytes = _positive_int(
        artifact.plaintext_bytes,
        label="receiver mapping artifact plaintext bytes",
        maximum=MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES,
    )
    if (
        hashlib.sha256(artifact.canonical_plaintext).hexdigest() != plaintext_sha256
        or len(artifact.canonical_plaintext) != plaintext_bytes
        or mapping.raw != artifact.canonical_plaintext
        or mapping.original_v1_inventory_sha256 != artifact.original_v1_inventory_sha256
        or mapping.original_v1_inventory_bytes != artifact.original_v1_inventory_bytes
        or mapping.shard_ordinal != artifact.shard_ordinal
        or mapping.entry_count != artifact.entry_count
        or mapping.blob_receipts_sha256 != artifact.blob_receipts_sha256
        or mapping.timeline_id != artifact.timeline_id
        or mapping.route_binding_sha256 != artifact.route_binding_sha256
        or artifact.object_key
        != _mapping_object_key(
            binding=binding,
            original_v1_inventory_sha256=mapping.original_v1_inventory_sha256,
            mapping_plaintext_sha256=plaintext_sha256,
        )
    ):
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping artifact was tampered")


def _write_private_plaintext(path: Path, value: bytes) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        raise PhysicalBlobReceiverInventoryMappingError(
            "platform lacks fail-closed receiver mapping plaintext open"
        )
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        view = memoryview(value)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise PhysicalBlobReceiverInventoryMappingError(
                    "receiver mapping plaintext write failed"
                )
            view = view[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != len(value)
        ):
            raise PhysicalBlobReceiverInventoryMappingError("receiver mapping plaintext is unsafe")
    except OSError as exc:
        raise PhysicalBlobReceiverInventoryMappingError("receiver mapping plaintext write failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def verify_physical_blob_receiver_inventory_mapping_plaintext(
    *,
    mapping_plaintext: bytes,
    mapping_receipt: PhysicalBlobReceiverInventoryMappingReceipt,
    original_v1_inventory_plaintext: bytes,
    mapping_signer_public_key: bytes,
    blob_receipt_signer_public_key: bytes,
    verified_binding: _storage.VerifiedPhysicalBlobObjectStorageBinding,
    now: datetime,
) -> VerifiedPhysicalBlobReceiverInventoryMapping:
    """Verify receiver plaintext against its receipt and the original v1 shard.

    A receiver must obtain the original v1 inventory using the descriptor that
    the mapping embeds, then provide its decrypted canonical plaintext here.
    This is what makes omissions and reordering detectable rather than merely
    trusting a source-signed replacement list.
    """

    mapping_key = _public_key(mapping_signer_public_key, label="receiver mapping signer public key")
    blob_key = _public_key(
        blob_receipt_signer_public_key, label="receiver mapping Blob receipt signer public key"
    )
    binding = _binding_facts(verified_binding, now=now)
    typed_receipt = verify_physical_blob_receiver_inventory_mapping_receipt(
        receipt=mapping_receipt,
        mapping_signer_public_key=mapping_key,
    )
    receipt_facts = _parse_mapping_receipt(
        typed_receipt.signed_receipt, mapping_signer_public_key=mapping_key
    )
    _require_mapping_binding(receipt_facts, binding=binding)
    original_v1 = _v1_inventory_from_raw(
        raw=original_v1_inventory_plaintext,
        binding=binding,
        maximum_blob_bytes=MAX_PHYSICAL_BLOB_BYTES,
    )
    mapping = _parse_mapping_plaintext(
        raw=mapping_plaintext,
        mapping_signer_public_key=mapping_key,
        blob_receipt_signer_public_key=blob_key,
        binding=binding,
        original_v1_inventory=original_v1,
    )
    if (
        hashlib.sha256(mapping_plaintext).hexdigest() != receipt_facts.mapping_plaintext_sha256
        or len(mapping_plaintext) != receipt_facts.mapping_plaintext_bytes
        or mapping.original_v1_inventory_sha256 != receipt_facts.original_v1_inventory_sha256
        or mapping.original_v1_inventory_bytes != receipt_facts.original_v1_inventory_bytes
        or mapping.shard_ordinal != receipt_facts.shard_ordinal
        or mapping.entry_count != receipt_facts.entry_count
        or mapping.blob_receipts_sha256 != receipt_facts.blob_receipts_sha256
        or receipt_facts.object_key
        != _mapping_object_key(
            binding=binding,
            original_v1_inventory_sha256=mapping.original_v1_inventory_sha256,
            mapping_plaintext_sha256=receipt_facts.mapping_plaintext_sha256,
        )
    ):
        raise PhysicalBlobReceiverInventoryMappingError(
            "receiver mapping plaintext does not match its pinned mapping receipt"
        )
    result = VerifiedPhysicalBlobReceiverInventoryMapping(
        canonical_plaintext=mapping_plaintext,
        mapping_receipt=typed_receipt,
        source_site=mapping.source_site,
        destination_site=mapping.destination_site,
        campaign_id=mapping.campaign_id,
        release_sha=mapping.release_sha,
        baseline_generation_id=mapping.baseline_generation_id,
        baseline_manifest_sha256=mapping.baseline_manifest_sha256,
        baseline_wal_lsn=mapping.baseline_wal_lsn,
        writer_epoch=mapping.writer_epoch,
        writer_lease_id=mapping.writer_lease_id,
        witnessed_term_proof_sha256=mapping.witnessed_term_proof_sha256,
        destination_age_recipient=mapping.destination_age_recipient,
        timeline_id=mapping.timeline_id,
        original_v1_inventory_sha256=mapping.original_v1_inventory_sha256,
        original_v1_inventory_bytes=mapping.original_v1_inventory_bytes,
        shard_ordinal=mapping.shard_ordinal,
        entry_count=mapping.entry_count,
        blob_receipts_sha256=mapping.blob_receipts_sha256,
    )
    object.__setattr__(result, "_capability", _VERIFIED_MAPPING_CAPABILITY)
    return result


def _validate_verified_mapping_wrapper(
    *,
    value: object,
    mapping_signer_public_key: bytes,
) -> tuple[
    VerifiedPhysicalBlobReceiverInventoryMapping,
    PhysicalBlobReceiverInventoryMappingReceipt,
]:
    """Strictly validate a capability projection before any equality checks.

    The capability marker prevents ordinary construction, but a hostile
    in-process mutation must still fail closed.  In particular, Python's
    ``True == 1`` must not make a forged epoch, timeline, shard ordinal,
    entry count, or byte count appear equal to a signed value.
    """

    if (
        type(value) is not VerifiedPhysicalBlobReceiverInventoryMapping
        or value._capability is not _VERIFIED_MAPPING_CAPABILITY
    ):
        raise PhysicalBlobReceiverInventoryMappingError(
            "verified receiver mapping capability is required"
        )
    if (
        not isinstance(value.canonical_plaintext, bytes)
        or not value.canonical_plaintext
        or len(value.canonical_plaintext)
        > MAX_PHYSICAL_BLOB_RECEIVER_MAPPING_PLAINTEXT_BYTES
    ):
        raise PhysicalBlobReceiverInventoryMappingError(
            "verified receiver mapping plaintext is invalid"
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
        if not isinstance(getattr(value, field_name), str) or not getattr(value, field_name):
            raise PhysicalBlobReceiverInventoryMappingError(
                f"verified receiver mapping {field_name} is invalid"
            )
    _sha256(value.baseline_manifest_sha256, label="verified receiver mapping baseline SHA-256")
    _sha256(value.witnessed_term_proof_sha256, label="verified receiver mapping Witness proof SHA-256")
    _sha256(value.original_v1_inventory_sha256, label="verified receiver mapping v1 SHA-256")
    _sha256(value.blob_receipts_sha256, label="verified receiver mapping Blob receipt-set SHA-256")
    _positive_int(
        value.writer_epoch,
        label="verified receiver mapping writer epoch",
        maximum=2**63 - 1,
    )
    _timeline_id(value.timeline_id, label="verified receiver mapping timeline")
    _positive_int(
        value.original_v1_inventory_bytes,
        label="verified receiver mapping v1 bytes",
        maximum=MAX_INVENTORY_SHARD_PLAINTEXT_BYTES,
    )
    _positive_int(
        value.shard_ordinal,
        label="verified receiver mapping shard ordinal",
        maximum=2**63 - 1,
    )
    _positive_int(
        value.entry_count,
        label="verified receiver mapping entry count",
        maximum=MAX_BLOBS_PER_INVENTORY_SHARD,
    )
    if type(value.mapping_receipt) is not PhysicalBlobReceiverInventoryMappingReceipt:
        raise PhysicalBlobReceiverInventoryMappingError(
            "verified receiver mapping receipt is invalid"
        )
    normalized_receipt = verify_physical_blob_receiver_inventory_mapping_receipt(
        receipt=value.mapping_receipt,
        mapping_signer_public_key=mapping_signer_public_key,
    )
    if normalized_receipt != value.mapping_receipt:
        raise PhysicalBlobReceiverInventoryMappingError(
            "verified receiver mapping receipt wrapper was tampered"
        )
    return value, normalized_receipt


def require_verified_physical_blob_receiver_inventory_mapping(
    value: object,
    *,
    mapping_signer_public_key: bytes,
    blob_receipt_signer_public_key: bytes,
    verified_binding: _storage.VerifiedPhysicalBlobObjectStorageBinding,
    now: datetime,
) -> VerifiedPhysicalBlobReceiverInventoryMapping:
    """Revalidate a mapping capability against current explicit public pins.

    The caller must supply both approved signer pins on every reuse; keys
    embedded in canonical mapping bytes are not an authorization source.  The
    supplied binding is a locally fresh/unexpired proof, not a live Witness
    network query.
    """

    mapping_key = _public_key(
        mapping_signer_public_key, label="receiver mapping signer public key"
    )
    blob_key = _public_key(
        blob_receipt_signer_public_key,
        label="receiver mapping Blob receipt signer public key",
    )
    mapping, normalized_receipt = _validate_verified_mapping_wrapper(
        value=value, mapping_signer_public_key=mapping_key
    )
    binding = _binding_facts(verified_binding, now=now)
    facts = _parse_mapping_plaintext(
        raw=mapping.canonical_plaintext,
        mapping_signer_public_key=mapping_key,
        blob_receipt_signer_public_key=blob_key,
        binding=binding,
        original_v1_inventory=None,
    )
    receipt_facts = _parse_mapping_receipt(
        normalized_receipt.signed_receipt,
        mapping_signer_public_key=mapping_key,
    )
    _require_mapping_binding(receipt_facts, binding=binding)
    expected_mapping_key = _mapping_object_key(
        binding=binding,
        original_v1_inventory_sha256=facts.original_v1_inventory_sha256,
        mapping_plaintext_sha256=hashlib.sha256(facts.raw).hexdigest(),
    )
    if (
        receipt_facts.mapping_plaintext_sha256 != hashlib.sha256(facts.raw).hexdigest()
        or receipt_facts.mapping_plaintext_bytes != len(facts.raw)
        or receipt_facts.original_v1_inventory_sha256
        != facts.original_v1_inventory_sha256
        or receipt_facts.original_v1_inventory_bytes
        != facts.original_v1_inventory_bytes
        or receipt_facts.shard_ordinal != facts.shard_ordinal
        or receipt_facts.entry_count != facts.entry_count
        or receipt_facts.blob_receipts_sha256 != facts.blob_receipts_sha256
        or receipt_facts.object_key != expected_mapping_key
        or mapping.source_site != facts.source_site
        or mapping.destination_site != facts.destination_site
        or mapping.campaign_id != facts.campaign_id
        or mapping.release_sha != facts.release_sha
        or mapping.baseline_generation_id != facts.baseline_generation_id
        or mapping.baseline_manifest_sha256 != facts.baseline_manifest_sha256
        or mapping.baseline_wal_lsn != facts.baseline_wal_lsn
        or mapping.writer_epoch != facts.writer_epoch
        or mapping.writer_lease_id != facts.writer_lease_id
        or mapping.witnessed_term_proof_sha256 != facts.witnessed_term_proof_sha256
        or mapping.destination_age_recipient != facts.destination_age_recipient
        or mapping.timeline_id != facts.timeline_id
        or mapping.original_v1_inventory_sha256 != facts.original_v1_inventory_sha256
        or mapping.original_v1_inventory_bytes != facts.original_v1_inventory_bytes
        or mapping.shard_ordinal != facts.shard_ordinal
        or mapping.entry_count != facts.entry_count
        or mapping.blob_receipts_sha256 != facts.blob_receipts_sha256
    ):
        raise PhysicalBlobReceiverInventoryMappingError(
            "verified receiver mapping capability was tampered"
        )
    return mapping


def build_physical_wal_blob_inventory_shard_from_receiver_mapping(
    *,
    verified_mapping: VerifiedPhysicalBlobReceiverInventoryMapping,
    mapping_signer_public_key: bytes,
    blob_receipt_signer_public_key: bytes,
    verified_binding: _storage.VerifiedPhysicalBlobObjectStorageBinding,
    now: datetime,
) -> dict[str, Any]:
    """Return one live-term-checked ``blob_inventory_shard`` frontier item."""

    verified_mapping = require_verified_physical_blob_receiver_inventory_mapping(
        verified_mapping,
        mapping_signer_public_key=mapping_signer_public_key,
        blob_receipt_signer_public_key=blob_receipt_signer_public_key,
        verified_binding=verified_binding,
        now=now,
    )
    receipt = verified_mapping.mapping_receipt
    binding = _binding_facts(verified_binding, now=now)
    mapping_key = _public_key(
        mapping_signer_public_key, label="receiver mapping signer public key"
    )
    typed_receipt = verify_physical_blob_receiver_inventory_mapping_receipt(
        receipt=receipt,
        mapping_signer_public_key=mapping_key,
    )
    receipt_facts = _parse_mapping_receipt(
        typed_receipt.signed_receipt,
        mapping_signer_public_key=mapping_key,
    )
    _require_mapping_binding(receipt_facts, binding=binding)
    if (
        typed_receipt.mapping_plaintext_sha256
        != hashlib.sha256(verified_mapping.canonical_plaintext).hexdigest()
        or typed_receipt.mapping_plaintext_bytes != len(verified_mapping.canonical_plaintext)
        or typed_receipt.shard_ordinal != verified_mapping.shard_ordinal
        or typed_receipt.entry_count != verified_mapping.entry_count
        or typed_receipt.blob_receipts_sha256 != verified_mapping.blob_receipts_sha256
        or receipt_facts.source_site != verified_mapping.source_site
        or receipt_facts.destination_site != verified_mapping.destination_site
        or receipt_facts.campaign_id != verified_mapping.campaign_id
        or receipt_facts.release_sha != verified_mapping.release_sha
        or receipt_facts.baseline_generation_id
        != verified_mapping.baseline_generation_id
        or receipt_facts.baseline_manifest_sha256
        != verified_mapping.baseline_manifest_sha256
        or receipt_facts.baseline_wal_lsn != verified_mapping.baseline_wal_lsn
        or receipt_facts.writer_epoch != verified_mapping.writer_epoch
        or receipt_facts.writer_lease_id != verified_mapping.writer_lease_id
        or receipt_facts.witnessed_term_proof_sha256
        != verified_mapping.witnessed_term_proof_sha256
        or receipt_facts.destination_age_recipient
        != verified_mapping.destination_age_recipient
        or receipt_facts.timeline_id != verified_mapping.timeline_id
    ):
        raise PhysicalBlobReceiverInventoryMappingError(
            "verified receiver mapping receipt was tampered"
        )
    return {
        "ordinal": verified_mapping.shard_ordinal,
        "plaintext_sha256": typed_receipt.mapping_plaintext_sha256,
        "plaintext_bytes": typed_receipt.mapping_plaintext_bytes,
        "entry_count": verified_mapping.entry_count,
        "object": {
            "schema": PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA,
            "version": PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION,
            "object_kind": "blob_inventory_shard",
            "object_key": typed_receipt.object_key,
            "version_id": typed_receipt.version_id,
            "ciphertext_sha256": typed_receipt.ciphertext_sha256,
            "ciphertext_bytes": typed_receipt.ciphertext_bytes,
            "encryption": PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
            "age_recipient": verified_mapping.destination_age_recipient,
            "immutability": PHYSICAL_WAL_OBJECT_IMMUTABILITY,
        },
    }
