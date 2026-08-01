"""Pure orchestration contract for the disposable-host read-only preflight.

This module owns no transport implementation.  It validates a root-controlled
controller configuration, consumes only injected *observation* interfaces,
parses their bounded raw provider/receipt bytes, and binds receipts to the
existing four-host aggregate.
There is deliberately no SSH, HTTP, provider SDK, subprocess, credential, or
mutation capability here.

The real transport adapters are a separately authorised future boundary.  In
particular, the Iran role may only be recovered from separately retrieved
dual-signed Witness evidence; a direct Finland-to-Iran route is rejected
before either injected interface is called.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import re
from typing import Any, Protocol
from uuid import UUID

from core.dedicated_host_preflight_aggregate import (
    PREFLIGHT_MANIFEST_BINDING_SCHEMA,
    ROLE_ORDER,
    validate_preflight_aggregate,
    validate_validated_manifest_binding,
)
from core.dedicated_host_preflight_receipt import (
    HEX40,
    HEX64,
    MAX_RECEIPT_BYTES,
    canonical_json_bytes,
    parse_preflight_receipt,
)
from scripts.dedicated_host_preflight_manifest import (
    EXPECTED_HOSTS,
    build_readonly_requests,
    manifest_sha256,
    validate_manifest,
)


__all__ = (
    "AGENT_DELIVERY_RESPONSE_SCHEMA",
    "CONTROLLER_CONFIG_SCHEMA",
    "CONTROLLER_RESULT_SCHEMA",
    "DELIVERY_CONTRACT_BY_ROLE",
    "RETIRED_DELIVERY_CONTRACT_BY_ROLE",
    "DisabledAgentDelivery",
    "DisabledProviderReadback",
    "DedicatedHostPreflightControllerError",
    "DedicatedHostTarget",
    "AgentDelivery",
    "PROVIDER_READBACK_PATH_BY_ROLE",
    "PROVIDER_READBACK_RESPONSE_SCHEMA",
    "PROVIDER_READBACK_SCHEMA",
    "ProviderReadback",
    "RECEIPT_PATH_BY_ROLE",
    "run_preflight_controller",
    "observe_preflight_controller",
    "validate_controller_config",
)


CONTROLLER_CONFIG_SCHEMA = "three-site-dedicated-host-readonly-preflight-controller-config-v1"
CONTROLLER_RESULT_SCHEMA = "three-site-dedicated-host-readonly-preflight-controller-result-v1"
PROVIDER_READBACK_SCHEMA = "three-site-dedicated-host-provider-readback-v1"
PROVIDER_READBACK_RESPONSE_SCHEMA = (
    "three-site-dedicated-host-provider-readback-response-v1"
)
AGENT_DELIVERY_RESPONSE_SCHEMA = "three-site-dedicated-host-readonly-preflight-delivery-v1"
OBSERVATION_MODE = "read-only"
PROVIDER_NAME = "arvan_ecc"
PROVIDER_READBACK_MODE = "get-only"
RESULT_OBSERVED = "observed"
RESULT_BLOCKED = "blocked"
MAX_PROVIDER_READBACK_BYTES = 8 * 1024

# The names are evidence-contract labels, not commands, URLs, credentials, or
# a request to execute a particular transport.  A future adapter receives only
# one already-validated target and must implement the corresponding narrow
# read-only delivery mechanism.
DELIVERY_CONTRACT_BY_ROLE: Mapping[str, tuple[str, str]] = {
    "bot_fi": ("pinned-ssh-readonly-agent", "collect-readonly-receipt"),
    "webapp_fi": ("pinned-ssh-readonly-agent", "collect-readonly-receipt"),
    "webapp_ir": (
        "witness-dual-signed-preflight-evidence",
        "collect-wa-ir-witness-preflight-evidence",
    ),
    "witness": ("pinned-ssh-readonly-agent", "collect-readonly-receipt"),
}
# These are former controller contracts, retained only so a candidate config
# can be rejected with an architectural reason before any observer is called.
# They are not fallbacks and are deliberately separate from the active map.
RETIRED_DELIVERY_CONTRACT_BY_ROLE: Mapping[str, frozenset[tuple[str, str]]] = {
    "webapp_ir": frozenset(
        {
            (
                "private-versioned-object-storage-pull-agent",
                "object-storage-pull-readonly-receipt",
            )
        }
    )
}
RECEIPT_PATH_BY_ROLE: Mapping[str, str] = {
    role: f"dedicated-host-preflight/{role}/receipt.json" for role in ROLE_ORDER
}
PROVIDER_READBACK_PATH_BY_ROLE: Mapping[str, str] = {
    role: f"dedicated-host-preflight/{role}/provider-readback.json"
    for role in ROLE_ORDER
}

_SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$", re.ASCII)
_HOST_KEY_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RECEIPT_PATH = re.compile(
    r"^dedicated-host-preflight/[a-z_]{2,31}/receipt\.json$", re.ASCII
)
_PROVIDER_READBACK_PATH = re.compile(
    r"^dedicated-host-preflight/[a-z_]{2,31}/provider-readback\.json$", re.ASCII
)
_URL_VALUE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.)")
_SENSITIVE_VALUE = re.compile(
    r"(?i)(?:bearer\s+|access[_ -]?key|authorization|credential|password|private[_ -]?key|secret|token)"
)


class DedicatedHostPreflightControllerError(ValueError):
    """The controller cannot produce trusted observation evidence."""


@dataclass(frozen=True)
class DedicatedHostTarget:
    """One source-pinned, non-executable disposable-host identity."""

    role: str
    instance_id: str
    public_ipv4: str
    region: str
    host_key_sha256: str
    delivery_route: str
    delivery_phase: str


@dataclass(frozen=True)
class _ControllerConfig:
    provider: str
    targets: tuple[DedicatedHostTarget, ...]


class ProviderReadback(Protocol):
    """Read-only provider identity observer; it has no provision/update method.

    The response is a fixed provenance wrapper around bounded canonical raw
    provider bytes.  The controller checks its hash and parses those bytes;
    it never trusts a self-asserted in-memory provider mapping.
    """

    async def readback(self, *, target: DedicatedHostTarget) -> Mapping[str, Any]:
        """Return one raw-byte provider readback wrapper for ``target``."""


class AgentDelivery(Protocol):
    """Deliver one canonical request and return raw receipt bytes plus metadata.

    The protocol intentionally accepts no arbitrary command, URL, credential,
    or caller-supplied destination.  ``receipt_path`` is a fixed logical
    evidence path, not a filesystem path supplied by an operator.
    """

    async def collect_readonly_receipt(
        self,
        *,
        target: DedicatedHostTarget,
        request_bytes: bytes,
        request_sha256: str,
        receipt_path: str,
    ) -> Mapping[str, Any]:
        """Return one raw-byte delivery response for the fixed target."""


class DisabledProviderReadback:
    """Safe default used by the local CLI until a reviewed adapter exists."""

    async def readback(self, *, target: DedicatedHostTarget) -> Mapping[str, Any]:
        del target
        raise DedicatedHostPreflightControllerError(
            "provider readback transport is intentionally disabled"
        )


class DisabledAgentDelivery:
    """Safe default used by the local CLI until a reviewed adapter exists."""

    async def collect_readonly_receipt(
        self,
        *,
        target: DedicatedHostTarget,
        request_bytes: bytes,
        request_sha256: str,
        receipt_path: str,
    ) -> Mapping[str, Any]:
        del target, request_bytes, request_sha256, receipt_path
        raise DedicatedHostPreflightControllerError(
            "agent delivery transport is intentionally disabled"
        )


def _mapping(value: object, *, label: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise DedicatedHostPreflightControllerError(f"{label} fields are invalid")
    return dict(value)


def _safe_text(value: object, *, label: str, pattern: re.Pattern[str] = _SAFE_TEXT) -> str:
    if not isinstance(value, str) or not value or pattern.fullmatch(value) is None:
        raise DedicatedHostPreflightControllerError(f"{label} is invalid")
    if _URL_VALUE.search(value) or _SENSITIVE_VALUE.search(value):
        raise DedicatedHostPreflightControllerError(
            f"{label} contains a URL or secret-shaped value"
        )
    return value


def _canonical_uuid(value: object, *, label: str) -> str:
    text = _safe_text(value, label=label)
    try:
        parsed = UUID(text)
    except (TypeError, ValueError, AttributeError) as exc:
        raise DedicatedHostPreflightControllerError(f"{label} is not a UUID") from exc
    if str(parsed) != text or parsed.int == 0:
        raise DedicatedHostPreflightControllerError(f"{label} is not a canonical UUID")
    return text


def _public_ipv4(value: object, *, label: str) -> str:
    text = _safe_text(value, label=label)
    try:
        address = ipaddress.ip_address(text)
    except ValueError as exc:
        raise DedicatedHostPreflightControllerError(f"{label} is invalid") from exc
    if address.version != 4 or not address.is_global or str(address) != text:
        raise DedicatedHostPreflightControllerError(f"{label} is not public IPv4")
    return text


def _host_key_sha256(value: object, *, label: str) -> str:
    digest = _safe_text(value, label=label, pattern=_HOST_KEY_SHA256)
    if digest == "0" * 64:
        raise DedicatedHostPreflightControllerError(f"{label} is an empty fingerprint")
    return digest


def _validate_target(value: object, *, expected_role: str) -> DedicatedHostTarget:
    item = _mapping(
        value,
        label="controller host",
        fields={
            "role",
            "instance_id",
            "public_ipv4",
            "region",
            "host_key_sha256",
            "delivery_route",
            "delivery_phase",
        },
    )
    role = _safe_text(item["role"], label="controller host role")
    if role != expected_role:
        raise DedicatedHostPreflightControllerError(
            "controller hosts are missing, duplicate, or out of order"
        )
    instance_id = _canonical_uuid(item["instance_id"], label="controller instance_id")
    public_ipv4 = _public_ipv4(item["public_ipv4"], label="controller public_ipv4")
    region = _safe_text(item["region"], label="controller region")
    expected = EXPECTED_HOSTS[role]
    if (
        instance_id != expected["instance_id"]
        or public_ipv4 != expected["public_ip"]
        or region != expected["region"]
    ):
        raise DedicatedHostPreflightControllerError(
            "controller host differs from the source-pinned disposable identity"
        )
    route = _safe_text(item["delivery_route"], label="controller delivery_route")
    phase = _safe_text(item["delivery_phase"], label="controller delivery_phase")
    if (route, phase) in RETIRED_DELIVERY_CONTRACT_BY_ROLE.get(role, frozenset()):
        raise DedicatedHostPreflightControllerError(
            "legacy WA-IR Object-Storage receipt route is retired; "
            "no direct or bypass route exists"
        )
    expected_route, expected_phase = DELIVERY_CONTRACT_BY_ROLE[role]
    if role == "webapp_ir" and (
        route != expected_route or phase != expected_phase or "object" in route.lower()
    ):
        raise DedicatedHostPreflightControllerError(
            "direct Finland-to-Iran transport is forbidden; no direct or bypass route exists; "
            "webapp_ir requires dual-signed Witness evidence"
        )
    if route != expected_route or phase != expected_phase:
        raise DedicatedHostPreflightControllerError(
            "controller delivery contract differs from the source-pinned role contract"
        )
    return DedicatedHostTarget(
        role=role,
        instance_id=instance_id,
        public_ipv4=public_ipv4,
        region=region,
        host_key_sha256=_host_key_sha256(
            item["host_key_sha256"], label="controller host_key_sha256"
        ),
        delivery_route=route,
        delivery_phase=phase,
    )


def validate_controller_config(value: object) -> _ControllerConfig:
    """Validate the exact root-controlled controller config content.

    This pure function deliberately does not open a config file.  The CLI's
    separate secure-file reader enforces root-only ownership and canonical
    bytes before calling it.
    """

    config = _mapping(
        value,
        label="controller config",
        fields={"schema", "mode", "provider", "hosts"},
    )
    if config["schema"] != CONTROLLER_CONFIG_SCHEMA or config["mode"] != OBSERVATION_MODE:
        raise DedicatedHostPreflightControllerError(
            "controller config schema or observation mode is invalid"
        )
    provider = _mapping(
        config["provider"], label="controller provider", fields={"name", "readback"}
    )
    if (
        _safe_text(provider["name"], label="controller provider name") != PROVIDER_NAME
        or _safe_text(provider["readback"], label="controller provider readback")
        != PROVIDER_READBACK_MODE
    ):
        raise DedicatedHostPreflightControllerError(
            "controller provider is not the fixed read-only provider contract"
        )
    raw_hosts = config["hosts"]
    if not isinstance(raw_hosts, list) or len(raw_hosts) != len(ROLE_ORDER):
        raise DedicatedHostPreflightControllerError(
            "controller config requires exactly four ordered hosts"
        )
    targets = tuple(
        _validate_target(raw, expected_role=role)
        for role, raw in zip(ROLE_ORDER, raw_hosts, strict=True)
    )
    if len({target.host_key_sha256 for target in targets}) != len(targets):
        raise DedicatedHostPreflightControllerError(
            "controller host-key fingerprints must be distinct"
        )
    return _ControllerConfig(provider=PROVIDER_NAME, targets=targets)


def _validated_manifest_binding(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return validate_validated_manifest_binding(
        {
            "schema": PREFLIGHT_MANIFEST_BINDING_SCHEMA,
            "status": "validated",
            "campaign_id": manifest["campaign_id"],
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "manifest_sha256": manifest_sha256(manifest),
            "roles": [
                {
                    "role": host["role"],
                    "instance_id": host["instance_id"],
                    "public_ipv4": host["public_ip"],
                }
                for host in manifest["hosts"]
            ],
        }
    )


def _bind_config_to_manifest(
    config: _ControllerConfig, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    binding = _validated_manifest_binding(manifest)
    for target, host in zip(config.targets, binding["roles"], strict=True):
        if (
            target.role != host["role"]
            or target.instance_id != host["instance_id"]
            or target.public_ipv4 != host["public_ipv4"]
        ):
            raise DedicatedHostPreflightControllerError(
                "controller config does not bind the validated manifest"
            )
    return binding


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise DedicatedHostPreflightControllerError(
                "provider readback raw bytes contain duplicate JSON fields"
            )
        result[key] = item
    return result


def _reject_json_constant(value: str) -> None:
    raise DedicatedHostPreflightControllerError(
        f"provider readback raw bytes contain unsupported JSON constant: {value}"
    )


def _parse_provider_readback_bytes(raw: object) -> dict[str, Any]:
    """Parse one bounded canonical provider response without doing I/O."""

    if (
        not isinstance(raw, bytes)
        or not raw
        or len(raw) > MAX_PROVIDER_READBACK_BYTES
    ):
        raise DedicatedHostPreflightControllerError(
            "provider readback must return bounded raw response bytes"
        )
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DedicatedHostPreflightControllerError(
            "provider readback raw bytes are not canonical ASCII JSON"
        ) from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value) + b"\n":
        raise DedicatedHostPreflightControllerError(
            "provider readback raw bytes are not canonical JSON"
        )
    return value


def _validate_provider_document(
    value: object, *, target: DedicatedHostTarget
) -> dict[str, str]:
    item = _mapping(
        value,
        label="provider readback document",
        fields={
            "schema",
            "role",
            "provider",
            "instance_id",
            "public_ipv4",
            "region",
            "status",
        },
    )
    if item["schema"] != PROVIDER_READBACK_SCHEMA:
        raise DedicatedHostPreflightControllerError("provider readback schema is invalid")
    normalized = {
        "role": _safe_text(item["role"], label="provider readback role"),
        "provider": _safe_text(item["provider"], label="provider readback provider"),
        "instance_id": _canonical_uuid(
            item["instance_id"], label="provider readback instance_id"
        ),
        "public_ipv4": _public_ipv4(
            item["public_ipv4"], label="provider readback public_ipv4"
        ),
        "region": _safe_text(item["region"], label="provider readback region"),
        "status": _safe_text(item["status"], label="provider readback status"),
    }
    if (
        normalized["role"] != target.role
        or normalized["provider"] != PROVIDER_NAME
        or normalized["instance_id"] != target.instance_id
        or normalized["public_ipv4"] != target.public_ipv4
        or normalized["region"] != target.region
        or normalized["status"] != "running"
    ):
        raise DedicatedHostPreflightControllerError(
            "provider readback does not match the pinned running host"
        )
    return {"schema": PROVIDER_READBACK_SCHEMA, **normalized}


def _validate_provider_readback(
    value: object, *, target: DedicatedHostTarget
) -> dict[str, Any]:
    """Bind provider provenance and parse its exact raw canonical document."""

    item = _mapping(
        value,
        label="provider readback response",
        fields={
            "schema",
            "role",
            "provider",
            "readback_mode",
            "readback_path",
            "readback_sha256",
            "readback_bytes",
        },
    )
    if item["schema"] != PROVIDER_READBACK_RESPONSE_SCHEMA:
        raise DedicatedHostPreflightControllerError(
            "provider readback response schema is invalid"
        )
    raw_readback = item["readback_bytes"]
    if (
        not isinstance(raw_readback, bytes)
        or not raw_readback
        or len(raw_readback) > MAX_PROVIDER_READBACK_BYTES
    ):
        raise DedicatedHostPreflightControllerError(
            "provider readback must return bounded raw response bytes"
        )
    metadata = {
        "role": _safe_text(item["role"], label="provider response role"),
        "provider": _safe_text(item["provider"], label="provider response provider"),
        "readback_mode": _safe_text(
            item["readback_mode"], label="provider response readback_mode"
        ),
        "readback_path": _safe_text(
            item["readback_path"],
            label="provider response readback_path",
            pattern=_PROVIDER_READBACK_PATH,
        ),
        "readback_sha256": _safe_text(
            item["readback_sha256"],
            label="provider response readback_sha256",
            pattern=HEX64,
        ),
    }
    expected_sha256 = hashlib.sha256(raw_readback).hexdigest()
    expected_path = PROVIDER_READBACK_PATH_BY_ROLE[target.role]
    if (
        metadata["role"] != target.role
        or metadata["provider"] != PROVIDER_NAME
        or metadata["readback_mode"] != PROVIDER_READBACK_MODE
        or metadata["readback_path"] != expected_path
        or metadata["readback_sha256"] != expected_sha256
    ):
        raise DedicatedHostPreflightControllerError(
            "provider readback provenance does not bind the pinned target and raw bytes"
        )
    document = _validate_provider_document(
        _parse_provider_readback_bytes(raw_readback), target=target
    )
    # The final outcome preserves the deterministic provenance and the parsed
    # fixed identity fields, but intentionally does not re-emit raw bytes.
    return {
        "schema": PROVIDER_READBACK_RESPONSE_SCHEMA,
        **metadata,
        "readback": document,
    }


def _validate_delivery_response(
    value: object,
    *,
    target: DedicatedHostTarget,
    request_sha256: str,
    receipt_path: str,
) -> tuple[dict[str, str], bytes]:
    item = _mapping(
        value,
        label="agent delivery response",
        fields={
            "schema",
            "role",
            "delivery_route",
            "delivery_phase",
            "host_key_sha256",
            "request_sha256",
            "receipt_path",
            "receipt_sha256",
            "receipt_bytes",
        },
    )
    if item["schema"] != AGENT_DELIVERY_RESPONSE_SCHEMA:
        raise DedicatedHostPreflightControllerError("agent delivery response schema is invalid")
    raw_receipt = item["receipt_bytes"]
    if (
        not isinstance(raw_receipt, bytes)
        or not raw_receipt
        or len(raw_receipt) > MAX_RECEIPT_BYTES
    ):
        raise DedicatedHostPreflightControllerError(
            "agent delivery must return bounded raw receipt bytes"
        )
    expected_receipt_sha256 = hashlib.sha256(raw_receipt).hexdigest()
    metadata = {
        "role": _safe_text(item["role"], label="agent delivery role"),
        "delivery_route": _safe_text(
            item["delivery_route"], label="agent delivery route"
        ),
        "delivery_phase": _safe_text(
            item["delivery_phase"], label="agent delivery phase"
        ),
        "host_key_sha256": _host_key_sha256(
            item["host_key_sha256"], label="agent delivery host_key_sha256"
        ),
        "request_sha256": _safe_text(
            item["request_sha256"], label="agent delivery request_sha256", pattern=HEX64
        ),
        "receipt_path": _safe_text(
            item["receipt_path"], label="agent delivery receipt_path", pattern=_RECEIPT_PATH
        ),
        "receipt_sha256": _safe_text(
            item["receipt_sha256"], label="agent delivery receipt_sha256", pattern=HEX64
        ),
    }
    if (
        metadata["role"] != target.role
        or metadata["delivery_route"] != target.delivery_route
        or metadata["delivery_phase"] != target.delivery_phase
        or metadata["host_key_sha256"] != target.host_key_sha256
        or metadata["request_sha256"] != request_sha256
        or metadata["receipt_path"] != receipt_path
        or metadata["receipt_sha256"] != expected_receipt_sha256
    ):
        raise DedicatedHostPreflightControllerError(
            "agent delivery metadata does not bind the pinned request, path, host key, and role"
        )
    return (
        {
            "schema": AGENT_DELIVERY_RESPONSE_SCHEMA,
            **metadata,
        },
        raw_receipt,
    )


async def run_preflight_controller(
    *,
    config: object,
    manifest: object,
    provider_readback: ProviderReadback,
    agent_delivery: AgentDelivery,
) -> dict[str, Any]:
    """Collect four trusted observations through injected, read-only adapters.

    A successful return is intentionally *only* ``status=observed``.  It is
    not a readiness decision and cannot authorize any Full Matrix operation.
    """

    checked_config = validate_controller_config(config)
    try:
        checked_manifest = validate_manifest(manifest)
    except ValueError as exc:
        raise DedicatedHostPreflightControllerError(
            "preflight manifest is invalid"
        ) from exc
    binding = _bind_config_to_manifest(checked_config, checked_manifest)
    requests = build_readonly_requests(checked_manifest)
    if len(requests) != len(checked_config.targets):
        raise DedicatedHostPreflightControllerError(
            "manifest did not derive exactly four read-only requests"
        )

    provider_evidence: list[dict[str, Any]] = []
    for target in checked_config.targets:
        provider_evidence.append(
            _validate_provider_readback(
                await provider_readback.readback(target=target), target=target
            )
        )

    receipts: list[dict[str, Any]] = []
    delivery_evidence: list[dict[str, str]] = []
    for target, request in zip(checked_config.targets, requests, strict=True):
        request_bytes = canonical_json_bytes(request) + b"\n"
        request_sha256 = hashlib.sha256(request_bytes).hexdigest()
        receipt_path = RECEIPT_PATH_BY_ROLE[target.role]
        metadata, raw_receipt = _validate_delivery_response(
            await agent_delivery.collect_readonly_receipt(
                target=target,
                request_bytes=request_bytes,
                request_sha256=request_sha256,
                receipt_path=receipt_path,
            ),
            target=target,
            request_sha256=request_sha256,
            receipt_path=receipt_path,
        )
        try:
            receipt = parse_preflight_receipt(
                raw_receipt,
                expected_role=target.role,
                expected_campaign_id=binding["campaign_id"],
                expected_operation_id=binding["operation_id"],
                expected_instance_id=target.instance_id,
                expected_manifest_sha256=binding["manifest_sha256"],
            )
        except ValueError as exc:
            raise DedicatedHostPreflightControllerError(
                "agent receipt bytes do not bind the canonical request"
            ) from exc
        if receipt["release_sha"] != binding["release_sha"]:
            raise DedicatedHostPreflightControllerError(
                "agent receipt release does not bind the validated manifest"
            )
        receipts.append(receipt)
        delivery_evidence.append(metadata)

    try:
        aggregate = validate_preflight_aggregate(binding, receipts)
    except ValueError as exc:
        raise DedicatedHostPreflightControllerError(
            "receipt aggregate does not bind the validated manifest"
        ) from exc
    return {
        "schema": CONTROLLER_RESULT_SCHEMA,
        "status": RESULT_OBSERVED,
        "observation_mode": OBSERVATION_MODE,
        "campaign_id": binding["campaign_id"],
        "operation_id": binding["operation_id"],
        "release_sha": binding["release_sha"],
        "manifest_sha256": binding["manifest_sha256"],
        "provider_readbacks": provider_evidence,
        "delivery_provenance": delivery_evidence,
        "aggregate": aggregate,
    }


async def observe_preflight_controller(
    *,
    config: object,
    manifest: object,
    provider_readback: ProviderReadback,
    agent_delivery: AgentDelivery,
) -> dict[str, Any]:
    """Return a deliberately non-diagnostic, non-authorizing outcome document."""

    try:
        return await run_preflight_controller(
            config=config,
            manifest=manifest,
            provider_readback=provider_readback,
            agent_delivery=agent_delivery,
        )
    # An injected observer may fail closed for ordinary transport reasons
    # (for example an unavailable provider readback or a failed pull-agent
    # delivery).  Do not leak a partial observation or turn such a failure
    # into a readiness claim.  ``BaseException`` remains intentionally outside
    # this boundary so process-control signals preserve their normal meaning.
    except Exception:
        return {
            "schema": CONTROLLER_RESULT_SCHEMA,
            "status": RESULT_BLOCKED,
            "observation_mode": OBSERVATION_MODE,
        }
