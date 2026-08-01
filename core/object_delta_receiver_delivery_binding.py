"""Root-only, default-off receiver permit projection for Object-delta delivery.

This module only loads a non-secret, release-bound local permit and validates
it against the pure controller-packet contract.  It does not download an
Object, open Object Storage, read an age identity, verify a packet, start a
worker, or import data.  No current runtime imports this module; a future
receiver adapter must opt in separately after its own review.

The file schema has no endpoint, URL, credential, private key, or payload
field.  Provider credentials remain controller-only by construction of the
transport policy.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import Any

from core.append_only_sync_delta_batch import RELEASE_SHA_RE, WEBAPP_SITES
from core.append_only_sync_delta_payload import REGISTRY_FINGERPRINT_RE
from core.object_delta_delivery_control_packet import (
    ObjectDeltaDeliveryControlPacketError,
    ObjectDeltaReceiverDeliveryPermit,
    controller_key_id_from_public_key,
    validate_object_delta_receiver_delivery_permit,
)
from core.object_delta_source_batch_attestation import source_key_id_from_public_key
from core.object_delta_transport_binding import (
    ObjectDeltaTransportBindingError,
    ObjectDeltaTransportPolicy,
    validate_object_delta_transport_policy,
)


OBJECT_DELTA_RECEIVER_DELIVERY_BINDING_SCHEMA = "gold-trade-object-delta-receiver-delivery-binding-v1"
MAX_RECEIVER_DELIVERY_BINDING_BYTES = 16 * 1024
OBJECT_DELTA_RECEIVER_DELIVERY_BINDING_FIELDS = frozenset(
    {
        "schema",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "stream_generation_id",
        "expected_registry_fingerprint",
        "bucket",
        "prefix",
        "webapp_fi_age_recipient",
        "webapp_ir_age_recipient",
        "destination_age_recipient",
        "source_public_key_base64",
        "source_key_id",
        "controller_public_key_base64",
        "controller_key_id",
        "writer_epoch",
        "writer_lease_id",
    }
)


class ObjectDeltaReceiverDeliveryBindingError(RuntimeError):
    """A local receiver permit is missing, unsafe, or incompatible."""


@dataclass(frozen=True)
class ObjectDeltaReceiverDeliveryBinding:
    """One validated non-secret policy and permit for a receiver only.

    ``expected_registry_fingerprint`` is an immutable, release-bound local
    expectation.  It is not selected by a controller packet or by decrypted
    payload bytes; a payload-admission contract must pass it to the canonical
    payload parser before an import plan can be derived.
    """

    policy: ObjectDeltaTransportPolicy
    permit: ObjectDeltaReceiverDeliveryPermit
    source_public_key: bytes
    source_key_id: str
    controller_public_key: bytes
    expected_registry_fingerprint: str


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ObjectDeltaReceiverDeliveryBindingError(
                "receiver delivery permit contains duplicate JSON fields"
            )
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ObjectDeltaReceiverDeliveryBindingError(
        f"receiver delivery permit JSON constant is forbidden: {value}"
    )


def _decode_controller_public_key(value: object) -> bytes:
    if not isinstance(value, str):
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery permit controller public key is invalid"
        )
    try:
        public_key = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery permit controller public key is invalid"
        ) from exc
    if len(public_key) != 32:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery permit controller public key is invalid"
        )
    return public_key


def _decode_source_public_key(value: object) -> bytes:
    if not isinstance(value, str):
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery permit source public key is invalid"
        )
    try:
        public_key = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery permit source public key is invalid"
        ) from exc
    if len(public_key) != 32:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery permit source public key is invalid"
        )
    try:
        # Validate the exact primitive here, not only when a future receiver
        # sees a batch.  A malformed pin must fail while loading the permit.
        source_key_id_from_public_key(public_key)
    except ValueError as exc:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery permit source public key is invalid"
        ) from exc
    return public_key


def _validate_root_controlled_ancestors(path: Path) -> None:
    """Open each parent without following symlinks before the leaf is opened."""

    if not isinstance(path, Path) or not path.is_absolute():
        raise ObjectDeltaReceiverDeliveryBindingError("receiver delivery permit path must be absolute")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery permit path validation is unavailable"
        )
    flags = os.O_RDONLY | os.O_CLOEXEC | nofollow | directory
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:-1]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            info = os.fstat(descriptor)
            mode = stat.S_IMODE(info.st_mode)
            sticky_root_parent = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != 0
                or (mode & 0o022 and not sticky_root_parent)
            ):
                raise ObjectDeltaReceiverDeliveryBindingError(
                    "receiver delivery permit parent is not root controlled"
                )
    except ObjectDeltaReceiverDeliveryBindingError:
        raise
    except OSError as exc:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery permit parent is unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_root_only_permit_bytes(path: Path) -> bytes:
    """Read one stable root-owned ``0600`` regular file without symlinks."""

    if not isinstance(path, Path) or not path.is_absolute():
        raise ObjectDeltaReceiverDeliveryBindingError("receiver delivery permit path must be absolute")
    _validate_root_controlled_ancestors(path)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery permit symlink protection is unavailable"
        )
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | nofollow)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > MAX_RECEIVER_DELIVERY_BINDING_BYTES
        ):
            raise ObjectDeltaReceiverDeliveryBindingError(
                "receiver delivery permit is not a root-only 0600 regular file"
            )
        chunks: list[bytes] = []
        remaining = MAX_RECEIVER_DELIVERY_BINDING_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise ObjectDeltaReceiverDeliveryBindingError(
                "receiver delivery permit changed while being read"
            )
    except ObjectDeltaReceiverDeliveryBindingError:
        raise
    except OSError as exc:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery permit cannot be opened safely"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not payload or len(payload) > MAX_RECEIVER_DELIVERY_BINDING_BYTES:
        raise ObjectDeltaReceiverDeliveryBindingError("receiver delivery permit size is invalid")
    return payload


def parse_object_delta_receiver_delivery_binding(
    value: object,
) -> ObjectDeltaReceiverDeliveryBinding:
    """Parse the exact non-secret file schema without filesystem I/O."""

    if not isinstance(value, Mapping) or set(value) != OBJECT_DELTA_RECEIVER_DELIVERY_BINDING_FIELDS:
        raise ObjectDeltaReceiverDeliveryBindingError("receiver delivery permit fields are invalid")
    if value.get("schema") != OBJECT_DELTA_RECEIVER_DELIVERY_BINDING_SCHEMA:
        raise ObjectDeltaReceiverDeliveryBindingError("receiver delivery permit schema is invalid")
    try:
        policy = validate_object_delta_transport_policy(
            ObjectDeltaTransportPolicy(
                bucket=value["bucket"],
                prefix=value["prefix"],
                webapp_fi_age_recipient=value["webapp_fi_age_recipient"],
                webapp_ir_age_recipient=value["webapp_ir_age_recipient"],
            )
        )
        if value["prefix"] != policy.prefix:
            raise ObjectDeltaReceiverDeliveryBindingError(
                "receiver delivery permit prefix is not canonical"
            )
        source_public_key = _decode_source_public_key(value["source_public_key_base64"])
        derived_source_key_id = source_key_id_from_public_key(source_public_key)
        if value["source_key_id"] != derived_source_key_id:
            raise ObjectDeltaReceiverDeliveryBindingError(
                "receiver delivery permit source key ID does not match its public key"
            )
        controller_public_key = _decode_controller_public_key(value["controller_public_key_base64"])
        derived_controller_key_id = controller_key_id_from_public_key(controller_public_key)
        if value["controller_key_id"] != derived_controller_key_id:
            raise ObjectDeltaReceiverDeliveryBindingError(
                "receiver delivery permit controller key ID does not match its public key"
            )
        permit = validate_object_delta_receiver_delivery_permit(
            ObjectDeltaReceiverDeliveryPermit(
                source_site=value["source_site"],
                destination_site=value["destination_site"],
                campaign_id=value["campaign_id"],
                release_sha=value["release_sha"],
                stream_generation_id=value["stream_generation_id"],
                bucket=value["bucket"],
                destination_age_recipient=value["destination_age_recipient"],
                controller_key_id=value["controller_key_id"],
                writer_epoch=value["writer_epoch"],
                writer_lease_id=value["writer_lease_id"],
            ),
            policy=policy,
        )
        expected_registry_fingerprint = value["expected_registry_fingerprint"]
        if (
            not isinstance(expected_registry_fingerprint, str)
            or REGISTRY_FINGERPRINT_RE.fullmatch(expected_registry_fingerprint) is None
        ):
            raise ObjectDeltaReceiverDeliveryBindingError(
                "receiver delivery permit expected registry fingerprint is invalid"
            )
    except (ObjectDeltaDeliveryControlPacketError, ObjectDeltaTransportBindingError) as exc:
        raise ObjectDeltaReceiverDeliveryBindingError("receiver delivery permit is invalid") from exc
    return ObjectDeltaReceiverDeliveryBinding(
        policy=policy,
        permit=permit,
        source_public_key=source_public_key,
        source_key_id=derived_source_key_id,
        controller_public_key=controller_public_key,
        expected_registry_fingerprint=expected_registry_fingerprint,
    )


def load_object_delta_receiver_delivery_binding(path: Path) -> ObjectDeltaReceiverDeliveryBinding:
    """Load one root-only permit while rejecting duplicate or non-finite JSON."""

    raw = _read_root_only_permit_bytes(path)
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except ObjectDeltaReceiverDeliveryBindingError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObjectDeltaReceiverDeliveryBindingError("receiver delivery permit JSON is invalid") from exc
    return parse_object_delta_receiver_delivery_binding(value)


def validate_object_delta_receiver_delivery_runtime(
    binding: ObjectDeltaReceiverDeliveryBinding,
    *,
    current_site: str,
    current_release_sha: str,
    current_registry_fingerprint: str,
) -> ObjectDeltaReceiverDeliveryBinding:
    """Bind the loaded permit to the local receiver role and installed release."""

    if not isinstance(binding, ObjectDeltaReceiverDeliveryBinding):
        raise ObjectDeltaReceiverDeliveryBindingError("receiver delivery binding is invalid")
    if current_site not in WEBAPP_SITES:
        raise ObjectDeltaReceiverDeliveryBindingError("receiver delivery local site is invalid")
    if binding.permit.destination_site != current_site:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery permit destination does not match this site"
        )
    if not isinstance(current_release_sha, str) or RELEASE_SHA_RE.fullmatch(current_release_sha) is None:
        raise ObjectDeltaReceiverDeliveryBindingError("receiver delivery current release is invalid")
    if binding.permit.release_sha != current_release_sha:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery permit does not match this release"
        )
    if (
        not isinstance(current_registry_fingerprint, str)
        or REGISTRY_FINGERPRINT_RE.fullmatch(current_registry_fingerprint) is None
    ):
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery current registry fingerprint is invalid"
        )
    if binding.expected_registry_fingerprint != current_registry_fingerprint:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery permit does not match this release registry fingerprint"
        )
    return binding


def receiver_delivery_binding_from_settings(
    settings: object,
) -> ObjectDeltaReceiverDeliveryBinding | None:
    """Project settings to a permit only when all writer fences are explicit.

    This default-off function intentionally has no caller in the application.
    The disabled path must not inspect the permit path, allowing legacy
    deployments to retain arbitrary or absent values without a new read.
    """

    enabled = getattr(settings, "object_delta_receiver_delivery_enabled", False)
    if enabled is False:
        return None
    if enabled is not True:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery runtime flag is invalid"
        )
    if getattr(settings, "single_writer_runtime_enabled", False) is not True:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery runtime requires single-writer mode"
        )
    if getattr(settings, "application_writer_term_enforced", False) is not True:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery runtime requires application writer terms"
        )
    source_outbox_enabled = getattr(settings, "object_delta_source_outbox_enabled", False)
    if source_outbox_enabled is not False:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery runtime requires source outbox to be disabled"
        )
    raw_path = getattr(settings, "object_delta_receiver_delivery_permit_file", None)
    if raw_path is None or raw_path == "":
        raise ObjectDeltaReceiverDeliveryBindingError("receiver delivery permit file is required")
    current_site = getattr(settings, "object_delta_receiver_delivery_local_site", None)
    current_release_sha = getattr(settings, "release_sha", None)
    if current_site not in WEBAPP_SITES:
        raise ObjectDeltaReceiverDeliveryBindingError("receiver delivery local site is invalid")
    if not isinstance(current_release_sha, str) or RELEASE_SHA_RE.fullmatch(current_release_sha) is None:
        raise ObjectDeltaReceiverDeliveryBindingError("receiver delivery current release is invalid")
    path = raw_path if isinstance(raw_path, Path) else Path(raw_path)
    binding = load_object_delta_receiver_delivery_binding(path)
    try:
        from core.sync_protocol import current_sync_registry_fingerprint

        current_registry_fingerprint = current_sync_registry_fingerprint()
    except Exception as exc:
        raise ObjectDeltaReceiverDeliveryBindingError(
            "receiver delivery current registry fingerprint is unavailable"
        ) from exc
    return validate_object_delta_receiver_delivery_runtime(
        binding,
        current_site=current_site,
        current_release_sha=current_release_sha,
        current_registry_fingerprint=current_registry_fingerprint,
    )
