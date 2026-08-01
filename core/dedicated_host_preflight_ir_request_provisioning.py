"""Signed, immutable provisioning records for a WA-IR preflight request.

This is a deliberately pure protocol boundary.  It turns the already typed
WA-IR/Witness attestation request into a short-lived FI-signed payload and a
second FI-signed, redacted immutable-object locator.  It does not open a
file, load a key, invoke ``age``, create an S3 client, contact either site, or
install the request.  The two root-owned runtimes are intentionally separate.

The signature key purpose is specific to this provisioning protocol.  It is
not the WA-IR attestation key, a Witness key, an age identity, an Object
Storage credential, or a writer-term key.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import base64
import binascii
import hashlib
import json
import re
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.append_only_sync_delta_batch import OBJECT_KEY_RE, VERSION_ID_RE
from core.dedicated_host_preflight_ir_witness_attestation import (
    MAX_WA_IR_WITNESS_ATTESTATION_REQUEST_BYTES,
    ParsedWaIrWitnessAttestationRequest,
    parse_wa_ir_witness_attestation_request,
)
from core.dedicated_host_preflight_receipt import CAMPAIGN_ID, HEX40, HEX64, canonical_json_bytes
from core.object_delta_transport_binding import AGE_RECIPIENT_RE


__all__ = (
    "FI_WA_IR_PREFLIGHT_REQUEST_LOCATOR_SCHEMA",
    "FI_WA_IR_PREFLIGHT_REQUEST_PAYLOAD_SCHEMA",
    "FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_KEY_PURPOSE",
    "FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_KEY_SCHEMA",
    "FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_PURPOSE",
    "MAX_FI_WA_IR_PREFLIGHT_REQUEST_VALIDITY_SECONDS",
    "FiWaIrPreflightRequestLocator",
    "FiWaIrPreflightRequestProvisioningBinding",
    "DedicatedHostPreflightIrRequestProvisioningError",
    "VerifiedFiWaIrPreflightRequestLocator",
    "VerifiedFiWaIrPreflightRequestPayload",
    "build_fi_wa_ir_preflight_request_locator",
    "build_fi_wa_ir_preflight_request_payload",
    "parse_fi_wa_ir_preflight_request_provisioning_key_record",
    "verify_fi_wa_ir_preflight_request_locator",
    "verify_fi_wa_ir_preflight_request_payload",
)


FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_KEY_SCHEMA = (
    "three-site-dedicated-host-preflight-fi-wa-ir-request-provisioning-key-v1"
)
FI_WA_IR_PREFLIGHT_REQUEST_PAYLOAD_SCHEMA = (
    "three-site-dedicated-host-preflight-fi-wa-ir-request-provisioning-payload-v1"
)
FI_WA_IR_PREFLIGHT_REQUEST_LOCATOR_SCHEMA = (
    "three-site-dedicated-host-preflight-fi-wa-ir-request-provisioning-locator-v1"
)
FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_PURPOSE = (
    "dedicated-host-preflight-fi-wa-ir-request-provisioning"
)
FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_KEY_PURPOSE = (
    "dedicated-host-preflight-fi-wa-ir-request-provisioning-key"
)

MAX_FI_WA_IR_PREFLIGHT_REQUEST_VALIDITY_SECONDS = 300
MAX_FI_WA_IR_PREFLIGHT_REQUEST_PAYLOAD_BYTES = (
    MAX_WA_IR_WITNESS_ATTESTATION_REQUEST_BYTES + 16 * 1024
)
MAX_FI_WA_IR_PREFLIGHT_REQUEST_LOCATOR_BYTES = 24 * 1024
MAX_FI_WA_IR_PREFLIGHT_REQUEST_KEY_BYTES = 4 * 1024
MAX_FI_WA_IR_PREFLIGHT_REQUEST_CIPHERTEXT_BYTES = 1024 * 1024

_VERSION = 1
_ROLE = "webapp_ir"
_SOURCE_SITE = "webapp_fi"
_DESTINATION_SITE = "webapp_ir"
_MAX_FUTURE_SKEW_SECONDS = 5
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)
_METADATA_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$", re.ASCII)
_MUTABLE_COMPONENTS = frozenset(
    {"alias", "current", "head", "latest", "pointer", "null", "undefined"}
)
_PAYLOAD_SIGNATURE_DOMAIN = (
    b"three-site-dedicated-host-preflight-fi-wa-ir-request-provisioning-payload-v1\x00"
)
_LOCATOR_SIGNATURE_DOMAIN = (
    b"three-site-dedicated-host-preflight-fi-wa-ir-request-provisioning-locator-v1\x00"
)
_CAPABILITY = object()


class DedicatedHostPreflightIrRequestProvisioningError(ValueError):
    """One redacted rejection from the FI-to-WA-IR request protocol."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FiWaIrPreflightRequestProvisioningBinding:
    """Fresh non-secret FI/IR Object-Storage binding for one signed request."""

    route_binding_sha256: str
    fi_publisher_identity_sha256: str
    ir_receiver_identity_sha256: str
    age_recipient: str
    issued_at: datetime
    maximum_validity_seconds: int


@dataclass(frozen=True)
class VerifiedFiWaIrPreflightRequestPayload:
    """One signature-verified short-lived request plaintext, not authority."""

    canonical_payload: bytes
    payload_sha256: str
    request: ParsedWaIrWitnessAttestationRequest
    route_binding_sha256: str
    fi_publisher_identity_sha256: str
    ir_receiver_identity_sha256: str
    age_recipient: str
    issued_at: datetime
    expires_at: datetime
    fi_signer_key_id: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class FiWaIrPreflightRequestLocator:
    """Non-secret immutable-object facts returned only after FI readback."""

    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    metadata: Mapping[str, str]


@dataclass(frozen=True)
class VerifiedFiWaIrPreflightRequestLocator:
    """One exact, FI-signed locator for a payload version; never a URL."""

    canonical_locator: bytes
    locator_sha256: str
    payload_sha256: str
    request_sha256: str
    campaign_id: str
    operation_id: str
    release_sha: str
    manifest_sha256: str
    attestation_id: str
    nonce: str
    route_binding_sha256: str
    fi_publisher_identity_sha256: str
    ir_receiver_identity_sha256: str
    age_recipient: str
    object: FiWaIrPreflightRequestLocator
    issued_at: datetime
    expires_at: datetime
    fi_signer_key_id: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


def _fail(code: str) -> None:
    raise DedicatedHostPreflightIrRequestProvisioningError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("FI_WA_IR_REQUEST_PROVISIONING_JSON_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("FI_WA_IR_REQUEST_PROVISIONING_JSON_INVALID")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise DedicatedHostPreflightIrRequestProvisioningError(code) from exc


def _parse_canonical_object(raw: object, *, maximum_bytes: int, code: str) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > maximum_bytes:
        _fail(code)
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except DedicatedHostPreflightIrRequestProvisioningError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        _fail(code)
    if type(value) is not dict or raw != _canonical(value, code=code) + b"\n":
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or HEX64.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _uuid(value: object, *, code: str) -> str:
    if type(value) is not str:
        _fail(code)
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        _fail(code)
    if parsed.int == 0 or str(parsed) != value:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    normalized = parsed.astimezone(timezone.utc)
    if normalized.isoformat().replace("+00:00", "Z") != value:
        _fail(code)
    return normalized


def _render_timestamp(value: object, *, code: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _public_key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except (TypeError, ValueError):
        _fail(code)
    return value


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _signer(value: object, *, code: str) -> tuple[Ed25519PrivateKey, bytes, str]:
    if not isinstance(value, Ed25519PrivateKey):
        _fail(code)
    try:
        public_key = value.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (TypeError, ValueError):
        _fail(code)
    return value, _public_key(public_key, code=code), _key_id(public_key)


def _signature(value: object, *, code: str) -> bytes:
    if not isinstance(value, Mapping) or set(value) != {"algorithm", "signature_base64"}:
        _fail(code)
    if value["algorithm"] != "ed25519" or type(value["signature_base64"]) is not str:
        _fail(code)
    try:
        result = base64.b64decode(value["signature_base64"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if len(result) != 64:
        _fail(code)
    return result


def _signer_mapping(public_key: bytes) -> dict[str, str]:
    return {
        "algorithm": "ed25519",
        "key_id": _key_id(public_key),
        "purpose": FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_KEY_PURPOSE,
    }


def _verify_signer_mapping(value: object, *, public_key: bytes, code: str) -> str:
    if not isinstance(value, Mapping) or set(value) != {"algorithm", "key_id", "purpose"}:
        _fail(code)
    expected = _key_id(public_key)
    if (
        value.get("algorithm") != "ed25519"
        or value.get("key_id") != expected
        or value.get("purpose") != FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_KEY_PURPOSE
        or type(value.get("key_id")) is not str
        or _KEY_ID_RE.fullmatch(value["key_id"]) is None
    ):
        _fail(code)
    return expected


def _request(value: object, *, code: str) -> ParsedWaIrWitnessAttestationRequest:
    if type(value) is not ParsedWaIrWitnessAttestationRequest:
        _fail(code)
    try:
        parsed = parse_wa_ir_witness_attestation_request(value.canonical_request)
    except Exception as exc:
        raise DedicatedHostPreflightIrRequestProvisioningError(code) from exc
    if parsed != value:
        _fail(code)
    return parsed


def _binding(value: object, *, request: ParsedWaIrWitnessAttestationRequest, code: str) -> FiWaIrPreflightRequestProvisioningBinding:
    if type(value) is not FiWaIrPreflightRequestProvisioningBinding:
        _fail(code)
    route = _sha256(value.route_binding_sha256, code=code)
    fi_identity = _sha256(value.fi_publisher_identity_sha256, code=code)
    ir_identity = _sha256(value.ir_receiver_identity_sha256, code=code)
    if fi_identity == ir_identity:
        _fail(code)
    if type(value.age_recipient) is not str or AGE_RECIPIENT_RE.fullmatch(value.age_recipient) is None:
        _fail(code)
    issued = _timestamp(_render_timestamp(value.issued_at, code=code), code=code)
    if (
        type(value.maximum_validity_seconds) is not int
        or not 1 <= value.maximum_validity_seconds <= MAX_FI_WA_IR_PREFLIGHT_REQUEST_VALIDITY_SECONDS
        or value.maximum_validity_seconds > request.maximum_validity_seconds
    ):
        _fail(code)
    return FiWaIrPreflightRequestProvisioningBinding(
        route_binding_sha256=route,
        fi_publisher_identity_sha256=fi_identity,
        ir_receiver_identity_sha256=ir_identity,
        age_recipient=value.age_recipient,
        issued_at=issued,
        maximum_validity_seconds=value.maximum_validity_seconds,
    )


def _request_mapping(request: ParsedWaIrWitnessAttestationRequest, *, code: str) -> dict[str, Any]:
    value = _parse_canonical_object(
        request.canonical_request,
        maximum_bytes=MAX_WA_IR_WITNESS_ATTESTATION_REQUEST_BYTES,
        code=code,
    )
    return value


def parse_fi_wa_ir_preflight_request_provisioning_key_record(raw: bytes) -> Ed25519PrivateKey:
    """Parse only a dedicated FI request-provisioning Ed25519 key record."""

    value = _parse_canonical_object(
        raw,
        maximum_bytes=MAX_FI_WA_IR_PREFLIGHT_REQUEST_KEY_BYTES,
        code="FI_WA_IR_REQUEST_PROVISIONING_KEY_INVALID",
    )
    if set(value) != {
        "schema",
        "version",
        "purpose",
        "algorithm",
        "private_key_base64",
        "public_key_sha256",
        "key_id",
    }:
        _fail("FI_WA_IR_REQUEST_PROVISIONING_KEY_INVALID")
    if (
        value["schema"] != FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_KEY_SCHEMA
        or value["version"] != _VERSION
        or value["purpose"] != FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_KEY_PURPOSE
        or value["algorithm"] != "ed25519"
        or type(value["private_key_base64"]) is not str
    ):
        _fail("FI_WA_IR_REQUEST_PROVISIONING_KEY_INVALID")
    try:
        private_raw = base64.b64decode(value["private_key_base64"].encode("ascii"), validate=True)
        signer = Ed25519PrivateKey.from_private_bytes(private_raw)
    except (UnicodeEncodeError, binascii.Error, TypeError, ValueError):
        _fail("FI_WA_IR_REQUEST_PROVISIONING_KEY_INVALID")
    _signer_value, public_key, key_id = _signer(signer, code="FI_WA_IR_REQUEST_PROVISIONING_KEY_INVALID")
    if (
        value["public_key_sha256"] != hashlib.sha256(public_key).hexdigest()
        or value["key_id"] != key_id
    ):
        _fail("FI_WA_IR_REQUEST_PROVISIONING_KEY_INVALID")
    return signer


def build_fi_wa_ir_preflight_request_payload(
    *,
    request: ParsedWaIrWitnessAttestationRequest,
    binding: FiWaIrPreflightRequestProvisioningBinding,
    signer: Ed25519PrivateKey,
) -> bytes:
    """Sign one typed request and one fresh route/identity binding for FI publish."""

    verified_request = _request(request, code="FI_WA_IR_REQUEST_PROVISIONING_REQUEST_INVALID")
    verified_binding = _binding(
        binding,
        request=verified_request,
        code="FI_WA_IR_REQUEST_PROVISIONING_BINDING_INVALID",
    )
    private, public_key, _key = _signer(signer, code="FI_WA_IR_REQUEST_PROVISIONING_SIGNER_INVALID")
    request_value = _request_mapping(
        verified_request,
        code="FI_WA_IR_REQUEST_PROVISIONING_REQUEST_INVALID",
    )
    issued_at = verified_binding.issued_at
    expires_at = issued_at + timedelta(seconds=verified_binding.maximum_validity_seconds)
    unsigned: dict[str, Any] = {
        "schema": FI_WA_IR_PREFLIGHT_REQUEST_PAYLOAD_SCHEMA,
        "version": _VERSION,
        "purpose": FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_PURPOSE,
        "source_site": _SOURCE_SITE,
        "destination_site": _DESTINATION_SITE,
        "role": _ROLE,
        "campaign_id": verified_request.readonly_request["campaign_id"],
        "operation_id": verified_request.readonly_request["operation_id"],
        "release_sha": verified_request.readonly_request["release_sha"],
        "manifest_sha256": verified_request.readonly_request["manifest_sha256"],
        "attestation_id": verified_request.attestation_id,
        "nonce": verified_request.nonce,
        "wa_ir_request": request_value,
        "wa_ir_request_sha256": verified_request.attestation_request_sha256,
        "route_binding_sha256": verified_binding.route_binding_sha256,
        "fi_publisher_identity_sha256": verified_binding.fi_publisher_identity_sha256,
        "ir_receiver_identity_sha256": verified_binding.ir_receiver_identity_sha256,
        "age_recipient": verified_binding.age_recipient,
        "issued_at": _render_timestamp(issued_at, code="FI_WA_IR_REQUEST_PROVISIONING_TIME_INVALID"),
        "expires_at": _render_timestamp(expires_at, code="FI_WA_IR_REQUEST_PROVISIONING_TIME_INVALID"),
        "fi_signer": _signer_mapping(public_key),
    }
    try:
        signature = private.sign(
            _PAYLOAD_SIGNATURE_DOMAIN
            + _canonical(unsigned, code="FI_WA_IR_REQUEST_PROVISIONING_PAYLOAD_INVALID")
        )
    except (TypeError, ValueError):
        _fail("FI_WA_IR_REQUEST_PROVISIONING_SIGNER_INVALID")
    return _canonical(
        {
            **unsigned,
            "fi_signature": {
                "algorithm": "ed25519",
                "signature_base64": base64.b64encode(signature).decode("ascii"),
            },
        },
        code="FI_WA_IR_REQUEST_PROVISIONING_PAYLOAD_INVALID",
    ) + b"\n"


def _payload_facts(
    raw: object,
    *,
    expected_fi_public_key: bytes,
    now: datetime,
    code: str,
) -> VerifiedFiWaIrPreflightRequestPayload:
    public_key = _public_key(expected_fi_public_key, code=code)
    value = _parse_canonical_object(raw, maximum_bytes=MAX_FI_WA_IR_PREFLIGHT_REQUEST_PAYLOAD_BYTES, code=code)
    required = {
        "schema", "version", "purpose", "source_site", "destination_site", "role",
        "campaign_id", "operation_id", "release_sha", "manifest_sha256", "attestation_id", "nonce",
        "wa_ir_request", "wa_ir_request_sha256", "route_binding_sha256",
        "fi_publisher_identity_sha256", "ir_receiver_identity_sha256", "age_recipient",
        "issued_at", "expires_at", "fi_signer", "fi_signature",
    }
    if set(value) != required or (
        value["schema"] != FI_WA_IR_PREFLIGHT_REQUEST_PAYLOAD_SCHEMA
        or value["version"] != _VERSION
        or value["purpose"] != FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_PURPOSE
        or value["source_site"] != _SOURCE_SITE
        or value["destination_site"] != _DESTINATION_SITE
        or value["role"] != _ROLE
    ):
        _fail(code)
    request_raw = _canonical(value["wa_ir_request"], code=code) + b"\n"
    try:
        request = parse_wa_ir_witness_attestation_request(request_raw)
    except Exception as exc:
        raise DedicatedHostPreflightIrRequestProvisioningError(code) from exc
    if (
        value["wa_ir_request_sha256"] != request.attestation_request_sha256
        or value["campaign_id"] != request.readonly_request["campaign_id"]
        or value["operation_id"] != request.readonly_request["operation_id"]
        or value["release_sha"] != request.readonly_request["release_sha"]
        or value["manifest_sha256"] != request.readonly_request["manifest_sha256"]
        or value["attestation_id"] != request.attestation_id
        or value["nonce"] != request.nonce
        or type(value["release_sha"]) is not str
        or HEX40.fullmatch(value["release_sha"]) is None
    ):
        _fail(code)
    route = _sha256(value["route_binding_sha256"], code=code)
    fi_identity = _sha256(value["fi_publisher_identity_sha256"], code=code)
    ir_identity = _sha256(value["ir_receiver_identity_sha256"], code=code)
    if fi_identity == ir_identity or type(value["age_recipient"]) is not str or AGE_RECIPIENT_RE.fullmatch(value["age_recipient"]) is None:
        _fail(code)
    issued = _timestamp(value["issued_at"], code=code)
    expires = _timestamp(value["expires_at"], code=code)
    normalized_now = _timestamp(_render_timestamp(now, code=code), code=code)
    if (
        expires <= issued
        or expires - issued > timedelta(seconds=MAX_FI_WA_IR_PREFLIGHT_REQUEST_VALIDITY_SECONDS)
        or expires - issued > timedelta(seconds=request.maximum_validity_seconds)
        or issued > normalized_now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or normalized_now > expires
    ):
        _fail(code)
    signer_key_id = _verify_signer_mapping(value["fi_signer"], public_key=public_key, code=code)
    signature = _signature(value["fi_signature"], code=code)
    unsigned = dict(value)
    del unsigned["fi_signature"]
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            _PAYLOAD_SIGNATURE_DOMAIN + _canonical(unsigned, code=code),
        )
    except (InvalidSignature, TypeError, ValueError):
        _fail(code)
    result = VerifiedFiWaIrPreflightRequestPayload(
        canonical_payload=raw,
        payload_sha256=hashlib.sha256(raw).hexdigest(),
        request=request,
        route_binding_sha256=route,
        fi_publisher_identity_sha256=fi_identity,
        ir_receiver_identity_sha256=ir_identity,
        age_recipient=value["age_recipient"],
        issued_at=issued,
        expires_at=expires,
        fi_signer_key_id=signer_key_id,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result


def verify_fi_wa_ir_preflight_request_payload(
    *,
    canonical_payload: bytes,
    expected_fi_public_key: bytes,
    now: datetime,
) -> VerifiedFiWaIrPreflightRequestPayload:
    """Verify fresh FI signature before the payload is encrypted or installed."""

    return _payload_facts(
        canonical_payload,
        expected_fi_public_key=expected_fi_public_key,
        now=now,
        code="FI_WA_IR_REQUEST_PROVISIONING_PAYLOAD_INVALID",
    )


def _safe_object_key(value: object, *, code: str) -> str:
    if type(value) is not str or OBJECT_KEY_RE.fullmatch(value) is None:
        _fail(code)
    parts = value.split("/")
    if (
        len(parts) < 5
        or not value.startswith("dedicated-host-preflight/v1/")
        or any(not part or part in {".", ".."} or part.lower() in _MUTABLE_COMPONENTS for part in parts)
    ):
        _fail(code)
    return value


def _safe_version(value: object, *, code: str) -> str:
    if type(value) is not str or VERSION_ID_RE.fullmatch(value) is None or value.lower() in _MUTABLE_COMPONENTS:
        _fail(code)
    return value


def _positive(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _metadata(value: object, *, digest: str, size: int, payload_sha256: str, request_sha256: str, recipient: str, code: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or len(value) != 5:
        _fail(code)
    result: dict[str, str] = {}
    for key, item in value.items():
        if (
            type(key) is not str
            or type(item) is not str
            or _METADATA_KEY_RE.fullmatch(key) is None
            or not item
            or len(item.encode("ascii", "strict")) > 1024
        ):
            _fail(code)
        result[key] = item
    if result != {
        "encryption": "age-v1",
        "ciphertext-sha256": digest,
        "ciphertext-bytes": str(size),
        "payload-sha256": payload_sha256,
        "request-sha256": request_sha256,
    }:
        _fail(code)
    del recipient  # Recipient is signed independently; never duplicated in opaque S3 metadata.
    return result


def build_fi_wa_ir_preflight_request_locator(
    *,
    canonical_payload: bytes,
    expected_fi_public_key: bytes,
    object: FiWaIrPreflightRequestLocator,
    signer: Ed25519PrivateKey,
    now: datetime,
) -> bytes:
    """Sign a redacted exact VersionId locator after FI's exact readback."""

    payload = verify_fi_wa_ir_preflight_request_payload(
        canonical_payload=canonical_payload,
        expected_fi_public_key=expected_fi_public_key,
        now=now,
    )
    private, public_key, key_id = _signer(signer, code="FI_WA_IR_REQUEST_PROVISIONING_SIGNER_INVALID")
    if key_id != payload.fi_signer_key_id or public_key != expected_fi_public_key:
        _fail("FI_WA_IR_REQUEST_PROVISIONING_SIGNER_NOT_PINNED")
    if type(object) is not FiWaIrPreflightRequestLocator:
        _fail("FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_INVALID")
    key = _safe_object_key(object.object_key, code="FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_INVALID")
    version = _safe_version(object.version_id, code="FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_INVALID")
    digest = _sha256(object.ciphertext_sha256, code="FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_INVALID")
    size = _positive(
        object.ciphertext_bytes,
        maximum=MAX_FI_WA_IR_PREFLIGHT_REQUEST_CIPHERTEXT_BYTES,
        code="FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_INVALID",
    )
    request_sha256 = payload.request.attestation_request_sha256
    metadata = _metadata(
        object.metadata,
        digest=digest,
        size=size,
        payload_sha256=payload.payload_sha256,
        request_sha256=request_sha256,
        recipient=payload.age_recipient,
        code="FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_INVALID",
    )
    unsigned: dict[str, Any] = {
        "schema": FI_WA_IR_PREFLIGHT_REQUEST_LOCATOR_SCHEMA,
        "version": _VERSION,
        "purpose": FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_PURPOSE,
        "source_site": _SOURCE_SITE,
        "destination_site": _DESTINATION_SITE,
        "role": _ROLE,
        "campaign_id": payload.request.readonly_request["campaign_id"],
        "operation_id": payload.request.readonly_request["operation_id"],
        "release_sha": payload.request.readonly_request["release_sha"],
        "manifest_sha256": payload.request.readonly_request["manifest_sha256"],
        "attestation_id": payload.request.attestation_id,
        "nonce": payload.request.nonce,
        "wa_ir_request_sha256": request_sha256,
        "payload_sha256": payload.payload_sha256,
        "route_binding_sha256": payload.route_binding_sha256,
        "fi_publisher_identity_sha256": payload.fi_publisher_identity_sha256,
        "ir_receiver_identity_sha256": payload.ir_receiver_identity_sha256,
        "age_recipient": payload.age_recipient,
        "issued_at": _render_timestamp(payload.issued_at, code="FI_WA_IR_REQUEST_PROVISIONING_TIME_INVALID"),
        "expires_at": _render_timestamp(payload.expires_at, code="FI_WA_IR_REQUEST_PROVISIONING_TIME_INVALID"),
        "object": {
            "object_key": key,
            "version_id": version,
            "ciphertext_sha256": digest,
            "ciphertext_bytes": size,
            "metadata": metadata,
        },
        "fi_signer": _signer_mapping(public_key),
    }
    try:
        signature = private.sign(
            _LOCATOR_SIGNATURE_DOMAIN
            + _canonical(unsigned, code="FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_INVALID")
        )
    except (TypeError, ValueError):
        _fail("FI_WA_IR_REQUEST_PROVISIONING_SIGNER_INVALID")
    return _canonical(
        {
            **unsigned,
            "fi_signature": {
                "algorithm": "ed25519",
                "signature_base64": base64.b64encode(signature).decode("ascii"),
            },
        },
        code="FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_INVALID",
    ) + b"\n"


def verify_fi_wa_ir_preflight_request_locator(
    *,
    canonical_locator: bytes,
    expected_fi_public_key: bytes,
    now: datetime,
) -> VerifiedFiWaIrPreflightRequestLocator:
    """Verify one FI-signed redacted locator before an exact WA-IR pull."""

    code = "FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_INVALID"
    public_key = _public_key(expected_fi_public_key, code=code)
    value = _parse_canonical_object(canonical_locator, maximum_bytes=MAX_FI_WA_IR_PREFLIGHT_REQUEST_LOCATOR_BYTES, code=code)
    required = {
        "schema", "version", "purpose", "source_site", "destination_site", "role",
        "campaign_id", "operation_id", "release_sha", "manifest_sha256", "attestation_id", "nonce",
        "wa_ir_request_sha256", "payload_sha256", "route_binding_sha256",
        "fi_publisher_identity_sha256", "ir_receiver_identity_sha256", "age_recipient",
        "issued_at", "expires_at", "object", "fi_signer", "fi_signature",
    }
    if set(value) != required or (
        value["schema"] != FI_WA_IR_PREFLIGHT_REQUEST_LOCATOR_SCHEMA
        or value["version"] != _VERSION
        or value["purpose"] != FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_PURPOSE
        or value["source_site"] != _SOURCE_SITE
        or value["destination_site"] != _DESTINATION_SITE
        or value["role"] != _ROLE
        or type(value["campaign_id"]) is not str
        or CAMPAIGN_ID.fullmatch(value["campaign_id"]) is None
        or _uuid(value["operation_id"], code=code) != value["operation_id"]
        or type(value["release_sha"]) is not str
        or HEX40.fullmatch(value["release_sha"]) is None
    ):
        _fail(code)
    request_sha = _sha256(value["wa_ir_request_sha256"], code=code)
    payload_sha = _sha256(value["payload_sha256"], code=code)
    _uuid(value["attestation_id"], code=code)
    _nonce(value["nonce"], code=code)
    route = _sha256(value["route_binding_sha256"], code=code)
    fi_identity = _sha256(value["fi_publisher_identity_sha256"], code=code)
    ir_identity = _sha256(value["ir_receiver_identity_sha256"], code=code)
    if fi_identity == ir_identity or type(value["age_recipient"]) is not str or AGE_RECIPIENT_RE.fullmatch(value["age_recipient"]) is None:
        _fail(code)
    issued = _timestamp(value["issued_at"], code=code)
    expires = _timestamp(value["expires_at"], code=code)
    normalized_now = _timestamp(_render_timestamp(now, code=code), code=code)
    if (
        expires <= issued
        or expires - issued > timedelta(seconds=MAX_FI_WA_IR_PREFLIGHT_REQUEST_VALIDITY_SECONDS)
        or issued > normalized_now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or normalized_now > expires
    ):
        _fail(code)
    object_value = value["object"]
    if not isinstance(object_value, Mapping) or set(object_value) != {
        "object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "metadata"
    }:
        _fail(code)
    digest = _sha256(object_value["ciphertext_sha256"], code=code)
    size = _positive(
        object_value["ciphertext_bytes"],
        maximum=MAX_FI_WA_IR_PREFLIGHT_REQUEST_CIPHERTEXT_BYTES,
        code=code,
    )
    object_facts = FiWaIrPreflightRequestLocator(
        object_key=_safe_object_key(object_value["object_key"], code=code),
        version_id=_safe_version(object_value["version_id"], code=code),
        ciphertext_sha256=digest,
        ciphertext_bytes=size,
        metadata=_metadata(
            object_value["metadata"],
            digest=digest,
            size=size,
            payload_sha256=payload_sha,
            request_sha256=request_sha,
            recipient=value["age_recipient"],
            code=code,
        ),
    )
    key_id = _verify_signer_mapping(value["fi_signer"], public_key=public_key, code=code)
    signature = _signature(value["fi_signature"], code=code)
    unsigned = dict(value)
    del unsigned["fi_signature"]
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            _LOCATOR_SIGNATURE_DOMAIN + _canonical(unsigned, code=code),
        )
    except (InvalidSignature, TypeError, ValueError):
        _fail(code)
    # The remaining fields are tied to an inner payload by the post-decryption
    # receiver check.  Here they are bounded and carried into that comparison.
    result = VerifiedFiWaIrPreflightRequestLocator(
        canonical_locator=canonical_locator,
        locator_sha256=hashlib.sha256(canonical_locator).hexdigest(),
        payload_sha256=payload_sha,
        request_sha256=request_sha,
        campaign_id=value["campaign_id"],
        operation_id=value["operation_id"],
        release_sha=value["release_sha"],
        manifest_sha256=_sha256(value["manifest_sha256"], code=code),
        attestation_id=value["attestation_id"],
        nonce=value["nonce"],
        route_binding_sha256=route,
        fi_publisher_identity_sha256=fi_identity,
        ir_receiver_identity_sha256=ir_identity,
        age_recipient=value["age_recipient"],
        object=object_facts,
        issued_at=issued,
        expires_at=expires,
        fi_signer_key_id=key_id,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result
