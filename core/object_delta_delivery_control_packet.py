"""Pure controller-signed delivery packets for Object-delta batches.

This contract is intentionally below every Object Storage, SSH, filesystem,
database, and worker adapter.  It lets a controller attest to the *identity*
of one immutable encrypted Object version without putting a presigned URL,
provider credential, or plaintext payload in the packet.  A receiver must use
the verified packet only with its fixed local Object Storage configuration,
then age-decrypt and validate the received batch before calling the post-
download binding check in this module.

An already-loaded controller signer is accepted only as an in-memory argument
to ``sign_object_delta_delivery_control_packet``.  This module never opens a
key file, invokes a command, or persists a packet.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any

from core.append_only_sync_delta_batch import (
    AppendOnlySyncDeltaBatch,
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    MAX_DELTA_PAYLOAD_BYTES,
    MAX_STREAM_SEQUENCE_IDS,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import (
    AGE_RECIPIENT_RE,
    BUCKET_RE,
    OBJECT_DELTA_ENCRYPTION,
    OBJECT_DELTA_TRANSPORT_SCHEMA,
    ObjectDeltaTransportBinding,
    ObjectDeltaTransportBindingError,
    ObjectDeltaTransportPolicy,
    bind_object_delta_batch,
    derive_object_delta_object_key,
    destination_age_recipient,
    validate_object_delta_transport_policy,
)


OBJECT_DELTA_DELIVERY_CONTROL_PACKET_SCHEMA = "gold-trade-object-delta-delivery-control-packet-v1"
OBJECT_DELTA_DELIVERY_CONTROL_PACKET_STATUS = "sealed"
OBJECT_DELTA_DELIVERY_CONTROL_PACKET_SIGNATURE_ALGORITHM = "ed25519"
OBJECT_DELTA_DELIVERY_CONTROL_PACKET_SIGNATURE_DOMAIN = (
    b"gold-trade-object-delta-delivery-control-packet-v1\x00"
)
OBJECT_DELTA_DELIVERY_OBJECT_KIND = "sync_delta_batch"
OBJECT_DELTA_PROVIDER_SIDE_ENCRYPTION = "none"
MAX_CONTROL_PACKET_TTL = timedelta(minutes=5)

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_NONCE_RE = re.compile(r"^[a-f0-9]{64}$")
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$")

_UNSIGNED_PACKET_FIELDS = frozenset(
    {
        "schema",
        "status",
        "issued_at",
        "expires_at",
        "nonce",
        "controller_signer",
        "delivery",
    }
)
_SEALED_PACKET_FIELDS = _UNSIGNED_PACKET_FIELDS | frozenset({"controller_signature"})
_CONTROLLER_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_DELIVERY_FIELDS = frozenset(
    {
        "transport_schema",
        "object_kind",
        "encryption",
        "provider_side_encryption",
        "bucket",
        "source_site",
        "destination_site",
        "destination_age_recipient",
        "campaign_id",
        "release_sha",
        "writer_epoch",
        "writer_lease_id",
        "stream_generation_id",
        "first_sequence",
        "last_sequence",
        "prior_chain_sha256",
        "batch_sha256",
        "payload_sha256",
        "object_key",
        "object_version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
    }
)


class ObjectDeltaDeliveryControlPacketError(ValueError):
    """The controller delivery packet is invalid, expired, or unbound."""


# A dataclass carrying claims is not itself proof that the controller signed
# them.  The token is intentionally private and process-local: it is minted
# only after ``verify_object_delta_delivery_control_packet`` has checked the
# pinned controller key and signature.  Direct construction and
# ``dataclasses.replace`` leave it unset.
_VERIFIED_DELIVERY_CONTROL_PACKET_CAPABILITY = object()


@dataclass(frozen=True)
class VerifiedObjectDeltaDeliveryControlPacket:
    """Controller-authenticated metadata only; no plaintext payload bytes.

    Consumers must call
    :func:`revalidate_verified_object_delta_delivery_control_packet` rather
    than trust ``isinstance``.  The private capability is attached only by
    the pinned-signature verifier below.
    """

    issued_at: datetime
    expires_at: datetime
    nonce: str
    controller_key_id: str
    bucket: str
    source_site: str
    destination_site: str
    destination_age_recipient: str
    campaign_id: str
    release_sha: str
    writer_epoch: int
    writer_lease_id: str
    stream_generation_id: str
    first_sequence: int
    last_sequence: int
    prior_chain_sha256: str
    batch_sha256: str
    payload_sha256: str
    object_key: str
    object_version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class ObjectDeltaReceiverDeliveryPermit:
    """Non-secret local permission for one release-bound receiver route.

    A future receiver adapter must load this value only from a root-only local
    permit.  Keeping the loader outside this pure module prevents a delivery
    packet from selecting a filesystem path or weakening local ownership rules.
    The exact Writer Witness term makes a permit unable to authorize an older
    or different source-writer epoch.
    """

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    bucket: str
    destination_age_recipient: str
    controller_key_id: str
    writer_epoch: int
    writer_lease_id: str


def _require_exact_mapping(value: object, *, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ObjectDeltaDeliveryControlPacketError(f"{label} fields are invalid")
    return dict(value)


def _require_text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ObjectDeltaDeliveryControlPacketError(f"{label} is invalid")
    return value


def _require_positive_int(value: object, *, label: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ObjectDeltaDeliveryControlPacketError(f"{label} is invalid")
    return value


def _utc(value: datetime, *, label: str, require_whole_seconds: bool) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ObjectDeltaDeliveryControlPacketError(f"{label} is invalid")
    normalized = value.astimezone(timezone.utc)
    if require_whole_seconds and normalized.microsecond != 0:
        raise ObjectDeltaDeliveryControlPacketError(f"{label} must use whole seconds")
    return normalized


def _format_timestamp(value: datetime, *, label: str) -> str:
    return _utc(value, label=label, require_whole_seconds=True).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise ObjectDeltaDeliveryControlPacketError(f"{label} is invalid")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ObjectDeltaDeliveryControlPacketError(f"{label} is invalid") from exc


def _validate_window(*, issued_at: datetime, expires_at: datetime) -> None:
    if expires_at <= issued_at or expires_at - issued_at > MAX_CONTROL_PACKET_TTL:
        raise ObjectDeltaDeliveryControlPacketError("control packet expiry window is invalid")


def _decode_exact_base64(value: object, *, label: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str):
        raise ObjectDeltaDeliveryControlPacketError(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ObjectDeltaDeliveryControlPacketError(f"{label} is invalid") from exc
    if len(decoded) != expected_bytes:
        raise ObjectDeltaDeliveryControlPacketError(f"{label} is invalid")
    return decoded


def _public_key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _require_public_key(value: object, *, label: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        raise ObjectDeltaDeliveryControlPacketError(f"{label} is invalid")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError) as exc:
        raise ObjectDeltaDeliveryControlPacketError(f"{label} is invalid") from exc
    return value


def _public_key_from_signer(signer: object) -> bytes:
    try:
        from cryptography.hazmat.primitives import serialization

        public_key = signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (AttributeError, ImportError, TypeError, ValueError) as exc:
        raise ObjectDeltaDeliveryControlPacketError("controller signer is invalid") from exc
    return _require_public_key(public_key, label="controller signer public key")


def _validate_delivery(value: object) -> dict[str, Any]:
    delivery = _require_exact_mapping(value, fields=_DELIVERY_FIELDS, label="control packet delivery")
    if (
        delivery["transport_schema"] != OBJECT_DELTA_TRANSPORT_SCHEMA
        or delivery["object_kind"] != OBJECT_DELTA_DELIVERY_OBJECT_KIND
        or delivery["encryption"] != OBJECT_DELTA_ENCRYPTION
        or delivery["provider_side_encryption"] != OBJECT_DELTA_PROVIDER_SIDE_ENCRYPTION
    ):
        raise ObjectDeltaDeliveryControlPacketError("control packet delivery protocol is invalid")
    source_site = delivery["source_site"]
    destination_site = delivery["destination_site"]
    if source_site not in WEBAPP_SITES or destination_site not in WEBAPP_SITES or source_site == destination_site:
        raise ObjectDeltaDeliveryControlPacketError("control packet delivery route is invalid")
    bucket = _require_text(delivery["bucket"], label="control packet bucket", pattern=BUCKET_RE)
    recipient = _require_text(
        delivery["destination_age_recipient"],
        label="control packet destination age recipient",
        pattern=AGE_RECIPIENT_RE,
    )
    campaign_id = _require_text(delivery["campaign_id"], label="control packet campaign", pattern=CAMPAIGN_ID_RE)
    release_sha = _require_text(delivery["release_sha"], label="control packet release", pattern=RELEASE_SHA_RE)
    writer_epoch = _require_positive_int(delivery["writer_epoch"], label="control packet writer epoch")
    writer_lease_id = _require_text(
        delivery["writer_lease_id"],
        label="control packet writer lease",
        pattern=LEASE_ID_RE,
    )
    stream_generation_id = _require_text(
        delivery["stream_generation_id"],
        label="control packet stream generation",
        pattern=STREAM_GENERATION_ID_RE,
    )
    first_sequence = _require_positive_int(delivery["first_sequence"], label="control packet first sequence")
    last_sequence = _require_positive_int(delivery["last_sequence"], label="control packet last sequence")
    if last_sequence < first_sequence or last_sequence - first_sequence + 1 > MAX_STREAM_SEQUENCE_IDS:
        raise ObjectDeltaDeliveryControlPacketError("control packet sequence range is invalid")
    prior_chain_sha256 = _require_text(
        delivery["prior_chain_sha256"],
        label="control packet prior chain SHA-256",
        pattern=SHA256_RE,
    )
    batch_sha256 = _require_text(
        delivery["batch_sha256"],
        label="control packet batch SHA-256",
        pattern=SHA256_RE,
    )
    payload_sha256 = _require_text(
        delivery["payload_sha256"],
        label="control packet payload SHA-256",
        pattern=SHA256_RE,
    )
    object_key = _require_text(delivery["object_key"], label="control packet Object key", pattern=OBJECT_KEY_RE)
    if ".." in object_key.split("/"):
        raise ObjectDeltaDeliveryControlPacketError("control packet Object key is invalid")
    object_version_id = _require_text(
        delivery["object_version_id"],
        label="control packet Object version",
        pattern=VERSION_ID_RE,
    )
    if object_version_id.lower() == "null":
        raise ObjectDeltaDeliveryControlPacketError("control packet Object version is invalid")
    ciphertext_sha256 = _require_text(
        delivery["ciphertext_sha256"],
        label="control packet ciphertext SHA-256",
        pattern=SHA256_RE,
    )
    ciphertext_bytes = _require_positive_int(
        delivery["ciphertext_bytes"],
        label="control packet ciphertext bytes",
        maximum=MAX_DELTA_PAYLOAD_BYTES + 1024 * 1024,
    )
    return {
        "bucket": bucket,
        "source_site": source_site,
        "destination_site": destination_site,
        "destination_age_recipient": recipient,
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "writer_epoch": writer_epoch,
        "writer_lease_id": writer_lease_id,
        "stream_generation_id": stream_generation_id,
        "first_sequence": first_sequence,
        "last_sequence": last_sequence,
        "prior_chain_sha256": prior_chain_sha256,
        "batch_sha256": batch_sha256,
        "payload_sha256": payload_sha256,
        "object_key": object_key,
        "object_version_id": object_version_id,
        "ciphertext_sha256": ciphertext_sha256,
        "ciphertext_bytes": ciphertext_bytes,
    }


def _parse_unsigned_packet(
    value: object,
) -> tuple[dict[str, Any], datetime, datetime, str, bytes, str, dict[str, Any]]:
    unsigned = _require_exact_mapping(value, fields=_UNSIGNED_PACKET_FIELDS, label="control packet")
    if (
        unsigned["schema"] != OBJECT_DELTA_DELIVERY_CONTROL_PACKET_SCHEMA
        or unsigned["status"] != OBJECT_DELTA_DELIVERY_CONTROL_PACKET_STATUS
    ):
        raise ObjectDeltaDeliveryControlPacketError("control packet schema is invalid")
    issued_at = _parse_timestamp(unsigned["issued_at"], label="control packet issued_at")
    expires_at = _parse_timestamp(unsigned["expires_at"], label="control packet expires_at")
    _validate_window(issued_at=issued_at, expires_at=expires_at)
    nonce = _require_text(unsigned["nonce"], label="control packet nonce", pattern=_NONCE_RE)
    signer = _require_exact_mapping(
        unsigned["controller_signer"],
        fields=_CONTROLLER_SIGNER_FIELDS,
        label="control packet controller signer",
    )
    if signer["algorithm"] != OBJECT_DELTA_DELIVERY_CONTROL_PACKET_SIGNATURE_ALGORITHM:
        raise ObjectDeltaDeliveryControlPacketError("control packet controller signer is invalid")
    public_key = _decode_exact_base64(
        signer["public_key_base64"],
        label="control packet controller public key",
        expected_bytes=32,
    )
    _require_public_key(public_key, label="control packet controller public key")
    key_id = _require_text(signer["key_id"], label="control packet controller key ID", pattern=_KEY_ID_RE)
    if key_id != _public_key_id(public_key):
        raise ObjectDeltaDeliveryControlPacketError("control packet controller key ID is invalid")
    delivery = _validate_delivery(unsigned["delivery"])
    return unsigned, issued_at, expires_at, nonce, public_key, key_id, delivery


def controller_key_id_from_public_key(controller_public_key: bytes) -> str:
    """Return the non-secret key identifier used by sealed delivery packets."""

    return _public_key_id(_require_public_key(controller_public_key, label="controller public key"))


def validate_object_delta_receiver_delivery_permit(
    permit: ObjectDeltaReceiverDeliveryPermit,
    *,
    policy: ObjectDeltaTransportPolicy,
) -> ObjectDeltaReceiverDeliveryPermit:
    """Validate a non-secret permit without loading it from a local path.

    The caller owns the root-only file boundary.  This function binds its
    release/stream/term claims to the same fixed Object Storage policy used to
    verify the controller packet.
    """

    if not isinstance(permit, ObjectDeltaReceiverDeliveryPermit):
        raise ObjectDeltaDeliveryControlPacketError("receiver delivery permit is invalid")
    normalized_policy = validate_object_delta_transport_policy(policy)
    if (
        permit.source_site not in WEBAPP_SITES
        or permit.destination_site not in WEBAPP_SITES
        or permit.source_site == permit.destination_site
    ):
        raise ObjectDeltaDeliveryControlPacketError("receiver delivery permit route is invalid")
    campaign_id = _require_text(
        permit.campaign_id,
        label="receiver delivery permit campaign",
        pattern=CAMPAIGN_ID_RE,
    )
    release_sha = _require_text(
        permit.release_sha,
        label="receiver delivery permit release",
        pattern=RELEASE_SHA_RE,
    )
    stream_generation_id = _require_text(
        permit.stream_generation_id,
        label="receiver delivery permit stream generation",
        pattern=STREAM_GENERATION_ID_RE,
    )
    if permit.bucket != normalized_policy.bucket:
        raise ObjectDeltaDeliveryControlPacketError("receiver delivery permit bucket does not match local policy")
    expected_recipient = destination_age_recipient(
        normalized_policy,
        destination_site=permit.destination_site,
    )
    if permit.destination_age_recipient != expected_recipient:
        raise ObjectDeltaDeliveryControlPacketError(
            "receiver delivery permit recipient does not match local policy"
        )
    controller_key_id = _require_text(
        permit.controller_key_id,
        label="receiver delivery permit controller key ID",
        pattern=_KEY_ID_RE,
    )
    writer_epoch = _require_positive_int(
        permit.writer_epoch,
        label="receiver delivery permit writer epoch",
    )
    writer_lease_id = _require_text(
        permit.writer_lease_id,
        label="receiver delivery permit writer lease",
        pattern=LEASE_ID_RE,
    )
    return ObjectDeltaReceiverDeliveryPermit(
        source_site=permit.source_site,
        destination_site=permit.destination_site,
        campaign_id=campaign_id,
        release_sha=release_sha,
        stream_generation_id=stream_generation_id,
        bucket=normalized_policy.bucket,
        destination_age_recipient=expected_recipient,
        controller_key_id=controller_key_id,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
    )


def unsigned_object_delta_delivery_control_packet_payload(packet: Mapping[str, Any]) -> bytes:
    """Return domain-separated deterministic bytes for exactly one unsigned packet."""

    unsigned, *_ = _parse_unsigned_packet(packet)
    return OBJECT_DELTA_DELIVERY_CONTROL_PACKET_SIGNATURE_DOMAIN + canonical_json_bytes(unsigned)


def build_unsigned_object_delta_delivery_control_packet(
    *,
    policy: ObjectDeltaTransportPolicy,
    batch: AppendOnlySyncDeltaBatch,
    binding: ObjectDeltaTransportBinding,
    issued_at: datetime,
    expires_at: datetime,
    nonce: str,
    controller_public_key: bytes,
) -> dict[str, Any]:
    """Build the metadata-only packet that a controller may sign in memory.

    ``batch`` must have already been parsed and validated from canonical bytes.
    ``binding`` must be the result for the same policy and batch.  Payload
    contents and byte count intentionally have no field in the packet.
    """

    normalized_policy = validate_object_delta_transport_policy(policy)
    if not isinstance(batch, AppendOnlySyncDeltaBatch):
        raise ObjectDeltaDeliveryControlPacketError("validated Object-delta batch is required")
    try:
        expected_binding = bind_object_delta_batch(normalized_policy, batch)
    except ObjectDeltaTransportBindingError as exc:
        raise ObjectDeltaDeliveryControlPacketError("Object-delta transport binding is invalid") from exc
    if not isinstance(binding, ObjectDeltaTransportBinding) or binding != expected_binding:
        raise ObjectDeltaDeliveryControlPacketError("Object-delta transport binding does not match the batch")
    issued = _format_timestamp(issued_at, label="control packet issued_at")
    expires = _format_timestamp(expires_at, label="control packet expires_at")
    parsed_issued = _parse_timestamp(issued, label="control packet issued_at")
    parsed_expires = _parse_timestamp(expires, label="control packet expires_at")
    _validate_window(issued_at=parsed_issued, expires_at=parsed_expires)
    packet_nonce = _require_text(nonce, label="control packet nonce", pattern=_NONCE_RE)
    public_key = _require_public_key(controller_public_key, label="controller public key")
    return {
        "schema": OBJECT_DELTA_DELIVERY_CONTROL_PACKET_SCHEMA,
        "status": OBJECT_DELTA_DELIVERY_CONTROL_PACKET_STATUS,
        "issued_at": issued,
        "expires_at": expires,
        "nonce": packet_nonce,
        "controller_signer": {
            "algorithm": OBJECT_DELTA_DELIVERY_CONTROL_PACKET_SIGNATURE_ALGORITHM,
            "public_key_base64": base64.b64encode(public_key).decode("ascii"),
            "key_id": _public_key_id(public_key),
        },
        "delivery": {
            "transport_schema": OBJECT_DELTA_TRANSPORT_SCHEMA,
            "object_kind": OBJECT_DELTA_DELIVERY_OBJECT_KIND,
            "encryption": OBJECT_DELTA_ENCRYPTION,
            "provider_side_encryption": OBJECT_DELTA_PROVIDER_SIDE_ENCRYPTION,
            "bucket": normalized_policy.bucket,
            "source_site": batch.source_site,
            "destination_site": batch.destination_site,
            "destination_age_recipient": expected_binding.destination_age_recipient,
            "campaign_id": batch.campaign_id,
            "release_sha": batch.release_sha,
            "writer_epoch": batch.writer_term.epoch,
            "writer_lease_id": batch.writer_term.lease_id,
            "stream_generation_id": batch.stream.generation_id,
            "first_sequence": batch.stream.first_sequence,
            "last_sequence": batch.stream.last_sequence,
            "prior_chain_sha256": batch.prior_chain_sha256,
            "batch_sha256": batch.batch_sha256,
            "payload_sha256": batch.payload_sha256,
            "object_key": expected_binding.object_key,
            "object_version_id": expected_binding.object_version_id,
            "ciphertext_sha256": expected_binding.ciphertext_sha256,
            "ciphertext_bytes": expected_binding.ciphertext_bytes,
        },
    }


def sign_object_delta_delivery_control_packet(
    unsigned_packet: Mapping[str, Any],
    *,
    controller_signer: object,
) -> dict[str, Any]:
    """Seal one packet with an already-loaded in-memory Ed25519 signer."""

    (
        unsigned,
        _issued_at,
        _expires_at,
        _nonce,
        controller_public_key,
        _key_id,
        _delivery,
    ) = _parse_unsigned_packet(unsigned_packet)
    if _public_key_from_signer(controller_signer) != controller_public_key:
        raise ObjectDeltaDeliveryControlPacketError("controller signer does not match packet signer")
    try:
        signature = controller_signer.sign(
            unsigned_object_delta_delivery_control_packet_payload(unsigned)
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaDeliveryControlPacketError("controller signer is invalid") from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise ObjectDeltaDeliveryControlPacketError("controller signature is invalid")
    return {
        **unsigned,
        "controller_signature": {
            "algorithm": OBJECT_DELTA_DELIVERY_CONTROL_PACKET_SIGNATURE_ALGORITHM,
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        },
    }


def verify_object_delta_delivery_control_packet(
    packet: Mapping[str, Any],
    *,
    policy: ObjectDeltaTransportPolicy,
    expected_destination_site: str,
    pinned_controller_public_key: bytes,
    observed_at: datetime,
) -> VerifiedObjectDeltaDeliveryControlPacket:
    """Verify a sealed packet before any download or age decryption.

    This validates the local destination, fixed bucket, deterministic Object
    key, pinned controller key, signature, and expiry.  The caller must later
    call ``assert_verified_delivery_matches_batch`` after the Object is
    downloaded and its contained batch is validated.
    """

    sealed = _require_exact_mapping(packet, fields=_SEALED_PACKET_FIELDS, label="sealed control packet")
    unsigned = {key: value for key, value in sealed.items() if key != "controller_signature"}
    unsigned, issued_at, expires_at, nonce, controller_public_key, key_id, delivery = _parse_unsigned_packet(unsigned)
    signature = _require_exact_mapping(
        sealed["controller_signature"],
        fields=_SIGNATURE_FIELDS,
        label="control packet signature",
    )
    if signature["algorithm"] != OBJECT_DELTA_DELIVERY_CONTROL_PACKET_SIGNATURE_ALGORITHM:
        raise ObjectDeltaDeliveryControlPacketError("control packet signature is invalid")
    raw_signature = _decode_exact_base64(
        signature["signature_base64"],
        label="control packet signature",
        expected_bytes=64,
    )
    pinned_public_key = _require_public_key(
        pinned_controller_public_key,
        label="pinned controller public key",
    )
    if controller_public_key != pinned_public_key or key_id != _public_key_id(pinned_public_key):
        raise ObjectDeltaDeliveryControlPacketError("control packet controller signer is not pinned")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(pinned_public_key).verify(
            raw_signature,
            unsigned_object_delta_delivery_control_packet_payload(unsigned),
        )
    except ImportError as exc:
        raise ObjectDeltaDeliveryControlPacketError("cryptography Ed25519 support is unavailable") from exc
    except InvalidSignature as exc:
        raise ObjectDeltaDeliveryControlPacketError("control packet signature verification failed") from exc
    normalized_policy = validate_object_delta_transport_policy(policy)
    if expected_destination_site not in WEBAPP_SITES:
        raise ObjectDeltaDeliveryControlPacketError("expected control packet destination is invalid")
    if delivery["destination_site"] != expected_destination_site:
        raise ObjectDeltaDeliveryControlPacketError("control packet destination does not match this receiver")
    if delivery["bucket"] != normalized_policy.bucket:
        raise ObjectDeltaDeliveryControlPacketError("control packet bucket does not match local policy")
    expected_recipient = destination_age_recipient(
        normalized_policy,
        destination_site=delivery["destination_site"],
    )
    if delivery["destination_age_recipient"] != expected_recipient:
        raise ObjectDeltaDeliveryControlPacketError("control packet recipient does not match local policy")
    expected_object_key = derive_object_delta_object_key(
        normalized_policy,
        source_site=delivery["source_site"],
        destination_site=delivery["destination_site"],
        campaign_id=delivery["campaign_id"],
        release_sha=delivery["release_sha"],
        stream_generation_id=delivery["stream_generation_id"],
        first_sequence=delivery["first_sequence"],
        last_sequence=delivery["last_sequence"],
        payload_sha256=delivery["payload_sha256"],
    )
    if delivery["object_key"] != expected_object_key:
        raise ObjectDeltaDeliveryControlPacketError("control packet Object key is not deterministic")
    observed = _utc(observed_at, label="control packet observed_at", require_whole_seconds=False)
    if observed < issued_at or observed >= expires_at:
        raise ObjectDeltaDeliveryControlPacketError("control packet is not currently valid")
    verified = VerifiedObjectDeltaDeliveryControlPacket(
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce,
        controller_key_id=key_id,
        bucket=delivery["bucket"],
        source_site=delivery["source_site"],
        destination_site=delivery["destination_site"],
        destination_age_recipient=delivery["destination_age_recipient"],
        campaign_id=delivery["campaign_id"],
        release_sha=delivery["release_sha"],
        writer_epoch=delivery["writer_epoch"],
        writer_lease_id=delivery["writer_lease_id"],
        stream_generation_id=delivery["stream_generation_id"],
        first_sequence=delivery["first_sequence"],
        last_sequence=delivery["last_sequence"],
        prior_chain_sha256=delivery["prior_chain_sha256"],
        batch_sha256=delivery["batch_sha256"],
        payload_sha256=delivery["payload_sha256"],
        object_key=delivery["object_key"],
        object_version_id=delivery["object_version_id"],
        ciphertext_sha256=delivery["ciphertext_sha256"],
        ciphertext_bytes=delivery["ciphertext_bytes"],
    )
    object.__setattr__(verified, "_capability", _VERIFIED_DELIVERY_CONTROL_PACKET_CAPABILITY)
    return revalidate_verified_object_delta_delivery_control_packet(
        verified,
        observed_at=observed,
    )


def revalidate_verified_object_delta_delivery_control_packet(
    verified_packet: object,
    *,
    observed_at: datetime | None = None,
) -> VerifiedObjectDeltaDeliveryControlPacket:
    """Reject forged/replaced verified-packet values before later use.

    Signature verification itself is intentionally performed only on the raw
    controller envelope.  This helper therefore cannot turn a manually
    assembled dataclass into evidence: it requires the private capability
    minted by :func:`verify_object_delta_delivery_control_packet`, then
    rechecks every non-secret claim and, when supplied, the current validity
    window.  It performs no I/O and does not select a local policy or key pin.
    """

    if type(verified_packet) is not VerifiedObjectDeltaDeliveryControlPacket:
        raise ObjectDeltaDeliveryControlPacketError("verified control packet is invalid")
    if verified_packet._capability is not _VERIFIED_DELIVERY_CONTROL_PACKET_CAPABILITY:
        raise ObjectDeltaDeliveryControlPacketError(
            "verified control packet was not produced by signature verification"
        )
    issued_at = _utc(
        verified_packet.issued_at,
        label="verified control packet issued_at",
        require_whole_seconds=True,
    )
    expires_at = _utc(
        verified_packet.expires_at,
        label="verified control packet expires_at",
        require_whole_seconds=True,
    )
    _validate_window(issued_at=issued_at, expires_at=expires_at)
    _require_text(verified_packet.nonce, label="verified control packet nonce", pattern=_NONCE_RE)
    _require_text(
        verified_packet.controller_key_id,
        label="verified control packet controller key ID",
        pattern=_KEY_ID_RE,
    )
    _validate_delivery(
        {
            "transport_schema": OBJECT_DELTA_TRANSPORT_SCHEMA,
            "object_kind": OBJECT_DELTA_DELIVERY_OBJECT_KIND,
            "encryption": OBJECT_DELTA_ENCRYPTION,
            "provider_side_encryption": OBJECT_DELTA_PROVIDER_SIDE_ENCRYPTION,
            "bucket": verified_packet.bucket,
            "source_site": verified_packet.source_site,
            "destination_site": verified_packet.destination_site,
            "destination_age_recipient": verified_packet.destination_age_recipient,
            "campaign_id": verified_packet.campaign_id,
            "release_sha": verified_packet.release_sha,
            "writer_epoch": verified_packet.writer_epoch,
            "writer_lease_id": verified_packet.writer_lease_id,
            "stream_generation_id": verified_packet.stream_generation_id,
            "first_sequence": verified_packet.first_sequence,
            "last_sequence": verified_packet.last_sequence,
            "prior_chain_sha256": verified_packet.prior_chain_sha256,
            "batch_sha256": verified_packet.batch_sha256,
            "payload_sha256": verified_packet.payload_sha256,
            "object_key": verified_packet.object_key,
            "object_version_id": verified_packet.object_version_id,
            "ciphertext_sha256": verified_packet.ciphertext_sha256,
            "ciphertext_bytes": verified_packet.ciphertext_bytes,
        }
    )
    if observed_at is not None:
        observed = _utc(
            observed_at,
            label="verified control packet observed_at",
            require_whole_seconds=False,
        )
        if observed < issued_at or observed >= expires_at:
            raise ObjectDeltaDeliveryControlPacketError("verified control packet is not currently valid")
    return verified_packet


def assert_verified_delivery_matches_receiver_permit(
    verified_packet: VerifiedObjectDeltaDeliveryControlPacket,
    *,
    policy: ObjectDeltaTransportPolicy,
    permit: ObjectDeltaReceiverDeliveryPermit,
) -> ObjectDeltaReceiverDeliveryPermit:
    """Require a verified packet to match one local release and Witness term.

    This is deliberately an equality gate rather than a permit loader or
    mutable nonce store.  A future receiver must load the permit from its
    root-only release-bound location and consume the nonce durably in the same
    transaction that records its immutable import receipt.
    """

    verified_packet = revalidate_verified_object_delta_delivery_control_packet(verified_packet)
    normalized_permit = validate_object_delta_receiver_delivery_permit(permit, policy=policy)
    expected = (
        normalized_permit.source_site,
        normalized_permit.destination_site,
        normalized_permit.campaign_id,
        normalized_permit.release_sha,
        normalized_permit.stream_generation_id,
        normalized_permit.bucket,
        normalized_permit.destination_age_recipient,
        normalized_permit.controller_key_id,
        normalized_permit.writer_epoch,
        normalized_permit.writer_lease_id,
    )
    actual = (
        verified_packet.source_site,
        verified_packet.destination_site,
        verified_packet.campaign_id,
        verified_packet.release_sha,
        verified_packet.stream_generation_id,
        verified_packet.bucket,
        verified_packet.destination_age_recipient,
        verified_packet.controller_key_id,
        verified_packet.writer_epoch,
        verified_packet.writer_lease_id,
    )
    if actual != expected:
        raise ObjectDeltaDeliveryControlPacketError(
            "verified control packet does not match the local receiver delivery permit"
        )
    return normalized_permit


def assert_verified_delivery_matches_batch(
    verified_packet: VerifiedObjectDeltaDeliveryControlPacket,
    *,
    policy: ObjectDeltaTransportPolicy,
    batch: AppendOnlySyncDeltaBatch,
) -> ObjectDeltaTransportBinding:
    """Bind post-download validated batch metadata to its verified packet.

    A receiver calls this only after it has retrieved the exact signed version,
    checked the Object metadata, age-decrypted it, and parsed the canonical
    append-only batch.  No data import happens here.
    """

    verified_packet = revalidate_verified_object_delta_delivery_control_packet(verified_packet)
    normalized_policy = validate_object_delta_transport_policy(policy)
    if not isinstance(batch, AppendOnlySyncDeltaBatch):
        raise ObjectDeltaDeliveryControlPacketError("validated Object-delta batch is required")
    try:
        binding = bind_object_delta_batch(normalized_policy, batch)
    except ObjectDeltaTransportBindingError as exc:
        raise ObjectDeltaDeliveryControlPacketError("Object-delta batch transport binding is invalid") from exc
    expected = (
        normalized_policy.bucket,
        batch.source_site,
        batch.destination_site,
        binding.destination_age_recipient,
        batch.campaign_id,
        batch.release_sha,
        batch.writer_term.epoch,
        batch.writer_term.lease_id,
        batch.stream.generation_id,
        batch.stream.first_sequence,
        batch.stream.last_sequence,
        batch.prior_chain_sha256,
        batch.batch_sha256,
        batch.payload_sha256,
        binding.object_key,
        binding.object_version_id,
        binding.ciphertext_sha256,
        binding.ciphertext_bytes,
    )
    actual = (
        verified_packet.bucket,
        verified_packet.source_site,
        verified_packet.destination_site,
        verified_packet.destination_age_recipient,
        verified_packet.campaign_id,
        verified_packet.release_sha,
        verified_packet.writer_epoch,
        verified_packet.writer_lease_id,
        verified_packet.stream_generation_id,
        verified_packet.first_sequence,
        verified_packet.last_sequence,
        verified_packet.prior_chain_sha256,
        verified_packet.batch_sha256,
        verified_packet.payload_sha256,
        verified_packet.object_key,
        verified_packet.object_version_id,
        verified_packet.ciphertext_sha256,
        verified_packet.ciphertext_bytes,
    )
    if actual != expected:
        raise ObjectDeltaDeliveryControlPacketError(
            "verified control packet does not match the downloaded Object-delta batch"
        )
    return binding
