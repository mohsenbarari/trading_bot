"""Fail-closed receiver staging for exact-version encrypted physical Blob objects.

This is deliberately a narrow receiver-side boundary.  It accepts only
already signed, exact-version Object-Storage receipts and a locally fresh
Writer-Witness binding; derives the full expected Arvan metadata from those
pins; and asks the injected exact-version reader to stream one ciphertext to a
new private staging directory.  It never lists Object Storage, follows an URL,
chooses ``latest``, loads credentials, decrypts an object, restores a Blob,
or talks directly to another WebApp.

The current Blob receipt envelopes happen to use a version-one *receipt*
grammar.  That is not a legacy data-plane admission path: this module requires
the current v2 Object-Storage transport metadata (or the current v2 receiver
mapping transport metadata) on every GET.  Raw v1 spool/handoff/inventory
artifacts and unbound receipt bytes have no entry point here.

An observation produced here proves only a private local copy of one exact
encrypted Object Storage version.  It is not an age-decryption result, a Blob
frontier proof, a remote-apply receipt, a strict acknowledgement, or a
promotion authorization.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

import core.physical_blob_object_storage_uploader as _storage
import core.physical_blob_receiver_inventory_mapping as _mapping
from core.append_only_sync_delta_batch import SHA256_RE, WEBAPP_SITES
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_arvan_exact_version_pull import (
    ARVAN_EXACT_VERSION_PULL_MAX_CIPHERTEXT_BYTES,
    ArvanExactVersionPullClientFactory,
    ArvanExactVersionPullError,
    ArvanExactVersionPullExpectation,
    ArvanExactVersionPullReader,
    RootOwnedArvanExactVersionPullConfig,
    validate_arvan_exact_version_pull_config,
)
from core.physical_blob_object_storage_uploader import (
    PHYSICAL_BLOB_OBJECT_STORAGE_ENCRYPTION,
    PHYSICAL_BLOB_OBJECT_STORAGE_UPLOADER_SCHEMA,
    PhysicalBlobInventoryShardObjectStorageReceipt,
    PhysicalBlobObjectStorageReceipt,
    VerifiedPhysicalBlobObjectStorageBinding,
    verify_physical_blob_object_storage_receipt,
)
from core.physical_blob_receiver_inventory_mapping import (
    PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_SCHEMA,
    PhysicalBlobReceiverInventoryMappingReceipt,
    verify_physical_blob_receiver_inventory_mapping_receipt,
)
from core.physical_wal_receiver_staging import PhysicalWalExactVersionReadback


__all__ = (
    "PHYSICAL_BLOB_RECEIVER_EXACT_PULL_STAGING_DEFAULT_ENABLED",
    "PHYSICAL_BLOB_RECEIVER_EXACT_PULL_STAGING_SCHEMA",
    "PhysicalBlobReceiverExactBlobPullObservation",
    "PhysicalBlobReceiverExactInventoryAnchorPullObservation",
    "PhysicalBlobReceiverExactInventoryMappingPullObservation",
    "PhysicalBlobReceiverExactPullStager",
    "PhysicalBlobReceiverExactPullStagingConfig",
    "PhysicalBlobReceiverExactPullStagingError",
    "require_verified_physical_blob_receiver_exact_pull_observation",
)


PHYSICAL_BLOB_RECEIVER_EXACT_PULL_STAGING_SCHEMA = (
    "gold-trade-physical-blob-receiver-exact-pull-staging-v1"
)
PHYSICAL_BLOB_RECEIVER_EXACT_PULL_STAGING_DEFAULT_ENABLED = False

_AGE_V1_HEADER = b"age-encryption.org/v1\n"
_COPY_CHUNK_BYTES = 1024 * 1024
_OBSERVATION_CAPABILITY = object()
_STAGE_DIRECTORY_PREFIX = ".physical-blob-exact-pull-"
_STAGED_CIPHERTEXT_NAME = "ciphertext.age"


class PhysicalBlobReceiverExactPullStagingError(ValueError):
    """A receiver-side encrypted Blob pull or its local evidence is unsafe."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalBlobReceiverExactPullStagingConfig:
    """Root-owned, default-disabled receiver staging policy for one route.

    ``staging_root`` must already exist, be root-owned, and have mode ``0700``.
    The adapter chooses the child directory and output filename itself, so a
    caller cannot smuggle a destination path or overwrite an existing object.
    """

    staging_root: Path | None = None
    receiver_site: str = ""
    receiver_age_recipient: str = ""
    enabled: bool = PHYSICAL_BLOB_RECEIVER_EXACT_PULL_STAGING_DEFAULT_ENABLED
    maximum_ciphertext_bytes: int = ARVAN_EXACT_VERSION_PULL_MAX_CIPHERTEXT_BYTES
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class PhysicalBlobReceiverExactBlobPullObservation:
    """One staged, signed v2 finalized Blob ciphertext observation."""

    schema: str
    storage_receipt: PhysicalBlobObjectStorageReceipt
    receipt_sha256: str
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
    destination_age_recipient: str
    source_record_id: str
    plaintext_sha256: str
    plaintext_bytes: int
    handoff_descriptor_sha256: str
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    ciphertext_path: Path
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalBlobReceiverExactInventoryAnchorPullObservation:
    """A staged current-v2 transport copy of the mapping's inventory anchor.

    It is intentionally not a legacy inventory admission capability.  The
    anchor is retained only because a later verified v2 mapping plaintext must
    compare itself byte-for-byte with the original signed inventory shard.
    """

    schema: str
    inventory_receipt: PhysicalBlobInventoryShardObjectStorageReceipt
    mapping_receipt: PhysicalBlobReceiverInventoryMappingReceipt
    receipt_sha256: str
    mapping_receipt_sha256: str
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
    destination_age_recipient: str
    plaintext_sha256: str
    plaintext_bytes: int
    shard_ordinal: int
    entry_count: int
    blob_receipts_sha256: str
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    ciphertext_path: Path
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalBlobReceiverExactInventoryMappingPullObservation:
    """One staged, signed v2 receiver-inventory-mapping ciphertext observation."""

    schema: str
    mapping_receipt: PhysicalBlobReceiverInventoryMappingReceipt
    receipt_sha256: str
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
    destination_age_recipient: str
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
    ciphertext_path: Path
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


_Observation = (
    PhysicalBlobReceiverExactBlobPullObservation
    | PhysicalBlobReceiverExactInventoryAnchorPullObservation
    | PhysicalBlobReceiverExactInventoryMappingPullObservation
)


@dataclass(frozen=True)
class _ConfigFacts:
    staging_root: Path
    receiver_site: str
    receiver_age_recipient: str
    maximum_ciphertext_bytes: int


@dataclass(frozen=True)
class _PullFacts:
    kind: str
    receipt: (
        PhysicalBlobObjectStorageReceipt
        | PhysicalBlobInventoryShardObjectStorageReceipt
        | PhysicalBlobReceiverInventoryMappingReceipt
    )
    related_mapping_receipt: PhysicalBlobReceiverInventoryMappingReceipt | None
    receipt_sha256: str
    related_mapping_receipt_sha256: str | None
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
    destination_age_recipient: str
    plaintext_sha256: str
    plaintext_bytes: int
    source_record_id: str | None
    handoff_descriptor_sha256: str | None
    shard_ordinal: int | None
    entry_count: int | None
    blob_receipts_sha256: str | None
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    metadata: Mapping[str, str]


def _fail(code: str) -> None:
    raise PhysicalBlobReceiverExactPullStagingError(code)


def _safe_sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _positive_int(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _private_root(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        _fail(code)
    try:
        metadata = os.lstat(value)
        resolved = value.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != value
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail(code)
    return resolved


def _config_facts(value: object, *, require_enabled: bool) -> _ConfigFacts:
    if type(value) is not PhysicalBlobReceiverExactPullStagingConfig:
        _fail("BLOB_EXACT_PULL_CONFIG_INVALID")
    if type(value.enabled) is not bool:
        _fail("BLOB_EXACT_PULL_CONFIG_INVALID")
    if require_enabled and value.enabled is not True:
        _fail("BLOB_EXACT_PULL_DISABLED")
    if value.direct_site_control != "forbidden" or value.destination_object_ingest != "pull-only":
        _fail("BLOB_EXACT_PULL_DIRECTION_POLICY_INVALID")
    if type(value.receiver_site) is not str or value.receiver_site not in WEBAPP_SITES:
        _fail("BLOB_EXACT_PULL_RECEIVER_SITE_INVALID")
    if (
        type(value.receiver_age_recipient) is not str
        or AGE_RECIPIENT_RE.fullmatch(value.receiver_age_recipient) is None
    ):
        _fail("BLOB_EXACT_PULL_RECEIVER_RECIPIENT_INVALID")
    return _ConfigFacts(
        staging_root=_private_root(value.staging_root, code="BLOB_EXACT_PULL_STAGING_ROOT_UNSAFE"),
        receiver_site=value.receiver_site,
        receiver_age_recipient=value.receiver_age_recipient,
        maximum_ciphertext_bytes=_positive_int(
            value.maximum_ciphertext_bytes,
            maximum=ARVAN_EXACT_VERSION_PULL_MAX_CIPHERTEXT_BYTES,
            code="BLOB_EXACT_PULL_MAXIMUM_BYTES_INVALID",
        ),
    )


def _binding_facts(value: object, *, now: datetime) -> Any:
    try:
        return _storage._binding_facts(value, now=now)
    except _storage.PhysicalBlobObjectStorageUploaderError:
        _fail("BLOB_EXACT_PULL_BINDING_INVALID")


def _require_receiver_route(config: _ConfigFacts, binding: Any) -> None:
    if (
        binding.manifest.destination_site != config.receiver_site
        or binding.manifest.destination_age_recipient != config.receiver_age_recipient
    ):
        _fail("BLOB_EXACT_PULL_RECEIVER_ROUTE_MISMATCH")


def _metadata(
    *,
    transport_schema: str,
    artifact_kind: str,
    descriptor_or_inventory_sha256: str,
    binding: Any,
    plaintext_sha256: str,
    plaintext_bytes: int,
    ciphertext_sha256: str,
    ciphertext_bytes: int,
) -> Mapping[str, str]:
    # Do not accept a caller-provided metadata map.  The exact reader will
    # compare this complete canonical map against the GET response.
    return {
        "transport-schema": transport_schema,
        "artifact-kind": artifact_kind,
        "route-binding-sha256": binding.route_binding_sha256,
        "timeline-id": str(binding.timeline_id),
        "writer-epoch": str(binding.writer_epoch),
        "witnessed-term-proof-sha256": binding.witnessed_term_proof_sha256,
        "destination-age-recipient": binding.manifest.destination_age_recipient,
        "descriptor-or-inventory-sha256": descriptor_or_inventory_sha256,
        "plaintext-sha256": plaintext_sha256,
        "plaintext-bytes": str(plaintext_bytes),
        "encryption": PHYSICAL_BLOB_OBJECT_STORAGE_ENCRYPTION,
        "ciphertext-sha256": ciphertext_sha256,
        "ciphertext-bytes": str(ciphertext_bytes),
    }


def _crosscheck_common(
    *,
    facts: Any,
    binding: Any,
    code: str,
) -> None:
    manifest = binding.manifest
    if (
        facts.source_site != manifest.source_site
        or facts.destination_site != manifest.destination_site
        or facts.campaign_id != manifest.campaign_id
        or facts.release_sha != manifest.release_sha
        or facts.baseline_generation_id != manifest.baseline_generation_id
        or facts.baseline_manifest_sha256 != manifest.baseline_manifest_sha256
        or facts.baseline_wal_lsn != manifest.baseline_wal_lsn
        or facts.route_binding_sha256 != binding.route_binding_sha256
        or facts.writer_epoch != binding.writer_epoch
        or facts.writer_lease_id != binding.writer_lease_id
        or facts.witnessed_term_proof_sha256 != binding.witnessed_term_proof_sha256
        or facts.destination_age_recipient != manifest.destination_age_recipient
        or facts.timeline_id != binding.timeline_id
    ):
        _fail(code)


def _storage_receipt_facts(
    *,
    receipt: object,
    receipt_signer_public_key: bytes,
    binding: Any,
    expected_type: type[PhysicalBlobObjectStorageReceipt]
    | type[PhysicalBlobInventoryShardObjectStorageReceipt],
    expected_kind: str,
) -> tuple[PhysicalBlobObjectStorageReceipt | PhysicalBlobInventoryShardObjectStorageReceipt, Any]:
    if type(receipt) is not expected_type:
        _fail("BLOB_EXACT_PULL_RECEIPT_TYPE_INVALID")
    try:
        typed = verify_physical_blob_object_storage_receipt(
            receipt=receipt,
            receipt_signer_public_key=receipt_signer_public_key,
        )
        facts = _storage._parse_receipt(
            typed.signed_receipt,
            receipt_signer_public_key=receipt_signer_public_key,
        )
        _storage._require_receipt_binding(facts, binding=binding)
    except _storage.PhysicalBlobObjectStorageUploaderError:
        _fail("BLOB_EXACT_PULL_RECEIPT_BINDING_INVALID")
    if type(typed) is not expected_type or facts.kind != expected_kind:
        _fail("BLOB_EXACT_PULL_RECEIPT_KIND_INVALID")
    _crosscheck_common(facts=facts, binding=binding, code="BLOB_EXACT_PULL_RECEIPT_BINDING_INVALID")
    return typed, facts


def _mapping_receipt_facts(
    *,
    receipt: object,
    mapping_signer_public_key: bytes,
    binding: Any,
) -> tuple[PhysicalBlobReceiverInventoryMappingReceipt, Any]:
    if type(receipt) is not PhysicalBlobReceiverInventoryMappingReceipt:
        _fail("BLOB_EXACT_PULL_MAPPING_RECEIPT_TYPE_INVALID")
    try:
        typed = verify_physical_blob_receiver_inventory_mapping_receipt(
            receipt=receipt,
            mapping_signer_public_key=mapping_signer_public_key,
        )
        facts = _mapping._parse_mapping_receipt(
            typed.signed_receipt,
            mapping_signer_public_key=mapping_signer_public_key,
        )
        _mapping._require_mapping_binding(facts, binding=binding)
    except _mapping.PhysicalBlobReceiverInventoryMappingError:
        _fail("BLOB_EXACT_PULL_MAPPING_RECEIPT_BINDING_INVALID")
    if type(typed) is not PhysicalBlobReceiverInventoryMappingReceipt:
        _fail("BLOB_EXACT_PULL_MAPPING_RECEIPT_TYPE_INVALID")
    _crosscheck_common(facts=facts, binding=binding, code="BLOB_EXACT_PULL_MAPPING_RECEIPT_BINDING_INVALID")
    return typed, facts


def _blob_pull_facts(
    *,
    receipt: object,
    receipt_signer_public_key: bytes,
    binding: Any,
) -> _PullFacts:
    typed, facts = _storage_receipt_facts(
        receipt=receipt,
        receipt_signer_public_key=receipt_signer_public_key,
        binding=binding,
        expected_type=PhysicalBlobObjectStorageReceipt,
        expected_kind="finalized_blob_object",
    )
    if (
        facts.source_record_id is None
        or facts.handoff_descriptor_sha256 is None
        or facts.object_key
        != _storage._derive_blob_object_key(
            binding=binding,
            source_record_id=facts.source_record_id,
            plaintext_sha256=facts.plaintext_sha256,
        )
        or typed.receipt_sha256 != hashlib.sha256(typed.signed_receipt).hexdigest()
        or typed.source_record_id != facts.source_record_id
        or typed.plaintext_sha256 != facts.plaintext_sha256
        or typed.plaintext_bytes != facts.plaintext_bytes
        or typed.handoff_descriptor_sha256 != facts.handoff_descriptor_sha256
        or typed.object_key != facts.object_key
        or typed.version_id != facts.version_id
        or typed.ciphertext_sha256 != facts.ciphertext_sha256
        or typed.ciphertext_bytes != facts.ciphertext_bytes
    ):
        _fail("BLOB_EXACT_PULL_RECEIPT_PIN_MISMATCH")
    return _PullFacts(
        kind="finalized_blob",
        receipt=typed,
        related_mapping_receipt=None,
        receipt_sha256=typed.receipt_sha256,
        related_mapping_receipt_sha256=None,
        source_site=facts.source_site,
        destination_site=facts.destination_site,
        campaign_id=facts.campaign_id,
        release_sha=facts.release_sha,
        baseline_generation_id=facts.baseline_generation_id,
        baseline_manifest_sha256=facts.baseline_manifest_sha256,
        baseline_wal_lsn=facts.baseline_wal_lsn,
        route_binding_sha256=facts.route_binding_sha256,
        writer_epoch=facts.writer_epoch,
        writer_lease_id=facts.writer_lease_id,
        witnessed_term_proof_sha256=facts.witnessed_term_proof_sha256,
        timeline_id=facts.timeline_id,
        destination_age_recipient=facts.destination_age_recipient,
        plaintext_sha256=facts.plaintext_sha256,
        plaintext_bytes=facts.plaintext_bytes,
        source_record_id=facts.source_record_id,
        handoff_descriptor_sha256=facts.handoff_descriptor_sha256,
        shard_ordinal=None,
        entry_count=None,
        blob_receipts_sha256=None,
        object_key=facts.object_key,
        version_id=facts.version_id,
        ciphertext_sha256=facts.ciphertext_sha256,
        ciphertext_bytes=facts.ciphertext_bytes,
        metadata=_metadata(
            transport_schema=PHYSICAL_BLOB_OBJECT_STORAGE_UPLOADER_SCHEMA,
            artifact_kind="finalized_blob",
            descriptor_or_inventory_sha256=facts.handoff_descriptor_sha256,
            binding=binding,
            plaintext_sha256=facts.plaintext_sha256,
            plaintext_bytes=facts.plaintext_bytes,
            ciphertext_sha256=facts.ciphertext_sha256,
            ciphertext_bytes=facts.ciphertext_bytes,
        ),
    )


def _mapping_pull_facts(
    *,
    receipt: object,
    mapping_signer_public_key: bytes,
    binding: Any,
) -> _PullFacts:
    typed, facts = _mapping_receipt_facts(
        receipt=receipt,
        mapping_signer_public_key=mapping_signer_public_key,
        binding=binding,
    )
    try:
        expected_key = _mapping._mapping_object_key(
            binding=binding,
            original_v1_inventory_sha256=facts.original_v1_inventory_sha256,
            mapping_plaintext_sha256=facts.mapping_plaintext_sha256,
        )
    except _mapping.PhysicalBlobReceiverInventoryMappingError:
        _fail("BLOB_EXACT_PULL_MAPPING_RECEIPT_PIN_MISMATCH")
    if (
        facts.object_key != expected_key
        or typed.receipt_sha256 != hashlib.sha256(typed.signed_receipt).hexdigest()
        or typed.mapping_plaintext_sha256 != facts.mapping_plaintext_sha256
        or typed.mapping_plaintext_bytes != facts.mapping_plaintext_bytes
        or typed.original_v1_inventory_sha256 != facts.original_v1_inventory_sha256
        or typed.original_v1_inventory_bytes != facts.original_v1_inventory_bytes
        or typed.shard_ordinal != facts.shard_ordinal
        or typed.entry_count != facts.entry_count
        or typed.blob_receipts_sha256 != facts.blob_receipts_sha256
        or typed.object_key != facts.object_key
        or typed.version_id != facts.version_id
        or typed.ciphertext_sha256 != facts.ciphertext_sha256
        or typed.ciphertext_bytes != facts.ciphertext_bytes
    ):
        _fail("BLOB_EXACT_PULL_MAPPING_RECEIPT_PIN_MISMATCH")
    return _PullFacts(
        kind="receiver_inventory_mapping",
        receipt=typed,
        related_mapping_receipt=None,
        receipt_sha256=typed.receipt_sha256,
        related_mapping_receipt_sha256=None,
        source_site=facts.source_site,
        destination_site=facts.destination_site,
        campaign_id=facts.campaign_id,
        release_sha=facts.release_sha,
        baseline_generation_id=facts.baseline_generation_id,
        baseline_manifest_sha256=facts.baseline_manifest_sha256,
        baseline_wal_lsn=facts.baseline_wal_lsn,
        route_binding_sha256=facts.route_binding_sha256,
        writer_epoch=facts.writer_epoch,
        writer_lease_id=facts.writer_lease_id,
        witnessed_term_proof_sha256=facts.witnessed_term_proof_sha256,
        timeline_id=facts.timeline_id,
        destination_age_recipient=facts.destination_age_recipient,
        plaintext_sha256=facts.mapping_plaintext_sha256,
        plaintext_bytes=facts.mapping_plaintext_bytes,
        source_record_id=None,
        handoff_descriptor_sha256=None,
        shard_ordinal=facts.shard_ordinal,
        entry_count=facts.entry_count,
        blob_receipts_sha256=facts.blob_receipts_sha256,
        object_key=facts.object_key,
        version_id=facts.version_id,
        ciphertext_sha256=facts.ciphertext_sha256,
        ciphertext_bytes=facts.ciphertext_bytes,
        metadata=_metadata(
            transport_schema=PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_SCHEMA,
            artifact_kind="receiver_inventory_mapping",
            descriptor_or_inventory_sha256=facts.original_v1_inventory_sha256,
            binding=binding,
            plaintext_sha256=facts.mapping_plaintext_sha256,
            plaintext_bytes=facts.mapping_plaintext_bytes,
            ciphertext_sha256=facts.ciphertext_sha256,
            ciphertext_bytes=facts.ciphertext_bytes,
        ),
    )


def _inventory_anchor_pull_facts(
    *,
    inventory_receipt: object,
    mapping_receipt: object,
    receipt_signer_public_key: bytes,
    mapping_signer_public_key: bytes,
    binding: Any,
) -> _PullFacts:
    mapping_typed, mapping_facts = _mapping_receipt_facts(
        receipt=mapping_receipt,
        mapping_signer_public_key=mapping_signer_public_key,
        binding=binding,
    )
    typed, facts = _storage_receipt_facts(
        receipt=inventory_receipt,
        receipt_signer_public_key=receipt_signer_public_key,
        binding=binding,
        expected_type=PhysicalBlobInventoryShardObjectStorageReceipt,
        expected_kind="blob_inventory_shard_object",
    )
    if (
        facts.shard_ordinal is None
        or facts.entry_count is None
        or facts.blob_receipts_sha256 is None
        or facts.object_key
        != _storage._derive_inventory_object_key(
            binding=binding,
            shard_ordinal=facts.shard_ordinal,
            plaintext_sha256=facts.plaintext_sha256,
        )
        or typed.receipt_sha256 != hashlib.sha256(typed.signed_receipt).hexdigest()
        or typed.shard_ordinal != facts.shard_ordinal
        or typed.entry_count != facts.entry_count
        or typed.plaintext_sha256 != facts.plaintext_sha256
        or typed.plaintext_bytes != facts.plaintext_bytes
        or typed.blob_receipts_sha256 != facts.blob_receipts_sha256
        or typed.object_key != facts.object_key
        or typed.version_id != facts.version_id
        or typed.ciphertext_sha256 != facts.ciphertext_sha256
        or typed.ciphertext_bytes != facts.ciphertext_bytes
        or mapping_typed.original_v1_inventory_sha256 != facts.plaintext_sha256
        or mapping_typed.original_v1_inventory_bytes != facts.plaintext_bytes
        or mapping_typed.shard_ordinal != facts.shard_ordinal
        or mapping_typed.entry_count != facts.entry_count
    ):
        _fail("BLOB_EXACT_PULL_INVENTORY_ANCHOR_PIN_MISMATCH")
    return _PullFacts(
        kind="inventory_anchor",
        receipt=typed,
        related_mapping_receipt=mapping_typed,
        receipt_sha256=typed.receipt_sha256,
        related_mapping_receipt_sha256=mapping_typed.receipt_sha256,
        source_site=facts.source_site,
        destination_site=facts.destination_site,
        campaign_id=facts.campaign_id,
        release_sha=facts.release_sha,
        baseline_generation_id=facts.baseline_generation_id,
        baseline_manifest_sha256=facts.baseline_manifest_sha256,
        baseline_wal_lsn=facts.baseline_wal_lsn,
        route_binding_sha256=facts.route_binding_sha256,
        writer_epoch=facts.writer_epoch,
        writer_lease_id=facts.writer_lease_id,
        witnessed_term_proof_sha256=facts.witnessed_term_proof_sha256,
        timeline_id=facts.timeline_id,
        destination_age_recipient=facts.destination_age_recipient,
        plaintext_sha256=facts.plaintext_sha256,
        plaintext_bytes=facts.plaintext_bytes,
        source_record_id=None,
        handoff_descriptor_sha256=None,
        shard_ordinal=facts.shard_ordinal,
        entry_count=facts.entry_count,
        blob_receipts_sha256=facts.blob_receipts_sha256,
        object_key=facts.object_key,
        version_id=facts.version_id,
        ciphertext_sha256=facts.ciphertext_sha256,
        ciphertext_bytes=facts.ciphertext_bytes,
        metadata=_metadata(
            transport_schema=PHYSICAL_BLOB_OBJECT_STORAGE_UPLOADER_SCHEMA,
            artifact_kind="blob_inventory_shard",
            descriptor_or_inventory_sha256=facts.plaintext_sha256,
            binding=binding,
            plaintext_sha256=facts.plaintext_sha256,
            plaintext_bytes=facts.plaintext_bytes,
            ciphertext_sha256=facts.ciphertext_sha256,
            ciphertext_bytes=facts.ciphertext_bytes,
        ),
    )


def _secure_child_directory(root: Path) -> Path:
    try:
        raw = tempfile.mkdtemp(prefix=_STAGE_DIRECTORY_PREFIX, dir=str(root))
        path = Path(raw)
        os.chmod(path, 0o700)
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("BLOB_EXACT_PULL_STAGING_CREATE_FAILED")
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        _fail("BLOB_EXACT_PULL_STAGING_CREATE_FAILED")
    if (
        len(relative.parts) != 1
        or not relative.name.startswith(_STAGE_DIRECTORY_PREFIX)
        or resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("BLOB_EXACT_PULL_STAGING_CREATE_FAILED")
    return resolved


def _open_new_private_ciphertext(directory: Path) -> tuple[Path, int]:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("BLOB_EXACT_PULL_NOFOLLOW_UNAVAILABLE")
    path = directory / _STAGED_CIPHERTEXT_NAME
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        metadata = os.fstat(descriptor)
    except OSError:
        _fail("BLOB_EXACT_PULL_STAGING_CREATE_FAILED")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size != 0
    ):
        try:
            os.close(descriptor)
        except OSError:
            pass
        _fail("BLOB_EXACT_PULL_STAGING_CREATE_FAILED")
    return path, descriptor


def _verify_private_ciphertext_fd(
    *,
    descriptor: int,
    path: Path,
    expected_sha256: str,
    expected_bytes: int,
) -> None:
    try:
        metadata = os.fstat(descriptor)
        path_metadata = os.lstat(path)
    except OSError:
        _fail("BLOB_EXACT_PULL_STAGED_FILE_UNSAFE")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size != expected_bytes
        or stat.S_ISLNK(path_metadata.st_mode)
        or path_metadata.st_dev != metadata.st_dev
        or path_metadata.st_ino != metadata.st_ino
        or path_metadata.st_uid != 0
        or path_metadata.st_nlink != 1
        or stat.S_IMODE(path_metadata.st_mode) != 0o600
        or path_metadata.st_size != expected_bytes
    ):
        _fail("BLOB_EXACT_PULL_STAGED_FILE_UNSAFE")
    try:
        header = os.pread(descriptor, len(_AGE_V1_HEADER), 0)
    except OSError:
        _fail("BLOB_EXACT_PULL_STAGED_FILE_UNSAFE")
    if header != _AGE_V1_HEADER:
        _fail("BLOB_EXACT_PULL_ENCRYPTION_AMBIGUOUS")
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError:
        _fail("BLOB_EXACT_PULL_STAGED_FILE_UNSAFE")
    digest = hashlib.sha256()
    total = 0
    while True:
        try:
            chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
        except OSError:
            _fail("BLOB_EXACT_PULL_STAGED_FILE_UNSAFE")
        if not isinstance(chunk, bytes):
            _fail("BLOB_EXACT_PULL_STAGED_FILE_UNSAFE")
        if not chunk:
            break
        total += len(chunk)
        if total > expected_bytes:
            _fail("BLOB_EXACT_PULL_STAGED_FILE_UNSAFE")
        digest.update(chunk)
    if total != expected_bytes or digest.hexdigest() != expected_sha256:
        _fail("BLOB_EXACT_PULL_STAGED_FILE_UNSAFE")


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        _fail("BLOB_EXACT_PULL_STAGED_FILE_UNSAFE")
    try:
        os.fsync(descriptor)
    except OSError:
        _fail("BLOB_EXACT_PULL_STAGED_FILE_UNSAFE")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _remove_failed_stage(*, directory: Path, path: Path | None) -> None:
    # The adapter created both paths itself.  Never recurse through an
    # attacker-controlled path or delete an existing staging directory.
    if path is not None:
        try:
            metadata = os.lstat(path)
            if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                os.unlink(path)
        except OSError:
            pass
    try:
        os.rmdir(directory)
    except OSError:
        pass


def _stage_ciphertext(
    *,
    facts: _PullFacts,
    config: _ConfigFacts,
    arvan_config: RootOwnedArvanExactVersionPullConfig,
    client_factory: ArvanExactVersionPullClientFactory | Callable[..., object],
) -> Path:
    if facts.ciphertext_bytes > config.maximum_ciphertext_bytes:
        _fail("BLOB_EXACT_PULL_OBJECT_EXCEEDS_LOCAL_BOUND")
    validated_arvan = validate_arvan_exact_version_pull_config(arvan_config)
    if validated_arvan.enabled is not True:
        _fail("BLOB_EXACT_PULL_ARVAN_READER_DISABLED")
    if facts.ciphertext_bytes > validated_arvan.maximum_ciphertext_bytes:
        _fail("BLOB_EXACT_PULL_OBJECT_EXCEEDS_TRANSPORT_BOUND")
    if not callable(client_factory):
        _fail("BLOB_EXACT_PULL_CLIENT_FACTORY_REQUIRED")
    try:
        reader = ArvanExactVersionPullReader(
            config=validated_arvan,
            client_factory=client_factory,
            expectations=(
                ArvanExactVersionPullExpectation(
                    object_key=facts.object_key,
                    version_id=facts.version_id,
                    ciphertext_sha256=facts.ciphertext_sha256,
                    ciphertext_bytes=facts.ciphertext_bytes,
                    metadata=dict(facts.metadata),
                ),
            ),
        )
    except ArvanExactVersionPullError:
        _fail("BLOB_EXACT_PULL_TRANSPORT_POLICY_INVALID")
    directory = _secure_child_directory(config.staging_root)
    path: Path | None = None
    descriptor = -1
    success = False
    try:
        path, descriptor = _open_new_private_ciphertext(directory)
        try:
            result = reader.read_exact_to_fd(
                object_key=facts.object_key,
                version_id=facts.version_id,
                destination_fd=descriptor,
            )
        except ArvanExactVersionPullError:
            _fail("BLOB_EXACT_PULL_TRANSPORT_READ_FAILED")
        if (
            type(result) is not PhysicalWalExactVersionReadback
            or result.object_key != facts.object_key
            or result.version_id != facts.version_id
            or result.ciphertext_sha256 != facts.ciphertext_sha256
            or result.ciphertext_bytes != facts.ciphertext_bytes
        ):
            _fail("BLOB_EXACT_PULL_TRANSPORT_READBACK_MISMATCH")
        try:
            os.fsync(descriptor)
        except OSError:
            _fail("BLOB_EXACT_PULL_STAGED_FILE_UNSAFE")
        _verify_private_ciphertext_fd(
            descriptor=descriptor,
            path=path,
            expected_sha256=facts.ciphertext_sha256,
            expected_bytes=facts.ciphertext_bytes,
        )
        try:
            os.close(descriptor)
        except OSError:
            _fail("BLOB_EXACT_PULL_STAGED_FILE_UNSAFE")
        descriptor = -1
        _fsync_directory(directory)
        success = True
        return path
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not success:
            _remove_failed_stage(directory=directory, path=path)


def _observation_from_facts(*, facts: _PullFacts, ciphertext_path: Path) -> _Observation:
    common = {
        "schema": PHYSICAL_BLOB_RECEIVER_EXACT_PULL_STAGING_SCHEMA,
        "receipt_sha256": facts.receipt_sha256,
        "source_site": facts.source_site,
        "destination_site": facts.destination_site,
        "campaign_id": facts.campaign_id,
        "release_sha": facts.release_sha,
        "baseline_generation_id": facts.baseline_generation_id,
        "baseline_manifest_sha256": facts.baseline_manifest_sha256,
        "baseline_wal_lsn": facts.baseline_wal_lsn,
        "route_binding_sha256": facts.route_binding_sha256,
        "writer_epoch": facts.writer_epoch,
        "writer_lease_id": facts.writer_lease_id,
        "witnessed_term_proof_sha256": facts.witnessed_term_proof_sha256,
        "timeline_id": facts.timeline_id,
        "destination_age_recipient": facts.destination_age_recipient,
        "object_key": facts.object_key,
        "version_id": facts.version_id,
        "ciphertext_sha256": facts.ciphertext_sha256,
        "ciphertext_bytes": facts.ciphertext_bytes,
        "ciphertext_path": ciphertext_path,
    }
    if facts.kind == "finalized_blob":
        if (
            type(facts.receipt) is not PhysicalBlobObjectStorageReceipt
            or facts.source_record_id is None
            or facts.handoff_descriptor_sha256 is None
        ):
            _fail("BLOB_EXACT_PULL_OBSERVATION_INVALID")
        result: _Observation = PhysicalBlobReceiverExactBlobPullObservation(
            storage_receipt=facts.receipt,
            source_record_id=facts.source_record_id,
            plaintext_sha256=facts.plaintext_sha256,
            plaintext_bytes=facts.plaintext_bytes,
            handoff_descriptor_sha256=facts.handoff_descriptor_sha256,
            **common,
        )
    elif facts.kind == "receiver_inventory_mapping":
        if (
            type(facts.receipt) is not PhysicalBlobReceiverInventoryMappingReceipt
            or facts.shard_ordinal is None
            or facts.entry_count is None
            or facts.blob_receipts_sha256 is None
        ):
            _fail("BLOB_EXACT_PULL_OBSERVATION_INVALID")
        result = PhysicalBlobReceiverExactInventoryMappingPullObservation(
            mapping_receipt=facts.receipt,
            mapping_plaintext_sha256=facts.plaintext_sha256,
            mapping_plaintext_bytes=facts.plaintext_bytes,
            original_v1_inventory_sha256=facts.receipt.original_v1_inventory_sha256,
            original_v1_inventory_bytes=facts.receipt.original_v1_inventory_bytes,
            shard_ordinal=facts.shard_ordinal,
            entry_count=facts.entry_count,
            blob_receipts_sha256=facts.blob_receipts_sha256,
            **common,
        )
    elif facts.kind == "inventory_anchor":
        if (
            type(facts.receipt) is not PhysicalBlobInventoryShardObjectStorageReceipt
            or type(facts.related_mapping_receipt) is not PhysicalBlobReceiverInventoryMappingReceipt
            or facts.related_mapping_receipt_sha256 is None
            or facts.shard_ordinal is None
            or facts.entry_count is None
            or facts.blob_receipts_sha256 is None
        ):
            _fail("BLOB_EXACT_PULL_OBSERVATION_INVALID")
        result = PhysicalBlobReceiverExactInventoryAnchorPullObservation(
            inventory_receipt=facts.receipt,
            mapping_receipt=facts.related_mapping_receipt,
            mapping_receipt_sha256=facts.related_mapping_receipt_sha256,
            plaintext_sha256=facts.plaintext_sha256,
            plaintext_bytes=facts.plaintext_bytes,
            shard_ordinal=facts.shard_ordinal,
            entry_count=facts.entry_count,
            blob_receipts_sha256=facts.blob_receipts_sha256,
            **common,
        )
    else:
        _fail("BLOB_EXACT_PULL_OBSERVATION_INVALID")
    object.__setattr__(result, "_capability", _OBSERVATION_CAPABILITY)
    return result


class PhysicalBlobReceiverExactPullStager:
    """Construct no network client itself; stage only exact pinned receipts."""

    def __init__(
        self,
        *,
        config: PhysicalBlobReceiverExactPullStagingConfig,
        arvan_pull_config: RootOwnedArvanExactVersionPullConfig,
        client_factory: ArvanExactVersionPullClientFactory | Callable[..., object],
        blob_receipt_signer_public_key: bytes,
        mapping_signer_public_key: bytes,
    ) -> None:
        self._config = config
        self._arvan_pull_config = arvan_pull_config
        self._client_factory = client_factory
        self._blob_receipt_signer_public_key = blob_receipt_signer_public_key
        self._mapping_signer_public_key = mapping_signer_public_key

    def stage_blob(
        self,
        *,
        receipt: PhysicalBlobObjectStorageReceipt,
        verified_binding: VerifiedPhysicalBlobObjectStorageBinding,
        now: datetime,
    ) -> PhysicalBlobReceiverExactBlobPullObservation:
        """Stage one current-v2 finalized Blob ciphertext from its exact receipt."""

        config = _config_facts(self._config, require_enabled=True)
        binding = _binding_facts(verified_binding, now=now)
        _require_receiver_route(config, binding)
        facts = _blob_pull_facts(
            receipt=receipt,
            receipt_signer_public_key=self._blob_receipt_signer_public_key,
            binding=binding,
        )
        result = _observation_from_facts(
            facts=facts,
            ciphertext_path=_stage_ciphertext(
                facts=facts,
                config=config,
                arvan_config=self._arvan_pull_config,
                client_factory=self._client_factory,
            ),
        )
        if type(result) is not PhysicalBlobReceiverExactBlobPullObservation:
            _fail("BLOB_EXACT_PULL_OBSERVATION_INVALID")
        return result

    def stage_inventory_mapping(
        self,
        *,
        receipt: PhysicalBlobReceiverInventoryMappingReceipt,
        verified_binding: VerifiedPhysicalBlobObjectStorageBinding,
        now: datetime,
    ) -> PhysicalBlobReceiverExactInventoryMappingPullObservation:
        """Stage one current-v2 receiver mapping ciphertext from its exact receipt."""

        config = _config_facts(self._config, require_enabled=True)
        binding = _binding_facts(verified_binding, now=now)
        _require_receiver_route(config, binding)
        facts = _mapping_pull_facts(
            receipt=receipt,
            mapping_signer_public_key=self._mapping_signer_public_key,
            binding=binding,
        )
        result = _observation_from_facts(
            facts=facts,
            ciphertext_path=_stage_ciphertext(
                facts=facts,
                config=config,
                arvan_config=self._arvan_pull_config,
                client_factory=self._client_factory,
            ),
        )
        if type(result) is not PhysicalBlobReceiverExactInventoryMappingPullObservation:
            _fail("BLOB_EXACT_PULL_OBSERVATION_INVALID")
        return result

    def stage_inventory_anchor(
        self,
        *,
        inventory_receipt: PhysicalBlobInventoryShardObjectStorageReceipt,
        mapping_receipt: PhysicalBlobReceiverInventoryMappingReceipt,
        verified_binding: VerifiedPhysicalBlobObjectStorageBinding,
        now: datetime,
    ) -> PhysicalBlobReceiverExactInventoryAnchorPullObservation:
        """Stage the v2-mapping-bound inventory anchor, never a raw v1 input."""

        config = _config_facts(self._config, require_enabled=True)
        binding = _binding_facts(verified_binding, now=now)
        _require_receiver_route(config, binding)
        facts = _inventory_anchor_pull_facts(
            inventory_receipt=inventory_receipt,
            mapping_receipt=mapping_receipt,
            receipt_signer_public_key=self._blob_receipt_signer_public_key,
            mapping_signer_public_key=self._mapping_signer_public_key,
            binding=binding,
        )
        result = _observation_from_facts(
            facts=facts,
            ciphertext_path=_stage_ciphertext(
                facts=facts,
                config=config,
                arvan_config=self._arvan_pull_config,
                client_factory=self._client_factory,
            ),
        )
        if type(result) is not PhysicalBlobReceiverExactInventoryAnchorPullObservation:
            _fail("BLOB_EXACT_PULL_OBSERVATION_INVALID")
        return result


def _require_observation_file(
    *,
    ciphertext_path: object,
    config: _ConfigFacts,
    expected_sha256: str,
    expected_bytes: int,
) -> Path:
    if not isinstance(ciphertext_path, Path) or not ciphertext_path.is_absolute():
        _fail("BLOB_EXACT_PULL_OBSERVATION_FILE_INVALID")
    try:
        resolved = ciphertext_path.resolve(strict=True)
        relative = resolved.relative_to(config.staging_root)
        directory = resolved.parent
        directory_metadata = os.lstat(directory)
    except (OSError, ValueError):
        _fail("BLOB_EXACT_PULL_OBSERVATION_FILE_INVALID")
    if (
        resolved != ciphertext_path
        or len(relative.parts) != 2
        or relative.parts[1] != _STAGED_CIPHERTEXT_NAME
        or not relative.parts[0].startswith(_STAGE_DIRECTORY_PREFIX)
        or stat.S_ISLNK(directory_metadata.st_mode)
        or not stat.S_ISDIR(directory_metadata.st_mode)
        or directory_metadata.st_uid != 0
        or stat.S_IMODE(directory_metadata.st_mode) != 0o700
        or not hasattr(os, "O_NOFOLLOW")
    ):
        _fail("BLOB_EXACT_PULL_OBSERVATION_FILE_INVALID")
    try:
        descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        _fail("BLOB_EXACT_PULL_OBSERVATION_FILE_INVALID")
    try:
        _verify_private_ciphertext_fd(
            descriptor=descriptor,
            path=resolved,
            expected_sha256=expected_sha256,
            expected_bytes=expected_bytes,
        )
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    return resolved


def _common_observation_matches(value: _Observation, facts: _PullFacts, *, path: Path) -> bool:
    # Validate scalar types before equality.  In particular, ``True == 1``
    # must not let an in-memory mutated epoch, timeline, or byte count retain
    # a capability after revalidation.
    if any(
        type(getattr(value, field_name)) is not str
        for field_name in (
            "schema",
            "receipt_sha256",
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
            "destination_age_recipient",
            "object_key",
            "version_id",
            "ciphertext_sha256",
        )
    ) or any(
        type(getattr(value, field_name)) is not int
        for field_name in ("writer_epoch", "timeline_id", "ciphertext_bytes")
    ):
        return False
    return (
        value.schema == PHYSICAL_BLOB_RECEIVER_EXACT_PULL_STAGING_SCHEMA
        and value.receipt_sha256 == facts.receipt_sha256
        and value.source_site == facts.source_site
        and value.destination_site == facts.destination_site
        and value.campaign_id == facts.campaign_id
        and value.release_sha == facts.release_sha
        and value.baseline_generation_id == facts.baseline_generation_id
        and value.baseline_manifest_sha256 == facts.baseline_manifest_sha256
        and value.baseline_wal_lsn == facts.baseline_wal_lsn
        and value.route_binding_sha256 == facts.route_binding_sha256
        and value.writer_epoch == facts.writer_epoch
        and value.writer_lease_id == facts.writer_lease_id
        and value.witnessed_term_proof_sha256 == facts.witnessed_term_proof_sha256
        and value.timeline_id == facts.timeline_id
        and value.destination_age_recipient == facts.destination_age_recipient
        and value.object_key == facts.object_key
        and value.version_id == facts.version_id
        and value.ciphertext_sha256 == facts.ciphertext_sha256
        and value.ciphertext_bytes == facts.ciphertext_bytes
        and value.ciphertext_path == path
    )


def require_verified_physical_blob_receiver_exact_pull_observation(
    value: object,
    *,
    config: PhysicalBlobReceiverExactPullStagingConfig,
    blob_receipt_signer_public_key: bytes,
    mapping_signer_public_key: bytes,
    verified_binding: VerifiedPhysicalBlobObjectStorageBinding,
    now: datetime,
) -> _Observation:
    """Revalidate a staged observation before decrypt/mapping/evidence use.

    It reruns signature, route, current-term, timeline, recipient, exact-key,
    local-file, hash, size, and age-v1-header checks.  A locally staged file
    never becomes reusable merely because a stale wrapper was kept in memory.
    """

    config_facts = _config_facts(config, require_enabled=True)
    binding = _binding_facts(verified_binding, now=now)
    _require_receiver_route(config_facts, binding)
    if getattr(value, "_capability", None) is not _OBSERVATION_CAPABILITY:
        _fail("BLOB_EXACT_PULL_OBSERVATION_CAPABILITY_REQUIRED")
    if type(value) is PhysicalBlobReceiverExactBlobPullObservation:
        facts = _blob_pull_facts(
            receipt=value.storage_receipt,
            receipt_signer_public_key=blob_receipt_signer_public_key,
            binding=binding,
        )
        if (
            type(value.source_record_id) is not str
            or type(value.plaintext_sha256) is not str
            or type(value.plaintext_bytes) is not int
            or type(value.handoff_descriptor_sha256) is not str
            or value.source_record_id != facts.source_record_id
            or value.plaintext_sha256 != facts.plaintext_sha256
            or value.plaintext_bytes != facts.plaintext_bytes
            or value.handoff_descriptor_sha256 != facts.handoff_descriptor_sha256
        ):
            _fail("BLOB_EXACT_PULL_OBSERVATION_TAMPERED")
    elif type(value) is PhysicalBlobReceiverExactInventoryMappingPullObservation:
        facts = _mapping_pull_facts(
            receipt=value.mapping_receipt,
            mapping_signer_public_key=mapping_signer_public_key,
            binding=binding,
        )
        if (
            type(value.mapping_plaintext_sha256) is not str
            or type(value.mapping_plaintext_bytes) is not int
            or type(value.original_v1_inventory_sha256) is not str
            or type(value.original_v1_inventory_bytes) is not int
            or type(value.shard_ordinal) is not int
            or type(value.entry_count) is not int
            or type(value.blob_receipts_sha256) is not str
            or value.mapping_plaintext_sha256 != facts.plaintext_sha256
            or value.mapping_plaintext_bytes != facts.plaintext_bytes
            or value.original_v1_inventory_sha256
            != value.mapping_receipt.original_v1_inventory_sha256
            or value.original_v1_inventory_bytes
            != value.mapping_receipt.original_v1_inventory_bytes
            or value.shard_ordinal != facts.shard_ordinal
            or value.entry_count != facts.entry_count
            or value.blob_receipts_sha256 != facts.blob_receipts_sha256
        ):
            _fail("BLOB_EXACT_PULL_OBSERVATION_TAMPERED")
    elif type(value) is PhysicalBlobReceiverExactInventoryAnchorPullObservation:
        facts = _inventory_anchor_pull_facts(
            inventory_receipt=value.inventory_receipt,
            mapping_receipt=value.mapping_receipt,
            receipt_signer_public_key=blob_receipt_signer_public_key,
            mapping_signer_public_key=mapping_signer_public_key,
            binding=binding,
        )
        if (
            type(value.mapping_receipt_sha256) is not str
            or type(value.plaintext_sha256) is not str
            or type(value.plaintext_bytes) is not int
            or type(value.shard_ordinal) is not int
            or type(value.entry_count) is not int
            or type(value.blob_receipts_sha256) is not str
            or value.mapping_receipt_sha256 != facts.related_mapping_receipt_sha256
            or value.plaintext_sha256 != facts.plaintext_sha256
            or value.plaintext_bytes != facts.plaintext_bytes
            or value.shard_ordinal != facts.shard_ordinal
            or value.entry_count != facts.entry_count
            or value.blob_receipts_sha256 != facts.blob_receipts_sha256
        ):
            _fail("BLOB_EXACT_PULL_OBSERVATION_TAMPERED")
    else:
        _fail("BLOB_EXACT_PULL_OBSERVATION_CAPABILITY_REQUIRED")
    path = _require_observation_file(
        ciphertext_path=value.ciphertext_path,
        config=config_facts,
        expected_sha256=facts.ciphertext_sha256,
        expected_bytes=facts.ciphertext_bytes,
    )
    if not _common_observation_matches(value, facts, path=path):
        _fail("BLOB_EXACT_PULL_OBSERVATION_TAMPERED")
    return value
