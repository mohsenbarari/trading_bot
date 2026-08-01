#!/usr/bin/env python3
"""Pure validation contract for the disposable four-host preflight.

This module deliberately has no filesystem, process, network, Docker, cloud,
or Object Storage capability.  It validates only a small, source-pinned
manifest for a future read-only host preflight.  A valid manifest is not an
execution authorization and cannot select a command, credential, path, or
destination.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from typing import Any, Mapping, Sequence
from uuid import UUID


MANIFEST_SCHEMA = "dedicated-host-preflight-v1"
PREFLIGHT_MODE = "local-validation-only"
READONLY_REQUEST_SCHEMA = "three-site-dedicated-host-readonly-preflight-request-v2"
ROLE_ORDER = ("bot_fi", "webapp_fi", "webapp_ir", "witness")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
CAMPAIGN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{7,119}$", re.ASCII)

# These identities are intentionally source-owned.  A future controller must
# obtain a fresh provider readback separately; this pure validator never does.
EXPECTED_HOSTS: Mapping[str, Mapping[str, str]] = {
    "bot_fi": {
        "instance_id": "b42750eb-1efb-4595-87b6-4f61606422f1",
        "public_ip": "130.185.121.98",
        "region": "eu-west1-a",
    },
    "webapp_fi": {
        "instance_id": "fcaeba99-622f-4dfc-8116-2c44bf2ef3ce",
        "public_ip": "194.5.206.69",
        "region": "eu-west1-a",
    },
    "webapp_ir": {
        "instance_id": "1dca4b24-6aba-4d11-b430-c8c7dcce2b8a",
        "public_ip": "188.213.198.115",
        "region": "ir-thr-fr1",
    },
    "witness": {
        "instance_id": "3d883b04-0299-4894-8517-9fa7982586a9",
        "public_ip": "130.185.121.152",
        "region": "eu-west1-a",
    },
}

# The active, rollback, and retired production boundaries all remain denied.
# Keeping retired infrastructure here prevents an accidental test-host swap.
KNOWN_PRODUCTION_HOST_IPS = tuple(
    sorted(
        {
            "65.109.216.187",
            "65.109.220.59",
            "95.38.164.29",
            "37.152.191.11",
            "185.231.182.6",
            "185.206.95.94",
        }
    )
)

CAPABILITY_FIELDS = frozenset(
    {
        "remote_execution",
        "provider_mutation",
        "host_mutation",
        "docker_mutation",
        "service_mutation",
        "storage_mutation",
        "object_storage_contact",
        "network_mutation",
        "data_mutation",
    }
)
MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "mode",
        "campaign_id",
        "operation_id",
        "release_sha",
        "hosts",
        "production_boundaries",
        "known_production_boundary_sha256",
        "capabilities",
    }
)
BOUNDARY_FIELDS = frozenset({"host_ips", "instance_ids"})
HOST_FIELDS = frozenset({"role", "instance_id", "public_ip", "region"})


class DedicatedHostPreflightError(ValueError):
    """The local-only disposable-host preflight manifest is invalid."""


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    """Encode a value as canonical ASCII JSON without performing I/O."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise DedicatedHostPreflightError("manifest is not canonical JSON") from exc


def known_production_boundary_sha256() -> str:
    """Return the source-owned production host boundary digest."""

    return hashlib.sha256(
        canonical_json_bytes({"host_ips": list(KNOWN_PRODUCTION_HOST_IPS)})
    ).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DedicatedHostPreflightError("manifest JSON contains duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise DedicatedHostPreflightError(
        f"manifest JSON contains unsupported constant: {value}"
    )


def parse_manifest_payload(payload: bytes) -> dict[str, Any]:
    """Parse one canonical manifest payload without reading a file."""

    if not isinstance(payload, bytes) or not payload:
        raise DedicatedHostPreflightError("manifest payload is invalid")
    try:
        document = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DedicatedHostPreflightError("manifest payload is not strict ASCII JSON") from exc
    if not isinstance(document, dict) or payload != canonical_json_bytes(document) + b"\n":
        raise DedicatedHostPreflightError("manifest payload is not canonical JSON")
    return document


def _require_text(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or any(ord(character) < 0x20 for character in value)
    ):
        raise DedicatedHostPreflightError(f"{field} is invalid")
    return value


def _require_uuid(value: object, *, field: str) -> str:
    text = _require_text(value, field=field)
    try:
        parsed = UUID(text)
    except (TypeError, ValueError, AttributeError) as exc:
        raise DedicatedHostPreflightError(f"{field} is not a canonical UUID") from exc
    if str(parsed) != text or parsed.int == 0:
        raise DedicatedHostPreflightError(f"{field} is not a canonical UUID")
    return text


def _require_campaign_id(value: object) -> str:
    text = _require_text(value, field="campaign_id")
    if CAMPAIGN_ID_RE.fullmatch(text) is None:
        raise DedicatedHostPreflightError("campaign_id has an unsupported format")
    return text


def _require_ipv4(value: object, *, field: str) -> str:
    text = _require_text(value, field=field)
    try:
        address = ipaddress.ip_address(text)
    except ValueError as exc:
        raise DedicatedHostPreflightError(f"{field} is not an IPv4 address") from exc
    if address.version != 4 or str(address) != text:
        raise DedicatedHostPreflightError(f"{field} is not an IPv4 address")
    return text


def _require_sorted_unique_text_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise DedicatedHostPreflightError(f"{field} must be a string list")
    normalized = [_require_text(item, field=field) for item in value]
    if normalized != sorted(normalized) or len(set(normalized)) != len(normalized):
        raise DedicatedHostPreflightError(f"{field} must be sorted and unique")
    return normalized


def _validate_production_boundaries(value: object) -> dict[str, list[str]]:
    if not isinstance(value, Mapping) or set(value) != BOUNDARY_FIELDS:
        raise DedicatedHostPreflightError("production boundary fields differ")
    host_ips = _require_sorted_unique_text_list(value.get("host_ips"), field="production host_ips")
    instance_ids = _require_sorted_unique_text_list(
        value.get("instance_ids"), field="production instance_ids"
    )
    for host_ip in host_ips:
        _require_ipv4(host_ip, field="production host_ips")
    for instance_id in instance_ids:
        _require_uuid(instance_id, field="production instance_ids")
    if not set(KNOWN_PRODUCTION_HOST_IPS).issubset(host_ips):
        raise DedicatedHostPreflightError(
            "production host boundary omits a source-owned production host"
        )
    return {"host_ips": host_ips, "instance_ids": instance_ids}


def _validate_capabilities(value: object) -> dict[str, bool]:
    if not isinstance(value, Mapping) or set(value) != CAPABILITY_FIELDS:
        raise DedicatedHostPreflightError("preflight capability fields differ")
    result = {field: value[field] for field in sorted(CAPABILITY_FIELDS)}
    if any(item is not False for item in result.values()):
        raise DedicatedHostPreflightError(
            "local-only preflight manifest must deny every execution capability"
        )
    return result


def _validate_hosts(
    value: object, *, boundaries: Mapping[str, Sequence[str]]
) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != len(ROLE_ORDER):
        raise DedicatedHostPreflightError("manifest must bind exactly four hosts")
    result: list[dict[str, str]] = []
    for expected_role, item in zip(ROLE_ORDER, value, strict=True):
        if not isinstance(item, Mapping) or set(item) != HOST_FIELDS:
            raise DedicatedHostPreflightError("host binding fields differ")
        role = _require_text(item.get("role"), field="host role")
        instance_id = _require_uuid(item.get("instance_id"), field="host instance_id")
        public_ip = _require_ipv4(item.get("public_ip"), field="host public_ip")
        region = _require_text(item.get("region"), field="host region")
        if role != expected_role:
            raise DedicatedHostPreflightError("host roles must use the fixed canonical order")
        expected = EXPECTED_HOSTS[role]
        if (
            instance_id != expected["instance_id"]
            or public_ip != expected["public_ip"]
            or region != expected["region"]
        ):
            raise DedicatedHostPreflightError(
                "host binding differs from the source-owned disposable host"
            )
        if (
            public_ip in KNOWN_PRODUCTION_HOST_IPS
            or public_ip in boundaries["host_ips"]
            or instance_id in boundaries["instance_ids"]
        ):
            raise DedicatedHostPreflightError("disposable host overlaps a production boundary")
        result.append(
            {
                "role": role,
                "instance_id": instance_id,
                "public_ip": public_ip,
                "region": region,
            }
        )
    if len({item["instance_id"] for item in result}) != len(result) or len(
        {item["public_ip"] for item in result}
    ) != len(result):
        raise DedicatedHostPreflightError("disposable host identities must be distinct")
    return result


def validate_manifest(value: object) -> dict[str, Any]:
    """Validate and normalize one non-executable four-host preflight manifest."""

    if not isinstance(value, Mapping) or set(value) != MANIFEST_FIELDS:
        raise DedicatedHostPreflightError("preflight manifest fields differ")
    if value.get("schema") != MANIFEST_SCHEMA or value.get("mode") != PREFLIGHT_MODE:
        raise DedicatedHostPreflightError("preflight manifest schema or mode differs")
    campaign_id = _require_campaign_id(value.get("campaign_id"))
    operation_id = _require_uuid(value.get("operation_id"), field="operation_id")
    if operation_id == campaign_id:
        raise DedicatedHostPreflightError("operation_id must differ from campaign_id")
    release_sha = _require_text(value.get("release_sha"), field="release_sha")
    if SHA40_RE.fullmatch(release_sha) is None:
        raise DedicatedHostPreflightError("release_sha is not an exact Git SHA")
    if value.get("known_production_boundary_sha256") != known_production_boundary_sha256():
        raise DedicatedHostPreflightError("source-owned production boundary digest differs")
    boundaries = _validate_production_boundaries(value.get("production_boundaries"))
    hosts = _validate_hosts(value.get("hosts"), boundaries=boundaries)
    capabilities = _validate_capabilities(value.get("capabilities"))
    return {
        "schema": MANIFEST_SCHEMA,
        "mode": PREFLIGHT_MODE,
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "hosts": hosts,
        "production_boundaries": boundaries,
        "known_production_boundary_sha256": known_production_boundary_sha256(),
        "capabilities": capabilities,
    }


def manifest_sha256(value: object) -> str:
    """Return the canonical digest after every fixed binding has been checked."""

    return hashlib.sha256(canonical_json_bytes(validate_manifest(value))).hexdigest()


def build_readonly_requests(value: object) -> list[dict[str, str]]:
    """Project one validated manifest into exactly four non-executable requests.

    This data-only bridge fixes the role order to the source-owned manifest;
    it cannot select a host, transport, command, path, or credential.
    """

    manifest = validate_manifest(value)
    digest = manifest_sha256(manifest)
    return [
        {
            "schema": READONLY_REQUEST_SCHEMA,
            "campaign_id": manifest["campaign_id"],
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "role": host["role"],
            "manifest_sha256": digest,
        }
        for host in manifest["hosts"]
    ]
