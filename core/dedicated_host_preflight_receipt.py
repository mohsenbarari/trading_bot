"""Pure validation for read-only dedicated-host preflight receipts.

This module deliberately has no network, subprocess, filesystem-write, or
provider integration. A receipt records observations made elsewhere; it never
contains an executable command, transport capability, URL, credential, or
secret-shaped value.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import ipaddress
import json
import re
from typing import Any
from uuid import UUID


PREFLIGHT_RECEIPT_SCHEMA = "three-site-dedicated-host-preflight-receipt-v2"
MAX_RECEIPT_BYTES = 32 * 1024
ROLES = frozenset({"bot_fi", "webapp_fi", "webapp_ir", "witness"})
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CAMPAIGN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{7,119}$")
SIMPLE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
URL_VALUE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.)")
SENSITIVE_KEY_PARTS = frozenset(
    {
        "action",
        "authorization",
        "capability",
        "command",
        "credential",
        "endpoint",
        "exec",
        "key",
        "mutation",
        "password",
        "presign",
        "private",
        "s3",
        "script",
        "secret",
        "ssh",
        "token",
        "transport",
        "uri",
        "url",
    }
)
SENSITIVE_VALUE = re.compile(
    r"(?i)(?:bearer\s+|access[_ -]?key|authorization|credential|password|private[_ -]?key|secret|token)"
)
ALLOWED_MOUNT_OPTIONS = frozenset({"rw", "nosuid", "nodev", "noexec"})


class DedicatedHostPreflightReceiptError(ValueError):
    """Raised when a receipt is not a bounded read-only observation."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DedicatedHostPreflightReceiptError("receipt contains duplicate JSON fields")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise DedicatedHostPreflightReceiptError(f"receipt JSON constant is forbidden: {value}")


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def preflight_receipt_sha256(value: object) -> str:
    """Return a digest only after the value passes the read-only schema."""

    normalized = validate_preflight_receipt(value)
    return hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()


def parse_preflight_receipt(raw: bytes, **expected: str) -> dict[str, Any]:
    """Parse one canonical receipt payload without external effects.

    Receipts are transportable evidence, so accepting semantically equivalent
    but differently encoded JSON would make their raw bytes ambiguous.  The
    parser therefore accepts only the canonical ASCII representation followed
    by one newline.  Object-level callers may use ``validate_preflight_receipt``
    when they do not have a receipt payload yet.
    """

    if isinstance(raw, bytes):
        if len(raw) > MAX_RECEIPT_BYTES:
            raise DedicatedHostPreflightReceiptError("receipt exceeds the maximum size")
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise DedicatedHostPreflightReceiptError("receipt is not canonical ASCII JSON") from exc
    else:
        raise DedicatedHostPreflightReceiptError("receipt input must be canonical bytes")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise DedicatedHostPreflightReceiptError("receipt JSON is invalid") from exc
    if raw != canonical_json_bytes(value) + b"\n":
        raise DedicatedHostPreflightReceiptError("receipt JSON is not canonical")
    return validate_preflight_receipt(value, **expected)


def _require_mapping(value: object, *, label: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise DedicatedHostPreflightReceiptError(f"{label} fields are invalid")
    return dict(value)


def _require_string(value: object, *, label: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise DedicatedHostPreflightReceiptError(f"{label} is invalid")
    if URL_VALUE.search(value) or SENSITIVE_VALUE.search(value):
        raise DedicatedHostPreflightReceiptError(f"{label} contains a URL or secret-shaped value")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise DedicatedHostPreflightReceiptError(f"{label} has an invalid format")
    return value


def _reject_sensitive_content(value: object, *, label: str = "receipt") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DedicatedHostPreflightReceiptError(f"{label} key is not text")
            normalized = key.lower().replace("-", "_")
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                raise DedicatedHostPreflightReceiptError(f"{label} contains a forbidden capability field")
            _reject_sensitive_content(item, label=f"{label}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_sensitive_content(item, label=f"{label}[{index}]")
        return
    if isinstance(value, str) and (URL_VALUE.search(value) or SENSITIVE_VALUE.search(value)):
        raise DedicatedHostPreflightReceiptError(f"{label} contains a URL or secret-shaped value")


def _validate_campaign_id(value: object) -> str:
    return _require_string(value, label="campaign_id", pattern=CAMPAIGN_ID)


def _validate_operation_id(value: object) -> str:
    operation_id = _require_string(value, label="operation_id")
    try:
        parsed = UUID(operation_id)
    except (ValueError, AttributeError) as exc:
        raise DedicatedHostPreflightReceiptError("operation_id is not a canonical UUID") from exc
    if str(parsed) != operation_id or parsed.int == 0:
        raise DedicatedHostPreflightReceiptError("operation_id is not a canonical UUID")
    return operation_id


def _validate_sha(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    return _require_string(value, label=label, pattern=pattern)


def _validate_role(value: object, *, label: str) -> str:
    if not isinstance(value, str) or value not in ROLES:
        raise DedicatedHostPreflightReceiptError(f"{label} is not an allowed role")
    return value


def _validate_instance(value: object) -> dict[str, str]:
    instance = _require_mapping(
        value,
        label="instance",
        fields={"provider", "server_id", "public_ipv4"},
    )
    provider = _require_string(instance["provider"], label="instance.provider", pattern=SIMPLE_NAME)
    if provider != "arvan_ecc":
        raise DedicatedHostPreflightReceiptError("instance.provider is unsupported")
    server_id = _require_string(instance["server_id"], label="instance.server_id")
    try:
        UUID(server_id)
    except (ValueError, AttributeError) as exc:
        raise DedicatedHostPreflightReceiptError("instance.server_id is not a UUID") from exc
    public_ipv4 = _require_string(instance["public_ipv4"], label="instance.public_ipv4")
    try:
        address = ipaddress.ip_address(public_ipv4)
    except ValueError as exc:
        raise DedicatedHostPreflightReceiptError("instance.public_ipv4 is invalid") from exc
    if address.version != 4 or not address.is_global:
        raise DedicatedHostPreflightReceiptError("instance.public_ipv4 is not public IPv4")
    return {"provider": provider, "server_id": server_id, "public_ipv4": public_ipv4}


def _validate_release(value: object, *, release_sha: str) -> dict[str, Any]:
    release = _require_mapping(value, label="observation.release", fields={"state", "release_sha", "clean"})
    state = release["state"]
    if state not in {"present", "missing"}:
        raise DedicatedHostPreflightReceiptError("observation.release.state is invalid")
    if state == "present":
        observed_sha = _validate_sha(release["release_sha"], label="observation.release.release_sha", pattern=HEX40)
        if observed_sha != release_sha or type(release["clean"]) is not bool:
            raise DedicatedHostPreflightReceiptError("present release observation does not bind the release")
        return {"state": state, "release_sha": observed_sha, "clean": release["clean"]}
    if release["release_sha"] is not None or release["clean"] is not None:
        raise DedicatedHostPreflightReceiptError("missing release observation must not assert release state")
    return {"state": state, "release_sha": None, "clean": None}


def _validate_runtime(value: object) -> dict[str, Any]:
    runtime = _require_mapping(
        value,
        label="observation.runtime",
        fields={"docker_state", "container_count", "matrix_process_count", "current_link_present"},
    )
    if runtime["docker_state"] not in {"active", "inactive", "unavailable"}:
        raise DedicatedHostPreflightReceiptError("observation.runtime.docker_state is invalid")
    for field in ("container_count", "matrix_process_count"):
        number = runtime[field]
        if type(number) is not int or number < 0 or number > 1_000_000:
            raise DedicatedHostPreflightReceiptError(f"observation.runtime.{field} is invalid")
    if type(runtime["current_link_present"]) is not bool:
        raise DedicatedHostPreflightReceiptError("observation.runtime.current_link_present is invalid")
    return dict(runtime)


def _validate_staging_mount(value: object) -> dict[str, Any]:
    mount = _require_mapping(
        value,
        label="observation.staging_mount",
        fields={"present", "filesystem", "available_bytes", "options"},
    )
    if type(mount["present"]) is not bool:
        raise DedicatedHostPreflightReceiptError("observation.staging_mount.present is invalid")
    if mount["present"]:
        filesystem = _require_string(mount["filesystem"], label="observation.staging_mount.filesystem", pattern=SIMPLE_NAME)
        available = mount["available_bytes"]
        if type(available) is not int or available < 0:
            raise DedicatedHostPreflightReceiptError("observation.staging_mount.available_bytes is invalid")
        options = mount["options"]
        if (
            not isinstance(options, list)
            or not options
            or any(type(item) is not str or item not in ALLOWED_MOUNT_OPTIONS for item in options)
            or options != sorted(set(options))
            or "rw" not in options
        ):
            raise DedicatedHostPreflightReceiptError("observation.staging_mount.options are invalid")
        return {
            "present": True,
            "filesystem": filesystem,
            "available_bytes": available,
            "options": list(options),
        }
    if mount["filesystem"] is not None or mount["available_bytes"] is not None or mount["options"] != []:
        raise DedicatedHostPreflightReceiptError("missing staging mount must not assert mount details")
    return {"present": False, "filesystem": None, "available_bytes": None, "options": []}


def _validate_observed_at(value: object) -> str:
    observed_at = _require_string(value, label="observed_at")
    if not observed_at.endswith("Z"):
        raise DedicatedHostPreflightReceiptError("observed_at must be UTC with a Z suffix")
    try:
        parsed = datetime.fromisoformat(observed_at[:-1] + "+00:00")
    except ValueError as exc:
        raise DedicatedHostPreflightReceiptError("observed_at is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DedicatedHostPreflightReceiptError("observed_at is not timezone-aware")
    return observed_at


def validate_preflight_receipt(
    value: object,
    *,
    expected_role: str | None = None,
    expected_campaign_id: str | None = None,
    expected_operation_id: str | None = None,
    expected_instance_id: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate one standalone, read-only role observation receipt.

    Optional expected values make the caller bind the receipt to one role,
    campaign, operation, provider instance, and immutable manifest digest
    before use.  This is still only validation of an observation: it never
    makes a readiness or execution decision.
    """

    _reject_sensitive_content(value)
    receipt = _require_mapping(
        value,
        label="receipt",
        fields={
            "schema",
            "status",
            "observation_mode",
            "campaign_id",
            "operation_id",
            "release_sha",
            "role",
            "instance",
            "manifest_sha256",
            "observed_at",
            "observation",
        },
    )
    if receipt["schema"] != PREFLIGHT_RECEIPT_SCHEMA or receipt["status"] != "observed":
        raise DedicatedHostPreflightReceiptError("receipt schema or status is invalid")
    if receipt["observation_mode"] != "read-only":
        raise DedicatedHostPreflightReceiptError("receipt is not explicitly read-only")
    campaign_id = _validate_campaign_id(receipt["campaign_id"])
    operation_id = _validate_operation_id(receipt["operation_id"])
    if operation_id == campaign_id:
        raise DedicatedHostPreflightReceiptError("operation_id must differ from campaign_id")
    release_sha = _validate_sha(receipt["release_sha"], label="release_sha", pattern=HEX40)
    role = _validate_role(receipt["role"], label="role")
    instance = _validate_instance(receipt["instance"])
    manifest_sha256 = _validate_sha(receipt["manifest_sha256"], label="manifest_sha256", pattern=HEX64)
    observation = _require_mapping(
        receipt["observation"],
        label="observation",
        fields={"role_marker", "release", "runtime", "staging_mount"},
    )
    if _validate_role(observation["role_marker"], label="observation.role_marker") != role:
        raise DedicatedHostPreflightReceiptError("observation role marker does not bind the role")
    normalized = {
        "schema": PREFLIGHT_RECEIPT_SCHEMA,
        "status": "observed",
        "observation_mode": "read-only",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "role": role,
        "instance": instance,
        "manifest_sha256": manifest_sha256,
        "observed_at": _validate_observed_at(receipt["observed_at"]),
        "observation": {
            "role_marker": role,
            "release": _validate_release(observation["release"], release_sha=release_sha),
            "runtime": _validate_runtime(observation["runtime"]),
            "staging_mount": _validate_staging_mount(observation["staging_mount"]),
        },
    }
    if expected_role is not None and _validate_role(expected_role, label="expected_role") != role:
        raise DedicatedHostPreflightReceiptError("receipt role does not match the expected role")
    if expected_campaign_id is not None and _validate_campaign_id(expected_campaign_id) != campaign_id:
        raise DedicatedHostPreflightReceiptError("receipt campaign does not match the expected campaign")
    if expected_operation_id is not None and _validate_operation_id(expected_operation_id) != operation_id:
        raise DedicatedHostPreflightReceiptError("receipt operation does not match the expected operation")
    if expected_instance_id is not None:
        expected_instance = _require_string(expected_instance_id, label="expected_instance_id")
        try:
            UUID(expected_instance)
        except (ValueError, AttributeError) as exc:
            raise DedicatedHostPreflightReceiptError("expected_instance_id is not a UUID") from exc
        if expected_instance != instance["server_id"]:
            raise DedicatedHostPreflightReceiptError("receipt instance does not match the expected instance")
    if expected_manifest_sha256 is not None and _validate_sha(
        expected_manifest_sha256,
        label="expected_manifest_sha256",
        pattern=HEX64,
    ) != manifest_sha256:
        raise DedicatedHostPreflightReceiptError("receipt manifest does not match the expected manifest")
    return normalized
