"""Sync protocol and registry compatibility contract."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


SYNC_PROTOCOL_VERSION = 2
SYNC_PROTOCOL_MIN_SUPPORTED_VERSION = 1
SYNC_PAYLOAD_SCHEMA_VERSION = 2
SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION = 1
SYNC_REGISTRY_VERSION = 3
SYNC_REGISTRY_MIN_SUPPORTED_VERSION = 1


@dataclass(frozen=True)
class SyncProtocolValidationResult:
    ok: bool
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _positive_int(value: Any) -> int | None:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return None
    return coerced if coerced > 0 else None


def current_sync_registry_fingerprint() -> str:
    from core.sync_registry import sync_registry_entries

    entries = sync_registry_entries(include_planned=False)
    registry_payload = [
        {
            "table": entry.table_name,
            "policy": entry.policy.value,
            "write_surfaces": list(entry.write_surfaces),
            "authority": entry.authority,
            "conflict_rule": entry.conflict_rule,
            "side_effect_classification": entry.side_effect_classification,
            "planned": entry.planned,
        }
        for entry in sorted(entries.values(), key=lambda item: item.table_name)
    ]
    encoded = json.dumps(registry_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _default_producer_server() -> str | None:
    try:
        from core.config import settings

        return str(getattr(settings, "server_mode", "") or "").strip() or None
    except Exception:
        return None


def build_sync_protocol_metadata(*, producer_server: str | None = None) -> dict[str, Any]:
    return {
        "protocol_version": SYNC_PROTOCOL_VERSION,
        "min_consumer_protocol_version": SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
        "payload_schema_version": SYNC_PAYLOAD_SCHEMA_VERSION,
        "min_consumer_payload_schema_version": SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
        "registry_version": SYNC_REGISTRY_VERSION,
        "min_consumer_registry_version": SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
        "registry_fingerprint": current_sync_registry_fingerprint(),
        "producer": {
            "server_mode": producer_server or _default_producer_server(),
        },
    }


def validate_sync_protocol_metadata(metadata: Any) -> SyncProtocolValidationResult:
    if metadata is None:
        return SyncProtocolValidationResult(
            ok=True,
            details={"compatibility": "legacy_missing_sync_protocol"},
        )
    if not isinstance(metadata, dict):
        return SyncProtocolValidationResult(
            ok=False,
            reason="malformed_sync_protocol",
            details={"metadata_type": type(metadata).__name__},
        )

    protocol_version = _positive_int(metadata.get("protocol_version"))
    min_consumer_protocol_version = _positive_int(metadata.get("min_consumer_protocol_version")) or 1
    payload_schema_version = _positive_int(metadata.get("payload_schema_version"))
    min_consumer_payload_schema_version = _positive_int(metadata.get("min_consumer_payload_schema_version")) or 1
    registry_version = _positive_int(metadata.get("registry_version"))
    min_consumer_registry_version = _positive_int(metadata.get("min_consumer_registry_version")) or 1
    registry_fingerprint = str(metadata.get("registry_fingerprint") or "").strip()
    detail = {
        "producer_protocol_version": protocol_version,
        "producer_payload_schema_version": payload_schema_version,
        "producer_registry_version": registry_version,
    }

    if protocol_version is None or payload_schema_version is None or registry_version is None:
        return SyncProtocolValidationResult(ok=False, reason="malformed_sync_protocol", details=detail)

    if min_consumer_protocol_version > SYNC_PROTOCOL_VERSION:
        detail["required_consumer_protocol_version"] = min_consumer_protocol_version
        return SyncProtocolValidationResult(
            ok=False,
            reason="producer_requires_newer_protocol",
            details=detail,
        )

    if min_consumer_payload_schema_version > SYNC_PAYLOAD_SCHEMA_VERSION:
        detail["required_consumer_payload_schema_version"] = min_consumer_payload_schema_version
        return SyncProtocolValidationResult(
            ok=False,
            reason="producer_requires_newer_payload_schema",
            details=detail,
        )

    if min_consumer_registry_version > SYNC_REGISTRY_VERSION:
        detail["required_consumer_registry_version"] = min_consumer_registry_version
        return SyncProtocolValidationResult(
            ok=False,
            reason="producer_requires_newer_registry",
            details=detail,
        )

    if (
        protocol_version > SYNC_PROTOCOL_VERSION
        or protocol_version < SYNC_PROTOCOL_MIN_SUPPORTED_VERSION
    ):
        return SyncProtocolValidationResult(ok=False, reason="unsupported_protocol_version", details=detail)

    if (
        payload_schema_version > SYNC_PAYLOAD_SCHEMA_VERSION
        or payload_schema_version < SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION
    ):
        return SyncProtocolValidationResult(ok=False, reason="unsupported_payload_schema_version", details=detail)

    if registry_version > SYNC_REGISTRY_VERSION or registry_version < SYNC_REGISTRY_MIN_SUPPORTED_VERSION:
        return SyncProtocolValidationResult(ok=False, reason="unsupported_registry_version", details=detail)

    if protocol_version >= SYNC_PROTOCOL_VERSION and registry_version >= SYNC_REGISTRY_VERSION:
        local_fingerprint = current_sync_registry_fingerprint()
        if not registry_fingerprint:
            return SyncProtocolValidationResult(ok=False, reason="missing_registry_fingerprint", details=detail)
        if registry_fingerprint != local_fingerprint:
            detail["producer_registry_fingerprint"] = registry_fingerprint
            detail["local_registry_fingerprint"] = local_fingerprint
            return SyncProtocolValidationResult(ok=False, reason="registry_fingerprint_mismatch", details=detail)

    return SyncProtocolValidationResult(ok=True, details=detail)
