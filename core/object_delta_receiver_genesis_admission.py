"""Pure, default-off genesis admission for a receiver Object-delta stream.

A sequence-one Object-delta batch creates a receiver history, so ordinary
delivery authorization is not sufficient.  This module accepts only opaque
capabilities minted after three independent signature verifications:

* a source-signed baseline manifest, pinned to the receiver's source key;
* a source-signed cutover attestation, pinned to that same source key; and
* a receiver-local restore evidence envelope, pinned to a separately managed
  root-verifier key.

It then binds those claims to one already-authorized receiver delivery.  The
result is another opaque, in-memory capability for that *exact object*.  This
is a composition contract only: it has no filesystem, database, Object
Storage, age, network, settings, or runtime dependency.  No current runtime
imports it.  A future adapter must explicitly require its result before it
opens a transaction for a sequence-one batch.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    GENESIS_PRIOR_CHAIN_SHA256,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.append_only_sync_delta_payload import REGISTRY_FINGERPRINT_RE
from core.object_delta_baseline_manifest import (
    ObjectDeltaBaselineManifestError,
    ObjectDeltaReceiverRestoreAttestation,
    VerifiedObjectDeltaBaselineManifest,
    assert_object_delta_baseline_matches_receiver_restore,
    verify_object_delta_baseline_manifest,
)
from core.object_delta_receiver_apply_scope import (
    AuthorizedObjectDeltaReceiverDelivery,
    ObjectDeltaReceiverApplyScopeError,
    validate_authorized_object_delta_receiver_delivery,
)
from core.object_delta_source_cutover_attestation import (
    VerifiedObjectDeltaSourceCutoverAttestation,
    verify_object_delta_source_cutover_attestation,
)


OBJECT_DELTA_RECEIVER_GENESIS_ADMISSION_CONTRACT = (
    "gold-trade-object-delta-receiver-genesis-admission-v1"
)
OBJECT_DELTA_RECEIVER_GENESIS_ADMISSION_DEFAULT_ENABLED = False
OBJECT_DELTA_RECEIVER_GENESIS_ADMISSION_ENABLES_RUNTIME = False

OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_SCHEMA = (
    "gold-trade-object-delta-receiver-restore-evidence-v1"
)
OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_STATUS = "restored_verified"
OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_SIGNATURE_ALGORITHM = "ed25519"
OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_SIGNATURE_DOMAIN = (
    b"gold-trade-object-delta-receiver-restore-evidence-v1\x00"
)
MAX_OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_BYTES = 64 * 1024

_RESTORE_EVIDENCE_UNSIGNED_FIELDS = frozenset(
    {
        "schema",
        "status",
        "restore",
        "baseline_manifest_sha256",
        "receiver_verifier",
    }
)
_RESTORE_EVIDENCE_FIELDS = _RESTORE_EVIDENCE_UNSIGNED_FIELDS | frozenset(
    {"receiver_signature"}
)
_RESTORE_FIELDS = frozenset(
    {
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "stream_generation_id",
        "registry_fingerprint",
        "source_generation",
        "snapshot_id",
        "alembic_revision",
        "manifest_object_key",
        "manifest_object_version_id",
        "manifest_ciphertext_sha256",
        "manifest_ciphertext_bytes",
        "database_sha256",
        "uploads_sha256",
    }
)
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_SNAPSHOT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{16,64}$")
_SOURCE_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ALEMBIC_REVISION_RE = re.compile(r"^[0-9a-z]{8,64}$")
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$")


class ObjectDeltaReceiverGenesisAdmissionError(ValueError):
    """A genesis delivery lacks authentic, exactly bound cutover evidence."""


# Capability tokens deliberately are not serializable.  The pattern mirrors
# the existing authorized receiver delivery: direct construction and
# dataclasses.replace() cannot mint a capability accepted by a future adapter.
_BASELINE_CAPABILITY = object()
_RESTORE_EVIDENCE_CAPABILITY = object()
_CUTOVER_CAPABILITY = object()
_GENESIS_ADMISSION_CAPABILITY = object()


@dataclass(frozen=True)
class VerifiedObjectDeltaReceiverGenesisBaseline:
    """Opaque wrapper minted only by baseline signature verification."""

    baseline: VerifiedObjectDeltaBaselineManifest
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedObjectDeltaReceiverRestoreEvidence:
    """Opaque receiver-local, signature-verified snapshot-restore evidence."""

    restore: ObjectDeltaReceiverRestoreAttestation
    baseline_manifest_sha256: str
    receiver_verifier_key_id: str
    evidence_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedObjectDeltaReceiverGenesisCutover:
    """Opaque wrapper minted only by source cutover signature verification."""

    cutover: VerifiedObjectDeltaSourceCutoverAttestation
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class AuthorizedObjectDeltaReceiverGenesisAdmission:
    """Opaque authority for one exact verified sequence-one delivery object."""

    baseline: VerifiedObjectDeltaReceiverGenesisBaseline
    restore_evidence: VerifiedObjectDeltaReceiverRestoreEvidence
    cutover: VerifiedObjectDeltaReceiverGenesisCutover
    authorization: AuthorizedObjectDeltaReceiverDelivery
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ObjectDeltaReceiverGenesisAdmissionError(
                "receiver restore evidence contains duplicate JSON fields"
            )
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ObjectDeltaReceiverGenesisAdmissionError(
        f"receiver restore evidence JSON constant is forbidden: {value}"
    )


def _exact_mapping(value: object, *, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ObjectDeltaReceiverGenesisAdmissionError(f"receiver restore evidence {label} fields are invalid")
    return dict(value)


def _text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ObjectDeltaReceiverGenesisAdmissionError(f"receiver restore evidence {label} is invalid")
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            f"receiver restore evidence {label} is invalid"
        ) from exc
    return value


def _positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ObjectDeltaReceiverGenesisAdmissionError(f"receiver restore evidence {label} is invalid")
    return value


def _decode_base64(value: object, *, label: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str):
        raise ObjectDeltaReceiverGenesisAdmissionError(f"receiver restore evidence {label} is invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            f"receiver restore evidence {label} is invalid"
        ) from exc
    if len(decoded) != expected_bytes:
        raise ObjectDeltaReceiverGenesisAdmissionError(f"receiver restore evidence {label} is invalid")
    return decoded


def _require_public_key(value: object, *, label: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        raise ObjectDeltaReceiverGenesisAdmissionError(f"receiver restore evidence {label} is invalid")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError) as exc:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            f"receiver restore evidence {label} is invalid"
        ) from exc
    return value


def _receiver_verifier_key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _public_key_from_signer(signer: object) -> bytes:
    try:
        from cryptography.hazmat.primitives import serialization

        public_key = signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (AttributeError, ImportError, TypeError, ValueError) as exc:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver restore evidence signer is invalid"
        ) from exc
    return _require_public_key(public_key, label="signer public key")


def _restore_mapping(restore: ObjectDeltaReceiverRestoreAttestation) -> dict[str, object]:
    if type(restore) is not ObjectDeltaReceiverRestoreAttestation:
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver restore evidence restore attestation is invalid")
    return {
        "source_site": restore.source_site,
        "destination_site": restore.destination_site,
        "campaign_id": restore.campaign_id,
        "release_sha": restore.release_sha,
        "stream_generation_id": restore.stream_generation_id,
        "registry_fingerprint": restore.registry_fingerprint,
        "source_generation": restore.source_generation,
        "snapshot_id": restore.snapshot_id,
        "alembic_revision": restore.alembic_revision,
        "manifest_object_key": restore.manifest_object_key,
        "manifest_object_version_id": restore.manifest_object_version_id,
        "manifest_ciphertext_sha256": restore.manifest_ciphertext_sha256,
        "manifest_ciphertext_bytes": restore.manifest_ciphertext_bytes,
        "database_sha256": restore.database_sha256,
        "uploads_sha256": restore.uploads_sha256,
    }


def _parse_restore(value: object) -> ObjectDeltaReceiverRestoreAttestation:
    raw = _exact_mapping(value, fields=_RESTORE_FIELDS, label="restore")
    source_site = raw["source_site"]
    destination_site = raw["destination_site"]
    if (
        not isinstance(source_site, str)
        or not isinstance(destination_site, str)
        or source_site not in WEBAPP_SITES
        or destination_site not in WEBAPP_SITES
        or source_site == destination_site
    ):
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver restore evidence stream sites are invalid")
    return ObjectDeltaReceiverRestoreAttestation(
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=_text(raw["campaign_id"], label="campaign", pattern=CAMPAIGN_ID_RE),
        release_sha=_text(raw["release_sha"], label="release", pattern=RELEASE_SHA_RE),
        stream_generation_id=_text(
            raw["stream_generation_id"],
            label="stream generation",
            pattern=STREAM_GENERATION_ID_RE,
        ),
        registry_fingerprint=_text(
            raw["registry_fingerprint"],
            label="registry fingerprint",
            pattern=REGISTRY_FINGERPRINT_RE,
        ),
        source_generation=_text(
            raw["source_generation"],
            label="source generation",
            pattern=_SOURCE_GENERATION_RE,
        ),
        snapshot_id=_text(raw["snapshot_id"], label="snapshot ID", pattern=_SNAPSHOT_ID_RE),
        alembic_revision=_text(
            raw["alembic_revision"], label="alembic revision", pattern=_ALEMBIC_REVISION_RE
        ),
        manifest_object_key=_text(
            raw["manifest_object_key"], label="manifest object key", pattern=OBJECT_KEY_RE
        ),
        manifest_object_version_id=_text(
            raw["manifest_object_version_id"],
            label="manifest object version",
            pattern=VERSION_ID_RE,
        ),
        manifest_ciphertext_sha256=_text(
            raw["manifest_ciphertext_sha256"],
            label="manifest ciphertext hash",
            pattern=SHA256_RE,
        ),
        manifest_ciphertext_bytes=_positive_int(
            raw["manifest_ciphertext_bytes"], label="manifest ciphertext bytes"
        ),
        database_sha256=_text(raw["database_sha256"], label="database hash", pattern=SHA256_RE),
        uploads_sha256=_text(raw["uploads_sha256"], label="uploads hash", pattern=SHA256_RE),
    )


def _parse_receiver_verifier(value: object) -> tuple[bytes, str]:
    raw = _exact_mapping(value, fields=_SIGNER_FIELDS, label="verifier")
    if raw["algorithm"] != OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_SIGNATURE_ALGORITHM:
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver restore evidence verifier algorithm is invalid")
    public_key = _decode_base64(raw["public_key_base64"], label="verifier public key", expected_bytes=32)
    _require_public_key(public_key, label="verifier public key")
    key_id = _text(raw["key_id"], label="verifier key ID", pattern=_KEY_ID_RE)
    if key_id != _receiver_verifier_key_id(public_key):
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver restore evidence verifier key ID does not match public key"
        )
    return public_key, key_id


def _parse_receiver_signature(value: object) -> bytes:
    raw = _exact_mapping(value, fields=_SIGNATURE_FIELDS, label="signature")
    if raw["algorithm"] != OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_SIGNATURE_ALGORITHM:
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver restore evidence signature algorithm is invalid")
    return _decode_base64(raw["signature_base64"], label="signature", expected_bytes=64)


def _validate_restore_evidence(
    evidence: object,
) -> tuple[dict[str, Any], ObjectDeltaReceiverRestoreAttestation, str, bytes, str, bytes]:
    raw = _exact_mapping(evidence, fields=_RESTORE_EVIDENCE_FIELDS, label="envelope")
    if (
        raw["schema"] != OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_SCHEMA
        or raw["status"] != OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_STATUS
    ):
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver restore evidence schema or status is invalid")
    restore = _parse_restore(raw["restore"])
    baseline_manifest_sha256 = _text(
        raw["baseline_manifest_sha256"], label="baseline manifest hash", pattern=SHA256_RE
    )
    public_key, key_id = _parse_receiver_verifier(raw["receiver_verifier"])
    signature = _parse_receiver_signature(raw["receiver_signature"])
    normalized = {
        "schema": OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_SCHEMA,
        "status": OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_STATUS,
        "restore": _restore_mapping(restore),
        "baseline_manifest_sha256": baseline_manifest_sha256,
        "receiver_verifier": {
            "algorithm": OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_SIGNATURE_ALGORITHM,
            "public_key_base64": base64.b64encode(public_key).decode("ascii"),
            "key_id": key_id,
        },
        "receiver_signature": {
            "algorithm": OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_SIGNATURE_ALGORITHM,
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        },
    }
    return normalized, restore, baseline_manifest_sha256, public_key, key_id, signature


def _unsigned_restore_evidence_bytes(normalized: Mapping[str, Any]) -> bytes:
    unsigned = {field: normalized[field] for field in _RESTORE_EVIDENCE_UNSIGNED_FIELDS}
    return OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_SIGNATURE_DOMAIN + canonical_json_bytes(unsigned)


def parse_object_delta_receiver_restore_evidence_json(raw: bytes | str) -> dict[str, Any]:
    """Parse exactly one canonical restore-evidence envelope.

    A mapping may be supplied directly to the verifier after a trusted local
    decryptor has parsed it.  Raw bytes, however, are deliberately accepted
    only in this canonical form so a sealed artifact has one representation
    before its signature and Object-version evidence are composed elsewhere.
    """

    if isinstance(raw, bytes):
        payload = raw
        if not payload or len(payload) > MAX_OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_BYTES:
            raise ObjectDeltaReceiverGenesisAdmissionError("receiver restore evidence size is invalid")
        try:
            decoded = payload.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ObjectDeltaReceiverGenesisAdmissionError("receiver restore evidence JSON is invalid") from exc
    elif isinstance(raw, str):
        try:
            payload = raw.encode("utf-8", "strict")
        except UnicodeEncodeError as exc:
            raise ObjectDeltaReceiverGenesisAdmissionError("receiver restore evidence JSON is invalid") from exc
        if not payload or len(payload) > MAX_OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_BYTES:
            raise ObjectDeltaReceiverGenesisAdmissionError("receiver restore evidence size is invalid")
        decoded = raw
    else:
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver restore evidence JSON is invalid")
    try:
        value = json.loads(
            decoded,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except ObjectDeltaReceiverGenesisAdmissionError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver restore evidence JSON is invalid") from exc
    normalized, _restore, _hash, _key, _key_id, _signature = _validate_restore_evidence(value)
    if payload != canonical_json_bytes(normalized) + b"\n":
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver restore evidence JSON is not canonical"
        )
    return normalized


def canonical_object_delta_receiver_restore_evidence_bytes(
    evidence: Mapping[str, Any],
) -> bytes:
    """Return canonical newline-terminated bytes for one sealed restore proof."""

    normalized, _restore, _hash, _key, _key_id, _signature = _validate_restore_evidence(evidence)
    return canonical_json_bytes(normalized) + b"\n"


def build_object_delta_receiver_restore_evidence(
    *,
    restore: ObjectDeltaReceiverRestoreAttestation,
    baseline_manifest_sha256: str,
    receiver_verifier_signer: object,
) -> dict[str, Any]:
    """Build signed local restore evidence in memory only.

    A future root-only adapter may call this only after it independently
    validates its snapshot restore/install receipts.  This pure helper cannot
    read those receipts and does not claim to perform that operational check.
    """

    public_key = _public_key_from_signer(receiver_verifier_signer)
    unsigned: dict[str, Any] = {
        "schema": OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_SCHEMA,
        "status": OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_STATUS,
        "restore": _restore_mapping(restore),
        "baseline_manifest_sha256": baseline_manifest_sha256,
        "receiver_verifier": {
            "algorithm": OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_SIGNATURE_ALGORITHM,
            "public_key_base64": base64.b64encode(public_key).decode("ascii"),
            "key_id": _receiver_verifier_key_id(public_key),
        },
        "receiver_signature": {
            "algorithm": OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_SIGNATURE_ALGORITHM,
            "signature_base64": base64.b64encode(b"\x00" * 64).decode("ascii"),
        },
    }
    normalized, _restore, _hash, _key, _key_id, _signature = _validate_restore_evidence(unsigned)
    try:
        signature = receiver_verifier_signer.sign(_unsigned_restore_evidence_bytes(normalized))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver restore evidence signer cannot sign"
        ) from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver restore evidence signer produced an invalid signature"
        )
    normalized["receiver_signature"] = {
        "algorithm": OBJECT_DELTA_RECEIVER_RESTORE_EVIDENCE_SIGNATURE_ALGORITHM,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }
    _validate_restore_evidence(normalized)
    return normalized


def _require_verified_baseline(
    baseline: object,
) -> VerifiedObjectDeltaReceiverGenesisBaseline:
    if type(baseline) is not VerifiedObjectDeltaReceiverGenesisBaseline:
        raise ObjectDeltaReceiverGenesisAdmissionError("verified receiver genesis baseline is required")
    if baseline._capability is not _BASELINE_CAPABILITY:
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver genesis baseline was not verified")
    if type(baseline.baseline) is not VerifiedObjectDeltaBaselineManifest:
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver genesis baseline is invalid")
    return baseline


def verify_object_delta_receiver_genesis_baseline(
    manifest: object,
    *,
    expected_source_public_key: bytes,
    expected_source_site: str,
    expected_destination_site: str,
    expected_campaign_id: str,
    expected_release_sha: str,
    expected_stream_generation_id: str,
    expected_registry_fingerprint: str,
) -> VerifiedObjectDeltaReceiverGenesisBaseline:
    """Verify a raw source baseline and mint an opaque local capability."""

    try:
        verified = verify_object_delta_baseline_manifest(
            manifest,
            expected_source_public_key=expected_source_public_key,
            expected_source_site=expected_source_site,
            expected_destination_site=expected_destination_site,
            expected_campaign_id=expected_campaign_id,
            expected_release_sha=expected_release_sha,
            expected_stream_generation_id=expected_stream_generation_id,
            expected_registry_fingerprint=expected_registry_fingerprint,
        )
    except ObjectDeltaBaselineManifestError as exc:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver genesis baseline signature verification failed"
        ) from exc
    capability = VerifiedObjectDeltaReceiverGenesisBaseline(baseline=verified)
    object.__setattr__(capability, "_capability", _BASELINE_CAPABILITY)
    return capability


def verify_object_delta_receiver_restore_evidence(
    evidence: object,
    *,
    expected_receiver_verifier_public_key: bytes,
    baseline: VerifiedObjectDeltaReceiverGenesisBaseline,
) -> VerifiedObjectDeltaReceiverRestoreEvidence:
    """Verify a pinned local restore envelope and bind it to one baseline."""

    verified_baseline = _require_verified_baseline(baseline)
    expected_key = _require_public_key(
        expected_receiver_verifier_public_key,
        label="expected verifier public key",
    )
    normalized, restore, baseline_hash, actual_key, key_id, signature = _validate_restore_evidence(evidence)
    if actual_key != expected_key:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver restore evidence verifier key does not match receiver pin"
        )
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(actual_key).verify(
            signature,
            _unsigned_restore_evidence_bytes(normalized),
        )
    except ImportError as exc:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver restore evidence signature verification is unavailable"
        ) from exc
    except (ValueError, InvalidSignature) as exc:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver restore evidence signature verification failed"
        ) from exc
    if baseline_hash != verified_baseline.baseline.manifest_sha256:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver restore evidence does not bind the verified baseline manifest"
        )
    try:
        assert_object_delta_baseline_matches_receiver_restore(verified_baseline.baseline, restore)
    except ObjectDeltaBaselineManifestError as exc:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver restore evidence does not match the verified baseline"
        ) from exc
    capability = VerifiedObjectDeltaReceiverRestoreEvidence(
        restore=restore,
        baseline_manifest_sha256=baseline_hash,
        receiver_verifier_key_id=key_id,
        evidence_sha256=hashlib.sha256(canonical_json_bytes(normalized)).hexdigest(),
    )
    object.__setattr__(capability, "_capability", _RESTORE_EVIDENCE_CAPABILITY)
    return capability


def _assert_cutover_matches_baseline(
    *,
    baseline: VerifiedObjectDeltaBaselineManifest,
    cutover: VerifiedObjectDeltaSourceCutoverAttestation,
) -> None:
    expected = (
        ("source site", baseline.source_site, cutover.source_site),
        ("destination site", baseline.destination_site, cutover.destination_site),
        ("campaign", baseline.campaign_id, cutover.campaign_id),
        ("release", baseline.release_sha, cutover.release_sha),
        ("stream generation", baseline.stream_generation_id, cutover.stream_generation_id),
        ("registry fingerprint", baseline.registry_fingerprint, cutover.registry_fingerprint),
        ("writer epoch", baseline.writer_epoch, cutover.writer_epoch),
        ("writer lease", baseline.writer_lease_id, cutover.writer_lease_id),
        ("write gate", baseline.write_gate_id, cutover.write_gate_id),
        ("source generation", baseline.source_generation, cutover.source_generation),
        ("snapshot ID", baseline.snapshot_id, cutover.snapshot_id),
        ("alembic revision", baseline.alembic_revision, cutover.alembic_revision),
        (
            "snapshot manifest object key",
            baseline.manifest_object_key,
            cutover.snapshot_manifest_object_key,
        ),
        (
            "snapshot manifest object version",
            baseline.manifest_object_version_id,
            cutover.snapshot_manifest_object_version_id,
        ),
        (
            "snapshot manifest ciphertext hash",
            baseline.manifest_ciphertext_sha256,
            cutover.snapshot_manifest_ciphertext_sha256,
        ),
        (
            "snapshot manifest ciphertext bytes",
            baseline.manifest_ciphertext_bytes,
            cutover.snapshot_manifest_ciphertext_bytes,
        ),
        ("database hash", baseline.database_sha256, cutover.database_sha256),
        ("uploads hash", baseline.uploads_sha256, cutover.uploads_sha256),
        ("baseline manifest hash", baseline.manifest_sha256, cutover.baseline_manifest_sha256),
        ("source signer key", baseline.source_key_id, cutover.source_key_id),
    )
    for label, expected_value, actual_value in expected:
        if expected_value != actual_value:
            raise ObjectDeltaReceiverGenesisAdmissionError(
                f"verified source cutover does not match the verified baseline {label}"
            )


def _require_verified_cutover(
    cutover: object,
    *,
    baseline: VerifiedObjectDeltaReceiverGenesisBaseline,
) -> VerifiedObjectDeltaReceiverGenesisCutover:
    if type(cutover) is not VerifiedObjectDeltaReceiverGenesisCutover:
        raise ObjectDeltaReceiverGenesisAdmissionError("verified receiver genesis cutover is required")
    if cutover._capability is not _CUTOVER_CAPABILITY:
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver genesis cutover was not verified")
    if type(cutover.cutover) is not VerifiedObjectDeltaSourceCutoverAttestation:
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver genesis cutover is invalid")
    _assert_cutover_matches_baseline(baseline=baseline.baseline, cutover=cutover.cutover)
    return cutover


def verify_object_delta_receiver_genesis_cutover(
    attestation: object,
    *,
    expected_source_public_key: bytes,
    baseline: VerifiedObjectDeltaReceiverGenesisBaseline,
) -> VerifiedObjectDeltaReceiverGenesisCutover:
    """Verify raw source cutover evidence and mint an opaque local capability."""

    verified_baseline = _require_verified_baseline(baseline)
    source = verified_baseline.baseline
    try:
        verified = verify_object_delta_source_cutover_attestation(
            attestation,
            expected_source_public_key=expected_source_public_key,
            expected_source_site=source.source_site,
            expected_destination_site=source.destination_site,
            expected_campaign_id=source.campaign_id,
            expected_release_sha=source.release_sha,
            expected_stream_generation_id=source.stream_generation_id,
            expected_registry_fingerprint=source.registry_fingerprint,
        )
    except Exception as exc:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver genesis source cutover signature verification failed"
        ) from exc
    if type(verified) is not VerifiedObjectDeltaSourceCutoverAttestation:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "source cutover verifier returned an invalid capability shape"
        )
    _assert_cutover_matches_baseline(baseline=source, cutover=verified)
    capability = VerifiedObjectDeltaReceiverGenesisCutover(cutover=verified)
    object.__setattr__(capability, "_capability", _CUTOVER_CAPABILITY)
    return capability


def _require_verified_restore_evidence(
    restore_evidence: object,
    *,
    baseline: VerifiedObjectDeltaReceiverGenesisBaseline,
) -> VerifiedObjectDeltaReceiverRestoreEvidence:
    if type(restore_evidence) is not VerifiedObjectDeltaReceiverRestoreEvidence:
        raise ObjectDeltaReceiverGenesisAdmissionError("verified receiver restore evidence is required")
    if restore_evidence._capability is not _RESTORE_EVIDENCE_CAPABILITY:
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver restore evidence was not verified")
    if type(restore_evidence.restore) is not ObjectDeltaReceiverRestoreAttestation:
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver restore evidence is invalid")
    if restore_evidence.baseline_manifest_sha256 != baseline.baseline.manifest_sha256:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver restore evidence does not bind the verified baseline manifest"
        )
    try:
        assert_object_delta_baseline_matches_receiver_restore(
            baseline.baseline,
            restore_evidence.restore,
        )
    except ObjectDeltaBaselineManifestError as exc:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver restore evidence does not match the verified baseline"
        ) from exc
    return restore_evidence


def _validate_genesis_delivery(
    *,
    baseline: VerifiedObjectDeltaReceiverGenesisBaseline,
    cutover: VerifiedObjectDeltaReceiverGenesisCutover,
    authorization: object,
) -> AuthorizedObjectDeltaReceiverDelivery:
    if type(authorization) is not AuthorizedObjectDeltaReceiverDelivery:
        raise ObjectDeltaReceiverGenesisAdmissionError("verified receiver delivery is required")
    try:
        verified_authorization = validate_authorized_object_delta_receiver_delivery(authorization)
    except ObjectDeltaReceiverApplyScopeError as exc:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "verified receiver delivery is no longer authorized"
        ) from exc
    source = baseline.baseline
    batch = verified_authorization.batch
    expected = (
        ("source site", source.source_site, batch.source_site),
        ("destination site", source.destination_site, batch.destination_site),
        ("campaign", source.campaign_id, batch.campaign_id),
        ("release", source.release_sha, batch.release_sha),
        ("stream generation", source.stream_generation_id, batch.stream.generation_id),
        ("writer epoch", source.writer_epoch, batch.writer_term.epoch),
        ("writer lease", source.writer_lease_id, batch.writer_term.lease_id),
        ("source signer key", source.source_key_id, verified_authorization.binding.source_key_id),
        (
            "cutover source signer key",
            cutover.cutover.source_key_id,
            verified_authorization.binding.source_key_id,
        ),
    )
    for label, expected_value, actual_value in expected:
        if expected_value != actual_value:
            raise ObjectDeltaReceiverGenesisAdmissionError(
                f"verified receiver delivery does not match genesis {label}"
            )
    # The current release-bound receiver permit intentionally has no registry
    # fingerprint field.  If a future permit/binding adds one, do not silently
    # ignore it: require it to agree with both source-signed genesis claims.
    binding_registry_fingerprint = getattr(
        verified_authorization.binding,
        "registry_fingerprint",
        None,
    )
    if binding_registry_fingerprint is not None and (
        type(binding_registry_fingerprint) is not str
        or binding_registry_fingerprint != source.registry_fingerprint
        or binding_registry_fingerprint != cutover.cutover.registry_fingerprint
    ):
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "verified receiver delivery registry fingerprint does not match genesis evidence"
        )
    if batch.stream.first_sequence != 1:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "verified receiver delivery is not a sequence-one genesis batch"
        )
    if batch.prior_chain_sha256 != GENESIS_PRIOR_CHAIN_SHA256:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "verified receiver delivery does not have the genesis prior chain"
        )
    return verified_authorization


def admit_object_delta_receiver_genesis(
    *,
    baseline: VerifiedObjectDeltaReceiverGenesisBaseline,
    restore_evidence: VerifiedObjectDeltaReceiverRestoreEvidence,
    cutover: VerifiedObjectDeltaReceiverGenesisCutover,
    authorization: AuthorizedObjectDeltaReceiverDelivery,
) -> AuthorizedObjectDeltaReceiverGenesisAdmission:
    """Mint a narrow in-memory capability for one exact sequence-one batch."""

    verified_baseline = _require_verified_baseline(baseline)
    verified_restore = _require_verified_restore_evidence(
        restore_evidence,
        baseline=verified_baseline,
    )
    verified_cutover = _require_verified_cutover(cutover, baseline=verified_baseline)
    verified_authorization = _validate_genesis_delivery(
        baseline=verified_baseline,
        cutover=verified_cutover,
        authorization=authorization,
    )
    capability = AuthorizedObjectDeltaReceiverGenesisAdmission(
        baseline=verified_baseline,
        restore_evidence=verified_restore,
        cutover=verified_cutover,
        authorization=verified_authorization,
    )
    object.__setattr__(capability, "_capability", _GENESIS_ADMISSION_CAPABILITY)
    return capability


def validate_authorized_object_delta_receiver_genesis_admission(
    admission: object,
) -> AuthorizedObjectDeltaReceiverGenesisAdmission:
    """Recheck an opaque admission immediately before a future DB boundary."""

    if type(admission) is not AuthorizedObjectDeltaReceiverGenesisAdmission:
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver genesis admission is invalid")
    if admission._capability is not _GENESIS_ADMISSION_CAPABILITY:
        raise ObjectDeltaReceiverGenesisAdmissionError("receiver genesis admission was not authorized")
    baseline = _require_verified_baseline(admission.baseline)
    restore_evidence = _require_verified_restore_evidence(
        admission.restore_evidence,
        baseline=baseline,
    )
    cutover = _require_verified_cutover(admission.cutover, baseline=baseline)
    authorization = _validate_genesis_delivery(
        baseline=baseline,
        cutover=cutover,
        authorization=admission.authorization,
    )
    if (
        admission.baseline is not baseline
        or admission.restore_evidence is not restore_evidence
        or admission.cutover is not cutover
        or admission.authorization is not authorization
    ):
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver genesis admission changed after authorization"
        )
    return admission


def require_object_delta_receiver_genesis_admission(
    *,
    authorization: AuthorizedObjectDeltaReceiverDelivery,
    admission: AuthorizedObjectDeltaReceiverGenesisAdmission,
) -> AuthorizedObjectDeltaReceiverDelivery:
    """Return a delivery only if this exact object holds a valid genesis permit."""

    verified_admission = validate_authorized_object_delta_receiver_genesis_admission(admission)
    if authorization is not verified_admission.authorization:
        raise ObjectDeltaReceiverGenesisAdmissionError(
            "receiver genesis admission cannot be reused for this delivery"
        )
    return verified_admission.authorization
