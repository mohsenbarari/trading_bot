"""Pure, source-signed evidence for an Object-delta baseline cutover.

``ObjectDeltaSourceCutover`` is deliberately a durable *source-local* record:
its database primary keys and timestamps are not portable proof for a
receiver.  This module turns the portable, non-secret fields of a committed
``baseline_published`` record into a separately signed claim.  It also binds
the exact already-signed baseline manifest that the source says was published
for that record.

The contract is intentionally below every runtime adapter.  It does not read
the source-cutover row, acquire a write gate, create a snapshot, publish or
download an Object, read a key file, contact Object Storage, or admit a
receiver transaction.  A future root-only coordinator must obtain its inputs
under the appropriate source transaction and a receiver must still establish
its own restore evidence before using the verified result for genesis
admission.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any
from uuid import UUID

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
from core.append_only_sync_delta_payload import REGISTRY_FINGERPRINT_RE
from core.object_delta_baseline_manifest import (
    ObjectDeltaBaselineManifestError,
    VerifiedObjectDeltaBaselineManifest,
    parse_object_delta_baseline_manifest_json,
    verify_object_delta_baseline_manifest,
)


OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SCHEMA = (
    "gold-trade-object-delta-source-cutover-attestation-v1"
)
OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_STATUS = "committed"
OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SIGNATURE_ALGORITHM = "ed25519"
OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SIGNATURE_DOMAIN = (
    b"gold-trade-object-delta-source-cutover-attestation-v1\x00"
)
OBJECT_DELTA_SOURCE_CUTOVER_BASELINE_PUBLISHED_STATE = "baseline_published"
MAX_OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_BYTES = 128 * 1024

_OUTER_FIELDS = frozenset(
    {
        "schema",
        "status",
        "cutover",
        "baseline_manifest",
        "source_signer",
    }
)
_SEALED_FIELDS = _OUTER_FIELDS | frozenset({"source_signature"})
_CUTOVER_FIELDS = frozenset(
    {
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "stream_generation_id",
        "state",
        "registry_fingerprint",
        "writer_term",
        "write_gate_id",
        "snapshot",
        "baseline_receipt",
    }
)
_WRITER_TERM_FIELDS = frozenset({"epoch", "lease_id"})
_SNAPSHOT_FIELDS = frozenset(
    {
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
_BASELINE_RECEIPT_FIELDS = frozenset(
    {
        "manifest_object_key",
        "manifest_object_version_id",
        "manifest_ciphertext_sha256",
        "manifest_ciphertext_bytes",
        "manifest_sha256",
    }
)
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})

_SOURCE_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SNAPSHOT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{16,64}$")
_ALEMBIC_REVISION_RE = re.compile(r"^[0-9a-z]{8,64}$")
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$")


class ObjectDeltaSourceCutoverAttestationError(ValueError):
    """The signed source cutover evidence is malformed or unauthentic."""


@dataclass(frozen=True)
class ObjectDeltaSourceCutoverRecord:
    """Portable, non-secret fields of a committed source-cutover row.

    Local ``id``, ``stream_id``, and timestamps are intentionally omitted:
    they identify a row only inside the source database.  The stream identity
    and immutable publication receipts are the portable evidence a receiver
    can bind to its independently restored baseline.
    """

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    state: str
    registry_fingerprint: str
    writer_epoch: int
    writer_lease_id: str
    write_gate_id: str
    source_generation: str
    snapshot_id: str
    alembic_revision: str
    snapshot_manifest_object_key: str
    snapshot_manifest_object_version_id: str
    snapshot_manifest_ciphertext_sha256: str
    snapshot_manifest_ciphertext_bytes: int
    database_sha256: str
    uploads_sha256: str
    baseline_manifest_object_key: str
    baseline_manifest_object_version_id: str
    baseline_manifest_ciphertext_sha256: str
    baseline_manifest_ciphertext_bytes: int


@dataclass(frozen=True)
class VerifiedObjectDeltaSourceCutoverAttestation:
    """Pinned source evidence ready for a future receiver admission check.

    The value is proof only of matching signed claims.  It is not proof that a
    source gate was held, a snapshot is restorable, an Object is retrievable,
    or that this receiver may write.  A future admission capability must
    require local restore evidence separately and keep that capability opaque.
    """

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    registry_fingerprint: str
    writer_epoch: int
    writer_lease_id: str
    write_gate_id: str
    source_generation: str
    snapshot_id: str
    alembic_revision: str
    snapshot_manifest_object_key: str
    snapshot_manifest_object_version_id: str
    snapshot_manifest_ciphertext_sha256: str
    snapshot_manifest_ciphertext_bytes: int
    database_sha256: str
    uploads_sha256: str
    baseline_manifest_object_key: str
    baseline_manifest_object_version_id: str
    baseline_manifest_ciphertext_sha256: str
    baseline_manifest_ciphertext_bytes: int
    baseline_manifest_sha256: str
    source_key_id: str
    attestation_sha256: str
    baseline: VerifiedObjectDeltaBaselineManifest


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ObjectDeltaSourceCutoverAttestationError(
                "source cutover attestation contains duplicate JSON fields"
            )
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ObjectDeltaSourceCutoverAttestationError(
        f"source cutover attestation JSON constant is forbidden: {value}"
    )


def _exact_mapping(value: object, *, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ObjectDeltaSourceCutoverAttestationError(f"{label} fields are invalid")
    return dict(value)


def _text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ObjectDeltaSourceCutoverAttestationError(f"{label} is invalid")
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        raise ObjectDeltaSourceCutoverAttestationError(f"{label} is invalid") from exc
    return value


def _positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ObjectDeltaSourceCutoverAttestationError(f"{label} is invalid")
    return value


def _canonical_uuid(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise ObjectDeltaSourceCutoverAttestationError(f"{label} is invalid")
    try:
        parsed = str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ObjectDeltaSourceCutoverAttestationError(f"{label} is invalid") from exc
    if parsed != value:
        raise ObjectDeltaSourceCutoverAttestationError(f"{label} is invalid")
    return parsed


def _decode_base64(value: object, *, label: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str):
        raise ObjectDeltaSourceCutoverAttestationError(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ObjectDeltaSourceCutoverAttestationError(f"{label} is invalid") from exc
    if len(decoded) != expected_bytes:
        raise ObjectDeltaSourceCutoverAttestationError(f"{label} is invalid")
    return decoded


def _require_public_key(value: object, *, label: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        raise ObjectDeltaSourceCutoverAttestationError(f"{label} is invalid")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError) as exc:
        raise ObjectDeltaSourceCutoverAttestationError(f"{label} is invalid") from exc
    return value


def _source_key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(
        _require_public_key(public_key, label="source public key")
    ).hexdigest()


def _public_key_from_signer(source_signer: object) -> bytes:
    try:
        from cryptography.hazmat.primitives import serialization

        public_key = source_signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (AttributeError, ImportError, TypeError, ValueError) as exc:
        raise ObjectDeltaSourceCutoverAttestationError("source signer is invalid") from exc
    return _require_public_key(public_key, label="source signer public key")


def _record_mapping(record: ObjectDeltaSourceCutoverRecord) -> dict[str, Any]:
    if not isinstance(record, ObjectDeltaSourceCutoverRecord):
        raise ObjectDeltaSourceCutoverAttestationError("source cutover record is invalid")
    return {
        "source_site": record.source_site,
        "destination_site": record.destination_site,
        "campaign_id": record.campaign_id,
        "release_sha": record.release_sha,
        "stream_generation_id": record.stream_generation_id,
        "state": record.state,
        "registry_fingerprint": record.registry_fingerprint,
        "writer_term": {
            "epoch": record.writer_epoch,
            "lease_id": record.writer_lease_id,
        },
        "write_gate_id": record.write_gate_id,
        "snapshot": {
            "source_generation": record.source_generation,
            "snapshot_id": record.snapshot_id,
            "alembic_revision": record.alembic_revision,
            "manifest_object_key": record.snapshot_manifest_object_key,
            "manifest_object_version_id": record.snapshot_manifest_object_version_id,
            "manifest_ciphertext_sha256": record.snapshot_manifest_ciphertext_sha256,
            "manifest_ciphertext_bytes": record.snapshot_manifest_ciphertext_bytes,
            "database_sha256": record.database_sha256,
            "uploads_sha256": record.uploads_sha256,
        },
        "baseline_receipt": {
            "manifest_object_key": record.baseline_manifest_object_key,
            "manifest_object_version_id": record.baseline_manifest_object_version_id,
            "manifest_ciphertext_sha256": record.baseline_manifest_ciphertext_sha256,
            "manifest_ciphertext_bytes": record.baseline_manifest_ciphertext_bytes,
            # This is not a source-row column.  The coordinator derives it
            # from the exact signed baseline plaintext after reading the
            # ``baseline_published`` row; the builder replaces this
            # syntactically valid placeholder before any envelope is sealed.
            "manifest_sha256": "0" * 64,
        },
    }


def _record_from_mapping(value: object) -> tuple[dict[str, Any], ObjectDeltaSourceCutoverRecord, str]:
    raw = _exact_mapping(value, fields=_CUTOVER_FIELDS, label="source cutover")
    source_site = raw["source_site"]
    destination_site = raw["destination_site"]
    if (
        not isinstance(source_site, str)
        or not isinstance(destination_site, str)
        or source_site not in WEBAPP_SITES
        or destination_site not in WEBAPP_SITES
        or source_site == destination_site
    ):
        raise ObjectDeltaSourceCutoverAttestationError("source cutover sites are invalid")
    campaign_id = _text(raw["campaign_id"], label="source cutover campaign", pattern=CAMPAIGN_ID_RE)
    release_sha = _text(raw["release_sha"], label="source cutover release", pattern=RELEASE_SHA_RE)
    stream_generation_id = _text(
        raw["stream_generation_id"],
        label="source cutover stream generation",
        pattern=STREAM_GENERATION_ID_RE,
    )
    if raw["state"] != OBJECT_DELTA_SOURCE_CUTOVER_BASELINE_PUBLISHED_STATE:
        raise ObjectDeltaSourceCutoverAttestationError("source cutover state is not baseline published")
    registry_fingerprint = _text(
        raw["registry_fingerprint"],
        label="source cutover registry fingerprint",
        pattern=REGISTRY_FINGERPRINT_RE,
    )
    writer_term = _exact_mapping(
        raw["writer_term"], fields=_WRITER_TERM_FIELDS, label="source cutover writer term"
    )
    writer_epoch = _positive_int(writer_term["epoch"], label="source cutover writer epoch")
    writer_lease_id = _text(
        writer_term["lease_id"], label="source cutover writer lease", pattern=LEASE_ID_RE
    )
    write_gate_id = _canonical_uuid(raw["write_gate_id"], label="source cutover write gate id")
    snapshot = _exact_mapping(raw["snapshot"], fields=_SNAPSHOT_FIELDS, label="source cutover snapshot")
    source_generation = _text(
        snapshot["source_generation"],
        label="source cutover snapshot source generation",
        pattern=_SOURCE_GENERATION_RE,
    )
    snapshot_id = _text(
        snapshot["snapshot_id"], label="source cutover snapshot id", pattern=_SNAPSHOT_ID_RE
    )
    alembic_revision = _text(
        snapshot["alembic_revision"],
        label="source cutover snapshot alembic revision",
        pattern=_ALEMBIC_REVISION_RE,
    )
    snapshot_manifest_object_key = _text(
        snapshot["manifest_object_key"],
        label="source cutover snapshot manifest object key",
        pattern=OBJECT_KEY_RE,
    )
    snapshot_manifest_object_version_id = _text(
        snapshot["manifest_object_version_id"],
        label="source cutover snapshot manifest object version",
        pattern=VERSION_ID_RE,
    )
    snapshot_manifest_ciphertext_sha256 = _text(
        snapshot["manifest_ciphertext_sha256"],
        label="source cutover snapshot manifest ciphertext hash",
        pattern=SHA256_RE,
    )
    snapshot_manifest_ciphertext_bytes = _positive_int(
        snapshot["manifest_ciphertext_bytes"],
        label="source cutover snapshot manifest ciphertext bytes",
    )
    database_sha256 = _text(
        snapshot["database_sha256"], label="source cutover database hash", pattern=SHA256_RE
    )
    uploads_sha256 = _text(
        snapshot["uploads_sha256"], label="source cutover uploads hash", pattern=SHA256_RE
    )
    baseline_receipt = _exact_mapping(
        raw["baseline_receipt"],
        fields=_BASELINE_RECEIPT_FIELDS,
        label="source cutover baseline receipt",
    )
    baseline_manifest_object_key = _text(
        baseline_receipt["manifest_object_key"],
        label="source cutover baseline manifest object key",
        pattern=OBJECT_KEY_RE,
    )
    baseline_manifest_object_version_id = _text(
        baseline_receipt["manifest_object_version_id"],
        label="source cutover baseline manifest object version",
        pattern=VERSION_ID_RE,
    )
    baseline_manifest_ciphertext_sha256 = _text(
        baseline_receipt["manifest_ciphertext_sha256"],
        label="source cutover baseline manifest ciphertext hash",
        pattern=SHA256_RE,
    )
    baseline_manifest_ciphertext_bytes = _positive_int(
        baseline_receipt["manifest_ciphertext_bytes"],
        label="source cutover baseline manifest ciphertext bytes",
    )
    baseline_manifest_sha256 = _text(
        baseline_receipt["manifest_sha256"],
        label="source cutover baseline manifest plaintext hash",
        pattern=SHA256_RE,
    )
    record = ObjectDeltaSourceCutoverRecord(
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=campaign_id,
        release_sha=release_sha,
        stream_generation_id=stream_generation_id,
        state=OBJECT_DELTA_SOURCE_CUTOVER_BASELINE_PUBLISHED_STATE,
        registry_fingerprint=registry_fingerprint,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
        write_gate_id=write_gate_id,
        source_generation=source_generation,
        snapshot_id=snapshot_id,
        alembic_revision=alembic_revision,
        snapshot_manifest_object_key=snapshot_manifest_object_key,
        snapshot_manifest_object_version_id=snapshot_manifest_object_version_id,
        snapshot_manifest_ciphertext_sha256=snapshot_manifest_ciphertext_sha256,
        snapshot_manifest_ciphertext_bytes=snapshot_manifest_ciphertext_bytes,
        database_sha256=database_sha256,
        uploads_sha256=uploads_sha256,
        baseline_manifest_object_key=baseline_manifest_object_key,
        baseline_manifest_object_version_id=baseline_manifest_object_version_id,
        baseline_manifest_ciphertext_sha256=baseline_manifest_ciphertext_sha256,
        baseline_manifest_ciphertext_bytes=baseline_manifest_ciphertext_bytes,
    )
    normalized = _record_mapping(record)
    normalized["baseline_receipt"]["manifest_sha256"] = baseline_manifest_sha256
    return normalized, record, baseline_manifest_sha256


def _validated_record(record: ObjectDeltaSourceCutoverRecord) -> tuple[dict[str, Any], ObjectDeltaSourceCutoverRecord]:
    raw = _record_mapping(record)
    normalized, parsed, _placeholder_hash = _record_from_mapping(raw)
    return normalized, parsed


def _parse_signer(value: object) -> tuple[dict[str, Any], bytes, str]:
    signer = _exact_mapping(value, fields=_SIGNER_FIELDS, label="source cutover source signer")
    if signer["algorithm"] != OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SIGNATURE_ALGORITHM:
        raise ObjectDeltaSourceCutoverAttestationError("source cutover source signer is invalid")
    public_key = _require_public_key(
        _decode_base64(
            signer["public_key_base64"],
            label="source cutover source public key",
            expected_bytes=32,
        ),
        label="source cutover source public key",
    )
    key_id = _text(signer["key_id"], label="source cutover source key id", pattern=_KEY_ID_RE)
    if key_id != _source_key_id(public_key):
        raise ObjectDeltaSourceCutoverAttestationError(
            "source cutover source key ID does not match its public key"
        )
    return (
        {
            "algorithm": OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SIGNATURE_ALGORITHM,
            "public_key_base64": base64.b64encode(public_key).decode("ascii"),
            "key_id": key_id,
        },
        public_key,
        key_id,
    )


def _parse_signature(value: object) -> tuple[dict[str, Any], bytes]:
    signature = _exact_mapping(value, fields=_SIGNATURE_FIELDS, label="source cutover signature")
    if signature["algorithm"] != OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SIGNATURE_ALGORITHM:
        raise ObjectDeltaSourceCutoverAttestationError("source cutover signature is invalid")
    raw = _decode_base64(
        signature["signature_base64"],
        label="source cutover signature",
        expected_bytes=64,
    )
    return (
        {
            "algorithm": OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SIGNATURE_ALGORITHM,
            "signature_base64": base64.b64encode(raw).decode("ascii"),
        },
        raw,
    )


def _verified_baseline_for_record(
    baseline_manifest: object,
    *,
    source_public_key: bytes,
    record: ObjectDeltaSourceCutoverRecord,
) -> tuple[dict[str, Any], VerifiedObjectDeltaBaselineManifest]:
    if not isinstance(baseline_manifest, Mapping):
        raise ObjectDeltaSourceCutoverAttestationError("source cutover baseline manifest is invalid")
    try:
        # Round-trip via the baseline's public canonical parser so the outer
        # envelope cannot preserve a semantically valid but non-normalized
        # nested manifest representation.
        normalized_mapping = parse_object_delta_baseline_manifest_json(
            canonical_json_bytes(dict(baseline_manifest))
        )
        baseline = verify_object_delta_baseline_manifest(
            normalized_mapping,
            expected_source_public_key=source_public_key,
            expected_source_site=record.source_site,
            expected_destination_site=record.destination_site,
            expected_campaign_id=record.campaign_id,
            expected_release_sha=record.release_sha,
            expected_stream_generation_id=record.stream_generation_id,
            expected_registry_fingerprint=record.registry_fingerprint,
        )
    except (ObjectDeltaBaselineManifestError, ValueError, TypeError) as exc:
        raise ObjectDeltaSourceCutoverAttestationError(
            "source cutover baseline manifest is invalid or not source-pinned"
        ) from exc
    expected = (
        ("writer epoch", record.writer_epoch, baseline.writer_epoch),
        ("writer lease", record.writer_lease_id, baseline.writer_lease_id),
        ("write gate id", record.write_gate_id, baseline.write_gate_id),
        ("snapshot source generation", record.source_generation, baseline.source_generation),
        ("snapshot id", record.snapshot_id, baseline.snapshot_id),
        ("snapshot alembic revision", record.alembic_revision, baseline.alembic_revision),
        (
            "snapshot manifest object key",
            record.snapshot_manifest_object_key,
            baseline.manifest_object_key,
        ),
        (
            "snapshot manifest object version",
            record.snapshot_manifest_object_version_id,
            baseline.manifest_object_version_id,
        ),
        (
            "snapshot manifest ciphertext hash",
            record.snapshot_manifest_ciphertext_sha256,
            baseline.manifest_ciphertext_sha256,
        ),
        (
            "snapshot manifest ciphertext bytes",
            record.snapshot_manifest_ciphertext_bytes,
            baseline.manifest_ciphertext_bytes,
        ),
        ("database hash", record.database_sha256, baseline.database_sha256),
        ("uploads hash", record.uploads_sha256, baseline.uploads_sha256),
    )
    for label, record_value, baseline_value in expected:
        if record_value != baseline_value:
            raise ObjectDeltaSourceCutoverAttestationError(
                f"source cutover does not match baseline {label}"
            )
    return normalized_mapping, baseline


def _parse_unsigned(
    value: object,
) -> tuple[
    dict[str, Any],
    ObjectDeltaSourceCutoverRecord,
    str,
    VerifiedObjectDeltaBaselineManifest,
    bytes,
    str,
]:
    raw = _exact_mapping(value, fields=_OUTER_FIELDS, label="source cutover attestation")
    if (
        raw["schema"] != OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SCHEMA
        or raw["status"] != OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_STATUS
    ):
        raise ObjectDeltaSourceCutoverAttestationError(
            "source cutover attestation schema or status is invalid"
        )
    cutover_mapping, record, baseline_manifest_sha256 = _record_from_mapping(raw["cutover"])
    signer_mapping, public_key, key_id = _parse_signer(raw["source_signer"])
    baseline_mapping, baseline = _verified_baseline_for_record(
        raw["baseline_manifest"],
        source_public_key=public_key,
        record=record,
    )
    if baseline.manifest_sha256 != baseline_manifest_sha256:
        raise ObjectDeltaSourceCutoverAttestationError(
            "source cutover baseline manifest hash does not match the signed baseline"
        )
    return (
        {
            "schema": OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SCHEMA,
            "status": OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_STATUS,
            "cutover": cutover_mapping,
            "baseline_manifest": baseline_mapping,
            "source_signer": signer_mapping,
        },
        record,
        baseline_manifest_sha256,
        baseline,
        public_key,
        key_id,
    )


def _parse_sealed(
    value: object,
) -> tuple[
    dict[str, Any],
    ObjectDeltaSourceCutoverRecord,
    str,
    VerifiedObjectDeltaBaselineManifest,
    bytes,
    str,
    bytes,
]:
    raw = _exact_mapping(value, fields=_SEALED_FIELDS, label="sealed source cutover attestation")
    unsigned = {key: item for key, item in raw.items() if key != "source_signature"}
    normalized, record, baseline_hash, baseline, public_key, key_id = _parse_unsigned(unsigned)
    signature_mapping, signature = _parse_signature(raw["source_signature"])
    return (
        {**normalized, "source_signature": signature_mapping},
        record,
        baseline_hash,
        baseline,
        public_key,
        key_id,
        signature,
    )


def unsigned_object_delta_source_cutover_attestation_payload(
    attestation: Mapping[str, Any],
) -> bytes:
    """Return the exact domain-separated bytes signed by the source key."""

    normalized, *_ = _parse_unsigned(attestation)
    return OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SIGNATURE_DOMAIN + canonical_json_bytes(normalized)


def build_object_delta_source_cutover_attestation(
    *,
    cutover: ObjectDeltaSourceCutoverRecord,
    baseline_manifest: Mapping[str, Any],
    source_signer: object,
) -> dict[str, Any]:
    """Sign one committed source-cutover record in memory only.

    The caller must provide a source record loaded from one committed
    ``baseline_published`` transaction and the exact signed baseline plaintext
    whose immutable ciphertext receipt is recorded there.  This function
    neither reads that row nor writes/publishes the resulting evidence.
    """

    _placeholder_mapping, record = _validated_record(cutover)
    public_key = _public_key_from_signer(source_signer)
    normalized_baseline, baseline = _verified_baseline_for_record(
        baseline_manifest,
        source_public_key=public_key,
        record=record,
    )
    cutover_mapping = _record_mapping(record)
    cutover_mapping["baseline_receipt"]["manifest_sha256"] = baseline.manifest_sha256
    unsigned: dict[str, Any] = {
        "schema": OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SCHEMA,
        "status": OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_STATUS,
        "cutover": cutover_mapping,
        "baseline_manifest": normalized_baseline,
        "source_signer": {
            "algorithm": OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SIGNATURE_ALGORITHM,
            "public_key_base64": base64.b64encode(public_key).decode("ascii"),
            "key_id": _source_key_id(public_key),
        },
    }
    normalized_unsigned, *_ = _parse_unsigned(unsigned)
    try:
        signature = source_signer.sign(
            unsigned_object_delta_source_cutover_attestation_payload(normalized_unsigned)
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaSourceCutoverAttestationError(
            "source signer cannot sign source cutover attestation"
        ) from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise ObjectDeltaSourceCutoverAttestationError(
            "source signer produced an invalid source cutover signature"
        )
    sealed = {
        **normalized_unsigned,
        "source_signature": {
            "algorithm": OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_SIGNATURE_ALGORITHM,
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        },
    }
    normalized, *_ = _parse_sealed(sealed)
    return normalized


def _mapping_from_raw_attestation(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, (bytes, str)):
        return parse_object_delta_source_cutover_attestation_json(value)
    raise ObjectDeltaSourceCutoverAttestationError("source cutover attestation is invalid")


def verify_object_delta_source_cutover_attestation(
    attestation: Mapping[str, Any] | bytes | str,
    *,
    expected_source_public_key: bytes,
    expected_source_site: str,
    expected_destination_site: str,
    expected_campaign_id: str,
    expected_release_sha: str,
    expected_stream_generation_id: str,
    expected_registry_fingerprint: str,
) -> VerifiedObjectDeltaSourceCutoverAttestation:
    """Verify canonical source evidence against receiver-local pins.

    Raw bytes are accepted only through the strict canonical parser.  Mapping
    input is useful after a trusted decryptor has already parsed an envelope,
    but it still receives complete nested signature and binding verification.
    No raw dataclass is accepted as evidence.
    """

    expected_key = _require_public_key(
        expected_source_public_key,
        label="expected source public key",
    )
    normalized, record, baseline_hash, baseline, actual_key, key_id, signature = _parse_sealed(
        _mapping_from_raw_attestation(attestation)
    )
    if actual_key != expected_key or key_id != _source_key_id(expected_key):
        raise ObjectDeltaSourceCutoverAttestationError(
            "source cutover source signer is not pinned"
        )
    expected_values = (
        (expected_source_site, record.source_site, "source site"),
        (expected_destination_site, record.destination_site, "destination site"),
        (expected_campaign_id, record.campaign_id, "campaign"),
        (expected_release_sha, record.release_sha, "release"),
        (expected_stream_generation_id, record.stream_generation_id, "stream generation"),
        (expected_registry_fingerprint, record.registry_fingerprint, "registry fingerprint"),
    )
    for expected, actual, label in expected_values:
        if not isinstance(expected, str) or expected != actual:
            raise ObjectDeltaSourceCutoverAttestationError(
                f"source cutover {label} does not match receiver binding"
            )
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise ObjectDeltaSourceCutoverAttestationError(
            "source cutover signature verification is unavailable"
        ) from exc
    unsigned = {key: item for key, item in normalized.items() if key != "source_signature"}
    try:
        Ed25519PublicKey.from_public_bytes(expected_key).verify(
            signature,
            unsigned_object_delta_source_cutover_attestation_payload(unsigned),
        )
    except (InvalidSignature, ValueError) as exc:
        raise ObjectDeltaSourceCutoverAttestationError(
            "source cutover signature verification failed"
        ) from exc
    return VerifiedObjectDeltaSourceCutoverAttestation(
        source_site=record.source_site,
        destination_site=record.destination_site,
        campaign_id=record.campaign_id,
        release_sha=record.release_sha,
        stream_generation_id=record.stream_generation_id,
        registry_fingerprint=record.registry_fingerprint,
        writer_epoch=record.writer_epoch,
        writer_lease_id=record.writer_lease_id,
        write_gate_id=record.write_gate_id,
        source_generation=record.source_generation,
        snapshot_id=record.snapshot_id,
        alembic_revision=record.alembic_revision,
        snapshot_manifest_object_key=record.snapshot_manifest_object_key,
        snapshot_manifest_object_version_id=record.snapshot_manifest_object_version_id,
        snapshot_manifest_ciphertext_sha256=record.snapshot_manifest_ciphertext_sha256,
        snapshot_manifest_ciphertext_bytes=record.snapshot_manifest_ciphertext_bytes,
        database_sha256=record.database_sha256,
        uploads_sha256=record.uploads_sha256,
        baseline_manifest_object_key=record.baseline_manifest_object_key,
        baseline_manifest_object_version_id=record.baseline_manifest_object_version_id,
        baseline_manifest_ciphertext_sha256=record.baseline_manifest_ciphertext_sha256,
        baseline_manifest_ciphertext_bytes=record.baseline_manifest_ciphertext_bytes,
        baseline_manifest_sha256=baseline_hash,
        source_key_id=key_id,
        attestation_sha256=hashlib.sha256(canonical_json_bytes(normalized)).hexdigest(),
        baseline=baseline,
    )


def canonical_object_delta_source_cutover_attestation_bytes(
    attestation: Mapping[str, Any],
) -> bytes:
    """Return canonical, newline-terminated sealed evidence bytes.

    This validates the nested source baseline and structural bindings but does
    not choose a receiver-local source key pin.  Call
    :func:`verify_object_delta_source_cutover_attestation` before treating the
    result as trusted evidence.
    """

    normalized, *_ = _parse_sealed(attestation)
    return canonical_json_bytes(normalized) + b"\n"


def parse_object_delta_source_cutover_attestation_json(raw: bytes | str) -> dict[str, Any]:
    """Parse exactly one canonical sealed source-cutover evidence envelope."""

    if isinstance(raw, bytes):
        payload = raw
    elif isinstance(raw, str):
        try:
            payload = raw.encode("utf-8", "strict")
        except UnicodeEncodeError as exc:
            raise ObjectDeltaSourceCutoverAttestationError(
                "source cutover attestation JSON is invalid"
            ) from exc
    else:
        raise ObjectDeltaSourceCutoverAttestationError("source cutover attestation JSON is invalid")
    if not payload or len(payload) > MAX_OBJECT_DELTA_SOURCE_CUTOVER_ATTESTATION_BYTES:
        raise ObjectDeltaSourceCutoverAttestationError(
            "source cutover attestation JSON size is invalid"
        )
    try:
        value = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except ObjectDeltaSourceCutoverAttestationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError) as exc:
        raise ObjectDeltaSourceCutoverAttestationError(
            "source cutover attestation JSON is invalid"
        ) from exc
    try:
        normalized, *_ = _parse_sealed(value)
        canonical = canonical_json_bytes(normalized) + b"\n"
    except RecursionError as exc:
        raise ObjectDeltaSourceCutoverAttestationError(
            "source cutover attestation JSON is invalid"
        ) from exc
    if payload != canonical:
        raise ObjectDeltaSourceCutoverAttestationError(
            "source cutover attestation JSON is not canonical"
        )
    return normalized
