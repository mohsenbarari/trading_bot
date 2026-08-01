"""Low-level, pure source authentication primitive for one Object-delta batch.

The existing append-only batch descriptor is deliberately self-hashed, but a
self-hash does not identify who produced its contents.  This module adds a
separate Ed25519 envelope that a future source publisher may create only after
it has exact-VersionId read-back evidence.  The envelope signs all batch
metadata plus the normalized fixed transport policy and binding, including the
bucket, destination recipient, ciphertext digest, and immutable version.

It performs no filesystem, database, Object Storage, age, network, or runtime
configuration work.  It also does not establish a signed baseline cutover:
the standard source delivery path must first pass a prepared candidate through
``object_delta_source_cutover_publication_gate`` and use its gated builder.
This standalone primitive remains available for isolated cryptographic tests;
a valid envelope returned here is not delivery authorization by itself.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from core.append_only_sync_delta_batch import (
    DELTA_BATCH_FIELDS,
    DELTA_BATCH_SCHEMA,
    DELTA_BATCH_STATUS,
    DELTA_OBJECT_KIND,
    IMMUTABLE_RECEIPT_SCHEMA,
    IMMUTABLE_RECEIPT_STATUS,
    IMPORT_MODE_VALIDATE_ONLY,
    MAX_BATCH_BYTES,
    AppendOnlySyncDeltaBatch,
    canonical_json_bytes,
    validate_delta_batch,
)
from core.object_delta_transport_binding import (
    CONTROLLER_CREDENTIAL_HOLDER,
    OBJECT_DELTA_ENCRYPTION,
    OBJECT_DELTA_TRANSPORT_SCHEMA,
    ObjectDeltaTransportBinding,
    ObjectDeltaTransportBindingError,
    ObjectDeltaTransportPolicy,
    bind_object_delta_batch,
    validate_object_delta_transport_policy,
)


OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SCHEMA = "gold-trade-object-delta-source-batch-attestation-v1"
OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_STATUS = "sealed"
OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SIGNATURE_ALGORITHM = "ed25519"
OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SIGNATURE_DOMAIN = (
    b"gold-trade-object-delta-source-batch-attestation-v1\x00"
)
MAX_OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_BYTES = MAX_BATCH_BYTES + 64 * 1024

_KEY_ID_PREFIX = "ed25519-sha256:"
_OUTER_FIELDS = frozenset(
    {
        "schema",
        "status",
        "batch",
        "transport_policy",
        "transport_binding",
        "source_signer",
    }
)
_SEALED_FIELDS = _OUTER_FIELDS | frozenset({"source_signature"})
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_POLICY_FIELDS = frozenset(
    {
        "transport_schema",
        "encryption",
        "bucket",
        "prefix",
        "webapp_fi_age_recipient",
        "webapp_ir_age_recipient",
        "credential_holder",
    }
)
_BINDING_FIELDS = frozenset(
    {
        "source_site",
        "destination_site",
        "destination_age_recipient",
        "object_key",
        "stream_generation_id",
        "first_sequence",
        "last_sequence",
        "payload_sha256",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "object_version_id",
    }
)


class ObjectDeltaSourceBatchAttestationError(ValueError):
    """A per-batch source attestation is malformed, unbound, or unauthentic."""


@dataclass(frozen=True)
class VerifiedObjectDeltaSourceBatchAttestation:
    """A source-pinned, exact transport claim for one immutable batch Object."""

    batch: AppendOnlySyncDeltaBatch
    transport_policy: ObjectDeltaTransportPolicy
    transport_binding: ObjectDeltaTransportBinding
    source_public_key: bytes
    source_key_id: str
    attestation_sha256: str


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ObjectDeltaSourceBatchAttestationError(
                "source batch attestation contains duplicate JSON fields"
            )
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ObjectDeltaSourceBatchAttestationError(
        f"source batch attestation JSON constant is forbidden: {value}"
    )


def _exact_mapping(value: object, *, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ObjectDeltaSourceBatchAttestationError(f"{label} fields are invalid")
    return dict(value)


def _decode_base64(value: object, *, label: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str):
        raise ObjectDeltaSourceBatchAttestationError(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ObjectDeltaSourceBatchAttestationError(f"{label} is invalid") from exc
    if len(decoded) != expected_bytes:
        raise ObjectDeltaSourceBatchAttestationError(f"{label} is invalid")
    return decoded


def _require_public_key(value: object, *, label: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        raise ObjectDeltaSourceBatchAttestationError(f"{label} is invalid")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError) as exc:
        raise ObjectDeltaSourceBatchAttestationError(f"{label} is invalid") from exc
    return value


def source_key_id_from_public_key(source_public_key: bytes) -> str:
    """Return the non-secret, stable identifier for a pinned source key."""

    key = _require_public_key(source_public_key, label="source public key")
    return _KEY_ID_PREFIX + hashlib.sha256(key).hexdigest()


def _public_key_from_signer(source_signer: object) -> bytes:
    try:
        from cryptography.hazmat.primitives import serialization

        public_key = source_signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (AttributeError, ImportError, TypeError, ValueError) as exc:
        raise ObjectDeltaSourceBatchAttestationError("source signer is invalid") from exc
    return _require_public_key(public_key, label="source signer public key")


def _batch_mapping(batch: AppendOnlySyncDeltaBatch) -> dict[str, Any]:
    if not isinstance(batch, AppendOnlySyncDeltaBatch):
        raise ObjectDeltaSourceBatchAttestationError("validated Object-delta batch is required")
    try:
        return {
            "schema": DELTA_BATCH_SCHEMA,
            "status": DELTA_BATCH_STATUS,
            "source_site": batch.source_site,
            "destination_site": batch.destination_site,
            "campaign_id": batch.campaign_id,
            "release_sha": batch.release_sha,
            "writer_term": {
                "epoch": batch.writer_term.epoch,
                "lease_id": batch.writer_term.lease_id,
            },
            "stream": {
                "generation_id": batch.stream.generation_id,
                "first_sequence": batch.stream.first_sequence,
                "last_sequence": batch.stream.last_sequence,
                "sequence_ids": list(batch.stream.sequence_ids),
            },
            "payload": {
                "sha256": batch.payload_sha256,
                "bytes": batch.payload_bytes,
            },
            "prior_chain_sha256": batch.prior_chain_sha256,
            "import_intent": {
                "mode": IMPORT_MODE_VALIDATE_ONLY,
                "side_effects_disabled": True,
            },
            "immutable_receipt": {
                "schema": IMMUTABLE_RECEIPT_SCHEMA,
                "status": IMMUTABLE_RECEIPT_STATUS,
                "object_kind": DELTA_OBJECT_KIND,
                "object_key": batch.immutable_receipt.object_key,
                "version_id": batch.immutable_receipt.version_id,
                "ciphertext_sha256": batch.immutable_receipt.ciphertext_sha256,
                "ciphertext_bytes": batch.immutable_receipt.ciphertext_bytes,
            },
            "batch_sha256": batch.batch_sha256,
        }
    except AttributeError as exc:
        raise ObjectDeltaSourceBatchAttestationError("validated Object-delta batch is required") from exc


def _validated_batch_instance(batch: AppendOnlySyncDeltaBatch) -> AppendOnlySyncDeltaBatch:
    raw = _batch_mapping(batch)
    try:
        normalized = validate_delta_batch(raw)
    except ValueError as exc:
        raise ObjectDeltaSourceBatchAttestationError("Object-delta batch is invalid") from exc
    if normalized != batch:
        raise ObjectDeltaSourceBatchAttestationError("Object-delta batch is not normalized")
    return normalized


def _batch_from_mapping(value: object) -> tuple[dict[str, Any], AppendOnlySyncDeltaBatch]:
    raw = _exact_mapping(value, fields=DELTA_BATCH_FIELDS, label="source batch attestation batch")
    try:
        batch = validate_delta_batch(raw)
    except ValueError as exc:
        raise ObjectDeltaSourceBatchAttestationError("source batch attestation batch is invalid") from exc
    normalized = _batch_mapping(batch)
    if raw != normalized:
        raise ObjectDeltaSourceBatchAttestationError("source batch attestation batch is not normalized")
    return normalized, batch


def _policy_mapping(policy: ObjectDeltaTransportPolicy) -> dict[str, Any]:
    try:
        normalized = validate_object_delta_transport_policy(policy)
    except ObjectDeltaTransportBindingError as exc:
        raise ObjectDeltaSourceBatchAttestationError("Object-delta transport policy is invalid") from exc
    return {
        "transport_schema": OBJECT_DELTA_TRANSPORT_SCHEMA,
        "encryption": OBJECT_DELTA_ENCRYPTION,
        "bucket": normalized.bucket,
        "prefix": normalized.prefix,
        "webapp_fi_age_recipient": normalized.webapp_fi_age_recipient,
        "webapp_ir_age_recipient": normalized.webapp_ir_age_recipient,
        "credential_holder": CONTROLLER_CREDENTIAL_HOLDER,
    }


def _policy_from_mapping(value: object) -> tuple[dict[str, Any], ObjectDeltaTransportPolicy]:
    raw = _exact_mapping(value, fields=_POLICY_FIELDS, label="source batch attestation transport policy")
    if (
        raw["transport_schema"] != OBJECT_DELTA_TRANSPORT_SCHEMA
        or raw["encryption"] != OBJECT_DELTA_ENCRYPTION
        or raw["credential_holder"] != CONTROLLER_CREDENTIAL_HOLDER
    ):
        raise ObjectDeltaSourceBatchAttestationError(
            "source batch attestation transport policy protocol is invalid"
        )
    try:
        policy = validate_object_delta_transport_policy(
            ObjectDeltaTransportPolicy(
                bucket=raw["bucket"],
                prefix=raw["prefix"],
                webapp_fi_age_recipient=raw["webapp_fi_age_recipient"],
                webapp_ir_age_recipient=raw["webapp_ir_age_recipient"],
                credential_holder=raw["credential_holder"],
            )
        )
    except (ObjectDeltaTransportBindingError, TypeError) as exc:
        raise ObjectDeltaSourceBatchAttestationError(
            "source batch attestation transport policy is invalid"
        ) from exc
    normalized = _policy_mapping(policy)
    if raw != normalized:
        raise ObjectDeltaSourceBatchAttestationError(
            "source batch attestation transport policy is not normalized"
        )
    return normalized, policy


def _binding_mapping(binding: ObjectDeltaTransportBinding) -> dict[str, Any]:
    if not isinstance(binding, ObjectDeltaTransportBinding):
        raise ObjectDeltaSourceBatchAttestationError("Object-delta transport binding is invalid")
    return {
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "destination_age_recipient": binding.destination_age_recipient,
        "object_key": binding.object_key,
        "stream_generation_id": binding.stream_generation_id,
        "first_sequence": binding.first_sequence,
        "last_sequence": binding.last_sequence,
        "payload_sha256": binding.payload_sha256,
        "ciphertext_sha256": binding.ciphertext_sha256,
        "ciphertext_bytes": binding.ciphertext_bytes,
        "object_version_id": binding.object_version_id,
    }


def _validated_input(
    *,
    batch: AppendOnlySyncDeltaBatch,
    transport_policy: ObjectDeltaTransportPolicy,
    transport_binding: ObjectDeltaTransportBinding,
) -> tuple[AppendOnlySyncDeltaBatch, ObjectDeltaTransportPolicy, ObjectDeltaTransportBinding]:
    normalized_batch = _validated_batch_instance(batch)
    policy_mapping = _policy_mapping(transport_policy)
    _normalized_policy_mapping, normalized_policy = _policy_from_mapping(policy_mapping)
    try:
        expected_binding = bind_object_delta_batch(normalized_policy, normalized_batch)
    except ObjectDeltaTransportBindingError as exc:
        raise ObjectDeltaSourceBatchAttestationError("Object-delta transport binding is invalid") from exc
    if not isinstance(transport_binding, ObjectDeltaTransportBinding) or transport_binding != expected_binding:
        raise ObjectDeltaSourceBatchAttestationError(
            "Object-delta transport binding does not match the batch and policy"
        )
    return normalized_batch, normalized_policy, expected_binding


def _parse_signer(value: object) -> tuple[dict[str, Any], bytes, str]:
    raw = _exact_mapping(value, fields=_SIGNER_FIELDS, label="source batch attestation source signer")
    if raw["algorithm"] != OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SIGNATURE_ALGORITHM:
        raise ObjectDeltaSourceBatchAttestationError("source batch attestation source signer is invalid")
    public_key = _require_public_key(
        _decode_base64(
            raw["public_key_base64"],
            label="source batch attestation source public key",
            expected_bytes=32,
        ),
        label="source batch attestation source public key",
    )
    key_id = raw["key_id"]
    if not isinstance(key_id, str) or key_id != source_key_id_from_public_key(public_key):
        raise ObjectDeltaSourceBatchAttestationError(
            "source batch attestation source key ID does not match its public key"
        )
    return {
        "algorithm": OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SIGNATURE_ALGORITHM,
        "public_key_base64": base64.b64encode(public_key).decode("ascii"),
        "key_id": key_id,
    }, public_key, key_id


def _parse_signature(value: object) -> tuple[dict[str, Any], bytes]:
    raw = _exact_mapping(value, fields=_SIGNATURE_FIELDS, label="source batch attestation signature")
    if raw["algorithm"] != OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SIGNATURE_ALGORITHM:
        raise ObjectDeltaSourceBatchAttestationError("source batch attestation signature is invalid")
    signature = _decode_base64(
        raw["signature_base64"],
        label="source batch attestation signature",
        expected_bytes=64,
    )
    return {
        "algorithm": OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SIGNATURE_ALGORITHM,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }, signature


def _parse_unsigned(
    value: object,
) -> tuple[
    dict[str, Any],
    AppendOnlySyncDeltaBatch,
    ObjectDeltaTransportPolicy,
    ObjectDeltaTransportBinding,
    bytes,
    str,
]:
    raw = _exact_mapping(value, fields=_OUTER_FIELDS, label="source batch attestation")
    if (
        raw["schema"] != OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SCHEMA
        or raw["status"] != OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_STATUS
    ):
        raise ObjectDeltaSourceBatchAttestationError("source batch attestation schema or status is invalid")
    batch_mapping, batch = _batch_from_mapping(raw["batch"])
    policy_mapping, policy = _policy_from_mapping(raw["transport_policy"])
    binding_mapping = _exact_mapping(
        raw["transport_binding"],
        fields=_BINDING_FIELDS,
        label="source batch attestation transport binding",
    )
    try:
        expected_binding = bind_object_delta_batch(policy, batch)
    except ObjectDeltaTransportBindingError as exc:
        raise ObjectDeltaSourceBatchAttestationError(
            "source batch attestation transport binding is invalid"
        ) from exc
    expected_binding_mapping = _binding_mapping(expected_binding)
    if binding_mapping != expected_binding_mapping:
        raise ObjectDeltaSourceBatchAttestationError(
            "source batch attestation transport binding does not match the batch and policy"
        )
    signer_mapping, public_key, key_id = _parse_signer(raw["source_signer"])
    return (
        {
            "schema": OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SCHEMA,
            "status": OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_STATUS,
            "batch": batch_mapping,
            "transport_policy": policy_mapping,
            "transport_binding": expected_binding_mapping,
            "source_signer": signer_mapping,
        },
        batch,
        policy,
        expected_binding,
        public_key,
        key_id,
    )


def _parse_sealed(
    value: object,
) -> tuple[
    dict[str, Any],
    AppendOnlySyncDeltaBatch,
    ObjectDeltaTransportPolicy,
    ObjectDeltaTransportBinding,
    bytes,
    str,
    bytes,
]:
    raw = _exact_mapping(value, fields=_SEALED_FIELDS, label="sealed source batch attestation")
    unsigned = {key: item for key, item in raw.items() if key != "source_signature"}
    normalized, batch, policy, binding, public_key, key_id = _parse_unsigned(unsigned)
    signature_mapping, signature = _parse_signature(raw["source_signature"])
    return (
        {**normalized, "source_signature": signature_mapping},
        batch,
        policy,
        binding,
        public_key,
        key_id,
        signature,
    )


def unsigned_object_delta_source_batch_attestation_payload(attestation: Mapping[str, Any]) -> bytes:
    """Return the exact domain-separated bytes signed by the source key."""

    normalized, *_ = _parse_unsigned(attestation)
    return OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SIGNATURE_DOMAIN + canonical_json_bytes(normalized)


def build_object_delta_source_batch_attestation(
    *,
    batch: AppendOnlySyncDeltaBatch,
    transport_policy: ObjectDeltaTransportPolicy,
    transport_binding: ObjectDeltaTransportBinding,
    source_signer: object,
) -> dict[str, Any]:
    """Sign one exact immutable Object-delta batch as a low-level primitive.

    The caller must create this only after its exact Object version and
    ciphertext receipt have been read back and bound into ``batch``.  The
    source private key is accepted only as an already-loaded signer object.
    This function does not verify source-cutover/baseline evidence; production
    publication code must call the cutover-gated builder instead.
    """

    normalized_batch, normalized_policy, normalized_binding = _validated_input(
        batch=batch,
        transport_policy=transport_policy,
        transport_binding=transport_binding,
    )
    public_key = _public_key_from_signer(source_signer)
    unsigned: dict[str, Any] = {
        "schema": OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SCHEMA,
        "status": OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_STATUS,
        "batch": _batch_mapping(normalized_batch),
        "transport_policy": _policy_mapping(normalized_policy),
        "transport_binding": _binding_mapping(normalized_binding),
        "source_signer": {
            "algorithm": OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SIGNATURE_ALGORITHM,
            "public_key_base64": base64.b64encode(public_key).decode("ascii"),
            "key_id": source_key_id_from_public_key(public_key),
        },
    }
    normalized_unsigned, *_ = _parse_unsigned(unsigned)
    try:
        signature = source_signer.sign(
            unsigned_object_delta_source_batch_attestation_payload(normalized_unsigned)
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaSourceBatchAttestationError(
            "source signer cannot sign Object-delta batch attestation"
        ) from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise ObjectDeltaSourceBatchAttestationError(
            "source signer produced an invalid Object-delta batch signature"
        )
    sealed = {
        **normalized_unsigned,
        "source_signature": {
            "algorithm": OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_SIGNATURE_ALGORITHM,
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        },
    }
    _parse_sealed(sealed)
    return sealed


def verify_object_delta_source_batch_attestation(
    attestation: Mapping[str, Any],
    *,
    expected_source_public_key: bytes,
    expected_transport_policy: ObjectDeltaTransportPolicy,
) -> VerifiedObjectDeltaSourceBatchAttestation:
    """Verify a source-pinned attestation before controller or receiver use.

    Both pins are mandatory.  The public key and transport policy embedded in
    the envelope are informative only and must exactly match locally supplied
    values before its signature is considered.
    """

    expected_key = _require_public_key(
        expected_source_public_key,
        label="expected source public key",
    )
    expected_policy_mapping = _policy_mapping(expected_transport_policy)
    _normalized_expected_policy_mapping, expected_policy = _policy_from_mapping(
        expected_policy_mapping
    )
    normalized, batch, policy, binding, actual_key, key_id, signature = _parse_sealed(attestation)
    if actual_key != expected_key or key_id != source_key_id_from_public_key(expected_key):
        raise ObjectDeltaSourceBatchAttestationError(
            "source batch attestation source signer is not pinned"
        )
    if policy != expected_policy:
        raise ObjectDeltaSourceBatchAttestationError(
            "source batch attestation transport policy does not match the local pin"
        )
    unsigned = {key: item for key, item in normalized.items() if key != "source_signature"}
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(expected_key).verify(
            signature,
            unsigned_object_delta_source_batch_attestation_payload(unsigned),
        )
    except ImportError as exc:
        raise ObjectDeltaSourceBatchAttestationError(
            "cryptography Ed25519 support is unavailable"
        ) from exc
    except (InvalidSignature, ValueError) as exc:
        raise ObjectDeltaSourceBatchAttestationError(
            "source batch attestation signature verification failed"
        ) from exc
    return VerifiedObjectDeltaSourceBatchAttestation(
        batch=batch,
        transport_policy=policy,
        transport_binding=binding,
        source_public_key=expected_key,
        source_key_id=key_id,
        attestation_sha256=hashlib.sha256(canonical_json_bytes(normalized)).hexdigest(),
    )


def canonical_object_delta_source_batch_attestation_bytes(attestation: Mapping[str, Any]) -> bytes:
    """Return canonical, newline-terminated sealed attestation bytes.

    This validates structure and exact batch/transport bindings but deliberately
    does not choose a source pin or verify the signature.  A receiver or
    controller must call :func:`verify_object_delta_source_batch_attestation`
    before trusting the returned contents.
    """

    normalized, *_ = _parse_sealed(attestation)
    return canonical_json_bytes(normalized) + b"\n"


def parse_object_delta_source_batch_attestation_json(raw: bytes | str) -> dict[str, Any]:
    """Parse exactly one canonical sealed envelope without authenticating it."""

    if isinstance(raw, bytes):
        payload = raw
    elif isinstance(raw, str):
        try:
            payload = raw.encode("utf-8", "strict")
        except UnicodeEncodeError as exc:
            raise ObjectDeltaSourceBatchAttestationError(
                "source batch attestation JSON is invalid"
            ) from exc
    else:
        raise ObjectDeltaSourceBatchAttestationError("source batch attestation JSON is invalid")
    if not payload or len(payload) > MAX_OBJECT_DELTA_SOURCE_BATCH_ATTESTATION_BYTES:
        raise ObjectDeltaSourceBatchAttestationError("source batch attestation JSON size is invalid")
    try:
        value = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except ObjectDeltaSourceBatchAttestationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError) as exc:
        raise ObjectDeltaSourceBatchAttestationError("source batch attestation JSON is invalid") from exc
    try:
        normalized, *_ = _parse_sealed(value)
        canonical = canonical_json_bytes(normalized) + b"\n"
    except RecursionError as exc:
        raise ObjectDeltaSourceBatchAttestationError("source batch attestation JSON is invalid") from exc
    if payload != canonical:
        raise ObjectDeltaSourceBatchAttestationError("source batch attestation JSON is not canonical")
    return normalized
