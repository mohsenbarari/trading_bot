"""Legacy pure contracts for a source batch after baseline cutover.

The low-level batch-preparation, source-attestation, and ledger primitives
intentionally remain independently testable.  None of them, by themselves,
proves that the source stream was created only after a signed
``baseline_published`` cutover.  This module historically supplied a
source-side delivery-facing boundary:

* a future root-only adapter loads an explicit immutable pin containing the
  release-bound stream, transport policy, and source public key;
* this module verifies raw canonical signed cutover evidence against that
  pin, including the nested signed baseline manifest;
* it accepts only provenance-minted ``PreparedObjectDeltaSourceBatch`` values
  whose canonical payload was checked under that exact stream/registry pin;
* it mints an opaque capability used to obtain the ledger candidate or create
  and verify the matching source batch attestation.

Those contracts cannot prove the mandatory transaction-scoped locked source
snapshot or a fresh live Writer Witness authority.  Every former public
authorization/attestation-minting entrypoint is therefore hard-disabled;
only explicitly named private test-contract helpers retain the mechanics.

The module deliberately has no database, filesystem, runtime-settings,
network, Object Storage, age, credential, or worker behaviour.  It is not a
crash-safe publication spool and does not replace the later transaction locks,
immutable read-back, or durable attestation-recording adapter.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.append_only_sync_delta_batch import (
    DELTA_BATCH_SCHEMA,
    DELTA_BATCH_STATUS,
    DELTA_OBJECT_KIND,
    IMMUTABLE_RECEIPT_SCHEMA,
    IMMUTABLE_RECEIPT_STATUS,
    IMPORT_MODE_VALIDATE_ONLY,
    AppendOnlySyncDeltaBatch,
    sha256_bytes,
    validate_delta_batch,
)
from core.legacy_source_publication_fence import (
    LegacyObjectDeltaSourcePublicationDisabledError,
    reject_legacy_object_delta_source_publication_runtime,
)
from core.object_delta_runtime_binding import (
    ObjectDeltaRuntimeBindingError,
    ObjectDeltaSourceRuntimeBinding,
)
from core.object_delta_source_batch_attestation import (
    ObjectDeltaSourceBatchAttestationError,
    VerifiedObjectDeltaSourceBatchAttestation,
    build_object_delta_source_batch_attestation,
    canonical_object_delta_source_batch_attestation_bytes,
    parse_object_delta_source_batch_attestation_json,
    source_key_id_from_public_key,
    verify_object_delta_source_batch_attestation,
)
from core.object_delta_source_batch_ledger import (
    SourceBatchLedgerEntry,
    SourceStreamIdentity,
)
from core.object_delta_source_batch_publication import (
    ObjectDeltaSourceBatchPublicationError,
    PreparedObjectDeltaSourceBatch,
    require_prepared_object_delta_source_batch_provenance,
)
from core.object_delta_source_cutover_attestation import (
    ObjectDeltaSourceCutoverAttestationError,
    VerifiedObjectDeltaSourceCutoverAttestation,
    canonical_object_delta_source_cutover_attestation_bytes,
    parse_object_delta_source_cutover_attestation_json,
    verify_object_delta_source_cutover_attestation,
)
from core.object_delta_transport_binding import (
    ObjectDeltaTransportBinding,
    ObjectDeltaTransportBindingError,
    ObjectDeltaTransportPolicy,
    bind_object_delta_batch,
    validate_object_delta_transport_policy,
)


OBJECT_DELTA_SOURCE_CUTOVER_PUBLICATION_GATE = (
    "gold-trade-object-delta-source-cutover-publication-gate-v1"
)


class ObjectDeltaSourceCutoverPublicationGateError(ValueError):
    """A source batch is not authorized by the pinned baseline cutover."""


_AUTHORIZED_SOURCE_BATCH_CAPABILITY = object()
_AUTHORIZED_SOURCE_ATTESTATION_CAPABILITY = object()


@dataclass(frozen=True)
class ObjectDeltaSourceCutoverPublicationPin:
    """Explicit root-controlled local trust input for one source stream.

    This is deliberately not inferred from a received attestation.  A future
    root-only adapter must load it from immutable release-controlled material;
    constructing this pure value does not itself authenticate a configuration
    file.  It has no default source key, transport policy, or registry value.
    """

    binding: ObjectDeltaSourceRuntimeBinding
    expected_source_public_key: bytes
    transport_policy: ObjectDeltaTransportPolicy

    def __post_init__(self) -> None:
        normalized_binding = _normalized_binding(self.binding)
        expected_key = _require_source_public_key(self.expected_source_public_key)
        normalized_policy = _normalized_transport_policy(self.transport_policy)
        object.__setattr__(self, "binding", normalized_binding)
        object.__setattr__(self, "expected_source_public_key", expected_key)
        object.__setattr__(self, "transport_policy", normalized_policy)


@dataclass(frozen=True)
class AuthorizedObjectDeltaSourceBatch:
    """Opaque cutover-authorized candidate for the standard source path.

    Public fields are diagnostic only.  Authority is the private capability,
    and every gated accessor re-verifies the canonical cutover evidence,
    provenance, stream, registry, writer term, baseline hash, policy, and
    exact prepared batch before returning anything useful to a publisher.
    """

    pin: ObjectDeltaSourceCutoverPublicationPin
    prepared: PreparedObjectDeltaSourceBatch
    source_cutover_attestation: bytes
    _capability: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class AuthorizedObjectDeltaSourceAttestation:
    """Opaque source-attestation capability required before ledger hand-off.

    This capability is minted only after raw canonical batch-attestation bytes
    have been reverified under the same cutover-authorized source key and
    transport policy.  A later adapter must persist its exact artifact before
    the ledger append, but cannot obtain the ledger candidate from the gate
    without first proving this signed envelope exists.
    """

    batch_authorization: AuthorizedObjectDeltaSourceBatch
    canonical_attestation_bytes: bytes
    source_key_id: str
    batch_sha256: str
    source_attestation_artifact_sha256: str
    source_attestation_artifact_bytes: int
    _capability: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class AuthorizedObjectDeltaSourceBatchAttestationArtifact:
    """Exact canonical source-attestation artifact for durable hand-off.

    The cryptographic envelope's ``attestation_sha256`` hashes canonical JSON
    *without* its final newline.  Durable state must instead record the exact
    newline-terminated artifact bytes that a later sender/controller will
    store or transmit.  This data object is not authority by itself; it is
    projected only from an opaque verified source-attestation capability.
    """

    canonical_attestation_bytes: bytes
    source_key_id: str
    batch_sha256: str
    source_attestation_artifact_sha256: str
    source_attestation_artifact_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_attestation_bytes, bytes) or not self.canonical_attestation_bytes:
            raise ObjectDeltaSourceCutoverPublicationGateError(
                "canonical source attestation artifact bytes are invalid"
            )
        if self.source_attestation_artifact_bytes != len(self.canonical_attestation_bytes):
            raise ObjectDeltaSourceCutoverPublicationGateError(
                "canonical source attestation artifact byte count is invalid"
            )
        if self.source_attestation_artifact_sha256 != sha256_bytes(
            self.canonical_attestation_bytes
        ):
            raise ObjectDeltaSourceCutoverPublicationGateError(
                "canonical source attestation artifact hash is invalid"
            )


def _normalized_binding(value: object) -> ObjectDeltaSourceRuntimeBinding:
    if not isinstance(value, ObjectDeltaSourceRuntimeBinding):
        raise ObjectDeltaSourceCutoverPublicationGateError("source publication binding is invalid")
    try:
        return ObjectDeltaSourceRuntimeBinding(
            source_site=value.source_site,
            destination_site=value.destination_site,
            campaign_id=value.campaign_id,
            release_sha=value.release_sha,
            stream_generation_id=value.stream_generation_id,
            expected_registry_fingerprint=value.expected_registry_fingerprint,
        )
    except (AttributeError, TypeError, ObjectDeltaRuntimeBindingError) as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "source publication binding is invalid"
        ) from exc


def _require_source_public_key(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "root-pinned source public key is invalid"
        )
    try:
        # The shared batch-attestation helper checks both exact length and
        # Ed25519 validity; its returned identifier is intentionally ignored.
        source_key_id_from_public_key(value)
    except ObjectDeltaSourceBatchAttestationError as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "root-pinned source public key is invalid"
        ) from exc
    return value


def _normalized_transport_policy(value: object) -> ObjectDeltaTransportPolicy:
    try:
        return validate_object_delta_transport_policy(value)
    except ObjectDeltaTransportBindingError as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "root-pinned source transport policy is invalid"
        ) from exc


def _normalized_pin(value: object) -> ObjectDeltaSourceCutoverPublicationPin:
    if type(value) is not ObjectDeltaSourceCutoverPublicationPin:
        raise ObjectDeltaSourceCutoverPublicationGateError("source cutover publication pin is invalid")
    try:
        return ObjectDeltaSourceCutoverPublicationPin(
            binding=value.binding,
            expected_source_public_key=value.expected_source_public_key,
            transport_policy=value.transport_policy,
        )
    except ObjectDeltaSourceCutoverPublicationGateError:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "source cutover publication pin is invalid"
        ) from exc


def _canonical_cutover_attestation(value: object) -> bytes:
    try:
        if isinstance(value, Mapping):
            return canonical_object_delta_source_cutover_attestation_bytes(value)
        if isinstance(value, (bytes, str)):
            parsed = parse_object_delta_source_cutover_attestation_json(value)
            return canonical_object_delta_source_cutover_attestation_bytes(parsed)
    except ObjectDeltaSourceCutoverAttestationError as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            f"source cutover attestation is invalid: {exc}"
        ) from exc
    raise ObjectDeltaSourceCutoverPublicationGateError("source cutover attestation is invalid")


def _canonical_batch_attestation(value: object) -> Mapping[str, Any]:
    try:
        if isinstance(value, Mapping):
            raw = canonical_object_delta_source_batch_attestation_bytes(value)
            return parse_object_delta_source_batch_attestation_json(raw)
        if isinstance(value, (bytes, str)):
            return parse_object_delta_source_batch_attestation_json(value)
    except ObjectDeltaSourceBatchAttestationError as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            f"source batch attestation is invalid: {exc}"
        ) from exc
    raise ObjectDeltaSourceCutoverPublicationGateError("source batch attestation is invalid")


def _verify_cutover(
    raw: bytes,
    *,
    pin: ObjectDeltaSourceCutoverPublicationPin,
) -> VerifiedObjectDeltaSourceCutoverAttestation:
    binding = pin.binding
    try:
        verified = verify_object_delta_source_cutover_attestation(
            raw,
            expected_source_public_key=pin.expected_source_public_key,
            expected_source_site=binding.source_site,
            expected_destination_site=binding.destination_site,
            expected_campaign_id=binding.campaign_id,
            expected_release_sha=binding.release_sha,
            expected_stream_generation_id=binding.stream_generation_id,
            expected_registry_fingerprint=binding.expected_registry_fingerprint,
        )
    except ObjectDeltaSourceCutoverAttestationError as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            f"source cutover attestation is invalid: {exc}"
        ) from exc
    if type(verified) is not VerifiedObjectDeltaSourceCutoverAttestation:
        raise ObjectDeltaSourceCutoverPublicationGateError("verified source cutover is invalid")
    expected_key_id = source_key_id_from_public_key(pin.expected_source_public_key)
    if (
        verified.source_key_id != expected_key_id
        or verified.baseline.source_key_id != expected_key_id
        or verified.baseline.manifest_sha256 != verified.baseline_manifest_sha256
    ):
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "verified source cutover baseline or source key does not match the root pin"
        )
    return verified


def _batch_mapping(batch: object) -> dict[str, Any]:
    """Round-trip a dataclass batch through the canonical public validator."""

    if type(batch) is not AppendOnlySyncDeltaBatch:
        raise ObjectDeltaSourceCutoverPublicationGateError("prepared source batch is invalid")
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
    except (AttributeError, TypeError) as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError("prepared source batch is invalid") from exc


def _expected_ledger_entry(
    batch: AppendOnlySyncDeltaBatch,
    *,
    binding: ObjectDeltaSourceRuntimeBinding,
) -> SourceBatchLedgerEntry:
    try:
        return SourceBatchLedgerEntry(
            stream=SourceStreamIdentity(
                source_site=binding.source_site,
                destination_site=binding.destination_site,
                campaign_id=binding.campaign_id,
                release_sha=binding.release_sha,
                stream_generation_id=binding.stream_generation_id,
            ),
            first_sequence=batch.stream.first_sequence,
            last_sequence=batch.stream.last_sequence,
            writer_epoch=batch.writer_term.epoch,
            writer_lease_id=batch.writer_term.lease_id,
            prior_chain_sha256=batch.prior_chain_sha256,
            batch_sha256=batch.batch_sha256,
            payload_sha256=batch.payload_sha256,
            payload_bytes=batch.payload_bytes,
            object_key=batch.immutable_receipt.object_key,
            object_version_id=batch.immutable_receipt.version_id,
            ciphertext_sha256=batch.immutable_receipt.ciphertext_sha256,
            ciphertext_bytes=batch.immutable_receipt.ciphertext_bytes,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "prepared source ledger entry is invalid"
        ) from exc


def _validate_prepared_against_cutover(
    prepared: object,
    *,
    pin: ObjectDeltaSourceCutoverPublicationPin,
    cutover: VerifiedObjectDeltaSourceCutoverAttestation,
) -> PreparedObjectDeltaSourceBatch:
    try:
        normalized_prepared = require_prepared_object_delta_source_batch_provenance(
            prepared,
            binding=pin.binding,
        )
    except ObjectDeltaSourceBatchPublicationError as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            f"prepared source batch provenance is invalid: {exc}"
        ) from exc
    try:
        batch = validate_delta_batch(
            _batch_mapping(normalized_prepared.batch),
            expected_source_site=pin.binding.source_site,
            expected_destination_site=pin.binding.destination_site,
            expected_campaign_id=pin.binding.campaign_id,
            expected_release_sha=pin.binding.release_sha,
            expected_writer_epoch=cutover.writer_epoch,
            expected_writer_lease_id=cutover.writer_lease_id,
            expected_stream_generation_id=pin.binding.stream_generation_id,
        )
    except (ValueError, ObjectDeltaSourceCutoverPublicationGateError) as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            f"prepared source batch does not match the cutover stream or Writer Witness term: {exc}"
        ) from exc
    if batch != normalized_prepared.batch:
        raise ObjectDeltaSourceCutoverPublicationGateError("prepared source batch is not normalized")
    try:
        expected_transport = bind_object_delta_batch(pin.transport_policy, batch)
    except ObjectDeltaTransportBindingError as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "prepared source transport binding is invalid"
        ) from exc
    if (
        type(normalized_prepared.transport_binding) is not ObjectDeltaTransportBinding
        or normalized_prepared.transport_binding != expected_transport
    ):
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "prepared source transport binding does not match the root-pinned policy"
        )
    expected_ledger = _expected_ledger_entry(batch, binding=pin.binding)
    if (
        type(normalized_prepared.ledger_entry) is not SourceBatchLedgerEntry
        or normalized_prepared.ledger_entry != expected_ledger
    ):
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "prepared source ledger entry does not match the cutover-authorized batch"
        )
    return normalized_prepared


def _validated_authorization(
    value: object,
) -> tuple[
    AuthorizedObjectDeltaSourceBatch,
    ObjectDeltaSourceCutoverPublicationPin,
    PreparedObjectDeltaSourceBatch,
    VerifiedObjectDeltaSourceCutoverAttestation,
]:
    if type(value) is not AuthorizedObjectDeltaSourceBatch:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "authorized source batch capability is required"
        )
    if value._capability is not _AUTHORIZED_SOURCE_BATCH_CAPABILITY:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "authorized source batch was not verified"
        )
    if not isinstance(value.source_cutover_attestation, bytes):
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "authorized source cutover attestation is invalid"
        )
    pin = _normalized_pin(value.pin)
    cutover = _verify_cutover(value.source_cutover_attestation, pin=pin)
    prepared = _validate_prepared_against_cutover(value.prepared, pin=pin, cutover=cutover)
    return value, pin, prepared, cutover


def _verified_source_batch_attestation(
    authorization: AuthorizedObjectDeltaSourceBatch,
    *,
    attestation: Mapping[str, Any] | bytes | str,
) -> tuple[
    ObjectDeltaSourceCutoverPublicationPin,
    PreparedObjectDeltaSourceBatch,
    VerifiedObjectDeltaSourceBatchAttestation,
    bytes,
]:
    """Verify an exact batch envelope against one cutover-authorized batch."""

    _authorized, pin, prepared, _cutover = _validated_authorization(authorization)
    normalized_attestation = _canonical_batch_attestation(attestation)
    canonical_bytes = canonical_object_delta_source_batch_attestation_bytes(normalized_attestation)
    try:
        verified = verify_object_delta_source_batch_attestation(
            normalized_attestation,
            expected_source_public_key=pin.expected_source_public_key,
            expected_transport_policy=pin.transport_policy,
        )
    except ObjectDeltaSourceBatchAttestationError as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            f"source batch attestation is invalid: {exc}"
        ) from exc
    if (
        type(verified) is not VerifiedObjectDeltaSourceBatchAttestation
        or verified.batch != prepared.batch
        or verified.transport_binding != prepared.transport_binding
    ):
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "source batch attestation does not match the cutover-authorized batch"
        )
    return pin, prepared, verified, canonical_bytes


def _validated_source_attestation(
    value: object,
) -> tuple[
    AuthorizedObjectDeltaSourceAttestation,
    ObjectDeltaSourceCutoverPublicationPin,
    PreparedObjectDeltaSourceBatch,
    VerifiedObjectDeltaSourceBatchAttestation,
]:
    if type(value) is not AuthorizedObjectDeltaSourceAttestation:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "authorized source attestation capability is required"
        )
    if value._capability is not _AUTHORIZED_SOURCE_ATTESTATION_CAPABILITY:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "authorized source attestation was not verified"
        )
    if not isinstance(value.canonical_attestation_bytes, bytes):
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "authorized source attestation artifact is invalid"
        )
    pin, prepared, verified, canonical_bytes = _verified_source_batch_attestation(
        value.batch_authorization,
        attestation=value.canonical_attestation_bytes,
    )
    expected = (
        verified.source_key_id,
        verified.batch.batch_sha256,
        sha256_bytes(canonical_bytes),
        len(canonical_bytes),
    )
    actual = (
        value.source_key_id,
        value.batch_sha256,
        value.source_attestation_artifact_sha256,
        value.source_attestation_artifact_bytes,
    )
    if actual != expected:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "authorized source attestation artifact does not match the verified envelope"
        )
    return value, pin, prepared, verified


def _legacy_test_only_authorize_object_delta_source_cutover_batch(
    *,
    pin: ObjectDeltaSourceCutoverPublicationPin,
    prepared: PreparedObjectDeltaSourceBatch,
    source_cutover_attestation: Mapping[str, Any] | bytes | str,
) -> AuthorizedObjectDeltaSourceBatch:
    """Test-only mechanics for legacy cutover authorization.

    ``pin`` is deliberately explicit and must come from root-controlled,
    release-bound configuration in a later adapter.  The source public key is
    never accepted from the received evidence.  No storage or database action
    occurs here.
    """

    normalized_pin = _normalized_pin(pin)
    raw_cutover = _canonical_cutover_attestation(source_cutover_attestation)
    cutover = _verify_cutover(raw_cutover, pin=normalized_pin)
    normalized_prepared = _validate_prepared_against_cutover(
        prepared,
        pin=normalized_pin,
        cutover=cutover,
    )
    authorized = AuthorizedObjectDeltaSourceBatch(
        pin=normalized_pin,
        prepared=normalized_prepared,
        source_cutover_attestation=raw_cutover,
    )
    object.__setattr__(authorized, "_capability", _AUTHORIZED_SOURCE_BATCH_CAPABILITY)
    _validated_authorization(authorized)
    return authorized


def authorize_object_delta_source_cutover_batch(
    *,
    pin: ObjectDeltaSourceCutoverPublicationPin,
    prepared: PreparedObjectDeltaSourceBatch,
    source_cutover_attestation: Mapping[str, Any] | bytes | str,
) -> AuthorizedObjectDeltaSourceBatch:
    """Reject the superseded source-cutover runtime authorization route."""

    del pin, prepared, source_cutover_attestation
    reject_legacy_object_delta_source_publication_runtime(
        entrypoint="authorize_object_delta_source_cutover_batch"
    )


def _legacy_test_only_require_authorized_object_delta_source_cutover_batch(
    value: object,
) -> AuthorizedObjectDeltaSourceBatch:
    """Test-only revalidation of a legacy cutover authorization capability."""

    authorized, _pin, _prepared, _cutover = _validated_authorization(value)
    return authorized


def require_authorized_object_delta_source_cutover_batch(
    value: object,
) -> AuthorizedObjectDeltaSourceBatch:
    """Reject the superseded legacy cutover-capability runtime route."""

    del value
    reject_legacy_object_delta_source_publication_runtime(
        entrypoint="require_authorized_object_delta_source_cutover_batch"
    )


def _legacy_test_only_authorized_object_delta_source_ledger_entry(
    authorization: AuthorizedObjectDeltaSourceAttestation,
) -> SourceBatchLedgerEntry:
    """Test-only projection of a legacy signed-attestation ledger candidate.

    A later caller-owned transaction must still lock and re-read the stream,
    source cutover, and ledger frontier before persisting this candidate.  It
    must first durably record the exact canonical attestation artifact exposed
    by ``authorized_object_delta_source_batch_attestation_artifact``; this
    pure gate cannot prove that persistence step.
    """

    _authorized, _pin, prepared, _verified = _validated_source_attestation(authorization)
    return prepared.ledger_entry


def authorized_object_delta_source_ledger_entry(
    authorization: AuthorizedObjectDeltaSourceAttestation,
) -> SourceBatchLedgerEntry:
    """Reject the superseded legacy ledger-candidate runtime route."""

    del authorization
    reject_legacy_object_delta_source_publication_runtime(
        entrypoint="authorized_object_delta_source_ledger_entry"
    )


def _source_public_key_from_signer(source_signer: object) -> bytes:
    try:
        from cryptography.hazmat.primitives import serialization

        public_key = source_signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (AttributeError, ImportError, TypeError, ValueError) as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError("source signer is invalid") from exc
    return _require_source_public_key(public_key)


def _legacy_test_only_build_authorized_object_delta_source_batch_attestation(
    authorization: AuthorizedObjectDeltaSourceBatch,
    *,
    source_signer: object,
) -> AuthorizedObjectDeltaSourceAttestation:
    """Test-only mechanics for legacy source-attestation construction.

    The signer is required to match the same root-pinned public key used to
    verify the source cutover; a caller cannot sign an authorized batch with
    an arbitrary key.  The result is still only in-memory evidence: durable
    record/spool semantics belong to a later adapter, but its opaque result is
    now required before the gate will release a ledger candidate.
    """

    _authorized, pin, prepared, _cutover = _validated_authorization(authorization)
    if _source_public_key_from_signer(source_signer) != pin.expected_source_public_key:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            "source signer does not match the root-pinned source public key"
        )
    try:
        attestation = build_object_delta_source_batch_attestation(
            batch=prepared.batch,
            transport_policy=pin.transport_policy,
            transport_binding=prepared.transport_binding,
            source_signer=source_signer,
        )
    except ObjectDeltaSourceBatchAttestationError as exc:
        raise ObjectDeltaSourceCutoverPublicationGateError(
            f"authorized source batch attestation cannot be built: {exc}"
        ) from exc
    return _legacy_test_only_verify_authorized_object_delta_source_batch_attestation(
        authorization,
        attestation=attestation,
    )


def build_authorized_object_delta_source_batch_attestation(
    authorization: AuthorizedObjectDeltaSourceBatch,
    *,
    source_signer: object,
) -> AuthorizedObjectDeltaSourceAttestation:
    """Reject the superseded legacy source-attestation runtime route."""

    del authorization, source_signer
    reject_legacy_object_delta_source_publication_runtime(
        entrypoint="build_authorized_object_delta_source_batch_attestation"
    )


def _legacy_test_only_verify_authorized_object_delta_source_batch_attestation(
    authorization: AuthorizedObjectDeltaSourceBatch,
    *,
    attestation: Mapping[str, Any] | bytes | str,
) -> AuthorizedObjectDeltaSourceAttestation:
    """Test-only mechanics for legacy source-attestation verification."""

    _pin, _prepared, verified, canonical_bytes = _verified_source_batch_attestation(
        authorization,
        attestation=attestation,
    )
    result = AuthorizedObjectDeltaSourceAttestation(
        batch_authorization=authorization,
        canonical_attestation_bytes=canonical_bytes,
        source_key_id=verified.source_key_id,
        batch_sha256=verified.batch.batch_sha256,
        source_attestation_artifact_sha256=sha256_bytes(canonical_bytes),
        source_attestation_artifact_bytes=len(canonical_bytes),
    )
    object.__setattr__(result, "_capability", _AUTHORIZED_SOURCE_ATTESTATION_CAPABILITY)
    _validated_source_attestation(result)
    return result


def verify_authorized_object_delta_source_batch_attestation(
    authorization: AuthorizedObjectDeltaSourceBatch,
    *,
    attestation: Mapping[str, Any] | bytes | str,
) -> AuthorizedObjectDeltaSourceAttestation:
    """Reject the superseded legacy source-attestation runtime route."""

    del authorization, attestation
    reject_legacy_object_delta_source_publication_runtime(
        entrypoint="verify_authorized_object_delta_source_batch_attestation"
    )


def _legacy_test_only_require_authorized_object_delta_source_batch_attestation(
    value: object,
) -> AuthorizedObjectDeltaSourceAttestation:
    """Test-only revalidation of a legacy source-attestation capability."""

    attestation, _pin, _prepared, _verified = _validated_source_attestation(value)
    return attestation


def require_authorized_object_delta_source_batch_attestation(
    value: object,
) -> AuthorizedObjectDeltaSourceAttestation:
    """Reject the superseded legacy source-attestation runtime route."""

    del value
    reject_legacy_object_delta_source_publication_runtime(
        entrypoint="require_authorized_object_delta_source_batch_attestation"
    )


def _legacy_test_only_authorized_object_delta_source_batch_attestation_artifact(
    authorization: AuthorizedObjectDeltaSourceAttestation,
) -> AuthorizedObjectDeltaSourceBatchAttestationArtifact:
    """Test-only projection of a legacy canonical attestation artifact.

    This is the only source-side helper that should feed a later durable
    source-publication-attempt record.  Its hash and byte count deliberately
    cover the exact canonical envelope including its required final newline,
    never the verifier's in-envelope JSON-only diagnostic hash.
    """

    authorized, _pin, _prepared, _verified = _validated_source_attestation(
        authorization
    )
    return AuthorizedObjectDeltaSourceBatchAttestationArtifact(
        canonical_attestation_bytes=authorized.canonical_attestation_bytes,
        source_key_id=authorized.source_key_id,
        batch_sha256=authorized.batch_sha256,
        source_attestation_artifact_sha256=authorized.source_attestation_artifact_sha256,
        source_attestation_artifact_bytes=authorized.source_attestation_artifact_bytes,
    )


def authorized_object_delta_source_batch_attestation_artifact(
    authorization: AuthorizedObjectDeltaSourceAttestation,
) -> AuthorizedObjectDeltaSourceBatchAttestationArtifact:
    """Reject the superseded legacy source-attestation artifact route."""

    del authorization
    reject_legacy_object_delta_source_publication_runtime(
        entrypoint="authorized_object_delta_source_batch_attestation_artifact"
    )


# Do not export either the disabled compatibility names or the private
# ``_legacy_test_only_*`` mechanics.  Direct compatibility imports fail
# closed; a future runtime must use a new coordinator rather than this module.
__all__ = (
    "AuthorizedObjectDeltaSourceBatch",
    "AuthorizedObjectDeltaSourceAttestation",
    "AuthorizedObjectDeltaSourceBatchAttestationArtifact",
    "OBJECT_DELTA_SOURCE_CUTOVER_PUBLICATION_GATE",
    "ObjectDeltaSourceCutoverPublicationGateError",
    "ObjectDeltaSourceCutoverPublicationPin",
)
