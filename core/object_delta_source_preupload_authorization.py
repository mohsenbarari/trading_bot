"""Pure, non-circular authorization for one locked Object-delta pre-upload.

The pre-upload boundary exists before encryption, Object Storage, and the
final source-batch attestation.  It consequently cannot consume the older
post-upload authorization capability without becoming circular.  Its only
source-side input is an opaque
``ObjectDeltaLockedSourcePublicationSnapshot`` minted by the local lock
adapter in the caller's still-open transaction.  The gate combines that
snapshot with a root-pinned stream/policy/key and raw canonical signed
cutover evidence to derive one immutable reservation intent and attempt.

This module is deliberately pure: it performs no database, filesystem,
spool, encryption, Object Storage, credential, subprocess, network, worker,
or runtime-enable action.  A snapshot capability proves only that the lock
adapter produced a structurally revalidated view; Python cannot prove here
that the caller still holds its original database transaction.  The future
root-only coordinator must therefore call this gate in that same transaction,
keep the source cutover/outbox/frontier rows locked through reservation, and
independently validate the live Writer Witness term immediately before its
private durable reservation operation.

Projected ``ObjectDeltaSourcePublicationIntent`` and ``...Attempt`` values
are diagnostic data, not authority.  A future reservation API must accept
only this opaque capability and re-project/revalidate it itself; it must
never accept a caller-supplied raw intent, attempt, prepared payload, or
prior-chain value as permission to reserve, seal, or PUT.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.append_only_sync_delta_batch import LEASE_ID_RE, SHA256_RE, WriterTermBinding, sha256_bytes
from core.object_delta_batch_assembler import (
    ObjectDeltaBatchAssemblyError,
    PreparedObjectDeltaPayload,
    _require_prepared_object_delta_payload_provenance,
)
from core.object_delta_runtime_binding import (
    ObjectDeltaRuntimeBindingError,
    ObjectDeltaSourceRuntimeBinding,
)
from core.object_delta_source_batch_attestation import (
    ObjectDeltaSourceBatchAttestationError,
    source_key_id_from_public_key,
)
from core.object_delta_source_batch_ledger import (
    GENESIS_PRIOR_CHAIN_SHA256,
    ObjectDeltaSourceLedgerError,
    SourceBatchLedgerEntry,
    SourceStreamIdentity,
)
from core.object_delta_source_cutover_attestation import (
    ObjectDeltaSourceCutoverAttestationError,
    VerifiedObjectDeltaSourceCutoverAttestation,
    canonical_object_delta_source_cutover_attestation_bytes,
    parse_object_delta_source_cutover_attestation_json,
    verify_object_delta_source_cutover_attestation,
)
from core.object_delta_source_cutover_publication_gate import (
    ObjectDeltaSourceCutoverPublicationGateError,
    ObjectDeltaSourceCutoverPublicationPin,
)
from core.object_delta_source_publication_attempt import (
    ObjectDeltaSourcePublicationAttempt,
    ObjectDeltaSourcePublicationAttemptError,
    ObjectDeltaSourcePublicationIntent,
    build_object_delta_source_publication_attempt,
    derive_object_delta_source_transport_policy_sha256,
)
from core.object_delta_source_publication_snapshot import (
    ObjectDeltaLockedSourcePublicationSnapshot,
    ObjectDeltaLockedSourcePublicationSnapshotError,
    require_locked_object_delta_source_publication_snapshot,
)
from core.object_delta_transport_binding import (
    ObjectDeltaTransportBindingError,
    derive_object_delta_object_key,
    destination_age_recipient,
    validate_object_delta_transport_policy,
)


OBJECT_DELTA_SOURCE_PREUPLOAD_AUTHORIZATION_SCHEMA = (
    "gold-trade-object-delta-source-preupload-authorization-v1"
)


class ObjectDeltaSourcePreuploadAuthorizationError(ValueError):
    """A pre-upload reservation input is not root-pinned, locked, and exact."""


_AUTHORIZED_SOURCE_PREUPLOAD_CAPABILITY = object()


@dataclass(frozen=True)
class AuthorizedObjectDeltaSourcePreupload:
    """Opaque authority for one exact pre-encryption publication attempt.

    ``locked_snapshot`` is retained solely so every later capability use can
    repeat the lock-adapter structural checks and bind intent facts to exactly
    the same source prefix.  It is not a portable lock lease: callers must
    retain the original database transaction until the future coordinator has
    durably reserved the attempt.
    """

    pin: ObjectDeltaSourceCutoverPublicationPin
    locked_snapshot: ObjectDeltaLockedSourcePublicationSnapshot
    source_cutover_attestation: bytes
    intent: ObjectDeltaSourcePublicationIntent
    attempt: ObjectDeltaSourcePublicationAttempt
    _capability: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class _ValidatedLockedSourcePreupload:
    """Private normalized facts returned after each locked-snapshot check."""

    snapshot: ObjectDeltaLockedSourcePublicationSnapshot
    binding: ObjectDeltaSourceRuntimeBinding
    stream: SourceStreamIdentity
    source_stream_id: int
    cutover_term: WriterTermBinding
    terminal_ledger_entry: SourceBatchLedgerEntry | None
    prior_chain_sha256: str
    prepared_payload: PreparedObjectDeltaPayload


@dataclass(frozen=True)
class _ValidatedSourcePreupload:
    """Private normalized facts returned after every opaque-capability check."""

    pin: ObjectDeltaSourceCutoverPublicationPin
    locked_snapshot: _ValidatedLockedSourcePreupload
    cutover: VerifiedObjectDeltaSourceCutoverAttestation
    intent: ObjectDeltaSourcePublicationIntent
    attempt: ObjectDeltaSourcePublicationAttempt


def _normalized_binding(value: object) -> ObjectDeltaSourceRuntimeBinding:
    if type(value) is not ObjectDeltaSourceRuntimeBinding:
        raise ObjectDeltaSourcePreuploadAuthorizationError("source pre-upload binding is invalid")
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
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload binding is invalid"
        ) from exc


def _normalized_pin(value: object) -> ObjectDeltaSourceCutoverPublicationPin:
    """Reconstruct all root-pinned fields before trusting a frozen dataclass."""

    if type(value) is not ObjectDeltaSourceCutoverPublicationPin:
        raise ObjectDeltaSourcePreuploadAuthorizationError("source pre-upload pin is invalid")
    try:
        binding = _normalized_binding(value.binding)
        if not isinstance(value.expected_source_public_key, bytes):
            raise ObjectDeltaSourcePreuploadAuthorizationError(
                "source pre-upload pinned source key is invalid"
            )
        # This validates exact Ed25519 public-key shape as well as deriving
        # the stable identifier checked again against the signed cutover.
        source_key_id_from_public_key(value.expected_source_public_key)
        policy = validate_object_delta_transport_policy(value.transport_policy)
        return ObjectDeltaSourceCutoverPublicationPin(
            binding=binding,
            expected_source_public_key=value.expected_source_public_key,
            transport_policy=policy,
        )
    except ObjectDeltaSourcePreuploadAuthorizationError:
        raise
    except (
        AttributeError,
        TypeError,
        ValueError,
        ObjectDeltaSourceBatchAttestationError,
        ObjectDeltaSourceCutoverPublicationGateError,
        ObjectDeltaTransportBindingError,
    ) as exc:
        raise ObjectDeltaSourcePreuploadAuthorizationError("source pre-upload pin is invalid") from exc


def _canonical_cutover_attestation(value: object) -> bytes:
    """Accept only raw canonical signed cutover evidence, never a dataclass."""

    if not isinstance(value, (bytes, str)):
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload cutover evidence must be raw canonical bytes"
        )
    try:
        parsed = parse_object_delta_source_cutover_attestation_json(value)
        return canonical_object_delta_source_cutover_attestation_bytes(parsed)
    except ObjectDeltaSourceCutoverAttestationError as exc:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            f"source pre-upload cutover evidence is invalid: {exc}"
        ) from exc


def _verified_cutover(
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
        expected_key_id = source_key_id_from_public_key(pin.expected_source_public_key)
    except (
        ObjectDeltaSourceCutoverAttestationError,
        ObjectDeltaSourceBatchAttestationError,
    ) as exc:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            f"source pre-upload cutover evidence is invalid: {exc}"
        ) from exc
    if type(verified) is not VerifiedObjectDeltaSourceCutoverAttestation:
        raise ObjectDeltaSourcePreuploadAuthorizationError("source pre-upload cutover is invalid")
    if (
        verified.source_key_id != expected_key_id
        or verified.baseline.source_key_id != expected_key_id
        or verified.baseline.manifest_sha256 != verified.baseline_manifest_sha256
    ):
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload cutover baseline or signer does not match the root pin"
        )
    return verified


def _normalized_writer_term(value: object, *, label: str) -> WriterTermBinding:
    if type(value) is not WriterTermBinding:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            f"source pre-upload {label} Writer Witness term is invalid"
        )
    if (
        type(value.epoch) is not int
        or value.epoch < 1
        or not isinstance(value.lease_id, str)
        or LEASE_ID_RE.fullmatch(value.lease_id) is None
    ):
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            f"source pre-upload {label} Writer Witness term is invalid"
        )
    return WriterTermBinding(epoch=value.epoch, lease_id=value.lease_id)


def _normalized_stream(value: object) -> SourceStreamIdentity:
    if type(value) is not SourceStreamIdentity:
        raise ObjectDeltaSourcePreuploadAuthorizationError("source pre-upload snapshot stream is invalid")
    try:
        return SourceStreamIdentity(
            source_site=value.source_site,
            destination_site=value.destination_site,
            campaign_id=value.campaign_id,
            release_sha=value.release_sha,
            stream_generation_id=value.stream_generation_id,
        )
    except (AttributeError, TypeError, ObjectDeltaSourceLedgerError) as exc:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload snapshot stream is invalid"
        ) from exc


def _normalized_terminal_entry(
    value: object,
    *,
    stream: SourceStreamIdentity,
) -> SourceBatchLedgerEntry | None:
    if value is None:
        return None
    if type(value) is not SourceBatchLedgerEntry:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload snapshot terminal ledger is invalid"
        )
    try:
        terminal = SourceBatchLedgerEntry(
            stream=value.stream,
            first_sequence=value.first_sequence,
            last_sequence=value.last_sequence,
            writer_epoch=value.writer_epoch,
            writer_lease_id=value.writer_lease_id,
            prior_chain_sha256=value.prior_chain_sha256,
            batch_sha256=value.batch_sha256,
            payload_sha256=value.payload_sha256,
            payload_bytes=value.payload_bytes,
            object_key=value.object_key,
            object_version_id=value.object_version_id,
            ciphertext_sha256=value.ciphertext_sha256,
            ciphertext_bytes=value.ciphertext_bytes,
        )
    except (AttributeError, TypeError, ObjectDeltaSourceLedgerError) as exc:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload snapshot terminal ledger is invalid"
        ) from exc
    if terminal.stream != stream:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload snapshot terminal ledger does not match the stream"
        )
    return terminal


def _validated_payload(
    value: object,
    *,
    pin: ObjectDeltaSourceCutoverPublicationPin,
    stream: SourceStreamIdentity,
    cutover_term: WriterTermBinding,
    expected_first_sequence: int,
) -> PreparedObjectDeltaPayload:
    """Revalidate assembler provenance, canonical bytes, stream, term, and frontier."""

    try:
        prepared = _require_prepared_object_delta_payload_provenance(
            value,
            expected_registry_fingerprint=pin.binding.expected_registry_fingerprint,
        )
    except ObjectDeltaBatchAssemblyError as exc:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            f"source pre-upload snapshot prepared payload provenance is invalid: {exc}"
        ) from exc
    if prepared.stream != stream:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload snapshot prepared payload stream does not match the locked stream"
        )
    term = _normalized_writer_term(prepared.writer_term, label="prepared payload")
    if term != cutover_term:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload snapshot prepared payload Writer Witness term does not match the locked cutover"
        )
    if prepared.first_sequence != expected_first_sequence:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload snapshot prepared payload does not begin at the locked ledger frontier"
        )
    return prepared


def _validated_locked_snapshot(
    value: object,
    *,
    pin: ObjectDeltaSourceCutoverPublicationPin,
    cutover: VerifiedObjectDeltaSourceCutoverAttestation,
) -> _ValidatedLockedSourcePreupload:
    """Require the opaque lock-adapter result and independently cross-check it.

    The adapter's opaque marker proves that only its fixed-order transaction
    reader could mint the snapshot.  The repetition here intentionally makes
    every authorization use bind the snapshot to this root pin and this exact
    signed cutover, rather than treating a public frozen dataclass as proof.
    """

    try:
        snapshot = require_locked_object_delta_source_publication_snapshot(value)
    except ObjectDeltaLockedSourcePublicationSnapshotError as exc:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            f"source pre-upload locked snapshot is invalid: {exc}"
        ) from exc
    if type(snapshot) is not ObjectDeltaLockedSourcePublicationSnapshot:
        raise ObjectDeltaSourcePreuploadAuthorizationError("source pre-upload locked snapshot is invalid")
    binding = _normalized_binding(snapshot.binding)
    if snapshot.binding != binding or binding != pin.binding:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload locked snapshot binding does not match the root pin"
        )
    stream = _normalized_stream(snapshot.stream)
    expected_stream = SourceStreamIdentity(
        source_site=binding.source_site,
        destination_site=binding.destination_site,
        campaign_id=binding.campaign_id,
        release_sha=binding.release_sha,
        stream_generation_id=binding.stream_generation_id,
    )
    if snapshot.stream != stream or stream != expected_stream:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload locked snapshot stream does not match the root pin"
        )
    if type(snapshot.source_stream_id) is not int or snapshot.source_stream_id < 1:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload locked snapshot source stream id is invalid"
        )
    cutover_term = _normalized_writer_term(snapshot.cutover_writer_term, label="locked cutover")
    if (cutover_term.epoch, cutover_term.lease_id) != (
        cutover.writer_epoch,
        cutover.writer_lease_id,
    ):
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload locked snapshot Writer Witness term does not match the signed cutover"
        )
    terminal = _normalized_terminal_entry(snapshot.terminal_ledger_entry, stream=stream)
    if terminal is not None and (
        terminal.writer_epoch,
        terminal.writer_lease_id,
    ) != (cutover_term.epoch, cutover_term.lease_id):
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload locked snapshot terminal ledger term does not match the signed cutover"
        )
    expected_prior_chain = terminal.batch_sha256 if terminal is not None else GENESIS_PRIOR_CHAIN_SHA256
    if (
        not isinstance(snapshot.prior_chain_sha256, str)
        or SHA256_RE.fullmatch(snapshot.prior_chain_sha256) is None
        or snapshot.prior_chain_sha256 != expected_prior_chain
    ):
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload locked snapshot prior chain does not match the terminal ledger"
        )
    if snapshot.prepared_payload is None:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "source pre-upload locked snapshot has no publishable outbox prefix"
        )
    prepared = _validated_payload(
        snapshot.prepared_payload,
        pin=pin,
        stream=stream,
        cutover_term=cutover_term,
        expected_first_sequence=(terminal.last_sequence + 1 if terminal is not None else 1),
    )
    return _ValidatedLockedSourcePreupload(
        snapshot=snapshot,
        binding=binding,
        stream=stream,
        source_stream_id=snapshot.source_stream_id,
        cutover_term=cutover_term,
        terminal_ledger_entry=terminal,
        prior_chain_sha256=snapshot.prior_chain_sha256,
        prepared_payload=prepared,
    )


def _derive_intent_and_attempt(
    *,
    pin: ObjectDeltaSourceCutoverPublicationPin,
    raw_cutover: bytes,
    locked_snapshot: _ValidatedLockedSourcePreupload,
) -> tuple[ObjectDeltaSourcePublicationIntent, ObjectDeltaSourcePublicationAttempt]:
    """Derive all attempt facts from verified root pin, cutover, and snapshot."""

    prepared = locked_snapshot.prepared_payload
    stream = locked_snapshot.stream
    try:
        policy = validate_object_delta_transport_policy(pin.transport_policy)
        policy_sha256 = derive_object_delta_source_transport_policy_sha256(policy)
        recipient = destination_age_recipient(policy, destination_site=stream.destination_site)
        object_key = derive_object_delta_object_key(
            policy,
            source_site=stream.source_site,
            destination_site=stream.destination_site,
            campaign_id=stream.campaign_id,
            release_sha=stream.release_sha,
            stream_generation_id=stream.stream_generation_id,
            first_sequence=prepared.first_sequence,
            last_sequence=prepared.last_sequence,
            payload_sha256=prepared.payload_sha256,
        )
        intent = ObjectDeltaSourcePublicationIntent(
            stream=stream,
            writer_epoch=locked_snapshot.cutover_term.epoch,
            writer_lease_id=locked_snapshot.cutover_term.lease_id,
            first_sequence=prepared.first_sequence,
            last_sequence=prepared.last_sequence,
            prior_chain_sha256=locked_snapshot.prior_chain_sha256,
            payload_sha256=prepared.payload_sha256,
            payload_bytes=prepared.payload_bytes,
            object_key=object_key,
            destination_age_recipient=recipient,
            transport_policy_sha256=policy_sha256,
            source_cutover_artifact_sha256=sha256_bytes(raw_cutover),
            source_cutover_artifact_bytes=len(raw_cutover),
        )
        return intent, build_object_delta_source_publication_attempt(intent)
    except (
        ObjectDeltaTransportBindingError,
        ObjectDeltaSourcePublicationAttemptError,
    ) as exc:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            f"source pre-upload publication intent is invalid: {exc}"
        ) from exc


def _validated_authorization(value: object) -> _ValidatedSourcePreupload:
    if type(value) is not AuthorizedObjectDeltaSourcePreupload:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "authorized source pre-upload capability is required"
        )
    if value._capability is not _AUTHORIZED_SOURCE_PREUPLOAD_CAPABILITY:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "authorized source pre-upload capability was not verified"
        )
    if not isinstance(value.source_cutover_attestation, bytes):
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "authorized source pre-upload cutover artifact is invalid"
        )
    pin = _normalized_pin(value.pin)
    raw_cutover = _canonical_cutover_attestation(value.source_cutover_attestation)
    if raw_cutover != value.source_cutover_attestation:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "authorized source pre-upload cutover artifact is not canonical"
        )
    cutover = _verified_cutover(raw_cutover, pin=pin)
    locked_snapshot = _validated_locked_snapshot(value.locked_snapshot, pin=pin, cutover=cutover)
    intent, attempt = _derive_intent_and_attempt(
        pin=pin,
        raw_cutover=raw_cutover,
        locked_snapshot=locked_snapshot,
    )
    if type(value.intent) is not ObjectDeltaSourcePublicationIntent or value.intent != intent:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "authorized source pre-upload intent does not match verified evidence"
        )
    if type(value.attempt) is not ObjectDeltaSourcePublicationAttempt or value.attempt != attempt:
        raise ObjectDeltaSourcePreuploadAuthorizationError(
            "authorized source pre-upload attempt does not match verified evidence"
        )
    return _ValidatedSourcePreupload(
        pin=pin,
        locked_snapshot=locked_snapshot,
        cutover=cutover,
        intent=intent,
        attempt=attempt,
    )


def authorize_object_delta_source_preupload(
    *,
    pin: ObjectDeltaSourceCutoverPublicationPin,
    locked_snapshot: ObjectDeltaLockedSourcePublicationSnapshot,
    source_cutover_attestation: bytes | str,
) -> AuthorizedObjectDeltaSourcePreupload:
    """Mint opaque authority for one exact locked source publication prefix.

    ``locked_snapshot`` must have been minted by the source lock adapter in
    the caller's active transaction.  This pure boundary verifies its opaque
    provenance, root pin, signed cutover, baseline, exact stream/frontier,
    canonical plaintext, and Writer Witness term.  It does not replace the
    future coordinator's responsibility to verify that same transaction and
    the live Writer Witness term before durable reservation.
    """

    normalized_pin = _normalized_pin(pin)
    raw_cutover = _canonical_cutover_attestation(source_cutover_attestation)
    cutover = _verified_cutover(raw_cutover, pin=normalized_pin)
    validated_snapshot = _validated_locked_snapshot(
        locked_snapshot,
        pin=normalized_pin,
        cutover=cutover,
    )
    intent, attempt = _derive_intent_and_attempt(
        pin=normalized_pin,
        raw_cutover=raw_cutover,
        locked_snapshot=validated_snapshot,
    )
    authorized = AuthorizedObjectDeltaSourcePreupload(
        pin=normalized_pin,
        locked_snapshot=validated_snapshot.snapshot,
        source_cutover_attestation=raw_cutover,
        intent=intent,
        attempt=attempt,
    )
    object.__setattr__(authorized, "_capability", _AUTHORIZED_SOURCE_PREUPLOAD_CAPABILITY)
    _validated_authorization(authorized)
    return authorized


def require_authorized_object_delta_source_preupload(
    value: object,
) -> AuthorizedObjectDeltaSourcePreupload:
    """Revalidate opaque pre-upload authority before every reservation action."""

    _validated_authorization(value)
    return value


def project_authorized_object_delta_source_preupload_intent(
    authorization: AuthorizedObjectDeltaSourcePreupload,
) -> ObjectDeltaSourcePublicationIntent:
    """Project the exact derived intent; this returned data is not authority."""

    return _validated_authorization(authorization).intent


def project_authorized_object_delta_source_preupload_attempt(
    authorization: AuthorizedObjectDeltaSourcePreupload,
) -> ObjectDeltaSourcePublicationAttempt:
    """Project the exact deterministic attempt; this returned data is not authority."""

    return _validated_authorization(authorization).attempt


__all__ = (
    "AuthorizedObjectDeltaSourcePreupload",
    "OBJECT_DELTA_SOURCE_PREUPLOAD_AUTHORIZATION_SCHEMA",
    "ObjectDeltaSourcePreuploadAuthorizationError",
    "authorize_object_delta_source_preupload",
    "project_authorized_object_delta_source_preupload_attempt",
    "project_authorized_object_delta_source_preupload_intent",
    "require_authorized_object_delta_source_preupload",
)
