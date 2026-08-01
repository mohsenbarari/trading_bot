"""Pure aggregation for four read-only dedicated-host preflight receipts.

The caller supplies a compact binding projection only after a separate
manifest verifier has accepted the real manifest. This module never loads the
real manifest, performs I/O, or exposes an execution, transport, credential,
or provider capability. It only verifies that every role observation binds to
the supplied reviewed identity.  Its aggregate is explicitly observation-only
and deliberately carries no readiness or execution decision.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import ipaddress
import re
from typing import Any
from uuid import UUID

from core.dedicated_host_preflight_receipt import (
    CAMPAIGN_ID,
    HEX40,
    HEX64,
    ROLES,
    DedicatedHostPreflightReceiptError,
    canonical_json_bytes,
    validate_preflight_receipt,
)


PREFLIGHT_MANIFEST_BINDING_SCHEMA = "three-site-dedicated-host-preflight-manifest-binding-v2"
PREFLIGHT_AGGREGATE_SCHEMA = "three-site-dedicated-host-preflight-aggregate-v2"
ROLE_ORDER = ("bot_fi", "webapp_fi", "webapp_ir", "witness")
OBSERVATION_AGGREGATE_STATUS = "observations-aggregated"
READINESS_DECISION_NOT_EVALUATED = "not-evaluated"
URL_VALUE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.)")
SENSITIVE_VALUE = re.compile(
    r"(?i)(?:bearer\s+|access[_ -]?key|authorization|credential|password|private[_ -]?key|secret|token)"
)


class DedicatedHostPreflightAggregateError(ValueError):
    """Raised when the four receipts do not bind one validated manifest."""


def _require_mapping(value: object, *, label: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise DedicatedHostPreflightAggregateError(f"{label} fields are invalid")
    return dict(value)


def _require_safe_string(value: object, *, label: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise DedicatedHostPreflightAggregateError(f"{label} is invalid")
    if URL_VALUE.search(value) or SENSITIVE_VALUE.search(value):
        raise DedicatedHostPreflightAggregateError(f"{label} contains a URL or secret-shaped value")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise DedicatedHostPreflightAggregateError(f"{label} has an invalid format")
    return value


def _validate_public_ipv4(value: object, *, label: str) -> str:
    public_ipv4 = _require_safe_string(value, label=label)
    try:
        address = ipaddress.ip_address(public_ipv4)
    except ValueError as exc:
        raise DedicatedHostPreflightAggregateError(f"{label} is invalid") from exc
    if address.version != 4 or not address.is_global:
        raise DedicatedHostPreflightAggregateError(f"{label} is not public IPv4")
    return public_ipv4


def _validate_instance_id(value: object, *, label: str) -> str:
    instance_id = _require_safe_string(value, label=label)
    try:
        UUID(instance_id)
    except (ValueError, AttributeError) as exc:
        raise DedicatedHostPreflightAggregateError(f"{label} is not a UUID") from exc
    return instance_id


def _validate_operation_id(value: object, *, label: str) -> str:
    operation_id = _require_safe_string(value, label=label)
    try:
        parsed = UUID(operation_id)
    except (ValueError, AttributeError) as exc:
        raise DedicatedHostPreflightAggregateError(f"{label} is not a canonical UUID") from exc
    if str(parsed) != operation_id or parsed.int == 0:
        raise DedicatedHostPreflightAggregateError(f"{label} is not a canonical UUID")
    return operation_id


def validate_validated_manifest_binding(value: object) -> dict[str, Any]:
    """Validate only the safe identity projection of an already verified manifest."""

    manifest = _require_mapping(
        value,
        label="validated manifest binding",
        fields={
            "schema",
            "status",
            "campaign_id",
            "operation_id",
            "release_sha",
            "manifest_sha256",
            "roles",
        },
    )
    if (
        manifest["schema"] != PREFLIGHT_MANIFEST_BINDING_SCHEMA
        or manifest["status"] != "validated"
    ):
        raise DedicatedHostPreflightAggregateError("validated manifest binding schema or status is invalid")
    campaign_id = _require_safe_string(manifest["campaign_id"], label="campaign_id", pattern=CAMPAIGN_ID)
    operation_id = _validate_operation_id(manifest["operation_id"], label="operation_id")
    if operation_id == campaign_id:
        raise DedicatedHostPreflightAggregateError("operation_id must differ from campaign_id")
    release_sha = _require_safe_string(manifest["release_sha"], label="release_sha", pattern=HEX40)
    manifest_sha256 = _require_safe_string(
        manifest["manifest_sha256"],
        label="manifest_sha256",
        pattern=HEX64,
    )
    raw_roles = manifest["roles"]
    if not isinstance(raw_roles, list) or len(raw_roles) != len(ROLE_ORDER):
        raise DedicatedHostPreflightAggregateError("validated manifest roles must contain exactly four entries")
    normalized_roles: list[dict[str, str]] = []
    seen_instance_ids: set[str] = set()
    seen_public_ipv4: set[str] = set()
    for expected_role, raw_role in zip(ROLE_ORDER, raw_roles, strict=True):
        role = _require_mapping(
            raw_role,
            label="validated manifest role",
            fields={"role", "instance_id", "public_ipv4"},
        )
        if (
            not isinstance(role["role"], str)
            or role["role"] not in ROLES
            or role["role"] != expected_role
        ):
            raise DedicatedHostPreflightAggregateError("validated manifest roles are missing, duplicate, or out of order")
        instance_id = _validate_instance_id(role["instance_id"], label="instance_id")
        public_ipv4 = _validate_public_ipv4(role["public_ipv4"], label="public_ipv4")
        if instance_id in seen_instance_ids or public_ipv4 in seen_public_ipv4:
            raise DedicatedHostPreflightAggregateError("validated manifest instances must be distinct")
        seen_instance_ids.add(instance_id)
        seen_public_ipv4.add(public_ipv4)
        normalized_roles.append(
            {"role": expected_role, "instance_id": instance_id, "public_ipv4": public_ipv4}
        )
    return {
        "schema": PREFLIGHT_MANIFEST_BINDING_SCHEMA,
        "status": "validated",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "manifest_sha256": manifest_sha256,
        "roles": normalized_roles,
    }


def validate_preflight_aggregate(
    validated_manifest: object,
    receipts: object,
) -> dict[str, Any]:
    """Bind exactly one ordered read-only receipt to every projected role.

    The returned ``decision`` is always ``not-evaluated``.  This pure module
    has neither enough policy nor any authenticated transport evidence to
    declare a host ready for a later action.
    """

    manifest = validate_validated_manifest_binding(validated_manifest)
    if not isinstance(receipts, (list, tuple)) or len(receipts) != len(ROLE_ORDER):
        raise DedicatedHostPreflightAggregateError("aggregate requires exactly four ordered receipts")
    normalized_receipts: list[dict[str, Any]] = []
    for role_binding, raw_receipt in zip(manifest["roles"], receipts, strict=True):
        try:
            receipt = validate_preflight_receipt(
                raw_receipt,
                expected_role=role_binding["role"],
                expected_campaign_id=manifest["campaign_id"],
                expected_operation_id=manifest["operation_id"],
                expected_instance_id=role_binding["instance_id"],
                expected_manifest_sha256=manifest["manifest_sha256"],
            )
        except DedicatedHostPreflightReceiptError as exc:
            raise DedicatedHostPreflightAggregateError("receipt does not bind the validated manifest") from exc
        if receipt["release_sha"] != manifest["release_sha"]:
            raise DedicatedHostPreflightAggregateError("receipt release does not match the validated manifest")
        if receipt["instance"]["public_ipv4"] != role_binding["public_ipv4"]:
            raise DedicatedHostPreflightAggregateError("receipt public IPv4 does not match the validated manifest")
        normalized_receipts.append(receipt)
    return {
        "schema": PREFLIGHT_AGGREGATE_SCHEMA,
        "status": OBSERVATION_AGGREGATE_STATUS,
        "decision": READINESS_DECISION_NOT_EVALUATED,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "manifest_sha256": manifest["manifest_sha256"],
        "receipts": normalized_receipts,
    }


def preflight_aggregate_sha256(validated_manifest: object, receipts: object) -> str:
    """Return a deterministic local digest after all four bindings validate."""

    aggregate = validate_preflight_aggregate(validated_manifest, receipts)
    return hashlib.sha256(canonical_json_bytes(aggregate)).hexdigest()
