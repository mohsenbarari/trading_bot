"""Pure, fail-closed state contracts for a future source Object-delta publisher.

The source database and Object Storage cannot share one transaction.  A
publisher that uploads a freshly encrypted Object and crashes before recording
its immutable source ledger therefore has an ambiguity: the conditional PUT
may have committed even when its client did not receive a response.  Retrying
with newly encrypted age bytes would create a different ciphertext and can
silently fork the receipt for one logical range.

This module describes the durable state a later root-only adapter must persist
around that boundary.  It is intentionally *only* dataclasses and transition
planners.  It has no filesystem, database, Object Storage, encryption,
subprocess, credential, network, or runtime-enable capability.

Required order for an eventual adapter is:

1. reserve the deterministic logical attempt;
2. create the ciphertext once, retain its exact bytes in a root-only
   content-addressed spool, and persist ``sealed`` before any PUT;
3. for every sealed/unresolved attempt, complete an exact-key version listing
   before any possible replay; adopt one matching singleton or PUT the same
   stored bytes only when the listing proves the key empty;
4. persist exact VersionId read-back receipt, then canonical source-attestation
   artifact hash, before source-ledger append/replay, and record the terminal
   ledger-bound attempt in that same database transaction;
5. persist SHA-256 and byte count of the exact newline-terminated canonical
   transport-receipt and source-attestation artifacts (not an in-envelope
   checksum or a verifier's internal digest);
6. never replace a sealed ciphertext, receipt, source attestation, or ledger
   binding for an existing attempt.

The state records below are not signatures and not database evidence by
themselves.  The future adapter must use the existing transport/read-back and
source-attestation verifiers before passing their normalized facts here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re
from typing import TypeAlias

from core.append_only_sync_delta_batch import (
    LEASE_ID_RE,
    MAX_BATCH_BYTES,
    MAX_DELTA_PAYLOAD_BYTES,
    MAX_STREAM_SEQUENCE_IDS,
    OBJECT_KEY_RE,
    SHA256_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
    sha256_bytes,
)
from core.object_delta_source_batch_ledger import (
    ObjectDeltaSourceLedgerError,
    SourceBatchLedgerEntry,
    SourceStreamIdentity,
)
from core.object_delta_transport_binding import (
    AGE_RECIPIENT_RE,
    CONTROLLER_CREDENTIAL_HOLDER,
    OBJECT_DELTA_ENCRYPTION,
    OBJECT_DELTA_TRANSPORT_SCHEMA,
    ObjectDeltaTransportBindingError,
    ObjectDeltaTransportPolicy,
    validate_object_delta_transport_policy,
)


OBJECT_DELTA_SOURCE_PUBLICATION_ATTEMPT_SCHEMA = (
    "gold-trade-object-delta-source-publication-attempt-v1"
)
OBJECT_DELTA_SOURCE_PUBLICATION_ATTEMPT_ID_PREFIX = "odsp-v1:"
MAX_OBJECT_DELTA_CIPHERTEXT_OVERHEAD_BYTES = 1024 * 1024
MAX_OBJECT_DELTA_SOURCE_TRANSPORT_RECEIPT_BYTES = 32 * 1024
MAX_OBJECT_DELTA_SOURCE_ATTESTATION_BYTES = MAX_BATCH_BYTES + 64 * 1024
MAX_OBJECT_DELTA_SOURCE_CUTOVER_ARTIFACT_BYTES = 128 * 1024

SOURCE_PUBLICATION_ATTEMPT_ACTION_RESERVE = "reserve"
SOURCE_PUBLICATION_ATTEMPT_ACTION_REPLAY = "replay"
SOURCE_PUBLICATION_CIPHERTEXT_ACTION_SEAL = "seal"
SOURCE_PUBLICATION_UPLOAD_ACTION_RECORD = "record_exact_upload"
SOURCE_PUBLICATION_UPLOAD_ACTION_REPLAY = "replay_exact_upload"
SOURCE_PUBLICATION_RECONCILIATION_ACTION_EXACT_PUT_REPLAY = "exact_put_replay"
SOURCE_PUBLICATION_RECONCILIATION_ACTION_ADOPT = "adopt_exact_receipt"
SOURCE_PUBLICATION_ATTESTATION_ACTION_RECORD = "record_source_attestation"
SOURCE_PUBLICATION_ATTESTATION_ACTION_REPLAY = "replay_source_attestation"
SOURCE_PUBLICATION_LEDGER_ACTION_APPEND = "append_source_ledger"
SOURCE_PUBLICATION_LEDGER_ACTION_REPLAY = "replay_source_ledger"

_ATTEMPT_ID_RE = re.compile(r"^odsp-v1:[0-9a-f]{64}$")
_SOURCE_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$")


class ObjectDeltaSourcePublicationAttemptError(ValueError):
    """A source-publication state transition would lose immutable evidence."""


def _require_text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ObjectDeltaSourcePublicationAttemptError(f"Object-delta source publication {label} is invalid")
    return value


def _require_positive_int(value: object, *, label: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ObjectDeltaSourcePublicationAttemptError(f"Object-delta source publication {label} is invalid")
    return value


def _require_nonnegative_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ObjectDeltaSourcePublicationAttemptError(f"Object-delta source publication {label} is invalid")
    return value


def _require_object_key(value: object, *, label: str) -> str:
    key = _require_text(value, label=label, pattern=OBJECT_KEY_RE)
    if ".." in key.split("/"):
        raise ObjectDeltaSourcePublicationAttemptError(
            f"Object-delta source publication {label} is invalid"
        )
    return key


def _require_version_id(value: object, *, label: str) -> str:
    version_id = _require_text(value, label=label, pattern=VERSION_ID_RE)
    if version_id.lower() == "null":
        raise ObjectDeltaSourcePublicationAttemptError(
            f"Object-delta source publication {label} is invalid"
        )
    return version_id


def _require_stream(value: object) -> SourceStreamIdentity:
    if not isinstance(value, SourceStreamIdentity):
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication stream is invalid"
        )
    try:
        # Reconstruct instead of relying on a publicly constructible frozen
        # dataclass as proof of prior validation.
        return SourceStreamIdentity(
            source_site=value.source_site,
            destination_site=value.destination_site,
            campaign_id=value.campaign_id,
            release_sha=value.release_sha,
            stream_generation_id=value.stream_generation_id,
        )
    except (AttributeError, TypeError, ObjectDeltaSourceLedgerError) as exc:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication stream is invalid"
        ) from exc


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationIntent:
    """One deterministic logical range reserved before encryption or a PUT.

    The Object key is already derived from the immutable transport policy and
    plaintext payload hash by a higher-level adapter.  The recipient *and the
    canonical policy hash* are held here so a sealed attempt cannot be resumed
    under a changed Object Storage bucket/prefix or destination key.  The
    exact canonical source-cutover artifact hash/size similarly pins the
    writer-term/baseline authorization that existed when the attempt was
    reserved.
    """

    stream: SourceStreamIdentity
    writer_epoch: int
    writer_lease_id: str
    first_sequence: int
    last_sequence: int
    prior_chain_sha256: str
    payload_sha256: str
    payload_bytes: int
    object_key: str
    destination_age_recipient: str
    transport_policy_sha256: str
    source_cutover_artifact_sha256: str
    source_cutover_artifact_bytes: int

    def __post_init__(self) -> None:
        stream = _require_stream(self.stream)
        first = _require_positive_int(self.first_sequence, label="first sequence")
        last = _require_positive_int(self.last_sequence, label="last sequence")
        if last < first or last - first + 1 > MAX_STREAM_SEQUENCE_IDS:
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication sequence range is invalid"
            )
        _require_positive_int(self.writer_epoch, label="writer epoch")
        _require_text(self.writer_lease_id, label="writer lease", pattern=LEASE_ID_RE)
        _require_text(self.prior_chain_sha256, label="prior chain SHA-256", pattern=SHA256_RE)
        _require_text(self.payload_sha256, label="payload SHA-256", pattern=SHA256_RE)
        _require_positive_int(
            self.payload_bytes,
            label="payload bytes",
            maximum=MAX_DELTA_PAYLOAD_BYTES,
        )
        _require_object_key(self.object_key, label="Object key")
        _require_text(
            self.destination_age_recipient,
            label="destination age recipient",
            pattern=AGE_RECIPIENT_RE,
        )
        _require_text(
            self.transport_policy_sha256,
            label="transport policy SHA-256",
            pattern=SHA256_RE,
        )
        _require_text(
            self.source_cutover_artifact_sha256,
            label="source cutover artifact SHA-256",
            pattern=SHA256_RE,
        )
        _require_positive_int(
            self.source_cutover_artifact_bytes,
            label="source cutover artifact bytes",
            maximum=MAX_OBJECT_DELTA_SOURCE_CUTOVER_ARTIFACT_BYTES,
        )
        # Ensure a malformed public subclass/mutation could not keep an
        # equivalence with a valid intent while selecting different bytes.
        if self.stream != stream:
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication stream is not normalized"
            )


def _intent_mapping(intent: ObjectDeltaSourcePublicationIntent) -> dict[str, object]:
    if not isinstance(intent, ObjectDeltaSourcePublicationIntent):
        raise ObjectDeltaSourcePublicationAttemptError("Object-delta source publication intent is invalid")
    # Re-run dataclass validation before hashing values supplied by a caller.
    ObjectDeltaSourcePublicationIntent(
        stream=intent.stream,
        writer_epoch=intent.writer_epoch,
        writer_lease_id=intent.writer_lease_id,
        first_sequence=intent.first_sequence,
        last_sequence=intent.last_sequence,
        prior_chain_sha256=intent.prior_chain_sha256,
        payload_sha256=intent.payload_sha256,
        payload_bytes=intent.payload_bytes,
        object_key=intent.object_key,
        destination_age_recipient=intent.destination_age_recipient,
        transport_policy_sha256=intent.transport_policy_sha256,
        source_cutover_artifact_sha256=intent.source_cutover_artifact_sha256,
        source_cutover_artifact_bytes=intent.source_cutover_artifact_bytes,
    )
    return {
        "schema": OBJECT_DELTA_SOURCE_PUBLICATION_ATTEMPT_SCHEMA,
        "stream": {
            "source_site": intent.stream.source_site,
            "destination_site": intent.stream.destination_site,
            "campaign_id": intent.stream.campaign_id,
            "release_sha": intent.stream.release_sha,
            "stream_generation_id": intent.stream.stream_generation_id,
        },
        "writer_term": {
            "epoch": intent.writer_epoch,
            "lease_id": intent.writer_lease_id,
        },
        "range": {
            "first_sequence": intent.first_sequence,
            "last_sequence": intent.last_sequence,
        },
        "prior_chain_sha256": intent.prior_chain_sha256,
        "payload": {
            "sha256": intent.payload_sha256,
            "bytes": intent.payload_bytes,
        },
        "object_key": intent.object_key,
        "destination_age_recipient": intent.destination_age_recipient,
        "transport_policy_sha256": intent.transport_policy_sha256,
        "source_cutover_artifact": {
            "sha256": intent.source_cutover_artifact_sha256,
            "bytes": intent.source_cutover_artifact_bytes,
        },
    }


def canonical_object_delta_source_transport_policy_bytes(
    policy: ObjectDeltaTransportPolicy,
) -> bytes:
    """Return canonical non-secret transport-policy bytes for an attempt.

    This deliberately covers the bucket, prefix, both site recipients and the
    fixed protocol constants.  Provider credentials and any presigned URL are
    absent.  A source publisher must obtain ``policy`` from the same
    root-pinned cutover-publication gate that authorizes the batch.
    """

    try:
        normalized = validate_object_delta_transport_policy(policy)
    except ObjectDeltaTransportBindingError as exc:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication transport policy is invalid"
        ) from exc
    return canonical_json_bytes(
        {
            "transport_schema": OBJECT_DELTA_TRANSPORT_SCHEMA,
            "encryption": OBJECT_DELTA_ENCRYPTION,
            "bucket": normalized.bucket,
            "prefix": normalized.prefix,
            "webapp_fi_age_recipient": normalized.webapp_fi_age_recipient,
            "webapp_ir_age_recipient": normalized.webapp_ir_age_recipient,
            "credential_holder": CONTROLLER_CREDENTIAL_HOLDER,
        }
    )


def derive_object_delta_source_transport_policy_sha256(
    policy: ObjectDeltaTransportPolicy,
) -> str:
    """Return the exact canonical transport-policy hash for attempt identity."""

    return sha256_bytes(canonical_object_delta_source_transport_policy_bytes(policy))


def derive_object_delta_source_publication_attempt_id(
    intent: ObjectDeltaSourcePublicationIntent,
) -> str:
    """Return the deterministic ID for one logical attempt, not one PUT."""

    return OBJECT_DELTA_SOURCE_PUBLICATION_ATTEMPT_ID_PREFIX + sha256_bytes(
        canonical_json_bytes(_intent_mapping(intent))
    )


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationAttempt:
    """The durable pre-encryption reservation for one immutable range."""

    intent: ObjectDeltaSourcePublicationIntent
    attempt_id: str

    def __post_init__(self) -> None:
        _intent_mapping(self.intent)
        _require_text(self.attempt_id, label="attempt ID", pattern=_ATTEMPT_ID_RE)
        if self.attempt_id != derive_object_delta_source_publication_attempt_id(self.intent):
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication attempt ID does not match its intent"
            )


def build_object_delta_source_publication_attempt(
    intent: ObjectDeltaSourcePublicationIntent,
) -> ObjectDeltaSourcePublicationAttempt:
    """Build the only legal reservation value for an intent."""

    return ObjectDeltaSourcePublicationAttempt(
        intent=intent,
        attempt_id=derive_object_delta_source_publication_attempt_id(intent),
    )


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationCiphertextSpool:
    """Hash-only evidence of exact ciphertext retained before any PUT.

    The adapter must keep the corresponding bytes in a root-only,
    content-addressed spool.  A path is intentionally absent: a future
    adapter owns that secure path boundary and cannot be redirected by an
    attempt record.
    """

    attempt_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    spool_sha256: str
    spool_bytes: int

    def __post_init__(self) -> None:
        _require_text(self.attempt_id, label="ciphertext attempt ID", pattern=_ATTEMPT_ID_RE)
        ciphertext_hash = _require_text(
            self.ciphertext_sha256,
            label="ciphertext SHA-256",
            pattern=SHA256_RE,
        )
        ciphertext_bytes = _require_positive_int(
            self.ciphertext_bytes,
            label="ciphertext bytes",
            maximum=MAX_DELTA_PAYLOAD_BYTES + MAX_OBJECT_DELTA_CIPHERTEXT_OVERHEAD_BYTES,
        )
        spool_hash = _require_text(self.spool_sha256, label="spool SHA-256", pattern=SHA256_RE)
        spool_bytes = _require_positive_int(
            self.spool_bytes,
            label="spool bytes",
            maximum=MAX_DELTA_PAYLOAD_BYTES + MAX_OBJECT_DELTA_CIPHERTEXT_OVERHEAD_BYTES,
        )
        if (spool_hash, spool_bytes) != (ciphertext_hash, ciphertext_bytes):
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication spool does not preserve the exact ciphertext"
            )


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationSealedAttempt:
    """A persisted ciphertext spool that has not yet reached source ledger."""

    attempt: ObjectDeltaSourcePublicationAttempt
    ciphertext: ObjectDeltaSourcePublicationCiphertextSpool

    def __post_init__(self) -> None:
        _require_attempt(self.attempt)
        if type(self.ciphertext) is not ObjectDeltaSourcePublicationCiphertextSpool:
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication ciphertext spool is invalid"
            )
        try:
            normalized_ciphertext = ObjectDeltaSourcePublicationCiphertextSpool(
                attempt_id=self.ciphertext.attempt_id,
                ciphertext_sha256=self.ciphertext.ciphertext_sha256,
                ciphertext_bytes=self.ciphertext.ciphertext_bytes,
                spool_sha256=self.ciphertext.spool_sha256,
                spool_bytes=self.ciphertext.spool_bytes,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication ciphertext spool is invalid"
            ) from exc
        if normalized_ciphertext != self.ciphertext:
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication ciphertext spool is not normalized"
            )
        if self.ciphertext.attempt_id != self.attempt.attempt_id:
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication ciphertext spool belongs to another attempt"
            )


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationExactReceipt:
    """Hash-only durable result of create-only PUT and exact-VersionId GET.

    ``transport_receipt_artifact_*`` refer to exact canonical receipt bytes,
    including their terminal newline.  They intentionally do not reuse a
    checksum field embedded inside the receipt.
    """

    attempt_id: str
    object_key: str
    object_version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    transport_receipt_artifact_sha256: str
    transport_receipt_artifact_bytes: int

    def __post_init__(self) -> None:
        _require_text(self.attempt_id, label="receipt attempt ID", pattern=_ATTEMPT_ID_RE)
        _require_object_key(self.object_key, label="receipt Object key")
        _require_version_id(self.object_version_id, label="receipt Object version")
        _require_text(self.ciphertext_sha256, label="receipt ciphertext SHA-256", pattern=SHA256_RE)
        _require_positive_int(
            self.ciphertext_bytes,
            label="receipt ciphertext bytes",
            maximum=MAX_DELTA_PAYLOAD_BYTES + MAX_OBJECT_DELTA_CIPHERTEXT_OVERHEAD_BYTES,
        )
        _require_text(
            self.transport_receipt_artifact_sha256,
            label="transport receipt artifact SHA-256",
            pattern=SHA256_RE,
        )
        _require_positive_int(
            self.transport_receipt_artifact_bytes,
            label="transport receipt artifact bytes",
            maximum=MAX_OBJECT_DELTA_SOURCE_TRANSPORT_RECEIPT_BYTES,
        )


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationUploadedAttempt:
    """A sealed attempt whose exact Object version has been read back."""

    sealed: ObjectDeltaSourcePublicationSealedAttempt
    receipt: ObjectDeltaSourcePublicationExactReceipt

    def __post_init__(self) -> None:
        _require_sealed_attempt(self.sealed)
        _require_exact_receipt_for_sealed(self.receipt, sealed=self.sealed)


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationAttestationArtifact:
    """Hash-only canonical source-attestation artifact persisted before ledger.

    ``batch_sha256`` and this artifact must come from the existing pinned
    Ed25519 source-attestation verifier.  The artifact hash/bytes cover exact
    newline-terminated canonical envelope bytes, not a verifier's internal
    JSON digest.  This pure type cannot perform verification and therefore
    never treats its public dataclass shape as a cryptographic capability.
    """

    attempt_id: str
    source_key_id: str
    batch_sha256: str
    source_attestation_artifact_sha256: str
    source_attestation_artifact_bytes: int

    def __post_init__(self) -> None:
        _require_text(self.attempt_id, label="attestation attempt ID", pattern=_ATTEMPT_ID_RE)
        _require_text(self.source_key_id, label="source key ID", pattern=_SOURCE_KEY_ID_RE)
        _require_text(self.batch_sha256, label="batch SHA-256", pattern=SHA256_RE)
        _require_text(
            self.source_attestation_artifact_sha256,
            label="source attestation artifact SHA-256",
            pattern=SHA256_RE,
        )
        _require_positive_int(
            self.source_attestation_artifact_bytes,
            label="source attestation artifact bytes",
            maximum=MAX_OBJECT_DELTA_SOURCE_ATTESTATION_BYTES,
        )


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationAttestedAttempt:
    """An uploaded exact receipt paired to one immutable source attestation."""

    uploaded: ObjectDeltaSourcePublicationUploadedAttempt
    attestation: ObjectDeltaSourcePublicationAttestationArtifact

    def __post_init__(self) -> None:
        _require_uploaded_attempt(self.uploaded)
        if type(self.attestation) is not ObjectDeltaSourcePublicationAttestationArtifact:
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication attestation artifact is invalid"
            )
        try:
            normalized_attestation = ObjectDeltaSourcePublicationAttestationArtifact(
                attempt_id=self.attestation.attempt_id,
                source_key_id=self.attestation.source_key_id,
                batch_sha256=self.attestation.batch_sha256,
                source_attestation_artifact_sha256=(
                    self.attestation.source_attestation_artifact_sha256
                ),
                source_attestation_artifact_bytes=(
                    self.attestation.source_attestation_artifact_bytes
                ),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication attestation artifact is invalid"
            ) from exc
        if normalized_attestation != self.attestation:
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication attestation artifact is not normalized"
            )
        if self.attestation.attempt_id != self.uploaded.sealed.attempt.attempt_id:
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication attestation belongs to another attempt"
            )


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationLedgeredAttempt:
    """Terminal attempt state after its immutable source-ledger binding.

    A future persistence adapter must create this state in the *same database
    transaction* as the immutable source-ledger append/replay.  Keeping the
    binding in the state contract makes a later resume fail closed if an
    allegedly terminal attempt and the ledger query disagree.
    """

    attested: ObjectDeltaSourcePublicationAttestedAttempt
    ledger_entry: SourceBatchLedgerEntry

    def __post_init__(self) -> None:
        attested = _require_attested_attempt(self.attested)
        entry = _require_matching_ledger_entry(self.ledger_entry, attested=attested)
        if self.attested != attested or self.ledger_entry != entry:
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication terminal ledger state is not normalized"
            )


ObjectDeltaSourcePublicationState: TypeAlias = (
    ObjectDeltaSourcePublicationAttempt
    | ObjectDeltaSourcePublicationSealedAttempt
    | ObjectDeltaSourcePublicationUploadedAttempt
    | ObjectDeltaSourcePublicationAttestedAttempt
    | ObjectDeltaSourcePublicationLedgeredAttempt
)


def _require_attempt(value: object) -> ObjectDeltaSourcePublicationAttempt:
    if not isinstance(value, ObjectDeltaSourcePublicationAttempt):
        raise ObjectDeltaSourcePublicationAttemptError("Object-delta source publication attempt is invalid")
    # Reconstruct from raw fields to make a stale/mutated nested value unable
    # to bypass the deterministic identity check.
    return ObjectDeltaSourcePublicationAttempt(intent=value.intent, attempt_id=value.attempt_id)


def _require_sealed_attempt(value: object) -> ObjectDeltaSourcePublicationSealedAttempt:
    if not isinstance(value, ObjectDeltaSourcePublicationSealedAttempt):
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication sealed attempt is invalid"
        )
    try:
        ciphertext = ObjectDeltaSourcePublicationCiphertextSpool(
            attempt_id=value.ciphertext.attempt_id,
            ciphertext_sha256=value.ciphertext.ciphertext_sha256,
            ciphertext_bytes=value.ciphertext.ciphertext_bytes,
            spool_sha256=value.ciphertext.spool_sha256,
            spool_bytes=value.ciphertext.spool_bytes,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication sealed attempt is invalid"
        ) from exc
    return ObjectDeltaSourcePublicationSealedAttempt(
        attempt=_require_attempt(value.attempt),
        ciphertext=ciphertext,
    )


def _require_exact_receipt_for_sealed(
    value: object,
    *,
    sealed: ObjectDeltaSourcePublicationSealedAttempt,
) -> ObjectDeltaSourcePublicationExactReceipt:
    if not isinstance(value, ObjectDeltaSourcePublicationExactReceipt):
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication exact receipt is invalid"
        )
    receipt = ObjectDeltaSourcePublicationExactReceipt(
        attempt_id=value.attempt_id,
        object_key=value.object_key,
        object_version_id=value.object_version_id,
        ciphertext_sha256=value.ciphertext_sha256,
        ciphertext_bytes=value.ciphertext_bytes,
        transport_receipt_artifact_sha256=value.transport_receipt_artifact_sha256,
        transport_receipt_artifact_bytes=value.transport_receipt_artifact_bytes,
    )
    attempt = sealed.attempt
    ciphertext = sealed.ciphertext
    if (
        receipt.attempt_id != attempt.attempt_id
        or receipt.object_key != attempt.intent.object_key
        or receipt.ciphertext_sha256 != ciphertext.ciphertext_sha256
        or receipt.ciphertext_bytes != ciphertext.ciphertext_bytes
    ):
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication exact receipt does not match the sealed attempt"
        )
    return receipt


def _require_uploaded_attempt(value: object) -> ObjectDeltaSourcePublicationUploadedAttempt:
    if not isinstance(value, ObjectDeltaSourcePublicationUploadedAttempt):
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication uploaded attempt is invalid"
        )
    sealed = _require_sealed_attempt(value.sealed)
    receipt = _require_exact_receipt_for_sealed(value.receipt, sealed=sealed)
    return ObjectDeltaSourcePublicationUploadedAttempt(sealed=sealed, receipt=receipt)


def _require_attested_attempt(value: object) -> ObjectDeltaSourcePublicationAttestedAttempt:
    if not isinstance(value, ObjectDeltaSourcePublicationAttestedAttempt):
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication attested attempt is invalid"
        )
    uploaded = _require_uploaded_attempt(value.uploaded)
    attestation = value.attestation
    return ObjectDeltaSourcePublicationAttestedAttempt(
        uploaded=uploaded,
        attestation=ObjectDeltaSourcePublicationAttestationArtifact(
            attempt_id=attestation.attempt_id,
            source_key_id=attestation.source_key_id,
            batch_sha256=attestation.batch_sha256,
            source_attestation_artifact_sha256=(
                attestation.source_attestation_artifact_sha256
            ),
            source_attestation_artifact_bytes=(
                attestation.source_attestation_artifact_bytes
            ),
        ),
    )


def _attempt_from_state(value: ObjectDeltaSourcePublicationState | object) -> ObjectDeltaSourcePublicationAttempt:
    if isinstance(value, ObjectDeltaSourcePublicationAttempt):
        return _require_attempt(value)
    if isinstance(value, ObjectDeltaSourcePublicationSealedAttempt):
        return _require_sealed_attempt(value).attempt
    if isinstance(value, ObjectDeltaSourcePublicationUploadedAttempt):
        return _require_uploaded_attempt(value).sealed.attempt
    if isinstance(value, ObjectDeltaSourcePublicationAttestedAttempt):
        return _require_attested_attempt(value).uploaded.sealed.attempt
    if isinstance(value, ObjectDeltaSourcePublicationLedgeredAttempt):
        return _require_ledgered_attempt(value).attested.uploaded.sealed.attempt
    raise ObjectDeltaSourcePublicationAttemptError("Object-delta source publication state is invalid")


def _sealed_from_state(
    value: ObjectDeltaSourcePublicationState | object,
) -> ObjectDeltaSourcePublicationSealedAttempt | None:
    if isinstance(value, ObjectDeltaSourcePublicationAttempt):
        _require_attempt(value)
        return None
    if isinstance(value, ObjectDeltaSourcePublicationSealedAttempt):
        return _require_sealed_attempt(value)
    if isinstance(value, ObjectDeltaSourcePublicationUploadedAttempt):
        return _require_uploaded_attempt(value).sealed
    if isinstance(value, ObjectDeltaSourcePublicationAttestedAttempt):
        return _require_attested_attempt(value).uploaded.sealed
    if isinstance(value, ObjectDeltaSourcePublicationLedgeredAttempt):
        return _require_ledgered_attempt(value).attested.uploaded.sealed
    raise ObjectDeltaSourcePublicationAttemptError("Object-delta source publication state is invalid")


def _uploaded_from_state(
    value: ObjectDeltaSourcePublicationState | object,
) -> ObjectDeltaSourcePublicationUploadedAttempt | None:
    if isinstance(value, ObjectDeltaSourcePublicationAttempt):
        _require_attempt(value)
        return None
    if isinstance(value, ObjectDeltaSourcePublicationSealedAttempt):
        _require_sealed_attempt(value)
        return None
    if isinstance(value, ObjectDeltaSourcePublicationUploadedAttempt):
        return _require_uploaded_attempt(value)
    if isinstance(value, ObjectDeltaSourcePublicationAttestedAttempt):
        return _require_attested_attempt(value).uploaded
    if isinstance(value, ObjectDeltaSourcePublicationLedgeredAttempt):
        return _require_ledgered_attempt(value).attested.uploaded
    raise ObjectDeltaSourcePublicationAttemptError("Object-delta source publication state is invalid")


def _attested_from_state(
    value: ObjectDeltaSourcePublicationState | object,
) -> ObjectDeltaSourcePublicationAttestedAttempt | None:
    if isinstance(value, ObjectDeltaSourcePublicationAttempt):
        _require_attempt(value)
        return None
    if isinstance(value, ObjectDeltaSourcePublicationSealedAttempt):
        _require_sealed_attempt(value)
        return None
    if isinstance(value, ObjectDeltaSourcePublicationUploadedAttempt):
        _require_uploaded_attempt(value)
        return None
    if isinstance(value, ObjectDeltaSourcePublicationAttestedAttempt):
        return _require_attested_attempt(value)
    if isinstance(value, ObjectDeltaSourcePublicationLedgeredAttempt):
        return _require_ledgered_attempt(value).attested
    raise ObjectDeltaSourcePublicationAttemptError("Object-delta source publication state is invalid")


def _ledgered_from_state(
    value: ObjectDeltaSourcePublicationState | object,
) -> ObjectDeltaSourcePublicationLedgeredAttempt | None:
    if isinstance(value, ObjectDeltaSourcePublicationLedgeredAttempt):
        return _require_ledgered_attempt(value)
    _attempt_from_state(value)
    return None


def _require_same_attempt(
    expected: ObjectDeltaSourcePublicationAttempt,
    actual: ObjectDeltaSourcePublicationAttempt,
) -> None:
    if actual.attempt_id != expected.attempt_id or actual != expected:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication attempt conflicts with its durable identity"
        )


def _require_no_foreign_object_key_attempt(
    candidate: ObjectDeltaSourcePublicationAttempt,
    existing_state: ObjectDeltaSourcePublicationState | object,
) -> None:
    """Reject a second logical attempt targeting an already-reserved key.

    The deterministic Object key intentionally does not include every intent
    field (for example, the recipient and prior-chain hash).  A persistence
    adapter must therefore query/index both attempt ID *and* Object key before
    reserving; otherwise a changed control binding could make a second random
    ciphertext compete for one create-only key.
    """

    existing = _attempt_from_state(existing_state)
    if existing.intent.object_key != candidate.intent.object_key:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication Object-key reservation lookup is invalid"
        )
    _require_same_attempt(candidate, existing)


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationAttemptPlan:
    """A create-only reservation or exact durable replay decision."""

    action: str
    attempt_to_insert: ObjectDeltaSourcePublicationAttempt | None


def plan_object_delta_source_publication_attempt(
    *,
    intent: ObjectDeltaSourcePublicationIntent,
    existing_state: ObjectDeltaSourcePublicationState | None,
    existing_object_key_state: ObjectDeltaSourcePublicationState | None,
) -> ObjectDeltaSourcePublicationAttemptPlan:
    """Reserve an intent once; identity and Object key may only replay exactly.

    ``existing_state`` is the row found by deterministic attempt ID.
    ``existing_object_key_state`` is the independently queried result for the
    immutable Object key.  It is intentionally mandatory, even when its
    value is ``None``: callers must take both lock-scoped lookups before a
    reservation decision.  This closes a control-field change that would
    otherwise create a second random ciphertext for one create-only Object
    key.
    """

    candidate = build_object_delta_source_publication_attempt(intent)
    if existing_object_key_state is not None:
        _require_no_foreign_object_key_attempt(candidate, existing_object_key_state)
    if existing_state is None:
        if existing_object_key_state is not None:
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication attempt-ID and Object-key reservation lookups disagree"
            )
        return ObjectDeltaSourcePublicationAttemptPlan(
            action=SOURCE_PUBLICATION_ATTEMPT_ACTION_RESERVE,
            attempt_to_insert=candidate,
        )
    _require_same_attempt(candidate, _attempt_from_state(existing_state))
    if existing_object_key_state is None:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication attempt-ID and Object-key reservation lookups disagree"
        )
    _require_same_attempt(
        _attempt_from_state(existing_state),
        _attempt_from_state(existing_object_key_state),
    )
    return ObjectDeltaSourcePublicationAttemptPlan(
        action=SOURCE_PUBLICATION_ATTEMPT_ACTION_REPLAY,
        attempt_to_insert=None,
    )


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationSealPlan:
    """A persisted ciphertext-seal action or an exact replay decision."""

    action: str
    sealed_attempt_to_write: ObjectDeltaSourcePublicationSealedAttempt | None


def plan_object_delta_source_publication_seal(
    *,
    attempt: ObjectDeltaSourcePublicationAttempt,
    ciphertext: ObjectDeltaSourcePublicationCiphertextSpool,
    existing_state: ObjectDeltaSourcePublicationState | None,
) -> ObjectDeltaSourcePublicationSealPlan:
    """Persist ciphertext once; a sealed attempt can never be re-encrypted."""

    candidate_attempt = _require_attempt(attempt)
    if not isinstance(ciphertext, ObjectDeltaSourcePublicationCiphertextSpool):
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication ciphertext spool is invalid"
        )
    candidate = ObjectDeltaSourcePublicationSealedAttempt(
        attempt=candidate_attempt,
        ciphertext=ciphertext,
    )
    if existing_state is None:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication ciphertext cannot be sealed before its durable reservation"
        )
    existing_attempt = _attempt_from_state(existing_state)
    _require_same_attempt(candidate_attempt, existing_attempt)
    existing_sealed = _sealed_from_state(existing_state)
    if existing_sealed is None:
        return ObjectDeltaSourcePublicationSealPlan(
            action=SOURCE_PUBLICATION_CIPHERTEXT_ACTION_SEAL,
            sealed_attempt_to_write=candidate,
        )
    if existing_sealed != candidate:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication unresolved attempt cannot be re-encrypted"
        )
    return ObjectDeltaSourcePublicationSealPlan(
        action=SOURCE_PUBLICATION_ATTEMPT_ACTION_REPLAY,
        sealed_attempt_to_write=None,
    )


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationUploadPlan:
    """A persisted exact Object receipt or exact durable replay decision."""

    action: str
    uploaded_attempt_to_write: ObjectDeltaSourcePublicationUploadedAttempt | None


def plan_object_delta_source_publication_exact_upload(
    *,
    sealed_attempt: ObjectDeltaSourcePublicationSealedAttempt,
    receipt: ObjectDeltaSourcePublicationExactReceipt,
    existing_state: ObjectDeltaSourcePublicationState | None,
) -> ObjectDeltaSourcePublicationUploadPlan:
    """Record an exact read-back receipt only after a durable sealed state."""

    sealed = _require_sealed_attempt(sealed_attempt)
    normalized_receipt = _require_exact_receipt_for_sealed(receipt, sealed=sealed)
    candidate = ObjectDeltaSourcePublicationUploadedAttempt(
        sealed=sealed,
        receipt=normalized_receipt,
    )
    if existing_state is None:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication exact receipt has no durable sealed attempt"
        )
    _require_same_attempt(sealed.attempt, _attempt_from_state(existing_state))
    existing_sealed = _sealed_from_state(existing_state)
    if existing_sealed != sealed:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication exact receipt conflicts with the durable ciphertext"
        )
    existing_uploaded = _uploaded_from_state(existing_state)
    if existing_uploaded is None:
        return ObjectDeltaSourcePublicationUploadPlan(
            action=SOURCE_PUBLICATION_UPLOAD_ACTION_RECORD,
            uploaded_attempt_to_write=candidate,
        )
    if existing_uploaded != candidate:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication immutable exact receipt conflicts with replay"
        )
    return ObjectDeltaSourcePublicationUploadPlan(
        action=SOURCE_PUBLICATION_UPLOAD_ACTION_REPLAY,
        uploaded_attempt_to_write=None,
    )


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationObjectHistory:
    """A complete exact-key Object version listing for sealed reconciliation."""

    object_key: str
    version_ids: Sequence[str]
    delete_marker_version_ids: Sequence[str]
    latest_version_id: str | None
    listing_complete: bool

    def __post_init__(self) -> None:
        _require_object_key(self.object_key, label="history Object key")
        if self.listing_complete is not True:
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication Object history listing is incomplete"
            )

        def normalize_ids(value: object, *, label: str) -> tuple[str, ...]:
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                raise ObjectDeltaSourcePublicationAttemptError(
                    f"Object-delta source publication {label} is invalid"
                )
            if len(value) > 100_000:
                raise ObjectDeltaSourcePublicationAttemptError(
                    f"Object-delta source publication {label} is invalid"
                )
            return tuple(_require_version_id(item, label=label) for item in value)

        versions = normalize_ids(self.version_ids, label="history versions")
        delete_markers = normalize_ids(self.delete_marker_version_ids, label="history delete markers")
        if len(set(versions)) != len(versions) or len(set(delete_markers)) != len(delete_markers):
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication Object history is ambiguous"
            )
        if self.latest_version_id is not None:
            _require_version_id(self.latest_version_id, label="history latest version")


def _require_history(value: object) -> ObjectDeltaSourcePublicationObjectHistory:
    if not isinstance(value, ObjectDeltaSourcePublicationObjectHistory):
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication Object history is invalid"
        )
    return ObjectDeltaSourcePublicationObjectHistory(
        object_key=value.object_key,
        version_ids=value.version_ids,
        delete_marker_version_ids=value.delete_marker_version_ids,
        latest_version_id=value.latest_version_id,
        listing_complete=value.listing_complete,
    )


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationReconciliationPlan:
    """Either replay identical stored bytes or adopt one exact singleton."""

    action: str
    receipt_to_record: ObjectDeltaSourcePublicationExactReceipt | None


def plan_object_delta_source_publication_reconciliation(
    *,
    sealed_attempt: ObjectDeltaSourcePublicationSealedAttempt,
    history: ObjectDeltaSourcePublicationObjectHistory,
    singleton_receipt: ObjectDeltaSourcePublicationExactReceipt | None,
    existing_state: ObjectDeltaSourcePublicationState,
) -> ObjectDeltaSourcePublicationReconciliationPlan:
    """Resolve a sealed attempt without ever generating new ciphertext.

    A complete empty listing permits only a conditional PUT of the exact bytes
    whose hash/length are in ``sealed_attempt``.  A complete one-version
    listing permits adoption only if an exact VersionId read-back produces the
    supplied matching receipt.  Any other history remains blocked for manual
    reconciliation; this function deliberately offers no overwrite/delete or
    fresh-encryption action.
    """

    sealed = _require_sealed_attempt(sealed_attempt)
    # An already uploaded/attested/terminal row must not run Object Storage
    # reconciliation again.  The exact receipt is already durable and a new
    # PUT decision would only expand the ambiguity surface.
    if type(existing_state) is not ObjectDeltaSourcePublicationSealedAttempt:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication reconciliation requires a sealed unresolved attempt"
        )
    durable_sealed = _require_sealed_attempt(existing_state)
    if durable_sealed != sealed:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication reconciliation conflicts with the durable ciphertext"
        )
    observed = _require_history(history)
    if observed.object_key != sealed.attempt.intent.object_key:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication Object history belongs to another attempt"
        )
    if (
        not observed.version_ids
        and not observed.delete_marker_version_ids
        and observed.latest_version_id is None
    ):
        if singleton_receipt is not None:
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication empty Object history cannot adopt a receipt"
            )
        return ObjectDeltaSourcePublicationReconciliationPlan(
            action=SOURCE_PUBLICATION_RECONCILIATION_ACTION_EXACT_PUT_REPLAY,
            receipt_to_record=None,
        )

    if singleton_receipt is None:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication existing Object requires exact singleton read-back"
        )
    receipt = _require_exact_receipt_for_sealed(singleton_receipt, sealed=sealed)
    if (
        len(observed.version_ids) != 1
        or observed.delete_marker_version_ids
        or observed.latest_version_id != observed.version_ids[0]
        or observed.version_ids[0] != receipt.object_version_id
    ):
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication existing Object is not one safe immutable singleton"
        )
    return ObjectDeltaSourcePublicationReconciliationPlan(
        action=SOURCE_PUBLICATION_RECONCILIATION_ACTION_ADOPT,
        receipt_to_record=receipt,
    )


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationAttestationPlan:
    """One immutable raw source-attestation artifact or exact replay."""

    action: str
    attested_attempt_to_write: ObjectDeltaSourcePublicationAttestedAttempt | None


def plan_object_delta_source_publication_attestation(
    *,
    uploaded_attempt: ObjectDeltaSourcePublicationUploadedAttempt,
    attestation: ObjectDeltaSourcePublicationAttestationArtifact,
    existing_state: ObjectDeltaSourcePublicationState | None,
) -> ObjectDeltaSourcePublicationAttestationPlan:
    """Persist a source-attestation hash once, after exact receipt persistence."""

    uploaded = _require_uploaded_attempt(uploaded_attempt)
    if not isinstance(attestation, ObjectDeltaSourcePublicationAttestationArtifact):
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication attestation artifact is invalid"
        )
    candidate = ObjectDeltaSourcePublicationAttestedAttempt(uploaded=uploaded, attestation=attestation)
    if existing_state is None:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication attestation has no durable exact receipt"
        )
    _require_same_attempt(uploaded.sealed.attempt, _attempt_from_state(existing_state))
    existing_uploaded = _uploaded_from_state(existing_state)
    if existing_uploaded != uploaded:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication attestation conflicts with the durable exact receipt"
        )
    existing_attested = _attested_from_state(existing_state)
    if existing_attested is None:
        return ObjectDeltaSourcePublicationAttestationPlan(
            action=SOURCE_PUBLICATION_ATTESTATION_ACTION_RECORD,
            attested_attempt_to_write=candidate,
        )
    if existing_attested != candidate:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication immutable source attestation conflicts with replay"
        )
    return ObjectDeltaSourcePublicationAttestationPlan(
        action=SOURCE_PUBLICATION_ATTESTATION_ACTION_REPLAY,
        attested_attempt_to_write=None,
    )


def _require_matching_ledger_entry(
    entry: object,
    *,
    attested: ObjectDeltaSourcePublicationAttestedAttempt,
) -> SourceBatchLedgerEntry:
    if not isinstance(entry, SourceBatchLedgerEntry):
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication source ledger entry is invalid"
        )
    intent = attested.uploaded.sealed.attempt.intent
    receipt = attested.uploaded.receipt
    try:
        normalized = SourceBatchLedgerEntry(
            stream=entry.stream,
            first_sequence=entry.first_sequence,
            last_sequence=entry.last_sequence,
            writer_epoch=entry.writer_epoch,
            writer_lease_id=entry.writer_lease_id,
            prior_chain_sha256=entry.prior_chain_sha256,
            batch_sha256=entry.batch_sha256,
            payload_sha256=entry.payload_sha256,
            payload_bytes=entry.payload_bytes,
            object_key=entry.object_key,
            object_version_id=entry.object_version_id,
            ciphertext_sha256=entry.ciphertext_sha256,
            ciphertext_bytes=entry.ciphertext_bytes,
        )
    except (ObjectDeltaSourceLedgerError, TypeError) as exc:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication source ledger entry is invalid"
        ) from exc
    expected = (
        intent.stream,
        intent.first_sequence,
        intent.last_sequence,
        intent.writer_epoch,
        intent.writer_lease_id,
        intent.prior_chain_sha256,
        attested.attestation.batch_sha256,
        intent.payload_sha256,
        intent.payload_bytes,
        intent.object_key,
        receipt.object_version_id,
        receipt.ciphertext_sha256,
        receipt.ciphertext_bytes,
    )
    actual = (
        normalized.stream,
        normalized.first_sequence,
        normalized.last_sequence,
        normalized.writer_epoch,
        normalized.writer_lease_id,
        normalized.prior_chain_sha256,
        normalized.batch_sha256,
        normalized.payload_sha256,
        normalized.payload_bytes,
        normalized.object_key,
        normalized.object_version_id,
        normalized.ciphertext_sha256,
        normalized.ciphertext_bytes,
    )
    if actual != expected:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication source ledger entry does not match the attested attempt"
        )
    return normalized


def _require_ledgered_attempt(
    value: object,
) -> ObjectDeltaSourcePublicationLedgeredAttempt:
    if not isinstance(value, ObjectDeltaSourcePublicationLedgeredAttempt):
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication terminal ledger state is invalid"
        )
    attested = _require_attested_attempt(value.attested)
    entry = _require_matching_ledger_entry(value.ledger_entry, attested=attested)
    return ObjectDeltaSourcePublicationLedgeredAttempt(
        attested=attested,
        ledger_entry=entry,
    )


@dataclass(frozen=True)
class ObjectDeltaSourcePublicationLedgerPlan:
    """Append/replay decision after an immutable source attestation exists."""

    action: str
    ledger_entry: SourceBatchLedgerEntry
    ledgered_attempt_to_write: ObjectDeltaSourcePublicationLedgeredAttempt | None


def plan_object_delta_source_publication_ledger(
    *,
    attested_attempt: ObjectDeltaSourcePublicationAttestedAttempt,
    candidate_ledger_entry: SourceBatchLedgerEntry,
    existing_state: ObjectDeltaSourcePublicationState,
    existing_ledger_entry: SourceBatchLedgerEntry | None,
) -> ObjectDeltaSourcePublicationLedgerPlan:
    """Atomically terminalize/replay only the exact attested ledger binding.

    The caller must load ``existing_state`` and the independently locked
    source-ledger row in one transaction.  A terminal state without its exact
    ledger row, or a ledger row without a terminal state, is an invariant
    failure rather than a recoverable reason to issue another write.
    """

    attested = _require_attested_attempt(attested_attempt)
    candidate = _require_matching_ledger_entry(candidate_ledger_entry, attested=attested)
    durable_attempt = _attempt_from_state(existing_state)
    _require_same_attempt(attested.uploaded.sealed.attempt, durable_attempt)
    durable_attested = _attested_from_state(existing_state)
    if durable_attested != attested:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication ledger conflicts with the durable source attestation"
        )
    durable_ledgered = _ledgered_from_state(existing_state)
    if durable_ledgered is None:
        if existing_ledger_entry is not None:
            _require_matching_ledger_entry(existing_ledger_entry, attested=attested)
            raise ObjectDeltaSourcePublicationAttemptError(
                "Object-delta source publication source ledger exists without terminal attempt state"
            )
        return ObjectDeltaSourcePublicationLedgerPlan(
            action=SOURCE_PUBLICATION_LEDGER_ACTION_APPEND,
            ledger_entry=candidate,
            ledgered_attempt_to_write=ObjectDeltaSourcePublicationLedgeredAttempt(
                attested=attested,
                ledger_entry=candidate,
            ),
        )
    if existing_ledger_entry is None:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication terminal attempt is missing its source ledger"
        )
    existing = _require_matching_ledger_entry(existing_ledger_entry, attested=attested)
    if existing != candidate or durable_ledgered.ledger_entry != candidate:
        raise ObjectDeltaSourcePublicationAttemptError(
            "Object-delta source publication immutable source ledger replay conflicts"
        )
    return ObjectDeltaSourcePublicationLedgerPlan(
        action=SOURCE_PUBLICATION_LEDGER_ACTION_REPLAY,
        ledger_entry=existing,
        ledgered_attempt_to_write=None,
    )


REQUIRED_OBJECT_DELTA_SOURCE_PUBLICATION_ATTEMPT_PERSISTENCE = (
    "derive the intent's transport-policy SHA-256 from the root-pinned canonical policy and retain the exact canonical source-cutover artifact hash/byte count before reservation",
    "derive and unique-index attempt_id from the complete immutable intent and separately unique-index the immutable Object key before encryption",
    "persist the reserved attempt before creating ciphertext or issuing a PUT",
    "write exact ciphertext bytes to a root-only content-addressed spool and persist sealed hash/byte evidence before every PUT",
    "on every resume of a sealed unresolved attempt, list all versions and delete markers for the exact Object key completely before any Object action",
    "when the listing is empty, PUT only the already-spooled exact bytes with conditional create-only semantics; never encrypt replacement bytes",
    "when the listing is one matching singleton, perform exact-VersionId read-back and persist the canonical transport receipt hash/bytes before ledger work",
    "reject incomplete listings, delete markers, multiple versions, changed metadata/hash/size, or any Object history that is not the exact singleton",
    "verify canonical raw source-attestation bytes with the pinned source key and persist its hash/byte count plus batch hash before source-ledger append/replay",
    "lock the stream, source cutover, attempt row, terminal ledger, same range/batch/object rows in deterministic order and re-check all bindings",
    "append/replay the immutable source ledger and transition the attempt to its ledger-bound terminal state atomically; never update ciphertext, receipt, attestation, or ledger binding in place",
)
