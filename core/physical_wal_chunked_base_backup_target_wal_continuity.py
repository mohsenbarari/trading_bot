"""Pure V2-only evidence for exact WAL continuity from a chunked base backup.

This module deliberately proves only a signed, immutable WAL selector chain
from a verified chunked-base-backup handoff's ``base_backup_end_lsn`` to one
target LSN.  It has no Object Storage, filesystem, database, PostgreSQL,
network, provider, driver, restore, promotion, or writer side effect.  A
future materializer must still fetch each exact object version, decrypt it,
and recompute the signed hashes before it can use this evidence.

No historical physical-WAL object/bundle contract is imported here.  Raw
caller selector lists never become continuity authority: a selector set must
be canonical, Witness-signed, route/lineage/base-manifest pinned, and then
turned into an opaque nonserializable capability.
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
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_chunked_base_backup_handoff_receipt import (
    PhysicalWalChunkedBaseBackupHandoffReceiptError,
    VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    require_verified_physical_wal_chunked_base_backup_handoff_receipt,
)
from core.physical_wal_chunked_base_backup_manifest import (
    PhysicalWalChunkedBaseBackupManifestError,
    VerifiedPhysicalWalChunkedBaseBackupManifest,
    require_verified_physical_wal_chunked_base_backup_manifest,
)
from core.physical_wal_chunked_base_backup_transfer import (
    PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SIGNATURE_ALGORITHM,
    PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSPORT_PLANE,
    PhysicalWalChunkedBaseBackupBinding,
)


__all__ = (
    "MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_AGE_SECONDS",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_SCHEMA",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCHEMA",
    "PhysicalWalChunkedBaseBackupTargetWalContinuityError",
    "PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector",
    "PhysicalWalChunkedBaseBackupTargetWalContinuityScope",
    "VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity",
    "VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt",
    "build_physical_wal_chunked_base_backup_target_wal_continuity_receipt",
    "canonical_physical_wal_chunked_base_backup_target_wal_continuity_receipt_bytes",
    "mint_physical_wal_chunked_base_backup_target_wal_continuity",
    "require_verified_physical_wal_chunked_base_backup_target_wal_continuity",
    "require_verified_physical_wal_chunked_base_backup_target_wal_continuity_receipt",
    "verify_physical_wal_chunked_base_backup_target_wal_continuity_receipt",
)


PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-target-wal-continuity-v2"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-target-wal-continuity-receipt-v2"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_VERSION = 2
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_BYTES = 16 * 1024 * 1024
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_AGE_SECONDS = 120
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_FUTURE_SKEW_SECONDS = 5
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_SELECTORS = 100_000
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_OBJECT_BYTES = 64 * 1024 * 1024

_DOMAIN = b"gold-trade-physical-wal-chunked-base-backup-target-wal-continuity-receipt-v2\x00"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$", re.ASCII)
_MUTABLE_ALIAS_COMPONENTS = frozenset({"alias", "current", "head", "latest", "pointer"})
_MUTABLE_VERSION_IDS = frozenset({"null", "none", "latest", "current", "head"})
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_SELECTOR_FIELDS = frozenset(
    {
        "index",
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "plaintext_sha256",
        "plaintext_bytes",
        "timeline_id",
        "start_lsn",
        "end_lsn",
        "age_recipient",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "binding",
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
        "target_lsn",
        "wal_object_selectors",
        "receipt_id",
        "receipt_nonce",
        "issued_at",
        "expires_at",
        "witness_signer",
        "witness_signature",
    }
)
_RECEIPT_CAPABILITY = object()
_CONTINUITY_CAPABILITY = object()


class PhysicalWalChunkedBaseBackupTargetWalContinuityError(ValueError):
    """The exact signed v2 WAL coverage cannot be safely admitted."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector:
    """One immutable encrypted WAL object and its precise logical LSN interval."""

    index: int
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    plaintext_sha256: str
    plaintext_bytes: int
    timeline_id: int
    start_lsn: str
    end_lsn: str
    age_recipient: str


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupTargetWalContinuityScope:
    """Expected V2 route, base lineage, and exact target LSN for one proof."""

    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    lineage_sha256: str
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    target_lsn: str


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt:
    """Opaque signed coverage receipt; never materialization or writer authority."""

    canonical_receipt: bytes
    receipt_id: str
    receipt_nonce: str
    issued_at: datetime
    expires_at: datetime
    receipt_sha256: str
    selector_set_sha256: str
    target_lsn: str
    selectors: tuple[PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector, ...]
    manifest_id: str
    manifest_sha256: str
    lineage_sha256: str
    scope_sha256: str
    witness_public_key: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity:
    """Opaque V2-only proof of exact WAL coverage at a target LSN."""

    schema: str
    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    canonical_manifest_sha256: str
    manifest_id: str
    handoff_receipt_id: str
    handoff_receipt_nonce: str
    handoff_expires_at: datetime
    continuity_receipt_id: str
    continuity_receipt_nonce: str
    continuity_receipt_sha256: str
    continuity_receipt_expires_at: datetime
    lineage_sha256: str
    scope_sha256: str
    base_backup_end_lsn: str
    target_lsn: str
    selector_set_sha256: str
    wal_object_selectors: tuple[PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector, ...]
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _ReceiptFacts:
    raw: bytes
    binding: Mapping[str, Any]
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
    target_lsn: str
    selectors: tuple[PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector, ...]
    receipt_id: str
    receipt_nonce: str
    issued_at: datetime
    expires_at: datetime
    witness_public_key: bytes


@dataclass(frozen=True)
class _EvidenceFacts:
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt
    scope: PhysicalWalChunkedBaseBackupTargetWalContinuityScope
    scope_sha256: str
    receipt: _ReceiptFacts
    selector_set_sha256: str


def _fail(code: str) -> None:
    raise PhysicalWalChunkedBaseBackupTargetWalContinuityError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalChunkedBaseBackupTargetWalContinuityError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_NONCANONICAL")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_NONCANONICAL")


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _id(value: object, *, code: str) -> str:
    return _text(value, pattern=_ID_RE, code=code)


def _nonce(value: object, *, code: str) -> str:
    return _text(value, pattern=_NONCE_RE, code=code)


def _sha256(value: object, *, code: str) -> str:
    digest = _text(value, pattern=SHA256_RE, code=code)
    if digest == "0" * 64:
        _fail(code)
    return digest


def _positive(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or not value or value != value.strip():
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None:
        _fail(code)
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        _fail(code)
    return normalized


def _timestamp_text(value: object, *, code: str) -> str:
    return _utc(value, code=code).isoformat()


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    text = _text(value, pattern=_LSN_RE, code=code)
    high, low = text.split("/", 1)
    try:
        return text, (int(high, 16) << 32) | int(low, 16)
    except ValueError:  # pragma: no cover - regex makes this defensive.
        _fail(code)


def _public_key(value: object, *, code: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        _fail(code)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError):
        _fail(code)
    return value


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _decode_base64(value: object, *, expected_bytes: int, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        decoded = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if len(decoded) != expected_bytes:
        _fail(code)
    return decoded


def _signer(value: object, *, code: str) -> bytes:
    signer = _exact_mapping(value, fields=_SIGNER_FIELDS, code=code)
    if signer["algorithm"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SIGNATURE_ALGORITHM:
        _fail(code)
    public_key = _public_key(
        _decode_base64(signer["public_key_base64"], expected_bytes=32, code=code),
        code=code,
    )
    if _text(signer["key_id"], pattern=_KEY_ID_RE, code=code) != _key_id(public_key):
        _fail(code)
    return public_key


def _signature(value: object, *, code: str) -> bytes:
    signature = _exact_mapping(value, fields=_SIGNATURE_FIELDS, code=code)
    if signature["algorithm"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SIGNATURE_ALGORITHM:
        _fail(code)
    return _decode_base64(signature["signature_base64"], expected_bytes=64, code=code)


def _signer_from_private(value: object, *, code: str) -> tuple[object, bytes, dict[str, str]]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover - environment dependency.
        raise PhysicalWalChunkedBaseBackupTargetWalContinuityError(code) from exc
    if not isinstance(value, Ed25519PrivateKey):
        _fail(code)
    try:
        public_key = value.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except ValueError:
        _fail(code)
    public_key = _public_key(public_key, code=code)
    return value, public_key, {
        "algorithm": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SIGNATURE_ALGORITHM,
        "public_key_base64": base64.b64encode(public_key).decode("ascii"),
        "key_id": _key_id(public_key),
    }


def _verify_signature(
    payload: Mapping[str, Any],
    *,
    witness_public_key: bytes,
    code: str,
) -> None:
    signature = _signature(payload.get("witness_signature"), code=code)
    unsigned = {key: value for key, value in payload.items() if key != "witness_signature"}
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(witness_public_key).verify(signature, _DOMAIN + _canonical(unsigned, code=code))
    except (InvalidSignature, ValueError):
        _fail(code)


def _sign(unsigned: Mapping[str, Any], *, signer: object, code: str) -> dict[str, str]:
    private, _public, _mapping = _signer_from_private(signer, code=code)
    try:
        signature = private.sign(_DOMAIN + _canonical(dict(unsigned), code=code))
    except ValueError:
        _fail(code)
    if not isinstance(signature, bytes) or len(signature) != 64:
        _fail(code)
    return {
        "algorithm": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SIGNATURE_ALGORITHM,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def _binding_mapping(binding: PhysicalWalChunkedBaseBackupBinding) -> dict[str, object]:
    return {
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


def _handoff_binding_sha256(binding: PhysicalWalChunkedBaseBackupBinding) -> str:
    try:
        return hashlib.sha256(
            canonical_json_bytes(
                {
                    "schema": "gold-trade-physical-wal-chunked-base-backup-handoff-binding-v2",
                    **_binding_mapping(binding),
                }
            )
        ).hexdigest()
    except (AttributeError, TypeError, ValueError):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_BINDING_INVALID")


def _require_binding(value: object) -> PhysicalWalChunkedBaseBackupBinding:
    if type(value) is not PhysicalWalChunkedBaseBackupBinding:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_BINDING_INVALID")
    if (
        value.source_site not in {"webapp_fi", "webapp_ir"}
        or value.destination_site not in {"webapp_fi", "webapp_ir"}
        or value.source_site == value.destination_site
        or CAMPAIGN_ID_RE.fullmatch(value.campaign_id) is None
        or RELEASE_SHA_RE.fullmatch(value.release_sha) is None
        or value.object_storage_namespace not in {"physical-wal", "physical-failback"}
        or SHA256_RE.fullmatch(value.route_commitment_sha256) is None
        or SHA256_RE.fullmatch(value.four_role_binding_sha256) is None
        or AGE_RECIPIENT_RE.fullmatch(value.destination_age_recipient) is None
        or value.transport_plane != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSPORT_PLANE
        or value.direct_webapp_transport != "forbidden"
        or value.writer_term.writer_holder_site != value.source_site
    ):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_BINDING_INVALID")
    return value


def _selector_mapping(value: PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector) -> dict[str, object]:
    return {
        "index": value.index,
        "object_key": value.object_key,
        "version_id": value.version_id,
        "ciphertext_sha256": value.ciphertext_sha256,
        "ciphertext_bytes": value.ciphertext_bytes,
        "plaintext_sha256": value.plaintext_sha256,
        "plaintext_bytes": value.plaintext_bytes,
        "timeline_id": value.timeline_id,
        "start_lsn": value.start_lsn,
        "end_lsn": value.end_lsn,
        "age_recipient": value.age_recipient,
    }


def _object_key(value: object, *, code: str) -> str:
    key = _text(value, pattern=OBJECT_KEY_RE, code=code)
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
        _fail(code)
    return key


def _selector_object_key_prefix(
    *,
    binding: PhysicalWalChunkedBaseBackupBinding,
    lineage_sha256: str,
) -> str:
    """Return the one immutable V2 WAL namespace for a verified handoff."""

    return (
        f"{binding.object_storage_namespace}/{binding.campaign_id}/"
        f"{binding.release_sha}/wal-v2/{lineage_sha256}/"
    )


def _version_id(value: object, *, code: str) -> str:
    version = _text(value, pattern=VERSION_ID_RE, code=code)
    if version.casefold() in _MUTABLE_VERSION_IDS:
        _fail(code)
    return version


def _selector_from_mapping(
    value: object,
    *,
    expected_index: int,
    expected_timeline_id: int,
    expected_recipient: str,
    expected_object_key_prefix: str,
    code: str,
) -> PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector:
    selector = _exact_mapping(value, fields=_SELECTOR_FIELDS, code=code)
    index = selector["index"]
    if type(index) is not int or index != expected_index:
        _fail(code)
    timeline_id = _positive(selector["timeline_id"], maximum=0xFFFFFFFF, code=code)
    if timeline_id != expected_timeline_id:
        _fail(code)
    start_lsn, start_value = _lsn(selector["start_lsn"], code=code)
    end_lsn, end_value = _lsn(selector["end_lsn"], code=code)
    if end_value <= start_value:
        _fail(code)
    recipient = _text(selector["age_recipient"], pattern=AGE_RECIPIENT_RE, code=code)
    if recipient != expected_recipient:
        _fail(code)
    object_key = _object_key(selector["object_key"], code=code)
    if not object_key.startswith(expected_object_key_prefix) or object_key == expected_object_key_prefix:
        _fail(code)
    return PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector(
        index=index,
        object_key=object_key,
        version_id=_version_id(selector["version_id"], code=code),
        ciphertext_sha256=_sha256(selector["ciphertext_sha256"], code=code),
        ciphertext_bytes=_positive(
            selector["ciphertext_bytes"],
            maximum=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_OBJECT_BYTES,
            code=code,
        ),
        plaintext_sha256=_sha256(selector["plaintext_sha256"], code=code),
        plaintext_bytes=_positive(
            selector["plaintext_bytes"],
            maximum=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_OBJECT_BYTES,
            code=code,
        ),
        timeline_id=timeline_id,
        start_lsn=start_lsn,
        end_lsn=end_lsn,
        age_recipient=recipient,
    )


def _selectors(
    value: object,
    *,
    base_backup_end_lsn: str,
    target_lsn: str,
    timeline_id: int,
    recipient: str,
    object_key_prefix: str,
    code: str,
) -> tuple[PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _fail(code)
    if len(value) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_SELECTORS:
        _fail(code)
    _base_text, base_value = _lsn(base_backup_end_lsn, code=code)
    _target_text, target_value = _lsn(target_lsn, code=code)
    if target_value < base_value:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_TARGET_BEFORE_BASE_END")
    if target_value == base_value:
        if len(value) != 0:
            _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SELECTOR_SET_INVALID")
        return ()
    if not value:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SELECTOR_SET_INVALID")
    selectors: list[PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector] = []
    expected_start = base_value
    seen_pairs: set[tuple[str, str]] = set()
    seen_keys: set[str] = set()
    for index, candidate in enumerate(value):
        selector = _selector_from_mapping(
            candidate,
            expected_index=index,
            expected_timeline_id=timeline_id,
            expected_recipient=recipient,
            expected_object_key_prefix=object_key_prefix,
            code=code,
        )
        _start_text, start_value = _lsn(selector.start_lsn, code=code)
        _end_text, end_value = _lsn(selector.end_lsn, code=code)
        pair = (selector.object_key, selector.version_id)
        if start_value != expected_start or pair in seen_pairs or selector.object_key in seen_keys:
            _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SELECTOR_SET_INVALID")
        seen_pairs.add(pair)
        seen_keys.add(selector.object_key)
        expected_start = end_value
        selectors.append(selector)
    if expected_start != target_value:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SELECTOR_SET_INVALID")
    return tuple(selectors)


def _selector_set_sha256(
    selectors: tuple[PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector, ...],
    *,
    binding_sha256: str,
    manifest_sha256: str,
    lineage_sha256: str,
    base_backup_end_lsn: str,
    target_lsn: str,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCHEMA,
                "binding_sha256": binding_sha256,
                "manifest_sha256": manifest_sha256,
                "lineage_sha256": lineage_sha256,
                "base_backup_end_lsn": base_backup_end_lsn,
                "target_lsn": target_lsn,
                "selectors": [_selector_mapping(item) for item in selectors],
            },
            code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SELECTOR_SET_INVALID",
        )
    ).hexdigest()


def _scope_sha256(scope: PhysicalWalChunkedBaseBackupTargetWalContinuityScope) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCHEMA,
                "binding": _binding_mapping(scope.transfer_binding),
                "lineage_sha256": scope.lineage_sha256,
                "baseline_generation_id": scope.baseline_generation_id,
                "database_system_identifier": scope.database_system_identifier,
                "timeline_id": scope.timeline_id,
                "wal_segment_size_bytes": scope.wal_segment_size_bytes,
                "baseline_wal_lsn": scope.baseline_wal_lsn,
                "wal_chain_start_lsn": scope.wal_chain_start_lsn,
                "base_backup_end_lsn": scope.base_backup_end_lsn,
                "target_lsn": scope.target_lsn,
            },
            code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_INVALID",
        )
    ).hexdigest()


def _require_scope(
    value: object,
    *,
    binding: PhysicalWalChunkedBaseBackupBinding,
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
) -> tuple[PhysicalWalChunkedBaseBackupTargetWalContinuityScope, str]:
    if type(value) is not PhysicalWalChunkedBaseBackupTargetWalContinuityScope:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_REQUIRED")
    scope = value
    expected = _require_binding(scope.transfer_binding)
    if (
        expected.source_site != binding.source_site
        or expected.destination_site != binding.destination_site
        or expected.campaign_id != binding.campaign_id
        or expected.release_sha != binding.release_sha
    ):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_ROUTE_MISMATCH")
    if expected.destination_age_recipient != binding.destination_age_recipient:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_RECIPIENT_MISMATCH")
    if expected.writer_term != binding.writer_term:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_TERM_MISMATCH")
    if expected != binding:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_BINDING_MISMATCH")
    if _sha256(scope.lineage_sha256, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_LINEAGE_INVALID") != handoff.lineage_sha256:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_LINEAGE_MISMATCH")
    if (
        type(scope.baseline_generation_id) is not str
        or not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", scope.baseline_generation_id, re.ASCII)
        or not re.fullmatch(r"^[1-9][0-9]{0,19}$", scope.database_system_identifier, re.ASCII)
        or type(scope.timeline_id) is not int
        or not 1 <= scope.timeline_id <= 0xFFFFFFFF
        or type(scope.wal_segment_size_bytes) is not int
        or scope.wal_segment_size_bytes != 16 * 1024 * 1024
    ):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_INVALID")
    try:
        _lsn(scope.baseline_wal_lsn, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_INVALID")
        _lsn(scope.wal_chain_start_lsn, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_INVALID")
        _base, base_value = _lsn(scope.base_backup_end_lsn, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_INVALID")
        _target, target_value = _lsn(scope.target_lsn, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_INVALID")
    except PhysicalWalChunkedBaseBackupTargetWalContinuityError:
        raise
    if target_value < base_value:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_TARGET_BEFORE_BASE_END")
    if (
        scope.baseline_generation_id != handoff.baseline_generation_id
        or scope.database_system_identifier != handoff.database_system_identifier
        or scope.timeline_id != handoff.timeline_id
        or scope.wal_segment_size_bytes != handoff.wal_segment_size_bytes
        or scope.baseline_wal_lsn != handoff.baseline_wal_lsn
        or scope.wal_chain_start_lsn != handoff.wal_chain_start_lsn
        or scope.base_backup_end_lsn != handoff.base_backup_end_lsn
    ):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCOPE_WAL_GEOMETRY_MISMATCH")
    return scope, _scope_sha256(scope)


def _parse_receipt(
    value: object,
    *,
    expected_witness_public_key: bytes,
    expected_timeline_id: int,
    expected_recipient: str,
    expected_object_key_prefix: str,
) -> _ReceiptFacts:
    if isinstance(value, Mapping):
        payload = dict(value)
        raw = _canonical(payload, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID")
    elif isinstance(value, bytes):
        raw = value
        if not raw or len(raw) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_BYTES:
            _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID")
        try:
            payload = json.loads(
                raw.decode("ascii", "strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_NONCANONICAL")
        if not isinstance(payload, dict) or _canonical(payload, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_NONCANONICAL") != raw:
            _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_NONCANONICAL")
    else:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID")
    if not raw or len(raw) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_BYTES:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID")
    receipt = _exact_mapping(
        payload,
        fields=_RECEIPT_FIELDS,
        code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID",
    )
    if (
        receipt["schema"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_SCHEMA
        or receipt["version"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_VERSION
        or receipt["kind"] != "physical_wal_chunked_base_backup_target_wal_continuity_receipt"
    ):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_SCHEMA_INVALID")
    witness = _signer(receipt["witness_signer"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_SIGNATURE_INVALID")
    if witness != _public_key(expected_witness_public_key, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_SIGNATURE_INVALID"):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_SIGNATURE_INVALID")
    _verify_signature(receipt, witness_public_key=witness, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_SIGNATURE_INVALID")
    issued = _timestamp(receipt["issued_at"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID")
    expires = _timestamp(receipt["expires_at"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID")
    if expires <= issued or expires - issued > timedelta(seconds=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_AGE_SECONDS):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID")
    selectors = _selectors(
        receipt["wal_object_selectors"],
        base_backup_end_lsn=receipt["base_backup_end_lsn"],
        target_lsn=receipt["target_lsn"],
        timeline_id=expected_timeline_id,
        recipient=expected_recipient,
        object_key_prefix=expected_object_key_prefix,
        code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SELECTOR_SET_INVALID",
    )
    return _ReceiptFacts(
        raw=raw,
        binding=receipt["binding"] if isinstance(receipt["binding"], Mapping) else _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        binding_sha256=_sha256(receipt["binding_sha256"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        destination_age_recipient=_text(receipt["destination_age_recipient"], pattern=AGE_RECIPIENT_RE, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        session_sha256=_sha256(receipt["session_sha256"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        manifest_id=_id(receipt["manifest_id"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        manifest_sha256=_sha256(receipt["manifest_sha256"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        finalization_permit_id=_id(receipt["finalization_permit_id"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        finalization_permit_sha256=_sha256(receipt["finalization_permit_sha256"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        committed_chunk_set_sha256=_sha256(receipt["committed_chunk_set_sha256"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        lineage_sha256=_sha256(receipt["lineage_sha256"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        snapshot_sha256=_sha256(receipt["snapshot_sha256"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        snapshot_bytes=_positive(receipt["snapshot_bytes"], maximum=2**63 - 1, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        baseline_generation_id=_text(receipt["baseline_generation_id"], pattern=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII), code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        database_system_identifier=_text(receipt["database_system_identifier"], pattern=re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII), code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        timeline_id=_positive(receipt["timeline_id"], maximum=0xFFFFFFFF, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        wal_segment_size_bytes=_positive(receipt["wal_segment_size_bytes"], maximum=2**31 - 1, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        baseline_wal_lsn=_lsn(receipt["baseline_wal_lsn"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID")[0],
        wal_chain_start_lsn=_lsn(receipt["wal_chain_start_lsn"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID")[0],
        base_backup_end_lsn=_lsn(receipt["base_backup_end_lsn"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID")[0],
        target_lsn=_lsn(receipt["target_lsn"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID")[0],
        selectors=selectors,
        receipt_id=_id(receipt["receipt_id"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        receipt_nonce=_nonce(receipt["receipt_nonce"], code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID"),
        issued_at=issued,
        expires_at=expires,
        witness_public_key=witness,
    )


def _assert_receipt_live(receipt: _ReceiptFacts, *, now: datetime) -> None:
    current = _utc(now, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_CLOCK_INVALID")
    if receipt.issued_at > current + timedelta(seconds=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_FUTURE_SKEW_SECONDS):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_FUTURE")
    if current >= receipt.expires_at:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_EXPIRED")


def _derive_facts(
    *,
    manifest: object,
    handoff_receipt: object,
    continuity_receipt: object,
    scope: object,
    now: datetime,
) -> _EvidenceFacts:
    try:
        verified_manifest = require_verified_physical_wal_chunked_base_backup_manifest(manifest, now=now)
    except PhysicalWalChunkedBaseBackupManifestError as exc:
        raise PhysicalWalChunkedBaseBackupTargetWalContinuityError(
            "CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_MANIFEST_INVALID"
        ) from exc
    try:
        handoff = require_verified_physical_wal_chunked_base_backup_handoff_receipt(
            handoff_receipt,
            manifest=verified_manifest,
            now=now,
        )
    except PhysicalWalChunkedBaseBackupHandoffReceiptError as exc:
        raise PhysicalWalChunkedBaseBackupTargetWalContinuityError(
            "CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_HANDOFF_INVALID"
        ) from exc
    binding = verified_manifest.finalization_permit.session.binding
    typed_scope, scope_sha = _require_scope(scope, binding=binding, handoff=handoff)
    receipt = _parse_receipt(
        continuity_receipt,
        expected_witness_public_key=verified_manifest.witness_public_key,
        expected_timeline_id=handoff.timeline_id,
        expected_recipient=binding.destination_age_recipient,
        expected_object_key_prefix=_selector_object_key_prefix(
            binding=binding,
            lineage_sha256=handoff.lineage_sha256,
        ),
    )
    _assert_receipt_live(receipt, now=now)
    manifest_sha = hashlib.sha256(verified_manifest.canonical_manifest).hexdigest()
    session_sha = hashlib.sha256(verified_manifest.finalization_permit.session.canonical_session).hexdigest()
    binding_sha = _handoff_binding_sha256(binding)
    if (
        receipt.binding != _binding_mapping(binding)
        or receipt.binding_sha256 != binding_sha
        or receipt.destination_age_recipient != binding.destination_age_recipient
        or receipt.session_sha256 != session_sha
        or receipt.manifest_id != verified_manifest.manifest_id
        or receipt.manifest_sha256 != manifest_sha
        or receipt.finalization_permit_id != verified_manifest.finalization_permit.finalization_permit_id
        or receipt.finalization_permit_sha256
        != hashlib.sha256(verified_manifest.finalization_permit.canonical_finalization_permit).hexdigest()
        or receipt.committed_chunk_set_sha256 != verified_manifest.finalization_permit.committed_chunk_set_sha256
    ):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_BASE_MANIFEST_MISMATCH")
    if (
        receipt.lineage_sha256 != handoff.lineage_sha256
        or receipt.snapshot_sha256 != handoff.snapshot_sha256
        or receipt.snapshot_bytes != handoff.snapshot_bytes
        or receipt.baseline_generation_id != handoff.baseline_generation_id
        or receipt.database_system_identifier != handoff.database_system_identifier
        or receipt.timeline_id != handoff.timeline_id
        or receipt.wal_segment_size_bytes != handoff.wal_segment_size_bytes
        or receipt.baseline_wal_lsn != handoff.baseline_wal_lsn
        or receipt.wal_chain_start_lsn != handoff.wal_chain_start_lsn
        or receipt.base_backup_end_lsn != handoff.base_backup_end_lsn
        or receipt.target_lsn != typed_scope.target_lsn
        or receipt.witness_public_key != verified_manifest.witness_public_key
        or len({receipt.receipt_id, receipt.receipt_nonce, handoff.receipt_id, handoff.receipt_nonce, verified_manifest.manifest_id, verified_manifest.finalization_permit.finalization_permit_id}) != 6
    ):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_LINEAGE_OR_TARGET_MISMATCH")
    selector_sha = _selector_set_sha256(
        receipt.selectors,
        binding_sha256=binding_sha,
        manifest_sha256=manifest_sha,
        lineage_sha256=handoff.lineage_sha256,
        base_backup_end_lsn=handoff.base_backup_end_lsn,
        target_lsn=typed_scope.target_lsn,
    )
    return _EvidenceFacts(
        manifest=verified_manifest,
        handoff=handoff,
        scope=typed_scope,
        scope_sha256=scope_sha,
        receipt=receipt,
        selector_set_sha256=selector_sha,
    )


def _receipt_result(facts: _EvidenceFacts) -> VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt:
    result = VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt(
        canonical_receipt=facts.receipt.raw,
        receipt_id=facts.receipt.receipt_id,
        receipt_nonce=facts.receipt.receipt_nonce,
        issued_at=facts.receipt.issued_at,
        expires_at=facts.receipt.expires_at,
        receipt_sha256=hashlib.sha256(facts.receipt.raw).hexdigest(),
        selector_set_sha256=facts.selector_set_sha256,
        target_lsn=facts.receipt.target_lsn,
        selectors=facts.receipt.selectors,
        manifest_id=facts.receipt.manifest_id,
        manifest_sha256=facts.receipt.manifest_sha256,
        lineage_sha256=facts.receipt.lineage_sha256,
        scope_sha256=facts.scope_sha256,
        witness_public_key=facts.receipt.witness_public_key,
    )
    object.__setattr__(result, "_capability", _RECEIPT_CAPABILITY)
    return result


def _require_receipt_capability(
    value: object,
    *,
    manifest: object,
    handoff_receipt: object,
    scope: object,
    now: datetime,
) -> tuple[VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt, _EvidenceFacts]:
    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt
        or value._capability is not _RECEIPT_CAPABILITY
    ):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_REQUIRED")
    facts = _derive_facts(
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        continuity_receipt=value.canonical_receipt,
        scope=scope,
        now=now,
    )
    receipt = facts.receipt
    if (
        value.canonical_receipt != receipt.raw
        or value.receipt_id != receipt.receipt_id
        or value.receipt_nonce != receipt.receipt_nonce
        or value.issued_at != receipt.issued_at
        or value.expires_at != receipt.expires_at
        or value.receipt_sha256 != hashlib.sha256(receipt.raw).hexdigest()
        or value.selector_set_sha256 != facts.selector_set_sha256
        or value.target_lsn != receipt.target_lsn
        or value.selectors != receipt.selectors
        or value.manifest_id != receipt.manifest_id
        or value.manifest_sha256 != receipt.manifest_sha256
        or value.lineage_sha256 != receipt.lineage_sha256
        or value.scope_sha256 != facts.scope_sha256
        or value.witness_public_key != receipt.witness_public_key
    ):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_TAMPERED")
    return value, facts


def build_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
    *,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    scope: PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    wal_object_selectors: Sequence[PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector],
    receipt_id: str,
    receipt_nonce: str,
    issued_at: datetime,
    expires_at: datetime,
    witness_signer: object,
) -> dict[str, Any]:
    """Build a Witness-signed canonical receipt over an exact V2 WAL chain."""

    try:
        verified_manifest = require_verified_physical_wal_chunked_base_backup_manifest(manifest, now=issued_at)
        handoff = require_verified_physical_wal_chunked_base_backup_handoff_receipt(
            handoff_receipt,
            manifest=verified_manifest,
            now=issued_at,
        )
    except Exception as exc:
        raise PhysicalWalChunkedBaseBackupTargetWalContinuityError(
            "CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_BUILD_INPUT_INVALID"
        ) from exc
    binding = verified_manifest.finalization_permit.session.binding
    typed_scope, _scope_sha = _require_scope(scope, binding=binding, handoff=handoff)
    selector_mappings: list[dict[str, object]] = []
    if isinstance(wal_object_selectors, (str, bytes)) or not isinstance(wal_object_selectors, Sequence):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SELECTOR_SET_INVALID")
    for item in wal_object_selectors:
        if type(item) is not PhysicalWalChunkedBaseBackupTargetWalContinuityReceiptSelector:
            _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SELECTOR_SET_INVALID")
        selector_mappings.append(_selector_mapping(item))
    selectors = _selectors(
        selector_mappings,
        base_backup_end_lsn=handoff.base_backup_end_lsn,
        target_lsn=typed_scope.target_lsn,
        timeline_id=handoff.timeline_id,
        recipient=binding.destination_age_recipient,
        object_key_prefix=_selector_object_key_prefix(
            binding=binding,
            lineage_sha256=handoff.lineage_sha256,
        ),
        code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SELECTOR_SET_INVALID",
    )
    receipt = _id(receipt_id, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_ID_INVALID")
    nonce = _nonce(receipt_nonce, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_NONCE_INVALID")
    if len({receipt, nonce, handoff.receipt_id, handoff.receipt_nonce, verified_manifest.manifest_id, verified_manifest.finalization_permit.finalization_permit_id}) != 6:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_IDENTITY_REUSE")
    issued_text = _timestamp_text(issued_at, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_TIME_INVALID")
    expires_text = _timestamp_text(expires_at, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_TIME_INVALID")
    issued = _timestamp(issued_text, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_TIME_INVALID")
    expires = _timestamp(expires_text, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_TIME_INVALID")
    if expires <= issued or expires - issued > timedelta(seconds=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_AGE_SECONDS):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_TIME_INVALID")
    _private, public_key, signer = _signer_from_private(
        witness_signer,
        code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_SIGNER_INVALID",
    )
    if public_key != verified_manifest.witness_public_key:
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_SIGNER_INVALID")
    manifest_sha = hashlib.sha256(verified_manifest.canonical_manifest).hexdigest()
    unsigned: dict[str, Any] = {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_SCHEMA,
        "version": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_VERSION,
        "kind": "physical_wal_chunked_base_backup_target_wal_continuity_receipt",
        "binding": _binding_mapping(binding),
        "binding_sha256": _handoff_binding_sha256(binding),
        "destination_age_recipient": binding.destination_age_recipient,
        "session_sha256": hashlib.sha256(verified_manifest.finalization_permit.session.canonical_session).hexdigest(),
        "manifest_id": verified_manifest.manifest_id,
        "manifest_sha256": manifest_sha,
        "finalization_permit_id": verified_manifest.finalization_permit.finalization_permit_id,
        "finalization_permit_sha256": hashlib.sha256(verified_manifest.finalization_permit.canonical_finalization_permit).hexdigest(),
        "committed_chunk_set_sha256": verified_manifest.finalization_permit.committed_chunk_set_sha256,
        "lineage_sha256": handoff.lineage_sha256,
        "snapshot_sha256": handoff.snapshot_sha256,
        "snapshot_bytes": handoff.snapshot_bytes,
        "baseline_generation_id": handoff.baseline_generation_id,
        "database_system_identifier": handoff.database_system_identifier,
        "timeline_id": handoff.timeline_id,
        "wal_segment_size_bytes": handoff.wal_segment_size_bytes,
        "baseline_wal_lsn": handoff.baseline_wal_lsn,
        "wal_chain_start_lsn": handoff.wal_chain_start_lsn,
        "base_backup_end_lsn": handoff.base_backup_end_lsn,
        "target_lsn": typed_scope.target_lsn,
        "wal_object_selectors": [_selector_mapping(item) for item in selectors],
        "receipt_id": receipt,
        "receipt_nonce": nonce,
        "issued_at": issued_text,
        "expires_at": expires_text,
        "witness_signer": signer,
    }
    return {
        **unsigned,
        "witness_signature": _sign(
            unsigned,
            signer=witness_signer,
            code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_SIGNER_INVALID",
        ),
    }


def canonical_physical_wal_chunked_base_backup_target_wal_continuity_receipt_bytes(
    value: Mapping[str, Any] | bytes,
) -> bytes:
    """Return canonical receipt bytes after syntax/signature-shape parsing only."""

    # This intentionally has no manifest/handoff context and therefore cannot
    # verify authority.  It is useful only for durable byte identity.
    if isinstance(value, Mapping):
        return _canonical(dict(value), code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID")
    if isinstance(value, bytes):
        try:
            payload = json.loads(
                value.decode("ascii", "strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_NONCANONICAL")
        raw = _canonical(payload, code="CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_NONCANONICAL")
        if raw != value:
            _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_NONCANONICAL")
        return raw
    _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_RECEIPT_INVALID")


def verify_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
    *,
    continuity_receipt: Mapping[str, Any] | bytes,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    scope: PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt:
    """Verify signed canonical V2 WAL coverage against one exact base handoff."""

    facts = _derive_facts(
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        continuity_receipt=continuity_receipt,
        scope=scope,
        now=now,
    )
    return _receipt_result(facts)


def require_verified_physical_wal_chunked_base_backup_target_wal_continuity_receipt(
    value: object,
    *,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    scope: PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt:
    """Revalidate a still-fresh opaque continuity receipt and all its pins."""

    verified, _facts = _require_receipt_capability(
        value,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        scope=scope,
        now=now,
    )
    return verified


def mint_physical_wal_chunked_base_backup_target_wal_continuity(
    *,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    continuity_receipt: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt,
    scope: PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity:
    """Mint opaque V2-only target continuity evidence, never a restore action."""

    verified_receipt, facts = _require_receipt_capability(
        continuity_receipt,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        scope=scope,
        now=now,
    )
    result = VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity(
        schema=PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCHEMA,
        transfer_binding=facts.manifest.finalization_permit.session.binding,
        canonical_manifest_sha256=hashlib.sha256(facts.manifest.canonical_manifest).hexdigest(),
        manifest_id=facts.manifest.manifest_id,
        handoff_receipt_id=facts.handoff.receipt_id,
        handoff_receipt_nonce=facts.handoff.receipt_nonce,
        handoff_expires_at=facts.handoff.expires_at,
        continuity_receipt_id=verified_receipt.receipt_id,
        continuity_receipt_nonce=verified_receipt.receipt_nonce,
        continuity_receipt_sha256=verified_receipt.receipt_sha256,
        continuity_receipt_expires_at=verified_receipt.expires_at,
        lineage_sha256=facts.handoff.lineage_sha256,
        scope_sha256=facts.scope_sha256,
        base_backup_end_lsn=facts.handoff.base_backup_end_lsn,
        target_lsn=verified_receipt.target_lsn,
        selector_set_sha256=verified_receipt.selector_set_sha256,
        wal_object_selectors=verified_receipt.selectors,
    )
    object.__setattr__(result, "_capability", _CONTINUITY_CAPABILITY)
    return require_verified_physical_wal_chunked_base_backup_target_wal_continuity(
        result,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        continuity_receipt=continuity_receipt,
        scope=scope,
        now=now,
    )


def require_verified_physical_wal_chunked_base_backup_target_wal_continuity(
    value: object,
    *,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    continuity_receipt: VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuityReceipt,
    scope: PhysicalWalChunkedBaseBackupTargetWalContinuityScope,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity:
    """Revalidate opaque target coverage evidence; no side effect occurs here."""

    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupTargetWalContinuity
        or value._capability is not _CONTINUITY_CAPABILITY
        or value.schema != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_SCHEMA
    ):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_CAPABILITY_REQUIRED")
    verified_receipt, facts = _require_receipt_capability(
        continuity_receipt,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        scope=scope,
        now=now,
    )
    if (
        value.transfer_binding != facts.manifest.finalization_permit.session.binding
        or value.canonical_manifest_sha256 != hashlib.sha256(facts.manifest.canonical_manifest).hexdigest()
        or value.manifest_id != facts.manifest.manifest_id
        or value.handoff_receipt_id != facts.handoff.receipt_id
        or value.handoff_receipt_nonce != facts.handoff.receipt_nonce
        or value.handoff_expires_at != facts.handoff.expires_at
        or value.continuity_receipt_id != verified_receipt.receipt_id
        or value.continuity_receipt_nonce != verified_receipt.receipt_nonce
        or value.continuity_receipt_sha256 != verified_receipt.receipt_sha256
        or value.continuity_receipt_expires_at != verified_receipt.expires_at
        or value.lineage_sha256 != facts.handoff.lineage_sha256
        or value.scope_sha256 != facts.scope_sha256
        or value.base_backup_end_lsn != facts.handoff.base_backup_end_lsn
        or value.target_lsn != verified_receipt.target_lsn
        or value.selector_set_sha256 != verified_receipt.selector_set_sha256
        or value.wal_object_selectors != verified_receipt.selectors
    ):
        _fail("CHUNKED_BASE_BACKUP_TARGET_WAL_CONTINUITY_CAPABILITY_TAMPERED")
    return value
