"""Narrow signed WA-IR -> Witness preflight-attestation contract.

This module is deliberately a cryptographic evidence contract, not a network
route, a Controller, a Writer-Witness term, an Object-Storage adapter, or an
execution authority.  WA-IR signs one already canonical read-only preflight
receipt against one root-pinned local campaign request.  A separate Witness
ledger may admit that envelope and sign a second evidence envelope.  A
central-side verifier can recover the *same* v2 read-only receipt only after
both independent signatures and all bindings validate.

No function opens a file, creates a process, contacts a host, calls Object
Storage, uses age, invokes SSH, or starts a service.  Runtime loading of the
WA-IR key/request and durable Witness storage are intentionally separate
root-owned boundaries.
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

from core.dedicated_host_preflight_receipt import (
    CAMPAIGN_ID,
    HEX40,
    HEX64,
    MAX_RECEIPT_BYTES,
    canonical_json_bytes,
    parse_preflight_receipt,
)
from scripts.dedicated_host_preflight_manifest import (
    EXPECTED_HOSTS,
    READONLY_REQUEST_SCHEMA,
)


__all__ = (
    "DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_SCHEMA",
    "DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_STATUS",
    "MAX_WA_IR_WITNESS_ATTESTATION_BYTES",
    "MAX_WA_IR_WITNESS_ATTESTATION_VALIDITY_SECONDS",
    "WA_IR_WITNESS_ATTESTATION_ENVELOPE_SCHEMA",
    "WA_IR_WITNESS_ATTESTATION_KEY_SCHEMA",
    "WA_IR_WITNESS_ATTESTATION_KEY_PURPOSE",
    "WA_IR_WITNESS_ATTESTATION_REQUEST_SCHEMA",
    "WA_IR_WITNESS_ATTESTATION_REQUEST_PURPOSE",
    "WA_IR_WITNESS_ATTESTATION_STATUS",
    "DedicatedHostPreflightIrWitnessAttestationError",
    "ParsedWaIrWitnessAttestationRequest",
    "VerifiedWaIrWitnessAttestation",
    "VerifiedWitnessPreflightEvidence",
    "build_wa_ir_witness_attestation_envelope",
    "build_witness_preflight_evidence",
    "parse_wa_ir_witness_attestation_key_record",
    "parse_wa_ir_witness_attestation_request",
    "verify_wa_ir_witness_attestation_envelope",
    "verify_witness_preflight_evidence",
)


WA_IR_WITNESS_ATTESTATION_REQUEST_SCHEMA = (
    "three-site-dedicated-host-preflight-wa-ir-witness-attestation-request-v1"
)
WA_IR_WITNESS_ATTESTATION_KEY_SCHEMA = (
    "three-site-dedicated-host-preflight-wa-ir-witness-attestation-key-v1"
)
WA_IR_WITNESS_ATTESTATION_ENVELOPE_SCHEMA = (
    "three-site-dedicated-host-preflight-wa-ir-witness-attestation-envelope-v1"
)
DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_SCHEMA = (
    "three-site-dedicated-host-preflight-witness-evidence-v1"
)

WA_IR_WITNESS_ATTESTATION_REQUEST_PURPOSE = (
    "dedicated-host-preflight-wa-ir-witness-attestation"
)
WA_IR_WITNESS_ATTESTATION_KEY_PURPOSE = (
    "dedicated-host-preflight-wa-ir-witness-attestation-key"
)
_WITNESS_EVIDENCE_PURPOSE = "dedicated-host-preflight-witness-evidence"
WA_IR_WITNESS_ATTESTATION_STATUS = "wa-ir-readonly-preflight-attested"
DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_STATUS = "witness-attested-readonly-evidence"

MAX_WA_IR_WITNESS_ATTESTATION_BYTES = MAX_RECEIPT_BYTES + 32 * 1024
MAX_WA_IR_WITNESS_ATTESTATION_REQUEST_BYTES = 8 * 1024
MAX_WA_IR_WITNESS_ATTESTATION_KEY_BYTES = 4 * 1024
MAX_WA_IR_WITNESS_ATTESTATION_VALIDITY_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_VERSION = 1
_ROLE = "webapp_ir"
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)
_WA_IR_SIGNATURE_DOMAIN = (
    b"three-site-dedicated-host-preflight-wa-ir-witness-attestation-v1\x00"
)
_WITNESS_SIGNATURE_DOMAIN = (
    b"three-site-dedicated-host-preflight-witness-evidence-v1\x00"
)
_CAPABILITY = object()


class DedicatedHostPreflightIrWitnessAttestationError(ValueError):
    """One redacted rejection from the WA-IR/Witness evidence contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ParsedWaIrWitnessAttestationRequest:
    """One exact, non-secret campaign request pinned locally on WA-IR."""

    canonical_request: bytes
    attestation_request_sha256: str
    readonly_request: dict[str, str]
    readonly_request_bytes: bytes
    readonly_request_sha256: str
    attestation_id: str
    nonce: str
    maximum_validity_seconds: int
    wa_ir_attestation_key_id: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedWaIrWitnessAttestation:
    """Verified bounded WA-IR evidence; it is not an execution capability."""

    canonical_envelope: bytes
    envelope_sha256: str
    canonical_receipt: bytes
    receipt_sha256: str
    attestation_request_sha256: str
    readonly_request_sha256: str
    attestation_id: str
    nonce: str
    issued_at: datetime
    expires_at: datetime
    wa_ir_key_id: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedWitnessPreflightEvidence:
    """A dual-signed observation containing the exact existing v2 receipt."""

    canonical_evidence: bytes
    evidence_sha256: str
    wa_ir_attestation: VerifiedWaIrWitnessAttestation
    canonical_receipt: bytes
    receipt: dict[str, Any]
    witness_key_id: str
    accepted_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


def _fail(code: str) -> None:
    raise DedicatedHostPreflightIrWitnessAttestationError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("WA_IR_WITNESS_ATTESTATION_JSON_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("WA_IR_WITNESS_ATTESTATION_JSON_INVALID")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise DedicatedHostPreflightIrWitnessAttestationError(code) from exc


def _parse_canonical_object(raw: object, *, maximum_bytes: int, code: str) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > maximum_bytes:
        _fail(code)
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except DedicatedHostPreflightIrWitnessAttestationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        _fail(code)
    if type(value) is not dict or raw != _canonical(value, code=code) + b"\n":
        _fail(code)
    return value


def _sha256_text(value: object, *, code: str) -> str:
    if type(value) is not str or HEX64.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or not value or len(value) > 256 or value != value.strip():
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _uuid(value: object, *, code: str) -> str:
    text = _identifier(value, code=code)
    try:
        parsed = UUID(text)
    except (TypeError, ValueError, AttributeError):
        _fail(code)
    if parsed.int == 0 or str(parsed) != text:
        _fail(code)
    return text


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


def _render_timestamp(value: datetime, *, code: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _public_key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except (TypeError, ValueError):
        _fail(code)
    return value


def _public_key_from_signer(value: object, *, code: str) -> tuple[Ed25519PrivateKey, bytes, str]:
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
        signature = base64.b64decode(value["signature_base64"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if len(signature) != 64:
        _fail(code)
    return signature


def _signer_mapping(public_key: bytes, *, purpose: str) -> dict[str, str]:
    return {"algorithm": "ed25519", "key_id": _key_id(public_key), "purpose": purpose}


def _verify_signer_mapping(
    value: object,
    *,
    expected_public_key: bytes,
    expected_purpose: str,
    code: str,
) -> str:
    if not isinstance(value, Mapping) or set(value) != {"algorithm", "key_id", "purpose"}:
        _fail(code)
    if (
        value["algorithm"] != "ed25519"
        or value["purpose"] != expected_purpose
        or value["key_id"] != _key_id(expected_public_key)
    ):
        _fail(code)
    if type(value["key_id"]) is not str or _KEY_ID_RE.fullmatch(value["key_id"]) is None:
        _fail(code)
    return value["key_id"]


def _readonly_request(value: object, *, code: str) -> tuple[dict[str, str], bytes, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "campaign_id",
        "operation_id",
        "release_sha",
        "role",
        "manifest_sha256",
    }:
        _fail(code)
    if value["schema"] != READONLY_REQUEST_SCHEMA or value["role"] != _ROLE:
        _fail(code)
    campaign_id = value["campaign_id"]
    if type(campaign_id) is not str or CAMPAIGN_ID.fullmatch(campaign_id) is None:
        _fail(code)
    operation_id = _uuid(value["operation_id"], code=code)
    release_sha = value["release_sha"]
    manifest_sha256 = value["manifest_sha256"]
    if (
        type(release_sha) is not str
        or HEX40.fullmatch(release_sha) is None
        or type(manifest_sha256) is not str
        or HEX64.fullmatch(manifest_sha256) is None
    ):
        _fail(code)
    normalized = {
        "schema": READONLY_REQUEST_SCHEMA,
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "role": _ROLE,
        "manifest_sha256": manifest_sha256,
    }
    raw = _canonical(normalized, code=code) + b"\n"
    return normalized, raw, hashlib.sha256(raw).hexdigest()


def _request_facts(value: object, *, code: str) -> ParsedWaIrWitnessAttestationRequest:
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "version",
        "purpose",
        "readonly_request",
        "readonly_request_sha256",
        "attestation_id",
        "nonce",
        "maximum_validity_seconds",
        "wa_ir_attestation_key_id",
    }:
        _fail(code)
    if (
        value["schema"] != WA_IR_WITNESS_ATTESTATION_REQUEST_SCHEMA
        or value["version"] != _VERSION
        or value["purpose"] != WA_IR_WITNESS_ATTESTATION_REQUEST_PURPOSE
    ):
        _fail(code)
    readonly_request, readonly_raw, readonly_sha256 = _readonly_request(value["readonly_request"], code=code)
    if value["readonly_request_sha256"] != readonly_sha256:
        _fail(code)
    attestation_id = _uuid(value["attestation_id"], code=code)
    nonce = _nonce(value["nonce"], code=code)
    maximum_validity_seconds = value["maximum_validity_seconds"]
    if (
        type(maximum_validity_seconds) is not int
        or not 1 <= maximum_validity_seconds <= MAX_WA_IR_WITNESS_ATTESTATION_VALIDITY_SECONDS
    ):
        _fail(code)
    key_id = value["wa_ir_attestation_key_id"]
    if type(key_id) is not str or _KEY_ID_RE.fullmatch(key_id) is None:
        _fail(code)
    canonical_request = _canonical(dict(value), code=code) + b"\n"
    result = ParsedWaIrWitnessAttestationRequest(
        canonical_request=canonical_request,
        attestation_request_sha256=hashlib.sha256(canonical_request).hexdigest(),
        readonly_request=readonly_request,
        readonly_request_bytes=readonly_raw,
        readonly_request_sha256=readonly_sha256,
        attestation_id=attestation_id,
        nonce=nonce,
        maximum_validity_seconds=maximum_validity_seconds,
        wa_ir_attestation_key_id=key_id,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result


def parse_wa_ir_witness_attestation_request(raw: bytes) -> ParsedWaIrWitnessAttestationRequest:
    """Parse the only campaign request a WA-IR attester may consume."""

    return _request_facts(
        _parse_canonical_object(
            raw,
            maximum_bytes=MAX_WA_IR_WITNESS_ATTESTATION_REQUEST_BYTES,
            code="WA_IR_WITNESS_ATTESTATION_REQUEST_INVALID",
        ),
        code="WA_IR_WITNESS_ATTESTATION_REQUEST_INVALID",
    )


def parse_wa_ir_witness_attestation_key_record(raw: bytes) -> Ed25519PrivateKey:
    """Parse one dedicated root-loaded WA-IR preflight signing-key record.

    The record has an explicit preflight-only purpose and cannot be a raw age,
    S3, or Writer-Witness key file.  Callers must still secure the fixed file
    path and never log or return its raw content.
    """

    value = _parse_canonical_object(
        raw,
        maximum_bytes=MAX_WA_IR_WITNESS_ATTESTATION_KEY_BYTES,
        code="WA_IR_WITNESS_ATTESTATION_KEY_INVALID",
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
        _fail("WA_IR_WITNESS_ATTESTATION_KEY_INVALID")
    if (
        value["schema"] != WA_IR_WITNESS_ATTESTATION_KEY_SCHEMA
        or value["version"] != _VERSION
        or value["purpose"] != WA_IR_WITNESS_ATTESTATION_KEY_PURPOSE
        or value["algorithm"] != "ed25519"
        or type(value["private_key_base64"]) is not str
    ):
        _fail("WA_IR_WITNESS_ATTESTATION_KEY_INVALID")
    try:
        private_raw = base64.b64decode(value["private_key_base64"].encode("ascii"), validate=True)
        signer = Ed25519PrivateKey.from_private_bytes(private_raw)
    except (UnicodeEncodeError, binascii.Error, TypeError, ValueError):
        _fail("WA_IR_WITNESS_ATTESTATION_KEY_INVALID")
    _signer, public_key, key_id = _public_key_from_signer(
        signer,
        code="WA_IR_WITNESS_ATTESTATION_KEY_INVALID",
    )
    if (
        value["public_key_sha256"] != hashlib.sha256(public_key).hexdigest()
        or value["key_id"] != key_id
    ):
        _fail("WA_IR_WITNESS_ATTESTATION_KEY_INVALID")
    return signer


def _validated_request(value: object, *, code: str) -> ParsedWaIrWitnessAttestationRequest:
    if type(value) is not ParsedWaIrWitnessAttestationRequest or value._capability is not _CAPABILITY:
        _fail(code)
    parsed = parse_wa_ir_witness_attestation_request(value.canonical_request)
    if parsed != value:
        _fail(code)
    return parsed


def _validated_attestation(value: object, *, code: str) -> VerifiedWaIrWitnessAttestation:
    if type(value) is not VerifiedWaIrWitnessAttestation or value._capability is not _CAPABILITY:
        _fail(code)
    return value


def _receipt_from_request(
    raw: object,
    *,
    request: ParsedWaIrWitnessAttestationRequest,
    code: str,
) -> tuple[dict[str, Any], bytes, str]:
    if type(raw) is not bytes or len(raw) > MAX_RECEIPT_BYTES:
        _fail(code)
    try:
        receipt = parse_preflight_receipt(
            raw,
            expected_role=_ROLE,
            expected_campaign_id=request.readonly_request["campaign_id"],
            expected_operation_id=request.readonly_request["operation_id"],
            expected_instance_id=EXPECTED_HOSTS[_ROLE]["instance_id"],
            expected_manifest_sha256=request.readonly_request["manifest_sha256"],
        )
    except Exception as exc:
        raise DedicatedHostPreflightIrWitnessAttestationError(code) from exc
    if (
        receipt["release_sha"] != request.readonly_request["release_sha"]
        or receipt["instance"]["provider"] != "arvan_ecc"
        or receipt["instance"]["public_ipv4"] != EXPECTED_HOSTS[_ROLE]["public_ip"]
    ):
        _fail(code)
    return receipt, raw, hashlib.sha256(raw).hexdigest()


def build_wa_ir_witness_attestation_envelope(
    *,
    request: ParsedWaIrWitnessAttestationRequest,
    canonical_receipt: bytes,
    signer: Ed25519PrivateKey,
    issued_at: datetime,
) -> bytes:
    """Sign one existing canonical WA-IR v2 receipt for the Witness path."""

    facts = _validated_request(request, code="WA_IR_WITNESS_ATTESTATION_REQUEST_INVALID")
    _receipt, receipt_raw, receipt_sha256 = _receipt_from_request(
        canonical_receipt,
        request=facts,
        code="WA_IR_WITNESS_ATTESTATION_RECEIPT_INVALID",
    )
    signer, public_key, key_id = _public_key_from_signer(
        signer,
        code="WA_IR_WITNESS_ATTESTATION_SIGNER_INVALID",
    )
    if key_id != facts.wa_ir_attestation_key_id:
        _fail("WA_IR_WITNESS_ATTESTATION_KEY_NOT_PINNED")
    issued = _timestamp(
        _render_timestamp(issued_at, code="WA_IR_WITNESS_ATTESTATION_TIME_INVALID"),
        code="WA_IR_WITNESS_ATTESTATION_TIME_INVALID",
    )
    expires = issued + timedelta(seconds=facts.maximum_validity_seconds)
    receipt_value = _parse_canonical_object(
        receipt_raw,
        maximum_bytes=MAX_RECEIPT_BYTES,
        code="WA_IR_WITNESS_ATTESTATION_RECEIPT_INVALID",
    )
    unsigned: dict[str, Any] = {
        "schema": WA_IR_WITNESS_ATTESTATION_ENVELOPE_SCHEMA,
        "version": _VERSION,
        "status": WA_IR_WITNESS_ATTESTATION_STATUS,
        "purpose": WA_IR_WITNESS_ATTESTATION_REQUEST_PURPOSE,
        "attestation_request_sha256": facts.attestation_request_sha256,
        "readonly_request_sha256": facts.readonly_request_sha256,
        "campaign_id": facts.readonly_request["campaign_id"],
        "operation_id": facts.readonly_request["operation_id"],
        "release_sha": facts.readonly_request["release_sha"],
        "manifest_sha256": facts.readonly_request["manifest_sha256"],
        "role": _ROLE,
        "instance": {
            "provider": "arvan_ecc",
            "server_id": EXPECTED_HOSTS[_ROLE]["instance_id"],
            "public_ipv4": EXPECTED_HOSTS[_ROLE]["public_ip"],
        },
        "attestation_id": facts.attestation_id,
        "nonce": facts.nonce,
        "issued_at": _render_timestamp(issued, code="WA_IR_WITNESS_ATTESTATION_TIME_INVALID"),
        "expires_at": _render_timestamp(expires, code="WA_IR_WITNESS_ATTESTATION_TIME_INVALID"),
        "preflight_receipt": receipt_value,
        "preflight_receipt_sha256": receipt_sha256,
        "wa_ir_signer": _signer_mapping(
            public_key,
            purpose=WA_IR_WITNESS_ATTESTATION_KEY_PURPOSE,
        ),
    }
    try:
        signature = signer.sign(_WA_IR_SIGNATURE_DOMAIN + _canonical(unsigned, code="WA_IR_WITNESS_ATTESTATION_INVALID"))
    except (TypeError, ValueError):
        _fail("WA_IR_WITNESS_ATTESTATION_SIGNER_INVALID")
    result = {
        **unsigned,
        "wa_ir_signature": {
            "algorithm": "ed25519",
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        },
    }
    return _canonical(result, code="WA_IR_WITNESS_ATTESTATION_INVALID") + b"\n"


def _envelope_facts(
    raw: object,
    *,
    expected_request: ParsedWaIrWitnessAttestationRequest,
    expected_wa_ir_public_key: bytes,
    now: datetime,
    code: str,
) -> VerifiedWaIrWitnessAttestation:
    request = _validated_request(expected_request, code=code)
    public_key = _public_key(expected_wa_ir_public_key, code=code)
    value = _parse_canonical_object(raw, maximum_bytes=MAX_WA_IR_WITNESS_ATTESTATION_BYTES, code=code)
    required = {
        "schema",
        "version",
        "status",
        "purpose",
        "attestation_request_sha256",
        "readonly_request_sha256",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "role",
        "instance",
        "attestation_id",
        "nonce",
        "issued_at",
        "expires_at",
        "preflight_receipt",
        "preflight_receipt_sha256",
        "wa_ir_signer",
        "wa_ir_signature",
    }
    if set(value) != required or (
        value["schema"] != WA_IR_WITNESS_ATTESTATION_ENVELOPE_SCHEMA
        or value["version"] != _VERSION
        or value["status"] != WA_IR_WITNESS_ATTESTATION_STATUS
        or value["purpose"] != WA_IR_WITNESS_ATTESTATION_REQUEST_PURPOSE
    ):
        _fail(code)
    if (
        value["attestation_request_sha256"] != request.attestation_request_sha256
        or value["readonly_request_sha256"] != request.readonly_request_sha256
        or value["campaign_id"] != request.readonly_request["campaign_id"]
        or value["operation_id"] != request.readonly_request["operation_id"]
        or value["release_sha"] != request.readonly_request["release_sha"]
        or value["manifest_sha256"] != request.readonly_request["manifest_sha256"]
        or value["role"] != _ROLE
        or _uuid(value["attestation_id"], code=code) != request.attestation_id
        or _nonce(value["nonce"], code=code) != request.nonce
    ):
        _fail(code)
    instance = value["instance"]
    if not isinstance(instance, Mapping) or dict(instance) != {
        "provider": "arvan_ecc",
        "server_id": EXPECTED_HOSTS[_ROLE]["instance_id"],
        "public_ipv4": EXPECTED_HOSTS[_ROLE]["public_ip"],
    }:
        _fail(code)
    issued_at = _timestamp(value["issued_at"], code=code)
    expires_at = _timestamp(value["expires_at"], code=code)
    normalized_now = _timestamp(
        _render_timestamp(now, code=code),
        code=code,
    )
    if (
        expires_at <= issued_at
        or expires_at - issued_at != timedelta(seconds=request.maximum_validity_seconds)
        or issued_at > normalized_now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or normalized_now > expires_at
    ):
        _fail(code)
    receipt_raw = _canonical(value["preflight_receipt"], code=code) + b"\n"
    _receipt, canonical_receipt, receipt_sha256 = _receipt_from_request(
        receipt_raw,
        request=request,
        code=code,
    )
    if value["preflight_receipt_sha256"] != receipt_sha256:
        _fail(code)
    key_id = _verify_signer_mapping(
        value["wa_ir_signer"],
        expected_public_key=public_key,
        expected_purpose=WA_IR_WITNESS_ATTESTATION_KEY_PURPOSE,
        code=code,
    )
    if key_id != request.wa_ir_attestation_key_id:
        _fail(code)
    signature = _signature(value["wa_ir_signature"], code=code)
    unsigned = dict(value)
    del unsigned["wa_ir_signature"]
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            _WA_IR_SIGNATURE_DOMAIN + _canonical(unsigned, code=code),
        )
    except (InvalidSignature, TypeError, ValueError):
        _fail(code)
    result = VerifiedWaIrWitnessAttestation(
        canonical_envelope=raw,
        envelope_sha256=hashlib.sha256(raw).hexdigest(),
        canonical_receipt=canonical_receipt,
        receipt_sha256=receipt_sha256,
        attestation_request_sha256=request.attestation_request_sha256,
        readonly_request_sha256=request.readonly_request_sha256,
        attestation_id=request.attestation_id,
        nonce=request.nonce,
        issued_at=issued_at,
        expires_at=expires_at,
        wa_ir_key_id=key_id,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result


def verify_wa_ir_witness_attestation_envelope(
    *,
    canonical_envelope: bytes,
    expected_request: ParsedWaIrWitnessAttestationRequest,
    expected_wa_ir_public_key: bytes,
    now: datetime,
) -> VerifiedWaIrWitnessAttestation:
    """Verify one fresh WA-IR envelope before a Witness admits it."""

    return _envelope_facts(
        canonical_envelope,
        expected_request=expected_request,
        expected_wa_ir_public_key=expected_wa_ir_public_key,
        now=now,
        code="WA_IR_WITNESS_ATTESTATION_ENVELOPE_INVALID",
    )


def build_witness_preflight_evidence(
    *,
    wa_ir_attestation: VerifiedWaIrWitnessAttestation,
    witness_signer: Ed25519PrivateKey,
    accepted_at: datetime,
) -> bytes:
    """Mint Witness-signed evidence for an already verified WA-IR envelope."""

    attestation = _validated_attestation(
        wa_ir_attestation,
        code="WITNESS_PREFLIGHT_EVIDENCE_ATTESTATION_INVALID",
    )
    signer, public_key, _key_id_value = _public_key_from_signer(
        witness_signer,
        code="WITNESS_PREFLIGHT_EVIDENCE_SIGNER_INVALID",
    )
    accepted = _timestamp(
        _render_timestamp(accepted_at, code="WITNESS_PREFLIGHT_EVIDENCE_TIME_INVALID"),
        code="WITNESS_PREFLIGHT_EVIDENCE_TIME_INVALID",
    )
    if accepted < attestation.issued_at or accepted > attestation.expires_at:
        _fail("WITNESS_PREFLIGHT_EVIDENCE_TIME_INVALID")
    envelope_value = _parse_canonical_object(
        attestation.canonical_envelope,
        maximum_bytes=MAX_WA_IR_WITNESS_ATTESTATION_BYTES,
        code="WITNESS_PREFLIGHT_EVIDENCE_ATTESTATION_INVALID",
    )
    unsigned: dict[str, Any] = {
        "schema": DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_SCHEMA,
        "version": _VERSION,
        "status": DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_STATUS,
        "purpose": _WITNESS_EVIDENCE_PURPOSE,
        "wa_ir_envelope": envelope_value,
        "wa_ir_envelope_sha256": attestation.envelope_sha256,
        "attestation_id": attestation.attestation_id,
        "nonce": attestation.nonce,
        "accepted_at": _render_timestamp(accepted, code="WITNESS_PREFLIGHT_EVIDENCE_TIME_INVALID"),
        "expires_at": _render_timestamp(attestation.expires_at, code="WITNESS_PREFLIGHT_EVIDENCE_TIME_INVALID"),
        "writer_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "witness_signer": _signer_mapping(public_key, purpose=_WITNESS_EVIDENCE_PURPOSE),
    }
    try:
        signature = signer.sign(
            _WITNESS_SIGNATURE_DOMAIN + _canonical(unsigned, code="WITNESS_PREFLIGHT_EVIDENCE_INVALID")
        )
    except (TypeError, ValueError):
        _fail("WITNESS_PREFLIGHT_EVIDENCE_SIGNER_INVALID")
    result = {
        **unsigned,
        "witness_signature": {
            "algorithm": "ed25519",
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        },
    }
    return _canonical(result, code="WITNESS_PREFLIGHT_EVIDENCE_INVALID") + b"\n"


def verify_witness_preflight_evidence(
    *,
    canonical_evidence: bytes,
    expected_request: ParsedWaIrWitnessAttestationRequest,
    expected_wa_ir_public_key: bytes,
    expected_witness_public_key: bytes,
    now: datetime,
) -> VerifiedWitnessPreflightEvidence:
    """Verify both signatures and return only the exact inner v2 receipt."""

    public_key = _public_key(
        expected_witness_public_key,
        code="WITNESS_PREFLIGHT_EVIDENCE_INVALID",
    )
    value = _parse_canonical_object(
        canonical_evidence,
        maximum_bytes=MAX_WA_IR_WITNESS_ATTESTATION_BYTES * 2,
        code="WITNESS_PREFLIGHT_EVIDENCE_INVALID",
    )
    required = {
        "schema",
        "version",
        "status",
        "purpose",
        "wa_ir_envelope",
        "wa_ir_envelope_sha256",
        "attestation_id",
        "nonce",
        "accepted_at",
        "expires_at",
        "writer_authorized",
        "promotion_authorized",
        "execution_authorized",
        "witness_signer",
        "witness_signature",
    }
    if set(value) != required or (
        value["schema"] != DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_SCHEMA
        or value["version"] != _VERSION
        or value["status"] != DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_STATUS
        or value["purpose"] != _WITNESS_EVIDENCE_PURPOSE
        or value["writer_authorized"] is not False
        or value["promotion_authorized"] is not False
        or value["execution_authorized"] is not False
    ):
        _fail("WITNESS_PREFLIGHT_EVIDENCE_INVALID")
    witness_key_id = _verify_signer_mapping(
        value["witness_signer"],
        expected_public_key=public_key,
        expected_purpose=_WITNESS_EVIDENCE_PURPOSE,
        code="WITNESS_PREFLIGHT_EVIDENCE_INVALID",
    )
    signature = _signature(value["witness_signature"], code="WITNESS_PREFLIGHT_EVIDENCE_INVALID")
    unsigned = dict(value)
    del unsigned["witness_signature"]
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            _WITNESS_SIGNATURE_DOMAIN + _canonical(unsigned, code="WITNESS_PREFLIGHT_EVIDENCE_INVALID"),
        )
    except (InvalidSignature, TypeError, ValueError):
        _fail("WITNESS_PREFLIGHT_EVIDENCE_INVALID")
    envelope_raw = _canonical(value["wa_ir_envelope"], code="WITNESS_PREFLIGHT_EVIDENCE_INVALID") + b"\n"
    if value["wa_ir_envelope_sha256"] != hashlib.sha256(envelope_raw).hexdigest():
        _fail("WITNESS_PREFLIGHT_EVIDENCE_INVALID")
    attestation = verify_wa_ir_witness_attestation_envelope(
        canonical_envelope=envelope_raw,
        expected_request=expected_request,
        expected_wa_ir_public_key=expected_wa_ir_public_key,
        now=now,
    )
    accepted_at = _timestamp(value["accepted_at"], code="WITNESS_PREFLIGHT_EVIDENCE_INVALID")
    if (
        value["attestation_id"] != attestation.attestation_id
        or value["nonce"] != attestation.nonce
        or value["expires_at"] != _render_timestamp(
            attestation.expires_at,
            code="WITNESS_PREFLIGHT_EVIDENCE_INVALID",
        )
        or accepted_at < attestation.issued_at
        or accepted_at > attestation.expires_at
    ):
        _fail("WITNESS_PREFLIGHT_EVIDENCE_INVALID")
    receipt_value = _parse_canonical_object(
        attestation.canonical_receipt,
        maximum_bytes=MAX_RECEIPT_BYTES,
        code="WITNESS_PREFLIGHT_EVIDENCE_INVALID",
    )
    result = VerifiedWitnessPreflightEvidence(
        canonical_evidence=canonical_evidence,
        evidence_sha256=hashlib.sha256(canonical_evidence).hexdigest(),
        wa_ir_attestation=attestation,
        canonical_receipt=attestation.canonical_receipt,
        receipt=receipt_value,
        witness_key_id=witness_key_id,
        accepted_at=accepted_at,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result
