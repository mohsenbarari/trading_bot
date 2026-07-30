#!/usr/bin/env python3
"""Pure canonical contract for one controller-to-WebApp-FI control packet.

The packet joins four existing controller/FI inputs after static adoption:
the controller-issued FI signer enrollment certificate, FI role config,
controller-signed static-assets provenance, and the nonsecret FI source
transport policy.  It contains no URL, credential, private key, Object
Storage client setting, or data-plane payload.

This module intentionally performs no filesystem, network, Object Storage,
SSH, Docker, service, or privilege action.  The controller builder and the
FI candidate reader use it only after their own root-only file boundaries have
been established.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


CONTROL_PACKET_SCHEMA = "gold-trade-webapp-fi-static-provenance-control-packet-v1"
CONTROL_PACKET_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-static-provenance-control-packet-v1\x00"
EXCHANGE_RECEIVE_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-exchange-receive-receipt-v1"
TRANSPORT_SCHEMA = "gold-trade-webapp-fi-source-transport-v1"
# The existing FI exchange config persists an endpoint URL, which its receive
# path quite deliberately refuses to retain in a decrypted control artifact.
# The packet therefore uses a URL-free projection of that policy.  The host is
# still exact and is reconstructed only in memory by a future exchange-policy
# adapter.
EXCHANGE_POLICY_SCHEMA = "gold-trade-webapp-fi-source-transport-config-v1"
SOURCE_TRANSPORT_POLICY_SCHEMA = "gold-trade-webapp-fi-static-provenance-transport-policy-v1"
SIGNER_ENROLLMENT_CERTIFICATE_SCHEMA = "gold-trade-webapp-fi-source-signer-enrollment-certificate-v2"
STATIC_ASSET_PROVENANCE_SCHEMA = "gold-trade-webapp-fi-static-asset-provenance-v1"
SOURCE_ROLE_CONFIG_SCHEMA = "gold-trade-webapp-fi-source-role-config-v3"
CAMPAIGN_BINDING_SCHEMA = "gold-trade-webapp-fi-source-campaign-binding-v1"
SIGNER_ENROLLMENT_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-source-signer-enrollment-v2\x00"
STATIC_ASSET_SIGNATURE_DOMAIN = b"gold-trade-webapp-fi-static-asset-provenance-v1\x00"

SOURCE_SITE = "controller"
DESTINATION_SITE = "webapp_fi"
OBJECT_KIND = "static-provenance"
RECIPIENT_MODE = "single"
RECEIVED_PACKET_NAME = "static-provenance.json"
EXCHANGE_RECEIPT_NAME = "receive-receipt.json"
FI_SOURCE_SIGNER_CAMPAIGN_ROOT = PurePosixPath("/etc/trading-bot-three-site/campaigns")
FI_SOURCE_SIGNER_DIRECTORY = "webapp-fi"
FI_SOURCE_SIGNER_KEY_NAME = "source-signing-ed25519.raw"

MAX_PACKET_BYTES = 20 * 1024 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_POLICY_BYTES = 1024 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_POLICY_PLAINTEXT_BYTES = 20 * 1024 * 1024 * 1024

CAMPAIGN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
GIT_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
ALEMBIC_RE = re.compile(r"^[0-9a-f]{12}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$")
VERSION_ID_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{1,1024}$")
OBJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/=-]{2,1023}$")
UTC_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class StaticProvenanceControlPacketError(RuntimeError):
    """A controller/FI static-provenance control packet is unsafe."""


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StaticProvenanceControlPacketError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise StaticProvenanceControlPacketError(f"JSON input contains unsupported constant: {value}")


def _reject_transient_url(
    payload: bytes,
    *,
    field: str,
    allow_structured_endpoint: bool = False,
) -> None:
    """Reject capability URLs while permitting the one structured endpoint input.

    A controller may read the pre-existing FI exchange config whose ``endpoint``
    is a public HTTPS origin.  The sealed packet never retains that origin as a
    URL: it carries only ``endpoint_host``.  Presigned URLs, an arbitrary URL
    key, and URL-like capability labels are never valid in either form.
    """

    lowered = payload.lower()
    if (
        b"presigned" in lowered
        or b'"url"' in lowered
        or b"x-amz-signature" in lowered
        or (not allow_structured_endpoint and b"://" in lowered)
    ):
        raise StaticProvenanceControlPacketError(f"{field} persists a forbidden transient URL")


def parse_canonical_json(
    payload: bytes,
    *,
    field: str,
    maximum_bytes: int,
    allow_structured_endpoint: bool = False,
) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= maximum_bytes:
        raise StaticProvenanceControlPacketError(f"{field} has an unsafe size")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StaticProvenanceControlPacketError(f"{field} is not strict canonical JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise StaticProvenanceControlPacketError(f"{field} is not canonical JSON")
    _reject_transient_url(
        payload,
        field=field,
        allow_structured_endpoint=allow_structured_endpoint,
    )
    return value


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise StaticProvenanceControlPacketError(f"{field} is invalid")
    return value


def _require_identifier(value: object, *, field: str, campaign: bool = False) -> str:
    pattern = CAMPAIGN_RE if campaign else IDENTIFIER_RE
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise StaticProvenanceControlPacketError(f"{field} is invalid")
    return value


def _require_timestamp(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not UTC_TIMESTAMP_RE.fullmatch(value):
        raise StaticProvenanceControlPacketError(f"{field} is invalid")
    try:
        dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise StaticProvenanceControlPacketError(f"{field} is invalid") from exc
    return value


def _require_application(value: object, *, include_tree: bool, field: str) -> dict[str, str]:
    names = {"release_sha", "expected_alembic_revision"}
    if include_tree:
        names.add("release_tree")
    if not isinstance(value, Mapping) or set(value) != names:
        raise StaticProvenanceControlPacketError(f"{field} is invalid")
    release = value.get("release_sha")
    revision = value.get("expected_alembic_revision")
    if not isinstance(release, str) or not GIT_SHA_RE.fullmatch(release):
        raise StaticProvenanceControlPacketError(f"{field}.release_sha is invalid")
    if not isinstance(revision, str) or not ALEMBIC_RE.fullmatch(revision):
        raise StaticProvenanceControlPacketError(f"{field}.expected_alembic_revision is invalid")
    result = {"release_sha": release, "expected_alembic_revision": revision}
    if include_tree:
        tree = value.get("release_tree")
        if not isinstance(tree, str) or not GIT_SHA_RE.fullmatch(tree):
            raise StaticProvenanceControlPacketError(f"{field}.release_tree is invalid")
        result["release_tree"] = tree
    return result


def _require_tooling(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"control_commit", "control_tree"}:
        raise StaticProvenanceControlPacketError(f"{field} is invalid")
    commit = value.get("control_commit")
    tree = value.get("control_tree")
    if not isinstance(commit, str) or not GIT_SHA_RE.fullmatch(commit):
        raise StaticProvenanceControlPacketError(f"{field}.control_commit is invalid")
    if not isinstance(tree, str) or not GIT_SHA_RE.fullmatch(tree):
        raise StaticProvenanceControlPacketError(f"{field}.control_tree is invalid")
    return {"control_commit": commit, "control_tree": tree}


def _decode_public_key(value: object, *, field: str) -> tuple[str, bytes]:
    if not isinstance(value, str):
        raise StaticProvenanceControlPacketError(f"{field} is invalid")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise StaticProvenanceControlPacketError(f"{field} is invalid") from exc
    if len(raw) != 32:
        raise StaticProvenanceControlPacketError(f"{field} is invalid")
    return value, raw


def public_key_id(public_key_base64: str) -> str:
    _, raw = _decode_public_key(public_key_base64, field="public key")
    return "ed25519-sha256:" + sha256_bytes(raw)


def expected_source_signing_key_path(campaign_id: str) -> str:
    """Return the only signer-key reference that can cross this control path."""

    campaign = _require_identifier(campaign_id, field="campaign ID", campaign=True)
    return str(
        FI_SOURCE_SIGNER_CAMPAIGN_ROOT
        / campaign
        / FI_SOURCE_SIGNER_DIRECTORY
        / FI_SOURCE_SIGNER_KEY_NAME
    )


def _verify_signature(
    *,
    unsigned: Mapping[str, Any],
    signature: object,
    public_key_base64: str,
    domain: bytes,
    field: str,
) -> None:
    if (
        not isinstance(signature, Mapping)
        or set(signature) != {"algorithm", "signature_base64"}
        or signature.get("algorithm") != "ed25519"
        or not isinstance(signature.get("signature_base64"), str)
    ):
        raise StaticProvenanceControlPacketError(f"{field} signature is invalid")
    try:
        raw_signature = base64.b64decode(signature["signature_base64"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise StaticProvenanceControlPacketError(f"{field} signature is invalid") from exc
    if len(raw_signature) != 64:
        raise StaticProvenanceControlPacketError(f"{field} signature is invalid")
    _, raw_public = _decode_public_key(public_key_base64, field=f"{field} public key")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise StaticProvenanceControlPacketError("cryptography Ed25519 support is unavailable") from exc
    try:
        Ed25519PublicKey.from_public_bytes(raw_public).verify(
            raw_signature,
            domain + canonical_json_bytes(unsigned),
        )
    except InvalidSignature as exc:
        raise StaticProvenanceControlPacketError(f"{field} signature verification failed") from exc


def controller_public_key_from_signer(signer: Any) -> str:
    """Derive a public key from an already-verified signer object."""

    try:
        from cryptography.hazmat.primitives import serialization

        public = signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise StaticProvenanceControlPacketError("controller signing signer is invalid") from exc
    public_base64 = base64.b64encode(public).decode("ascii")
    _decode_public_key(public_base64, field="controller signing signer public key")
    return public_base64


def _sign_with_signer(
    unsigned: Mapping[str, Any],
    signer: Any,
    *,
    controller_public_key_base64: str,
    domain: bytes,
) -> dict[str, str]:
    observed_public = controller_public_key_from_signer(signer)
    if observed_public != controller_public_key_base64:
        raise StaticProvenanceControlPacketError("controller signing signer changed while sealing packet")
    try:
        signature = signer.sign(domain + canonical_json_bytes(unsigned))
    except (AttributeError, TypeError, ValueError) as exc:
        raise StaticProvenanceControlPacketError("controller signing signer is invalid") from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise StaticProvenanceControlPacketError("controller signature has an unsafe length")
    return {"algorithm": "ed25519", "signature_base64": base64.b64encode(signature).decode("ascii")}


def _binding_identity(value: object) -> dict[str, Any]:
    expected = {
        "schema", "campaign_id", "application", "tooling", "binding_sha256", "payload_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != expected or value.get("schema") != CAMPAIGN_BINDING_SCHEMA:
        raise StaticProvenanceControlPacketError("campaign binding identity is unsupported")
    campaign_id = _require_identifier(value.get("campaign_id"), field="campaign binding campaign_id", campaign=True)
    application = _require_application(value.get("application"), include_tree=True, field="campaign binding application")
    tooling = _require_tooling(value.get("tooling"), field="campaign binding tooling")
    unsigned = {
        "schema": CAMPAIGN_BINDING_SCHEMA,
        "status": "bound",
        "campaign_id": campaign_id,
        "application": application,
        "tooling": tooling,
    }
    binding_sha256 = _require_sha256(value.get("binding_sha256"), field="campaign binding checksum")
    if binding_sha256 != sha256_bytes(canonical_json_bytes(unsigned)):
        raise StaticProvenanceControlPacketError("campaign binding checksum is invalid")
    return {
        "schema": CAMPAIGN_BINDING_SCHEMA,
        "campaign_id": campaign_id,
        "application": application,
        "tooling": tooling,
        "binding_sha256": binding_sha256,
        "payload_sha256": _require_sha256(value.get("payload_sha256"), field="campaign binding payload checksum"),
    }


def binding_identity_from_payload(payload: bytes) -> dict[str, Any]:
    """Derive the exact compact identity of a canonical root-only binding."""

    value = parse_canonical_json(payload, field="campaign binding", maximum_bytes=MAX_ARTIFACT_BYTES)
    expected = {"schema", "status", "campaign_id", "application", "tooling", "binding_sha256"}
    if set(value) != expected or value.get("schema") != CAMPAIGN_BINDING_SCHEMA or value.get("status") != "bound":
        raise StaticProvenanceControlPacketError("campaign binding is unsupported")
    identity = {
        "schema": CAMPAIGN_BINDING_SCHEMA,
        "campaign_id": value.get("campaign_id"),
        "application": value.get("application"),
        "tooling": value.get("tooling"),
        "binding_sha256": value.get("binding_sha256"),
        "payload_sha256": sha256_bytes(payload),
    }
    return _binding_identity(identity)


def _embedded_artifact(value: object, *, field: str, maximum_bytes: int) -> tuple[dict[str, Any], bytes, str]:
    if not isinstance(value, Mapping) or set(value) != {"payload", "payload_sha256"}:
        raise StaticProvenanceControlPacketError(f"{field} wrapper is invalid")
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        raise StaticProvenanceControlPacketError(f"{field} payload is invalid")
    raw = canonical_json_bytes(dict(payload)) + b"\n"
    digest = _require_sha256(value.get("payload_sha256"), field=f"{field} payload checksum")
    if len(raw) > maximum_bytes or sha256_bytes(raw) != digest:
        raise StaticProvenanceControlPacketError(f"{field} payload checksum is invalid")
    _reject_transient_url(raw, field=field)
    return dict(payload), raw, digest


def _campaign_binding_artifact(
    value: object,
    *,
    field: str,
) -> tuple[dict[str, Any], bytes, str, dict[str, Any]]:
    """Read the full canonical binding embedded in one signed packet.

    The compact binding identity is useful for request construction, but it is
    insufficient for first installation on WebApp-FI: there is intentionally
    no pre-existing local binding to compare it with.  The packet therefore
    retains the exact canonical binding bytes under its controller signature.
    """

    binding_value, binding_raw, binding_sha256 = _embedded_artifact(
        value,
        field=field,
        maximum_bytes=MAX_ARTIFACT_BYTES,
    )
    binding = binding_identity_from_payload(binding_raw)
    if binding["payload_sha256"] != binding_sha256:  # pragma: no cover - both derive from binding_raw.
        raise StaticProvenanceControlPacketError("campaign binding payload checksum is inconsistent")
    return binding_value, binding_raw, binding_sha256, binding


def _validate_role_config(
    value: Mapping[str, Any],
    *,
    binding: Mapping[str, Any],
) -> None:
    expected = {
        "schema", "campaign_id", "campaign_binding_sha256", "source_site", "destination_site",
        "application", "tooling", "application_container", "sync_worker_container",
        "source_signing_private_key_file",
    }
    if set(value) != expected or value.get("schema") != SOURCE_ROLE_CONFIG_SCHEMA:
        raise StaticProvenanceControlPacketError("FI source role config is unsupported")
    if value.get("campaign_id") != binding["campaign_id"]:
        raise StaticProvenanceControlPacketError("FI source role config campaign binding is invalid")
    if value.get("campaign_binding_sha256") != binding["binding_sha256"]:
        raise StaticProvenanceControlPacketError("FI source role config binding checksum is invalid")
    if value.get("source_site") != "webapp_fi" or value.get("destination_site") != "webapp_ir":
        raise StaticProvenanceControlPacketError("FI source role config site binding is invalid")
    if _require_application(value.get("application"), include_tree=True, field="FI source role config application") != binding["application"]:
        raise StaticProvenanceControlPacketError("FI source role config application binding is invalid")
    if _require_tooling(value.get("tooling"), field="FI source role config tooling") != binding["tooling"]:
        raise StaticProvenanceControlPacketError("FI source role config tooling binding is invalid")
    for field in ("application_container", "sync_worker_container"):
        item = value.get(field)
        if not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", item):
            raise StaticProvenanceControlPacketError(f"FI source role config {field} is invalid")
    if value["application_container"] == value["sync_worker_container"]:
        raise StaticProvenanceControlPacketError("FI source role config runtime containers must be distinct")
    signer_path = value.get("source_signing_private_key_file")
    if signer_path != expected_source_signing_key_path(binding["campaign_id"]):
        raise StaticProvenanceControlPacketError("FI source role config signing key path is not campaign-derived")


def _normalize_common_policy_fields(value: Mapping[str, Any], *, endpoint_host: str) -> dict[str, Any]:
    """Validate the common nonsecret FI policy fields in one exact form."""

    region = value.get("region")
    bucket = value.get("bucket")
    prefix = value.get("prefix")
    if not isinstance(region, str) or not re.fullmatch(r"[a-z0-9-]{2,63}", region):
        raise StaticProvenanceControlPacketError("FI source transport region is invalid")
    expected_host = f"s3.{region}.arvanstorage.ir"
    if endpoint_host != expected_host:
        raise StaticProvenanceControlPacketError("FI source transport endpoint host is invalid")
    if not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", bucket):
        raise StaticProvenanceControlPacketError("FI source transport bucket is invalid")
    if not isinstance(prefix, str) or not prefix or prefix.startswith("/") or prefix.endswith("/"):
        raise StaticProvenanceControlPacketError("FI source transport prefix is invalid")
    components = prefix.split("/")
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._=-]{0,127}", item) for item in components):
        raise StaticProvenanceControlPacketError("FI source transport prefix is invalid")
    normalized: dict[str, Any] = {
        "schema": SOURCE_TRANSPORT_POLICY_SCHEMA,
        "endpoint_host": endpoint_host,
        "region": region,
        "bucket": bucket,
        "prefix": prefix,
    }
    for field in ("age_binary", "workspace"):
        item = value.get(field)
        path = Path(item) if isinstance(item, str) else None
        if (
            path is None
            or "\x00" in item
            or not path.is_absolute()
            or any(part in {".", ".."} for part in path.parts[1:])
            or str(path) != str(PurePosixPath(path.as_posix()))
        ):
            raise StaticProvenanceControlPacketError(f"FI source transport {field} is invalid")
        normalized[field] = str(path)
    for field in ("controller_age_recipient", "webapp_fi_age_recipient", "webapp_ir_age_recipient"):
        item = value.get(field)
        if not isinstance(item, str) or not AGE_RECIPIENT_RE.fullmatch(item):
            raise StaticProvenanceControlPacketError(f"FI source transport {field} is invalid")
        normalized[field] = item
    if len({normalized["controller_age_recipient"], normalized["webapp_fi_age_recipient"], normalized["webapp_ir_age_recipient"]}) != 3:
        raise StaticProvenanceControlPacketError("FI source transport recipients must be distinct")
    maximum = value.get("maximum_plaintext_bytes")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= MAX_POLICY_PLAINTEXT_BYTES:
        raise StaticProvenanceControlPacketError("FI source transport maximum plaintext bytes is invalid")
    normalized["maximum_plaintext_bytes"] = maximum
    return normalized


def _validate_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the URL-free policy form retained in a control packet."""

    expected = {
        "schema", "endpoint_host", "region", "bucket", "prefix", "age_binary", "workspace",
        "controller_age_recipient", "webapp_fi_age_recipient", "webapp_ir_age_recipient",
        "maximum_plaintext_bytes",
    }
    if set(value) != expected or value.get("schema") != SOURCE_TRANSPORT_POLICY_SCHEMA:
        raise StaticProvenanceControlPacketError("FI source transport policy is unsupported")
    endpoint_host = value.get("endpoint_host")
    if not isinstance(endpoint_host, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,253}", endpoint_host):
        raise StaticProvenanceControlPacketError("FI source transport endpoint host is invalid")
    return _normalize_common_policy_fields(value, endpoint_host=endpoint_host)


def _project_exchange_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    """Project a pre-existing exchange config into the URL-free packet form."""

    expected = {
        "schema", "endpoint", "region", "bucket", "prefix", "age_binary", "workspace",
        "controller_age_recipient", "webapp_fi_age_recipient", "webapp_ir_age_recipient",
        "maximum_plaintext_bytes",
    }
    if set(value) != expected or value.get("schema") != EXCHANGE_POLICY_SCHEMA:
        raise StaticProvenanceControlPacketError("FI source exchange policy is unsupported")
    endpoint = value.get("endpoint")
    if not isinstance(endpoint, str) or len(endpoint) > 512 or "\x00" in endpoint:
        raise StaticProvenanceControlPacketError("FI source transport endpoint is invalid")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise StaticProvenanceControlPacketError("FI source transport endpoint is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise StaticProvenanceControlPacketError("FI source transport endpoint is invalid")
    return _normalize_common_policy_fields(value, endpoint_host=parsed.hostname)


def source_transport_policy_from_payload(payload: bytes) -> tuple[dict[str, Any], bytes, str]:
    """Parse input policy and return the URL-free bytes permitted in the packet.

    The controller accepts the existing exchange config as local input for
    compatibility, but serializes only the projected policy.  Re-reading an
    already-projected packet policy is also accepted for deterministic tests
    and offline orchestration.
    """

    value = parse_canonical_json(
        payload,
        field="FI source transport policy",
        maximum_bytes=MAX_POLICY_BYTES,
        allow_structured_endpoint=True,
    )
    if value.get("schema") == EXCHANGE_POLICY_SCHEMA:
        policy = _project_exchange_policy(value)
    elif value.get("schema") == SOURCE_TRANSPORT_POLICY_SCHEMA:
        policy = _validate_policy(value)
    else:
        raise StaticProvenanceControlPacketError("FI source transport policy is unsupported")
    raw = canonical_json_bytes(policy) + b"\n"
    return policy, raw, sha256_bytes(raw)


def _validate_certificate(
    value: Mapping[str, Any],
    *,
    binding: Mapping[str, Any],
    controller_public_key_base64: str,
) -> None:
    expected = {
        "schema", "status", "certificate_id", "operation_id", "issued_at", "not_before", "not_after",
        "campaign_id", "source_site", "destination_site", "package_id", "application", "tooling",
        "canonical_release_tree_sha256", "source_adoption_install_receipt_sha256", "delivery_envelope_sha256",
        "source_adoption_object", "fi_bootstrap_recipient", "fi_ssh_host_public_key_sha256",
        "source_signing_public_key_base64", "source_signing_key_id", "controller_public_key_base64",
        "controller_key_id", "controller_signature",
    }
    if (
        set(value) != expected
        or value.get("schema") != SIGNER_ENROLLMENT_CERTIFICATE_SCHEMA
        or value.get("status") != "issued"
    ):
        raise StaticProvenanceControlPacketError("signer enrollment certificate is unsupported")
    _require_identifier(value.get("certificate_id"), field="certificate ID")
    _require_identifier(value.get("operation_id"), field="certificate operation ID")
    _require_identifier(value.get("package_id"), field="certificate package ID")
    timestamps = {
        field: _require_timestamp(value.get(field), field=f"certificate {field}")
        for field in ("issued_at", "not_before", "not_after")
    }
    try:
        issued_at = dt.datetime.strptime(timestamps["issued_at"], "%Y-%m-%dT%H:%M:%SZ")
        not_before = dt.datetime.strptime(timestamps["not_before"], "%Y-%m-%dT%H:%M:%SZ")
        not_after = dt.datetime.strptime(timestamps["not_after"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:  # pragma: no cover - `_require_timestamp` has already checked this.
        raise StaticProvenanceControlPacketError("signer enrollment certificate lifetime is invalid") from exc
    if issued_at > not_before or not_before > not_after or (not_after - issued_at).total_seconds() > 60 * 60:
        raise StaticProvenanceControlPacketError("signer enrollment certificate lifetime is invalid")
    if (
        value.get("campaign_id") != binding["campaign_id"]
        or value.get("source_site") != "webapp_fi"
        or value.get("destination_site") != "webapp_ir"
        or _require_application(value.get("application"), include_tree=False, field="certificate application")
        != {
            "release_sha": binding["application"]["release_sha"],
            "expected_alembic_revision": binding["application"]["expected_alembic_revision"],
        }
        or _require_tooling(value.get("tooling"), field="certificate tooling") != binding["tooling"]
    ):
        raise StaticProvenanceControlPacketError("signer enrollment certificate campaign binding is invalid")
    for field in (
        "canonical_release_tree_sha256",
        "source_adoption_install_receipt_sha256",
        "delivery_envelope_sha256",
        "fi_ssh_host_public_key_sha256",
    ):
        _require_sha256(value.get(field), field=f"certificate {field}")
    object_value = value.get("source_adoption_object")
    object_fields = {
        "object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes",
    }
    if not isinstance(object_value, Mapping) or set(object_value) != object_fields:
        raise StaticProvenanceControlPacketError("signer enrollment certificate source object is invalid")
    if not isinstance(object_value.get("object_key"), str) or not OBJECT_KEY_RE.fullmatch(object_value["object_key"]):
        raise StaticProvenanceControlPacketError("signer enrollment certificate source object is invalid")
    if (
        not isinstance(object_value.get("version_id"), str)
        or object_value["version_id"].lower() == "null"
        or not VERSION_ID_RE.fullmatch(object_value["version_id"])
    ):
        raise StaticProvenanceControlPacketError("signer enrollment certificate source object is invalid")
    for field in ("ciphertext_sha256", "plaintext_sha256"):
        _require_sha256(object_value.get(field), field=f"certificate source object {field}")
    for field in ("ciphertext_bytes", "plaintext_bytes"):
        number = object_value.get(field)
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= MAX_POLICY_PLAINTEXT_BYTES:
            raise StaticProvenanceControlPacketError("signer enrollment certificate source object is invalid")
    recipient = value.get("fi_bootstrap_recipient")
    if not isinstance(recipient, str) or not AGE_RECIPIENT_RE.fullmatch(recipient):
        raise StaticProvenanceControlPacketError("signer enrollment certificate bootstrap recipient is invalid")
    source_public = value.get("source_signing_public_key_base64")
    _decode_public_key(source_public, field="certificate source signing public key")
    if value.get("source_signing_key_id") != public_key_id(source_public):
        raise StaticProvenanceControlPacketError("signer enrollment certificate source key ID is invalid")
    if (
        value.get("controller_public_key_base64") != controller_public_key_base64
        or value.get("controller_key_id") != public_key_id(controller_public_key_base64)
    ):
        raise StaticProvenanceControlPacketError("signer enrollment certificate controller key is not pinned")
    if public_key_id(source_public) == public_key_id(controller_public_key_base64):
        raise StaticProvenanceControlPacketError("signer enrollment certificate source key must be distinct from the controller key")
    _verify_signature(
        unsigned={key: item for key, item in value.items() if key != "controller_signature"},
        signature=value.get("controller_signature"),
        public_key_base64=controller_public_key_base64,
        domain=SIGNER_ENROLLMENT_SIGNATURE_DOMAIN,
        field="signer enrollment certificate",
    )


def _validate_static_provenance(
    value: Mapping[str, Any],
    *,
    binding: Mapping[str, Any],
    controller_public_key_base64: str,
) -> None:
    expected = {
        "schema", "status", "campaign_id", "application", "source_kind", "artifact", "files",
        "files_sha256", "controller_public_key_base64", "controller_signature",
    }
    if (
        set(value) != expected
        or value.get("schema") != STATIC_ASSET_PROVENANCE_SCHEMA
        or value.get("status") != "verified"
        or value.get("campaign_id") != binding["campaign_id"]
        or _require_application(value.get("application"), include_tree=False, field="static provenance application")
        != {
            "release_sha": binding["application"]["release_sha"],
            "expected_alembic_revision": binding["application"]["expected_alembic_revision"],
        }
        or value.get("source_kind") != "deterministic_2c08_dist_manifest"
        or value.get("controller_public_key_base64") != controller_public_key_base64
    ):
        raise StaticProvenanceControlPacketError("static assets provenance campaign binding is invalid")
    artifact = value.get("artifact")
    artifact_fields = {
        "object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes",
    }
    if not isinstance(artifact, Mapping) or set(artifact) != artifact_fields:
        raise StaticProvenanceControlPacketError("static assets provenance artifact is invalid")
    if not isinstance(artifact.get("object_key"), str) or not OBJECT_KEY_RE.fullmatch(artifact["object_key"]):
        raise StaticProvenanceControlPacketError("static assets provenance artifact is invalid")
    if (
        not isinstance(artifact.get("version_id"), str)
        or artifact["version_id"].lower() == "null"
        or not VERSION_ID_RE.fullmatch(artifact["version_id"])
    ):
        raise StaticProvenanceControlPacketError("static assets provenance artifact is invalid")
    for field in ("ciphertext_sha256", "plaintext_sha256"):
        _require_sha256(artifact.get(field), field=f"static assets provenance {field}")
    for field in ("ciphertext_bytes", "plaintext_bytes"):
        number = artifact.get(field)
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= MAX_POLICY_PLAINTEXT_BYTES:
            raise StaticProvenanceControlPacketError(f"static assets provenance {field} is invalid")
    raw_files = value.get("files")
    if not isinstance(raw_files, list) or len(raw_files) > 100_000:
        raise StaticProvenanceControlPacketError("static assets provenance files are invalid")
    files: list[dict[str, Any]] = []
    previous = ""
    for item in raw_files:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256", "bytes"}:
            raise StaticProvenanceControlPacketError("static assets provenance file is invalid")
        relative = item.get("path")
        if not isinstance(relative, str) or not relative or "\x00" in relative:
            raise StaticProvenanceControlPacketError("static assets provenance file is invalid")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or pure.as_posix() != relative or any(part in {".", ".."} for part in pure.parts):
            raise StaticProvenanceControlPacketError("static assets provenance file is invalid")
        if previous and relative <= previous:
            raise StaticProvenanceControlPacketError("static assets provenance files are not strictly ordered")
        previous = relative
        byte_count = item.get("bytes")
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or not 0 <= byte_count <= MAX_POLICY_PLAINTEXT_BYTES:
            raise StaticProvenanceControlPacketError("static assets provenance file is invalid")
        files.append(
            {
                "path": relative,
                "sha256": _require_sha256(item.get("sha256"), field="static assets provenance file checksum"),
                "bytes": byte_count,
            }
        )
    if value.get("files_sha256") != sha256_bytes(canonical_json_bytes(files)):
        raise StaticProvenanceControlPacketError("static assets provenance files checksum is invalid")
    _verify_signature(
        unsigned={key: item for key, item in value.items() if key != "controller_signature"},
        signature=value.get("controller_signature"),
        public_key_base64=controller_public_key_base64,
        domain=STATIC_ASSET_SIGNATURE_DOMAIN,
        field="static assets provenance",
    )


def _packet_unsigned(
    *,
    created_at: str,
    binding_payload: Mapping[str, Any],
    binding_payload_sha256: str,
    packet_id: str,
    certificate: Mapping[str, Any],
    certificate_sha256: str,
    role_config: Mapping[str, Any],
    role_config_sha256: str,
    static_provenance: Mapping[str, Any],
    static_provenance_sha256: str,
    policy: Mapping[str, Any],
    policy_sha256: str,
    controller_public_key_base64: str,
) -> dict[str, Any]:
    return {
        "schema": CONTROL_PACKET_SCHEMA,
        "status": "sealed",
        "created_at": created_at,
        "campaign_binding": {
            "payload": dict(binding_payload),
            "payload_sha256": binding_payload_sha256,
        },
        "transport": {
            "source_site": SOURCE_SITE,
            "destination_site": DESTINATION_SITE,
            "object_kind": OBJECT_KIND,
            "recipient_mode": RECIPIENT_MODE,
            "packet_id": packet_id,
            "direct_webapp_fi_to_webapp_ir_transfer": False,
        },
        "signer_enrollment_certificate": {
            "payload": dict(certificate),
            "payload_sha256": certificate_sha256,
        },
        "source_role_config": {
            "payload": dict(role_config),
            "payload_sha256": role_config_sha256,
        },
        "static_assets_provenance": {
            "payload": dict(static_provenance),
            "payload_sha256": static_provenance_sha256,
        },
        "source_transport_policy": {
            "payload": dict(policy),
            "payload_sha256": policy_sha256,
        },
        "controller_signer": {
            "public_key_base64": controller_public_key_base64,
            "key_id": public_key_id(controller_public_key_base64),
        },
    }


def build_control_packet_payload_with_signer(
    *,
    created_at: str,
    campaign_binding_payload: bytes,
    signer_enrollment_certificate_payload: bytes,
    source_role_config_payload: bytes,
    static_assets_provenance_payload: bytes,
    source_transport_policy_payload: bytes,
    packet_id: str,
    controller_signer: Any,
    controller_public_key_base64: str,
) -> bytes:
    """Seal one packet with an already-verified controller signer object."""

    created_at = _require_timestamp(created_at, field="control packet timestamp")
    packet_id = _require_identifier(packet_id, field="control packet ID")
    binding_value, binding_raw, binding_sha, binding = _campaign_binding_artifact(
        {
            "payload": parse_canonical_json(
                campaign_binding_payload,
                field="campaign binding",
                maximum_bytes=MAX_ARTIFACT_BYTES,
            ),
            "payload_sha256": sha256_bytes(campaign_binding_payload),
        },
        field="campaign binding",
    )
    certificate, certificate_raw, certificate_sha = _embedded_artifact(
        {
            "payload": parse_canonical_json(
                signer_enrollment_certificate_payload,
                field="signer enrollment certificate",
                maximum_bytes=MAX_ARTIFACT_BYTES,
            ),
            "payload_sha256": sha256_bytes(signer_enrollment_certificate_payload),
        },
        field="signer enrollment certificate",
        maximum_bytes=MAX_ARTIFACT_BYTES,
    )
    role_config, role_raw, role_sha = _embedded_artifact(
        {
            "payload": parse_canonical_json(
                source_role_config_payload,
                field="FI source role config",
                maximum_bytes=MAX_ARTIFACT_BYTES,
            ),
            "payload_sha256": sha256_bytes(source_role_config_payload),
        },
        field="FI source role config",
        maximum_bytes=MAX_ARTIFACT_BYTES,
    )
    static_provenance, static_raw, static_sha = _embedded_artifact(
        {
            "payload": parse_canonical_json(
                static_assets_provenance_payload,
                field="static assets provenance",
                maximum_bytes=MAX_ARTIFACT_BYTES,
            ),
            "payload_sha256": sha256_bytes(static_assets_provenance_payload),
        },
        field="static assets provenance",
        maximum_bytes=MAX_ARTIFACT_BYTES,
    )
    policy, policy_raw, policy_sha = source_transport_policy_from_payload(source_transport_policy_payload)
    del binding_raw, certificate_raw, role_raw, static_raw, policy_raw
    controller_public = controller_public_key_from_signer(controller_signer)
    if controller_public != controller_public_key_base64:
        raise StaticProvenanceControlPacketError("controller signing signer does not match its pinned public key")
    _validate_certificate(
        certificate,
        binding=binding,
        controller_public_key_base64=controller_public,
    )
    _validate_role_config(
        role_config,
        binding=binding,
    )
    _validate_static_provenance(
        static_provenance,
        binding=binding,
        controller_public_key_base64=controller_public,
    )
    _validate_policy(policy)
    unsigned = _packet_unsigned(
        created_at=created_at,
        binding_payload=binding_value,
        binding_payload_sha256=binding_sha,
        packet_id=packet_id,
        certificate=certificate,
        certificate_sha256=certificate_sha,
        role_config=role_config,
        role_config_sha256=role_sha,
        static_provenance=static_provenance,
        static_provenance_sha256=static_sha,
        policy=policy,
        policy_sha256=policy_sha,
        controller_public_key_base64=controller_public,
    )
    signature = _sign_with_signer(
        unsigned,
        controller_signer,
        controller_public_key_base64=controller_public,
        domain=CONTROL_PACKET_SIGNATURE_DOMAIN,
    )
    packet = {**unsigned, "controller_signature": signature}
    payload = canonical_json_bytes(packet) + b"\n"
    verify_control_packet_payload(
        payload=payload,
        pinned_controller_public_key_base64=controller_public,
        expected_campaign_binding_identity=binding,
    )
    return payload


def verify_control_packet_payload(
    *,
    payload: bytes,
    pinned_controller_public_key_base64: str,
    expected_campaign_binding_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify one sealed packet and return canonical raw material payloads."""

    value = parse_canonical_json(payload, field="static-provenance control packet", maximum_bytes=MAX_PACKET_BYTES)
    expected = {
        "schema", "status", "created_at", "campaign_binding", "transport",
        "signer_enrollment_certificate", "source_role_config", "static_assets_provenance",
        "source_transport_policy", "controller_signer", "controller_signature",
    }
    if (
        set(value) != expected
        or value.get("schema") != CONTROL_PACKET_SCHEMA
        or value.get("status") != "sealed"
    ):
        raise StaticProvenanceControlPacketError("static-provenance control packet is unsupported")
    created_at = _require_timestamp(value.get("created_at"), field="control packet timestamp")
    _binding_value, binding_raw, binding_sha, binding = _campaign_binding_artifact(
        value.get("campaign_binding"),
        field="campaign binding",
    )
    if expected_campaign_binding_identity is not None and binding != _binding_identity(expected_campaign_binding_identity):
        raise StaticProvenanceControlPacketError("control packet campaign binding identity does not match the local binding")
    transport = value.get("transport")
    if not isinstance(transport, Mapping) or set(transport) != {
        "source_site", "destination_site", "object_kind", "recipient_mode", "packet_id",
        "direct_webapp_fi_to_webapp_ir_transfer",
    }:
        raise StaticProvenanceControlPacketError("control packet transport is invalid")
    packet_id = _require_identifier(transport.get("packet_id"), field="control packet ID")
    if (
        transport.get("source_site") != SOURCE_SITE
        or transport.get("destination_site") != DESTINATION_SITE
        or transport.get("object_kind") != OBJECT_KIND
        or transport.get("recipient_mode") != RECIPIENT_MODE
        or transport.get("direct_webapp_fi_to_webapp_ir_transfer") is not False
    ):
        raise StaticProvenanceControlPacketError("control packet transport is invalid")
    signer = value.get("controller_signer")
    if not isinstance(signer, Mapping) or set(signer) != {"public_key_base64", "key_id"}:
        raise StaticProvenanceControlPacketError("control packet controller signer is invalid")
    public = signer.get("public_key_base64")
    _decode_public_key(public, field="control packet controller public key")
    if (
        public != pinned_controller_public_key_base64
        or signer.get("key_id") != public_key_id(pinned_controller_public_key_base64)
    ):
        raise StaticProvenanceControlPacketError("control packet controller signer is not pinned")
    certificate, certificate_raw, certificate_sha = _embedded_artifact(
        value.get("signer_enrollment_certificate"),
        field="signer enrollment certificate",
        maximum_bytes=MAX_ARTIFACT_BYTES,
    )
    role_config, role_raw, role_sha = _embedded_artifact(
        value.get("source_role_config"),
        field="FI source role config",
        maximum_bytes=MAX_ARTIFACT_BYTES,
    )
    static_provenance, static_raw, static_sha = _embedded_artifact(
        value.get("static_assets_provenance"),
        field="static assets provenance",
        maximum_bytes=MAX_ARTIFACT_BYTES,
    )
    policy, policy_raw, policy_sha = _embedded_artifact(
        value.get("source_transport_policy"),
        field="FI source transport policy",
        maximum_bytes=MAX_POLICY_BYTES,
    )
    _validate_certificate(certificate, binding=binding, controller_public_key_base64=public)
    _validate_role_config(
        role_config,
        binding=binding,
    )
    _validate_static_provenance(static_provenance, binding=binding, controller_public_key_base64=public)
    normalized_policy = _validate_policy(policy)
    _verify_signature(
        unsigned={key: item for key, item in value.items() if key != "controller_signature"},
        signature=value.get("controller_signature"),
        public_key_base64=public,
        domain=CONTROL_PACKET_SIGNATURE_DOMAIN,
        field="control packet",
    )
    return {
        "status": "verified",
        "packet_id": packet_id,
        "created_at": created_at,
        "campaign_binding": binding,
        "campaign_binding_payload": binding_raw,
        "campaign_binding_sha256": binding_sha,
        "controller_public_key_base64": public,
        "signer_enrollment_certificate_payload": certificate_raw,
        "signer_enrollment_certificate_sha256": certificate_sha,
        "source_role_config_payload": role_raw,
        "source_role_config_sha256": role_sha,
        "static_assets_provenance_payload": static_raw,
        "static_assets_provenance_sha256": static_sha,
        "source_transport_policy_payload": policy_raw,
        "source_transport_policy_sha256": policy_sha,
        "source_transport_policy": normalized_policy,
    }


def verify_exchange_receive_receipt(
    *,
    payload: bytes,
    control_packet_payload: bytes,
    packet: Mapping[str, Any],
    expected_object_key: str,
) -> dict[str, Any]:
    """Verify the FI exchange receipt that retained the received packet.

    The exchange already verified the exact Object VersionId during its GET.
    This pure check binds that local receipt to the signed packet bytes, fixed
    controller-to-FI route, packet ID, recipient, and deterministic object key
    before the reader creates any candidate-local files.
    """

    value = parse_canonical_json(payload, field="FI static-provenance receive receipt", maximum_bytes=MAX_RECEIPT_BYTES)
    expected = {
        "schema", "status", "request", "object", "controller_publish_receipt_sha256",
        "plaintext", "transport", "receive_receipt_sha256",
    }
    if (
        set(value) != expected
        or value.get("schema") != EXCHANGE_RECEIVE_RECEIPT_SCHEMA
        or value.get("status") != "received"
    ):
        raise StaticProvenanceControlPacketError("FI static-provenance receive receipt is unsupported")
    request = value.get("request")
    request_fields = {
        "campaign_id", "release_sha", "control_commit", "control_tree", "source_site",
        "destination_site", "object_kind", "object_id", "recipient_mode", "recipients",
    }
    _binding_value, _binding_raw, _binding_sha, binding = _campaign_binding_artifact(
        packet.get("campaign_binding"),
        field="control packet campaign binding",
    )
    policy = _validate_policy(packet.get("source_transport_policy", {}).get("payload") if isinstance(packet.get("source_transport_policy"), Mapping) else None)
    if not isinstance(request, Mapping) or set(request) != request_fields or not isinstance(request.get("recipients"), list):
        raise StaticProvenanceControlPacketError("FI static-provenance receive receipt request is invalid")
    if (
        request.get("campaign_id") != binding["campaign_id"]
        or request.get("release_sha") != binding["application"]["release_sha"]
        or request.get("control_commit") != binding["tooling"]["control_commit"]
        or request.get("control_tree") != binding["tooling"]["control_tree"]
        or request.get("source_site") != SOURCE_SITE
        or request.get("destination_site") != DESTINATION_SITE
        or request.get("object_kind") != OBJECT_KIND
        or request.get("object_id") != packet["transport"]["packet_id"]
        or request.get("recipient_mode") != RECIPIENT_MODE
        or request.get("recipients") != [policy["webapp_fi_age_recipient"]]
    ):
        raise StaticProvenanceControlPacketError("FI static-provenance receive receipt route is invalid")
    descriptor = value.get("object")
    descriptor_fields = {
        "object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256", "plaintext_bytes",
    }
    if not isinstance(descriptor, Mapping) or set(descriptor) != descriptor_fields:
        raise StaticProvenanceControlPacketError("FI static-provenance receive receipt object is invalid")
    if (
        descriptor.get("object_key") != expected_object_key
        or not isinstance(descriptor.get("version_id"), str)
        or descriptor["version_id"].lower() == "null"
        or not VERSION_ID_RE.fullmatch(descriptor["version_id"])
    ):
        raise StaticProvenanceControlPacketError("FI static-provenance receive receipt object is invalid")
    for field in ("ciphertext_sha256", "plaintext_sha256"):
        _require_sha256(descriptor.get(field), field=f"FI static-provenance receive receipt {field}")
    for field in ("ciphertext_bytes", "plaintext_bytes"):
        item = descriptor.get(field)
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise StaticProvenanceControlPacketError(f"FI static-provenance receive receipt {field} is invalid")
    plaintext = value.get("plaintext")
    if (
        not isinstance(plaintext, Mapping)
        or set(plaintext) != {"name", "sha256", "bytes"}
        or plaintext.get("name") != RECEIVED_PACKET_NAME
        or plaintext.get("sha256") != sha256_bytes(control_packet_payload)
        or plaintext.get("bytes") != len(control_packet_payload)
    ):
        raise StaticProvenanceControlPacketError("FI static-provenance receive receipt plaintext is invalid")
    if (
        descriptor.get("plaintext_sha256") != plaintext.get("sha256")
        or descriptor.get("plaintext_bytes") != plaintext.get("bytes")
        or value.get("transport") != {
            "private_bucket": True,
            "provider_side_sse": False,
            "version_bound_get": True,
        }
    ):
        raise StaticProvenanceControlPacketError("FI static-provenance receive receipt transport is invalid")
    _require_sha256(value.get("controller_publish_receipt_sha256"), field="controller publish receipt checksum")
    unsigned = {key: item for key, item in value.items() if key != "receive_receipt_sha256"}
    if value.get("receive_receipt_sha256") != sha256_bytes(canonical_json_bytes(unsigned)):
        raise StaticProvenanceControlPacketError("FI static-provenance receive receipt checksum is invalid")
    return {
        "status": "verified",
        "object": dict(descriptor),
        "controller_publish_receipt_sha256": value["controller_publish_receipt_sha256"],
    }
