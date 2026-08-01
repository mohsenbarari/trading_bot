"""Receiver-facing Witness-signed handoff receipt for a v2 chunked backup.

This is a portable, pure evidence contract.  It pins the exact canonical
chunk-set manifest, its finalization permit, and its route binding for a
receiver.  It does not publish, fetch, restore, promote, or contact any host.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_wal_chunked_base_backup_manifest import (
    VerifiedPhysicalWalChunkedBaseBackupManifest,
    require_verified_physical_wal_chunked_base_backup_manifest,
)
from core.physical_wal_chunked_base_backup_lineage_envelope import (
    VerifiedPhysicalWalChunkedBaseBackupLineageEnvelope,
    require_verified_physical_wal_chunked_base_backup_lineage_envelope,
)


__all__ = (
    "MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_AGE_SECONDS",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_SCHEMA",
    "PhysicalWalChunkedBaseBackupHandoffReceiptError",
    "VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt",
    "build_physical_wal_chunked_base_backup_handoff_receipt",
    "canonical_physical_wal_chunked_base_backup_handoff_receipt_bytes",
    "require_verified_physical_wal_chunked_base_backup_handoff_receipt",
    "verify_physical_wal_chunked_base_backup_handoff_receipt",
)


PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-handoff-receipt-v2"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_VERSION = 2
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_SIGNATURE_ALGORITHM = "ed25519"
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_BYTES = 128 * 1024
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_AGE_SECONDS = 120
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_FUTURE_SKEW_SECONDS = 5

_DOMAIN = b"gold-trade-physical-wal-chunked-base-backup-handoff-receipt-v2\x00"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$", re.ASCII)
_TRANSITION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SUPPORTED_WAL_SEGMENT_SIZES = frozenset({16 * 1024 * 1024})
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "binding_sha256",
        "destination_age_recipient",
        "session_sha256",
        "manifest_id",
        "manifest_sha256",
        "finalization_permit_id",
        "finalization_permit_sha256",
        "committed_chunk_set_sha256",
        "lineage_sha256",
        "snapshot_sha256",
        "snapshot_bytes",
        "baseline_generation_id",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "baseline_wal_lsn",
        "wal_chain_start_lsn",
        "base_backup_end_lsn",
        "completion_attestation_sha256",
        "legacy_route_binding_sha256",
        "witness_transition_id",
        "receipt_id",
        "receipt_nonce",
        "issued_at",
        "expires_at",
        "witness_signer",
        "witness_signature",
    }
)
_VERIFIED_CAPABILITY = object()


class PhysicalWalChunkedBaseBackupHandoffReceiptError(ValueError):
    """A v2 receiver handoff receipt is malformed, stale, or unbound."""


@dataclass(frozen=True)
class VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt:
    """Opaque Witness receipt; not pull, restore, promotion, or writer authority."""

    canonical_receipt: bytes
    binding_sha256: str
    destination_age_recipient: str
    session_sha256: str
    manifest_id: str
    manifest_sha256: str
    finalization_permit_id: str
    finalization_permit_sha256: str
    committed_chunk_set_sha256: str
    lineage_sha256: str
    snapshot_sha256: str
    snapshot_bytes: int
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    completion_attestation_sha256: str
    legacy_route_binding_sha256: str
    witness_transition_id: str
    receipt_id: str
    receipt_nonce: str
    issued_at: datetime
    expires_at: datetime
    witness_public_key: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _Facts:
    raw: bytes
    binding_sha256: str
    destination_age_recipient: str
    session_sha256: str
    manifest_id: str
    manifest_sha256: str
    finalization_permit_id: str
    finalization_permit_sha256: str
    committed_chunk_set_sha256: str
    lineage_sha256: str
    snapshot_sha256: str
    snapshot_bytes: int
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    completion_attestation_sha256: str
    legacy_route_binding_sha256: str
    witness_transition_id: str
    receipt_id: str
    receipt_nonce: str
    issued_at: datetime
    expires_at: datetime
    witness_public_key: bytes


def _fail(message: str) -> None:
    raise PhysicalWalChunkedBaseBackupHandoffReceiptError(message)


def _canonical(value: object, *, label: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalChunkedBaseBackupHandoffReceiptError(f"{label} is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("chunked base-backup handoff receipt JSON has duplicate fields")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("chunked base-backup handoff receipt JSON has a forbidden constant")


def _exact_mapping(value: object, *, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(f"{label} fields are invalid")
    return dict(value)


def _text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(f"{label} is invalid")
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(f"{label} is invalid")
    return value


def _id(value: object, *, label: str) -> str:
    return _text(value, label=label, pattern=_ID_RE)


def _nonce(value: object, *, label: str) -> str:
    return _text(value, label=label, pattern=_NONCE_RE)


def _sha256(value: object, *, label: str) -> str:
    digest = _text(value, label=label, pattern=_SHA256_RE)
    if digest == "0" * 64:
        _fail(f"{label} is invalid")
    return digest


def _positive(value: object, *, label: str, maximum: int) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        _fail(f"{label} is invalid")
    return value


def _matching(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    return _text(value, label=label, pattern=pattern)


def _lineage_payload(
    lineage_envelope: VerifiedPhysicalWalChunkedBaseBackupLineageEnvelope,
    *,
    transfer_binding,
    now: datetime,
) -> dict[str, Any]:
    envelope = require_verified_physical_wal_chunked_base_backup_lineage_envelope(
        lineage_envelope,
        transfer_binding=transfer_binding,
        now=now,
    )
    return {
        "lineage_sha256": envelope.lineage_sha256,
        "snapshot_sha256": envelope.snapshot_sha256,
        "snapshot_bytes": envelope.snapshot_bytes,
        "baseline_generation_id": envelope.baseline_generation_id,
        "database_system_identifier": envelope.database_system_identifier,
        "timeline_id": envelope.timeline_id,
        "wal_segment_size_bytes": envelope.wal_segment_size_bytes,
        "baseline_wal_lsn": envelope.baseline_wal_lsn,
        "wal_chain_start_lsn": envelope.wal_chain_start_lsn,
        "base_backup_end_lsn": envelope.base_backup_end_lsn,
        "completion_attestation_sha256": envelope.completion_attestation_sha256,
        "legacy_route_binding_sha256": envelope.legacy_route_binding_sha256,
        "witness_transition_id": envelope.witness_transition_id,
    }


def _utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(f"{label} is invalid")
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(f"{label} is invalid")
    if parsed.tzinfo is None:
        _fail(f"{label} is invalid")
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        _fail(f"{label} is not canonical UTC")
    return normalized


def _timestamp_text(value: object, *, label: str) -> str:
    return _utc(value, label=label).isoformat()


def _public_key(value: object, *, label: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        _fail(f"{label} is invalid")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError):
        _fail(f"{label} is invalid")
    return value


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _decode_base64(value: object, *, label: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str):
        _fail(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(f"{label} is invalid")
    if len(decoded) != expected_bytes:
        _fail(f"{label} is invalid")
    return decoded


def _signer(value: object, *, label: str) -> bytes:
    signer = _exact_mapping(value, fields=_SIGNER_FIELDS, label=f"{label} signer")
    if signer["algorithm"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_SIGNATURE_ALGORITHM:
        _fail(f"{label} signer algorithm is invalid")
    public_key = _public_key(
        _decode_base64(signer["public_key_base64"], label=f"{label} signer public key", expected_bytes=32),
        label=f"{label} signer public key",
    )
    if _text(signer["key_id"], label=f"{label} signer key ID", pattern=_KEY_ID_RE) != _key_id(public_key):
        _fail(f"{label} signer key ID does not match public key")
    return public_key


def _signature(value: object, *, label: str) -> bytes:
    signature = _exact_mapping(value, fields=_SIGNATURE_FIELDS, label=f"{label} signature")
    if signature["algorithm"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_SIGNATURE_ALGORITHM:
        _fail(f"{label} signature algorithm is invalid")
    return _decode_base64(signature["signature_base64"], label=f"{label} signature", expected_bytes=64)


def _signer_from_private(value: object, *, label: str) -> tuple[object, bytes, dict[str, str]]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover
        raise PhysicalWalChunkedBaseBackupHandoffReceiptError(f"{label} signing unavailable") from exc
    if not isinstance(value, Ed25519PrivateKey):
        _fail(f"{label} signer is invalid")
    public_key = value.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    public_key = _public_key(public_key, label=f"{label} signer public key")
    return value, public_key, {
        "algorithm": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_SIGNATURE_ALGORITHM,
        "public_key_base64": base64.b64encode(public_key).decode("ascii"),
        "key_id": _key_id(public_key),
    }


def _sign(unsigned: Mapping[str, Any], *, signer: object, label: str) -> dict[str, str]:
    private, _public, _mapping = _signer_from_private(signer, label=label)
    try:
        signature = private.sign(_DOMAIN + _canonical(dict(unsigned), label=label))
    except ValueError:
        _fail(f"{label} signer failed")
    if not isinstance(signature, bytes) or len(signature) != 64:
        _fail(f"{label} signer produced invalid signature")
    return {
        "algorithm": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_SIGNATURE_ALGORITHM,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def _binding_payload(manifest: VerifiedPhysicalWalChunkedBaseBackupManifest) -> tuple[str, str, str]:
    binding = manifest.finalization_permit.session.binding
    payload = {
        "schema": "gold-trade-physical-wal-chunked-base-backup-handoff-binding-v2",
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "object_storage_namespace": binding.object_storage_namespace,
        "route_commitment_sha256": binding.route_commitment_sha256,
        "four_role_binding_sha256": binding.four_role_binding_sha256,
        "destination_age_recipient": binding.destination_age_recipient,
        "writer_term": {
            "writer_holder_site": binding.writer_term.writer_holder_site,
            "writer_epoch": binding.writer_term.writer_epoch,
            "writer_lease_id": binding.writer_term.writer_lease_id,
            "witnessed_term_proof_sha256": binding.writer_term.witnessed_term_proof_sha256,
        },
        "transport_plane": binding.transport_plane,
        "direct_webapp_transport": binding.direct_webapp_transport,
    }
    return (
        hashlib.sha256(_canonical(payload, label="chunked base-backup handoff binding")).hexdigest(),
        binding.destination_age_recipient,
        hashlib.sha256(manifest.finalization_permit.session.canonical_session).hexdigest(),
    )


def _assert_lineage_matches_manifest(
    facts: _Facts,
    *,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
) -> None:
    binding = manifest.finalization_permit.session.binding
    if (
        binding.source_site == binding.destination_site
        or binding.writer_term.writer_holder_site != binding.source_site
        or facts.wal_segment_size_bytes not in _SUPPORTED_WAL_SEGMENT_SIZES
    ):
        _fail("chunked base-backup handoff receipt lineage route or WAL geometry is invalid")
    try:
        baseline_high, baseline_low = facts.baseline_wal_lsn.split("/", 1)
        chain_high, chain_low = facts.wal_chain_start_lsn.split("/", 1)
        end_high, end_low = facts.base_backup_end_lsn.split("/", 1)
        baseline = (int(baseline_high, 16) << 32) | int(baseline_low, 16)
        chain_start = (int(chain_high, 16) << 32) | int(chain_low, 16)
        backup_end = (int(end_high, 16) << 32) | int(end_low, 16)
    except ValueError:
        _fail("chunked base-backup handoff receipt lineage LSN is invalid")
    if (
        backup_end <= baseline
        or chain_start % facts.wal_segment_size_bytes
        or chain_start > baseline
        or baseline >= chain_start + facts.wal_segment_size_bytes
    ):
        _fail("chunked base-backup handoff receipt lineage WAL geometry is invalid")
    if (
        facts.snapshot_sha256 != manifest.total_plaintext_sha256
        or facts.snapshot_bytes != manifest.total_plaintext_bytes
    ):
        _fail("chunked base-backup handoff receipt snapshot does not match final manifest")
    payload: dict[str, Any] = {
        "schema": "gold-trade-physical-wal-chunked-base-backup-lineage-envelope-v2",
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "object_storage_namespace": binding.object_storage_namespace,
        "destination_age_recipient": binding.destination_age_recipient,
        "baseline_generation_id": facts.baseline_generation_id,
        "database_system_identifier": facts.database_system_identifier,
        "timeline_id": facts.timeline_id,
        "wal_segment_size_bytes": facts.wal_segment_size_bytes,
        "baseline_wal_lsn": facts.baseline_wal_lsn,
        "wal_chain_start_lsn": facts.wal_chain_start_lsn,
        "base_backup_end_lsn": facts.base_backup_end_lsn,
        "snapshot_sha256": facts.snapshot_sha256,
        "snapshot_bytes": facts.snapshot_bytes,
        "completion_attestation_sha256": facts.completion_attestation_sha256,
        "legacy_route_binding_sha256": facts.legacy_route_binding_sha256,
        "writer_epoch": binding.writer_term.writer_epoch,
        "writer_lease_id": binding.writer_term.writer_lease_id,
        "witness_transition_id": facts.witness_transition_id,
        "witnessed_term_proof_sha256": binding.writer_term.witnessed_term_proof_sha256,
        "v2_route_commitment_sha256": binding.route_commitment_sha256,
        "v2_four_role_binding_sha256": binding.four_role_binding_sha256,
    }
    if hashlib.sha256(_canonical(payload, label="chunked base-backup handoff lineage")).hexdigest() != facts.lineage_sha256:
        _fail("chunked base-backup handoff receipt lineage hash is invalid")


def build_physical_wal_chunked_base_backup_handoff_receipt(
    *,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    lineage_envelope: VerifiedPhysicalWalChunkedBaseBackupLineageEnvelope,
    receipt_id: str,
    receipt_nonce: str,
    issued_at: datetime,
    expires_at: datetime,
    witness_signer: object,
) -> dict[str, Any]:
    """Build a fresh Witness receipt over one verified v2 manifest."""

    verified_manifest = require_verified_physical_wal_chunked_base_backup_manifest(manifest, now=issued_at)
    lineage = _lineage_payload(
        lineage_envelope,
        transfer_binding=verified_manifest.finalization_permit.session.binding,
        now=issued_at,
    )
    if (
        verified_manifest.total_plaintext_sha256 != lineage["snapshot_sha256"]
        or verified_manifest.total_plaintext_bytes != lineage["snapshot_bytes"]
    ):
        _fail("chunked base-backup handoff receipt manifest snapshot does not match verified lineage")
    receipt = _id(receipt_id, label="chunked base-backup handoff receipt ID")
    nonce = _nonce(receipt_nonce, label="chunked base-backup handoff receipt nonce")
    if receipt == nonce or receipt in {verified_manifest.manifest_id, verified_manifest.finalization_permit.finalization_permit_id}:
        _fail("chunked base-backup handoff receipt identity reuses manifest value")
    issued_text = _timestamp_text(issued_at, label="chunked base-backup handoff receipt issued_at")
    expires_text = _timestamp_text(expires_at, label="chunked base-backup handoff receipt expires_at")
    issued = _timestamp(issued_text, label="chunked base-backup handoff receipt issued_at")
    expires = _timestamp(expires_text, label="chunked base-backup handoff receipt expires_at")
    if expires <= issued or expires - issued > timedelta(seconds=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_AGE_SECONDS):
        _fail("chunked base-backup handoff receipt lifetime is invalid")
    _private, public, signer = _signer_from_private(witness_signer, label="chunked base-backup handoff receipt")
    if public != verified_manifest.witness_public_key:
        _fail("chunked base-backup handoff receipt signer does not match manifest Witness")
    binding_sha, recipient, session_sha = _binding_payload(verified_manifest)
    unsigned = {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_SCHEMA,
        "version": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_VERSION,
        "kind": "physical_wal_chunked_base_backup_receiver_handoff_receipt",
        "binding_sha256": binding_sha,
        "destination_age_recipient": recipient,
        "session_sha256": session_sha,
        "manifest_id": verified_manifest.manifest_id,
        "manifest_sha256": hashlib.sha256(verified_manifest.canonical_manifest).hexdigest(),
        "finalization_permit_id": verified_manifest.finalization_permit.finalization_permit_id,
        "finalization_permit_sha256": hashlib.sha256(
            verified_manifest.finalization_permit.canonical_finalization_permit
        ).hexdigest(),
        "committed_chunk_set_sha256": verified_manifest.finalization_permit.committed_chunk_set_sha256,
        **lineage,
        "receipt_id": receipt,
        "receipt_nonce": nonce,
        "issued_at": issued_text,
        "expires_at": expires_text,
        "witness_signer": signer,
    }
    return {**unsigned, "witness_signature": _sign(unsigned, signer=witness_signer, label="chunked base-backup handoff receipt")}


def _parse(value: object, *, expected_witness_public_key: bytes | None = None) -> _Facts:
    if isinstance(value, Mapping):
        try:
            payload = dict(value)
            raw = _canonical(payload, label="chunked base-backup handoff receipt")
        except (TypeError, ValueError):
            _fail("chunked base-backup handoff receipt is invalid")
    elif isinstance(value, bytes):
        raw = value
        if not raw or len(raw) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_BYTES:
            _fail("chunked base-backup handoff receipt byte size is invalid")
        try:
            payload = json.loads(raw.decode("ascii", "strict"), object_pairs_hook=_strict_object, parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            _fail("chunked base-backup handoff receipt is invalid JSON")
        if not isinstance(payload, dict) or _canonical(payload, label="chunked base-backup handoff receipt") != raw:
            _fail("chunked base-backup handoff receipt is not canonical JSON")
    else:
        _fail("chunked base-backup handoff receipt is invalid")
    if not raw or len(raw) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_BYTES:
        _fail("chunked base-backup handoff receipt byte size is invalid")
    receipt = _exact_mapping(payload, fields=_RECEIPT_FIELDS, label="chunked base-backup handoff receipt")
    if (
        receipt["schema"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_SCHEMA
        or receipt["version"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_VERSION
        or receipt["kind"] != "physical_wal_chunked_base_backup_receiver_handoff_receipt"
    ):
        _fail("chunked base-backup handoff receipt schema is invalid")
    witness = _signer(receipt["witness_signer"], label="chunked base-backup handoff receipt")
    if expected_witness_public_key is not None and witness != expected_witness_public_key:
        _fail("chunked base-backup handoff receipt signer does not match expected Witness key")
    signature = _signature(receipt["witness_signature"], label="chunked base-backup handoff receipt")
    unsigned = {key: item for key, item in receipt.items() if key != "witness_signature"}
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(witness).verify(signature, _DOMAIN + _canonical(unsigned, label="chunked base-backup handoff receipt"))
    except (InvalidSignature, ValueError):
        _fail("chunked base-backup handoff receipt signature is invalid")
    issued = _timestamp(receipt["issued_at"], label="chunked base-backup handoff receipt issued_at")
    expires = _timestamp(receipt["expires_at"], label="chunked base-backup handoff receipt expires_at")
    if expires <= issued or expires - issued > timedelta(seconds=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_AGE_SECONDS):
        _fail("chunked base-backup handoff receipt lifetime is invalid")
    facts = _Facts(
        raw=raw,
        binding_sha256=_sha256(receipt["binding_sha256"], label="chunked base-backup handoff receipt binding hash"),
        destination_age_recipient=receipt["destination_age_recipient"],
        session_sha256=_sha256(receipt["session_sha256"], label="chunked base-backup handoff receipt session hash"),
        manifest_id=_id(receipt["manifest_id"], label="chunked base-backup handoff receipt manifest ID"),
        manifest_sha256=_sha256(receipt["manifest_sha256"], label="chunked base-backup handoff receipt manifest hash"),
        finalization_permit_id=_id(receipt["finalization_permit_id"], label="chunked base-backup handoff receipt finalization ID"),
        finalization_permit_sha256=_sha256(receipt["finalization_permit_sha256"], label="chunked base-backup handoff receipt finalization hash"),
        committed_chunk_set_sha256=_sha256(receipt["committed_chunk_set_sha256"], label="chunked base-backup handoff receipt committed set hash"),
        lineage_sha256=_sha256(receipt["lineage_sha256"], label="chunked base-backup handoff receipt lineage hash"),
        snapshot_sha256=_sha256(receipt["snapshot_sha256"], label="chunked base-backup handoff receipt snapshot hash"),
        snapshot_bytes=_positive(receipt["snapshot_bytes"], label="chunked base-backup handoff receipt snapshot bytes", maximum=2**63 - 1),
        baseline_generation_id=_matching(receipt["baseline_generation_id"], label="chunked base-backup handoff receipt baseline generation", pattern=_GENERATION_RE),
        database_system_identifier=_matching(receipt["database_system_identifier"], label="chunked base-backup handoff receipt database system identifier", pattern=_SYSTEM_IDENTIFIER_RE),
        timeline_id=_positive(receipt["timeline_id"], label="chunked base-backup handoff receipt timeline ID", maximum=0xFFFFFFFF),
        wal_segment_size_bytes=_positive(receipt["wal_segment_size_bytes"], label="chunked base-backup handoff receipt WAL segment size", maximum=2**31 - 1),
        baseline_wal_lsn=_matching(receipt["baseline_wal_lsn"], label="chunked base-backup handoff receipt baseline LSN", pattern=_LSN_RE),
        wal_chain_start_lsn=_matching(receipt["wal_chain_start_lsn"], label="chunked base-backup handoff receipt chain start LSN", pattern=_LSN_RE),
        base_backup_end_lsn=_matching(receipt["base_backup_end_lsn"], label="chunked base-backup handoff receipt end LSN", pattern=_LSN_RE),
        completion_attestation_sha256=_sha256(receipt["completion_attestation_sha256"], label="chunked base-backup handoff receipt completion attestation"),
        legacy_route_binding_sha256=_sha256(receipt["legacy_route_binding_sha256"], label="chunked base-backup handoff receipt legacy route hash"),
        witness_transition_id=_matching(receipt["witness_transition_id"], label="chunked base-backup handoff receipt Witness transition", pattern=_TRANSITION_RE),
        receipt_id=_id(receipt["receipt_id"], label="chunked base-backup handoff receipt ID"),
        receipt_nonce=_nonce(receipt["receipt_nonce"], label="chunked base-backup handoff receipt nonce"),
        issued_at=issued,
        expires_at=expires,
        witness_public_key=witness,
    )
    if len({facts.manifest_id, facts.finalization_permit_id, facts.receipt_id, facts.receipt_nonce}) != 4:
        _fail("chunked base-backup handoff receipt identity reuses a bound value")
    return facts


def canonical_physical_wal_chunked_base_backup_handoff_receipt_bytes(value: Mapping[str, Any] | bytes) -> bytes:
    return _parse(value).raw


def _assert_live(facts: _Facts, *, now: datetime) -> None:
    current = _utc(now, label="chunked base-backup handoff receipt verification clock")
    if facts.issued_at > current + timedelta(seconds=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_HANDOFF_RECEIPT_FUTURE_SKEW_SECONDS):
        _fail("chunked base-backup handoff receipt is from the future")
    if current > facts.expires_at:
        _fail("chunked base-backup handoff receipt is expired")


def _observed(value: object, *, label: str, validator) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Collection):
        _fail(f"{label} replay set is invalid")
    return frozenset(validator(item, label=f"{label} value") for item in value)


def verify_physical_wal_chunked_base_backup_handoff_receipt(
    *,
    handoff_receipt: Mapping[str, Any] | bytes,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    expected_witness_public_key: bytes,
    now: datetime,
    consumed_receipt_ids: Collection[str] = (),
    consumed_receipt_nonces: Collection[str] = (),
) -> VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt:
    """Verify a fresh receiver receipt against its exact pinned manifest."""

    verified_manifest = require_verified_physical_wal_chunked_base_backup_manifest(manifest, now=now)
    witness = _public_key(expected_witness_public_key, label="expected Witness public key")
    if witness != verified_manifest.witness_public_key:
        _fail("expected Witness key does not match manifest")
    facts = _parse(handoff_receipt, expected_witness_public_key=witness)
    _assert_live(facts, now=now)
    binding_sha, recipient, session_sha = _binding_payload(verified_manifest)
    if (
        facts.binding_sha256 != binding_sha
        or facts.destination_age_recipient != recipient
        or facts.session_sha256 != session_sha
        or facts.manifest_id != verified_manifest.manifest_id
        or facts.manifest_sha256 != hashlib.sha256(verified_manifest.canonical_manifest).hexdigest()
        or facts.finalization_permit_id != verified_manifest.finalization_permit.finalization_permit_id
        or facts.finalization_permit_sha256 != hashlib.sha256(verified_manifest.finalization_permit.canonical_finalization_permit).hexdigest()
        or facts.committed_chunk_set_sha256 != verified_manifest.finalization_permit.committed_chunk_set_sha256
    ):
        _fail("chunked base-backup handoff receipt does not pin exact manifest and finalization")
    _assert_lineage_matches_manifest(facts, manifest=verified_manifest)
    if facts.receipt_id in _observed(consumed_receipt_ids, label="chunked base-backup handoff receipt ID", validator=_id):
        _fail("chunked base-backup handoff receipt ID was replayed")
    if facts.receipt_nonce in _observed(consumed_receipt_nonces, label="chunked base-backup handoff receipt nonce", validator=_nonce):
        _fail("chunked base-backup handoff receipt nonce was replayed")
    result = VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt(
        canonical_receipt=facts.raw,
        binding_sha256=facts.binding_sha256,
        destination_age_recipient=facts.destination_age_recipient,
        session_sha256=facts.session_sha256,
        manifest_id=facts.manifest_id,
        manifest_sha256=facts.manifest_sha256,
        finalization_permit_id=facts.finalization_permit_id,
        finalization_permit_sha256=facts.finalization_permit_sha256,
        committed_chunk_set_sha256=facts.committed_chunk_set_sha256,
        lineage_sha256=facts.lineage_sha256,
        snapshot_sha256=facts.snapshot_sha256,
        snapshot_bytes=facts.snapshot_bytes,
        baseline_generation_id=facts.baseline_generation_id,
        database_system_identifier=facts.database_system_identifier,
        timeline_id=facts.timeline_id,
        wal_segment_size_bytes=facts.wal_segment_size_bytes,
        baseline_wal_lsn=facts.baseline_wal_lsn,
        wal_chain_start_lsn=facts.wal_chain_start_lsn,
        base_backup_end_lsn=facts.base_backup_end_lsn,
        completion_attestation_sha256=facts.completion_attestation_sha256,
        legacy_route_binding_sha256=facts.legacy_route_binding_sha256,
        witness_transition_id=facts.witness_transition_id,
        receipt_id=facts.receipt_id,
        receipt_nonce=facts.receipt_nonce,
        issued_at=facts.issued_at,
        expires_at=facts.expires_at,
        witness_public_key=witness,
    )
    object.__setattr__(result, "_capability", _VERIFIED_CAPABILITY)
    return result


def require_verified_physical_wal_chunked_base_backup_handoff_receipt(
    value: object,
    *,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt:
    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt
        or value._capability is not _VERIFIED_CAPABILITY
    ):
        _fail("verified chunked base-backup handoff receipt capability is required")
    verified_manifest = require_verified_physical_wal_chunked_base_backup_manifest(manifest, now=now)
    facts = _parse(value.canonical_receipt, expected_witness_public_key=verified_manifest.witness_public_key)
    _assert_live(facts, now=now)
    binding_sha, recipient, session_sha = _binding_payload(verified_manifest)
    if (
        facts.binding_sha256 != binding_sha
        or facts.destination_age_recipient != recipient
        or facts.session_sha256 != session_sha
        or facts.manifest_id != value.manifest_id
        or facts.manifest_sha256 != value.manifest_sha256
        or facts.finalization_permit_id != value.finalization_permit_id
        or facts.finalization_permit_sha256 != value.finalization_permit_sha256
        or facts.committed_chunk_set_sha256 != value.committed_chunk_set_sha256
        or facts.lineage_sha256 != value.lineage_sha256
        or facts.snapshot_sha256 != value.snapshot_sha256
        or facts.snapshot_bytes != value.snapshot_bytes
        or facts.baseline_generation_id != value.baseline_generation_id
        or facts.database_system_identifier != value.database_system_identifier
        or facts.timeline_id != value.timeline_id
        or facts.wal_segment_size_bytes != value.wal_segment_size_bytes
        or facts.baseline_wal_lsn != value.baseline_wal_lsn
        or facts.wal_chain_start_lsn != value.wal_chain_start_lsn
        or facts.base_backup_end_lsn != value.base_backup_end_lsn
        or facts.completion_attestation_sha256 != value.completion_attestation_sha256
        or facts.legacy_route_binding_sha256 != value.legacy_route_binding_sha256
        or facts.witness_transition_id != value.witness_transition_id
        or facts.receipt_id != value.receipt_id
        or facts.receipt_nonce != value.receipt_nonce
        or facts.issued_at != value.issued_at
        or facts.expires_at != value.expires_at
        or facts.witness_public_key != value.witness_public_key
    ):
        _fail("verified chunked base-backup handoff receipt was tampered")
    _assert_lineage_matches_manifest(facts, manifest=verified_manifest)
    return value
