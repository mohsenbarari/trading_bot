"""Pure Object-Storage binding for append-only cross-site delta batches.

This module is deliberately below any S3, age, filesystem, or database
adapter.  It defines the stable Object key and public recipient binding that a
future controller-operated relay must use for a batch that already belongs to
the append-only delta contracts.

Only the controller may hold provider credentials.  A source or destination
site receives at most one short-lived presigned capability in a transient
control message; URLs and credentials are intentionally absent from every
type and return value in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from core.append_only_sync_delta_batch import (
    AppendOnlySyncDeltaBatch,
    CAMPAIGN_ID_RE,
    MAX_DELTA_PAYLOAD_BYTES,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
)


OBJECT_DELTA_TRANSPORT_SCHEMA = "gold-trade-object-delta-transport-v1"
OBJECT_DELTA_TRANSPORT_LAYOUT_VERSION = "v1"
OBJECT_DELTA_ENCRYPTION = "age-v1"
CONTROLLER_CREDENTIAL_HOLDER = "controller"
OBJECT_DELTA_NAMESPACE = "object-delta"

BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")
PREFIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,127}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$")
SITE_RE = re.compile(r"^webapp_(?:fi|ir)$")


class ObjectDeltaTransportBindingError(ValueError):
    """A future controller-credentialed delta relay binding is unsafe."""


@dataclass(frozen=True)
class ObjectDeltaTransportPolicy:
    """Non-secret public policy for the controller-operated delta relay.

    There is no credential path or credential value here by design.  The
    eventual adapter must obtain provider access only from the controller's
    separately trusted configuration after it has validated this policy.
    """

    bucket: str
    prefix: str
    webapp_fi_age_recipient: str
    webapp_ir_age_recipient: str
    credential_holder: str = CONTROLLER_CREDENTIAL_HOLDER


@dataclass(frozen=True)
class ObjectDeltaTransportBinding:
    """One deterministic Object route, independent of any upload mechanism."""

    source_site: str
    destination_site: str
    destination_age_recipient: str
    object_key: str
    stream_generation_id: str
    first_sequence: int
    last_sequence: int
    payload_sha256: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    object_version_id: str


def _require_text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ObjectDeltaTransportBindingError(f"{label} is invalid")
    return value


def _require_positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ObjectDeltaTransportBindingError(f"{label} is invalid")
    return value


def _validate_prefix(value: object) -> str:
    if not isinstance(value, str):
        raise ObjectDeltaTransportBindingError("Object Storage prefix is invalid")
    prefix = value.strip("/")
    if not prefix or any(PREFIX_COMPONENT_RE.fullmatch(part) is None for part in prefix.split("/")):
        raise ObjectDeltaTransportBindingError("Object Storage prefix is invalid")
    return prefix


def _require_age_recipient(value: object, *, label: str) -> str:
    return _require_text(value, label=label, pattern=AGE_RECIPIENT_RE)


def validate_object_delta_transport_policy(
    policy: ObjectDeltaTransportPolicy,
) -> ObjectDeltaTransportPolicy:
    """Validate policy without loading credentials or touching Object Storage."""

    if not isinstance(policy, ObjectDeltaTransportPolicy):
        raise ObjectDeltaTransportBindingError("Object-delta transport policy is invalid")
    if policy.credential_holder != CONTROLLER_CREDENTIAL_HOLDER:
        raise ObjectDeltaTransportBindingError(
            "Object-delta provider credentials may be held only by the controller"
        )
    bucket = _require_text(policy.bucket, label="Object Storage bucket", pattern=BUCKET_RE)
    prefix = _validate_prefix(policy.prefix)
    webapp_fi = _require_age_recipient(
        policy.webapp_fi_age_recipient,
        label="WebApp-FI age recipient",
    )
    webapp_ir = _require_age_recipient(
        policy.webapp_ir_age_recipient,
        label="WebApp-IR age recipient",
    )
    if webapp_fi == webapp_ir:
        raise ObjectDeltaTransportBindingError(
            "WebApp-FI and WebApp-IR delta recipients must be distinct"
        )
    return ObjectDeltaTransportPolicy(
        bucket=bucket,
        prefix=prefix,
        webapp_fi_age_recipient=webapp_fi,
        webapp_ir_age_recipient=webapp_ir,
        credential_holder=CONTROLLER_CREDENTIAL_HOLDER,
    )


def destination_age_recipient(
    policy: ObjectDeltaTransportPolicy,
    *,
    destination_site: str,
) -> str:
    """Return exactly one receiver identity; controller is not a recipient."""

    policy = validate_object_delta_transport_policy(policy)
    if destination_site == "webapp_fi":
        return policy.webapp_fi_age_recipient
    if destination_site == "webapp_ir":
        return policy.webapp_ir_age_recipient
    raise ObjectDeltaTransportBindingError("Object-delta destination site is invalid")


def derive_object_delta_object_key(
    policy: ObjectDeltaTransportPolicy,
    *,
    source_site: str,
    destination_site: str,
    campaign_id: str,
    release_sha: str,
    stream_generation_id: str,
    first_sequence: int,
    last_sequence: int,
    payload_sha256: str,
) -> str:
    """Return the one create-only Object key for a logical delta range.

    The payload hash, rather than the final batch hash, is in the key because
    the final batch binds the Object VersionId and is created only after the
    controller has read that version back.  A retry of the same logical range
    and payload therefore targets the same create-only key; a changed payload
    cannot overwrite it.
    """

    policy = validate_object_delta_transport_policy(policy)
    source = _require_text(source_site, label="Object-delta source site", pattern=SITE_RE)
    destination = _require_text(destination_site, label="Object-delta destination site", pattern=SITE_RE)
    if source == destination:
        raise ObjectDeltaTransportBindingError("Object-delta source and destination must differ")
    campaign = _require_text(campaign_id, label="Object-delta campaign", pattern=CAMPAIGN_ID_RE)
    release = _require_text(release_sha, label="Object-delta release", pattern=RELEASE_SHA_RE)
    generation = _require_text(
        stream_generation_id,
        label="Object-delta stream generation",
        pattern=STREAM_GENERATION_ID_RE,
    )
    first = _require_positive_int(first_sequence, label="Object-delta first sequence")
    last = _require_positive_int(last_sequence, label="Object-delta last sequence")
    if last < first:
        raise ObjectDeltaTransportBindingError("Object-delta sequence range is invalid")
    payload = _require_text(payload_sha256, label="Object-delta payload SHA-256", pattern=SHA256_RE)
    # Resolve the recipient as part of planning.  It is intentionally not a
    # key component: the direction already selects exactly one fixed recipient.
    destination_age_recipient(policy, destination_site=destination)
    key = "/".join(
        (
            policy.prefix,
            OBJECT_DELTA_NAMESPACE,
            OBJECT_DELTA_TRANSPORT_LAYOUT_VERSION,
            campaign,
            release,
            source,
            destination,
            generation,
            f"{first:020d}-{last:020d}-{payload}.age",
        )
    )
    if len(key.encode("ascii")) > 1024:
        raise ObjectDeltaTransportBindingError("Object-delta Object Storage key is oversized")
    return key


def bind_object_delta_batch(
    policy: ObjectDeltaTransportPolicy,
    batch: AppendOnlySyncDeltaBatch,
) -> ObjectDeltaTransportBinding:
    """Bind a validated append-only batch to its sole storage route.

    The caller must parse and authenticate the batch before calling this
    function.  This function makes no claim that a remote Object exists; it
    only proves that the batch's immutable receipt names the deterministic
    create-only key expected for its route and payload.
    """

    policy = validate_object_delta_transport_policy(policy)
    if not isinstance(batch, AppendOnlySyncDeltaBatch):
        raise ObjectDeltaTransportBindingError("append-only Object-delta batch is invalid")
    if batch.source_site not in WEBAPP_SITES or batch.destination_site not in WEBAPP_SITES:
        raise ObjectDeltaTransportBindingError("append-only Object-delta route is invalid")
    expected_key = derive_object_delta_object_key(
        policy,
        source_site=batch.source_site,
        destination_site=batch.destination_site,
        campaign_id=batch.campaign_id,
        release_sha=batch.release_sha,
        stream_generation_id=batch.stream.generation_id,
        first_sequence=batch.stream.first_sequence,
        last_sequence=batch.stream.last_sequence,
        payload_sha256=batch.payload_sha256,
    )
    if batch.immutable_receipt.object_key != expected_key:
        raise ObjectDeltaTransportBindingError(
            "append-only Object-delta immutable receipt is not bound to its deterministic Object key"
        )
    return ObjectDeltaTransportBinding(
        source_site=batch.source_site,
        destination_site=batch.destination_site,
        destination_age_recipient=destination_age_recipient(
            policy,
            destination_site=batch.destination_site,
        ),
        object_key=expected_key,
        stream_generation_id=batch.stream.generation_id,
        first_sequence=batch.stream.first_sequence,
        last_sequence=batch.stream.last_sequence,
        payload_sha256=batch.payload_sha256,
        ciphertext_sha256=batch.immutable_receipt.ciphertext_sha256,
        ciphertext_bytes=batch.immutable_receipt.ciphertext_bytes,
        object_version_id=batch.immutable_receipt.version_id,
    )


def required_object_metadata(binding: ObjectDeltaTransportBinding) -> Mapping[str, str]:
    """Return exact non-secret S3 metadata for a later controller adapter."""

    if not isinstance(binding, ObjectDeltaTransportBinding):
        raise ObjectDeltaTransportBindingError("Object-delta transport binding is invalid")
    if binding.source_site not in WEBAPP_SITES or binding.destination_site not in WEBAPP_SITES:
        raise ObjectDeltaTransportBindingError("Object-delta transport route is invalid")
    if binding.source_site == binding.destination_site:
        raise ObjectDeltaTransportBindingError("Object-delta transport route is invalid")
    _require_age_recipient(binding.destination_age_recipient, label="Object-delta destination recipient")
    object_key = _require_text(
        binding.object_key,
        label="Object-delta Object Storage key",
        pattern=OBJECT_KEY_RE,
    )
    if ".." in object_key.split("/"):
        raise ObjectDeltaTransportBindingError("Object-delta Object Storage key is invalid")
    _require_text(binding.stream_generation_id, label="Object-delta stream generation", pattern=STREAM_GENERATION_ID_RE)
    _require_positive_int(binding.first_sequence, label="Object-delta first sequence")
    _require_positive_int(binding.last_sequence, label="Object-delta last sequence")
    _require_text(binding.payload_sha256, label="Object-delta payload SHA-256", pattern=SHA256_RE)
    _require_text(binding.ciphertext_sha256, label="Object-delta ciphertext SHA-256", pattern=SHA256_RE)
    _require_positive_int(
        binding.ciphertext_bytes,
        label="Object-delta ciphertext bytes",
    )
    if binding.ciphertext_bytes > MAX_DELTA_PAYLOAD_BYTES + 1024 * 1024:
        raise ObjectDeltaTransportBindingError("Object-delta ciphertext bytes are invalid")
    version_id = _require_text(
        binding.object_version_id,
        label="Object-delta Object Storage version",
        pattern=VERSION_ID_RE,
    )
    if version_id.lower() == "null":
        raise ObjectDeltaTransportBindingError("Object-delta Object Storage version is invalid")
    if binding.last_sequence < binding.first_sequence:
        raise ObjectDeltaTransportBindingError("Object-delta sequence range is invalid")
    return {
        "transport-schema": OBJECT_DELTA_TRANSPORT_SCHEMA,
        "encryption": OBJECT_DELTA_ENCRYPTION,
        "ciphertext-sha256": binding.ciphertext_sha256,
        "source-site": binding.source_site,
        "destination-site": binding.destination_site,
        "stream-generation-id": binding.stream_generation_id,
    }
