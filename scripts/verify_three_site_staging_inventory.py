#!/usr/bin/env python3
"""Fail-closed isolation gate for the authoritative three-site Full Matrix."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.human_approval import (
    approval_policy_hash,
    approval_subject,
    verify_human_approval,
)
from core.three_site_execution_safety import (
    DEDICATED_HOST_DESTRUCTIVE,
    EXECUTION_CLASSES as HOST_SAFETY_MODES,
    SHARED_HOST_SAFE,
)


PRODUCTION_IPS = frozenset(
    {"65.109.216.187", "65.109.220.59", "95.38.164.29", "185.206.95.94", "185.231.182.6"}
)
PRODUCTION_DOMAINS = frozenset({"gold-trade.ir", "coin.gold-trade.ir"})
PRODUCTION_BUCKETS = frozenset({"production-sync-coin"})
ROLES = frozenset({"bot_fi", "webapp_fi", "webapp_ir", "witness"})
ROLE_COMPOSE_PROJECT = {
    "bot_fi": "trading-bot-three-site-staging-bot-fi",
    "webapp_fi": "trading-bot-three-site-staging-webapp-fi",
    "webapp_ir": "trading-bot-three-site-staging-webapp-ir",
    "witness": "trading-bot-three-site-staging-witness",
}
ROLE_VOLUME_LOGICAL_NAMES = {
    "bot_fi": {
        "postgres_volume_id": "bot_fi_postgres",
        "redis_volume_id": "bot_fi_redis",
        "uploads_volume_id": "bot_fi_uploads",
        "audit_root_id": "bot_fi_audit",
    },
    "webapp_fi": {
        "postgres_volume_id": "webapp_fi_postgres",
        "redis_volume_id": "webapp_fi_redis",
        "uploads_volume_id": "webapp_fi_uploads",
        "audit_root_id": "webapp_fi_audit",
    },
    "webapp_ir": {
        "postgres_volume_id": "webapp_ir_postgres",
        "redis_volume_id": "webapp_ir_redis",
        "uploads_volume_id": "webapp_ir_uploads",
        "audit_root_id": "webapp_ir_audit",
    },
    "witness": {
        "postgres_volume_id": "witness_postgres",
        "audit_root_id": "witness_audit",
    },
}
STAGING_DATA_ROOT = "/srv/trading-bot-three-site-staging-data"
SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)


class InventoryError(RuntimeError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _strict_object(pairs):  # noqa: ANN001
    result = {}
    for key, value in pairs:
        if key in result:
            raise InventoryError(f"duplicate inventory key: {key}")
        result[key] = value
    return result


def load_inventory(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)
    except (OSError, json.JSONDecodeError, InventoryError) as exc:
        raise InventoryError("inventory is unreadable or not strict JSON") from exc
    if not isinstance(payload, dict):
        raise InventoryError("inventory root must be an object")
    return payload


def inventory_host_destructive(payload: dict[str, Any]) -> bool:
    mode = payload.get("host_safety_mode")
    if mode not in HOST_SAFETY_MODES:
        raise InventoryError("inventory host_safety_mode is invalid")
    return mode == DEDICATED_HOST_DESTRUCTIVE


def verify_inventory(
    payload: dict[str, Any], *, host_destructive: bool | None = None
) -> dict[str, Any]:
    required = {
        "schema", "inventory_stage", "host_safety_mode", "campaign_id", "release_sha", "canonical_domain", "optional_ingress",
        "deployment_id", "object_storage", "roles", "credential_scope",
        "production_boundaries",
    }
    if set(payload) != required or payload["schema"] != "three-site-staging-inventory-v3":
        raise InventoryError("inventory fields/schema are invalid")
    def contains_placeholder(value: Any) -> bool:
        if isinstance(value, str):
            normalized = value.strip().lower()
            return "replace" in normalized or normalized in {
                "00000000-0000-4000-8000-000000000000",
                "0" * 40,
                "0" * 64,
            }
        if isinstance(value, list):
            return any(contains_placeholder(item) for item in value)
        if isinstance(value, dict):
            return any(contains_placeholder(item) for item in value.values())
        return False

    if contains_placeholder(payload):
        raise InventoryError("inventory still contains template placeholders")
    effective_host_destructive = inventory_host_destructive(payload)
    if (
        host_destructive is not None
        and host_destructive != effective_host_destructive
    ):
        raise InventoryError("inventory host safety mode differs from the required execution class")
    inventory_stage = payload["inventory_stage"]
    if inventory_stage not in {"planned", "provisioned"}:
        raise InventoryError("inventory_stage must be planned or provisioned")
    try:
        UUID(str(payload["campaign_id"]))
    except ValueError as exc:
        raise InventoryError("campaign_id must be a UUID") from exc
    release_sha = str(payload["release_sha"]).lower()
    if not SHA_RE.fullmatch(release_sha):
        raise InventoryError("release_sha must be one exact Git SHA")
    if payload["canonical_domain"] != "staging.gold-trade.ir":
        raise InventoryError("canonical staging domain is not staging.gold-trade.ir")
    if payload["optional_ingress"] not in {None, "app.gold-trading.ir"}:
        raise InventoryError("optional ingress is outside the isolated Arvan test root")
    if payload["credential_scope"] != "staging-only":
        raise InventoryError("all credentials must be explicitly staging-only")
    boundaries = payload["production_boundaries"]
    boundary_fields = {
        "host_ips", "machine_ids", "docker_daemon_ids", "postgres_system_ids",
        "volume_ids", "audit_root_ids", "storage_mount_uuids", "domains", "buckets",
    }
    if not isinstance(boundaries, dict) or set(boundaries) != boundary_fields:
        raise InventoryError("production boundary inventory is incomplete")
    normalized_boundaries: dict[str, set[str]] = {}
    for field in boundary_fields:
        values = boundaries[field]
        if not isinstance(values, list) or not values or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise InventoryError(f"production boundary {field} must be a non-empty string list")
        normalized = {value.strip().lower() for value in values}
        if len(normalized) != len(values):
            raise InventoryError(f"production boundary {field} contains duplicates")
        normalized_boundaries[field] = normalized
    ingress_domains = {
        str(payload["canonical_domain"]).strip().lower(),
        *(
            [str(payload["optional_ingress"]).strip().lower()]
            if payload["optional_ingress"] is not None
            else []
        ),
    }
    if ingress_domains & normalized_boundaries["domains"]:
        raise InventoryError("staging ingress overlaps a declared production domain")
    object_storage = payload["object_storage"]
    if not isinstance(object_storage, dict) or set(object_storage) != {
        "bucket", "prefix", "credential_id", "versioning", "private"
    }:
        raise InventoryError("object-storage inventory is incomplete")
    if (
        object_storage["bucket"] in PRODUCTION_BUCKETS
        or str(object_storage["bucket"]).lower() in normalized_boundaries["buckets"]
    ):
        raise InventoryError("production Object Storage bucket is forbidden")
    if not str(object_storage["prefix"]).startswith("staging/"):
        raise InventoryError("Object Storage prefix must be staging-owned")
    if object_storage["versioning"] is not True or object_storage["private"] is not True:
        raise InventoryError("staging Object Storage must be private and versioned")

    roles = payload["roles"]
    if not isinstance(roles, list) or len(roles) != len(ROLES):
        raise InventoryError("exactly four role records are required")
    expected_fields = {
        "role", "physical_site", "host_ip", "machine_id", "docker_daemon_id",
        "postgres_system_id", "postgres_volume_id", "redis_volume_id",
        "uploads_volume_id", "audit_root_id", "storage_root",
        "storage_mount_uuid", "resource_limits", "release_sha", "deployment_id",
    }
    by_role: dict[str, dict[str, Any]] = {}
    for role in roles:
        if not isinstance(role, dict) or set(role) != expected_fields:
            raise InventoryError("role inventory fields are invalid")
        name = str(role["role"])
        if name not in ROLES or name in by_role or role["physical_site"] != name:
            raise InventoryError("role/physical identity is unknown or duplicate")
        if not re.fullmatch(r"[0-9a-f]{32}", str(role["machine_id"]).lower()):
            raise InventoryError(f"{name} machine_id is not an exact Linux machine id")
        try:
            ip_value = ipaddress.ip_address(str(role["host_ip"]))
            ip = str(ip_value)
        except ValueError as exc:
            raise InventoryError(f"invalid host IP for {name}") from exc
        if any(ip_value in network for network in DOCUMENTATION_NETWORKS):
            raise InventoryError(f"documentation-only host IP is forbidden for staging role {name}")
        if effective_host_destructive and ip in PRODUCTION_IPS:
            raise InventoryError(f"production host is forbidden for staging role {name}")
        if effective_host_destructive and ip.lower() in normalized_boundaries["host_ips"]:
            raise InventoryError(f"declared production host is forbidden for staging role {name}")
        if str(role["release_sha"]).lower() != release_sha:
            raise InventoryError("mixed release SHA detected")
        if role["deployment_id"] != payload["deployment_id"]:
            raise InventoryError("mixed deployment identity detected")
        if role["storage_root"] != STAGING_DATA_ROOT:
            raise InventoryError(f"{name} storage root is outside the fixed staging boundary")
        try:
            storage_mount_uuid = str(UUID(str(role["storage_mount_uuid"]))).lower()
        except ValueError as exc:
            raise InventoryError(f"{name} storage mount UUID is invalid") from exc
        if storage_mount_uuid in normalized_boundaries["storage_mount_uuids"]:
            raise InventoryError(f"{name} staging storage overlaps production storage")
        limits = role["resource_limits"]
        if not isinstance(limits, dict) or set(limits) != {
            "cpu_quota_percent", "memory_high_bytes", "memory_max_bytes",
            "tasks_max",
        }:
            raise InventoryError(f"{name} aggregate resource limits are incomplete")
        if (
            isinstance(limits["cpu_quota_percent"], bool)
            or not isinstance(limits["cpu_quota_percent"], int)
            or not 10 <= limits["cpu_quota_percent"] <= 800
            or isinstance(limits["memory_high_bytes"], bool)
            or not isinstance(limits["memory_high_bytes"], int)
            or isinstance(limits["memory_max_bytes"], bool)
            or not isinstance(limits["memory_max_bytes"], int)
            or not 256 * 1024**2 <= limits["memory_high_bytes"] < limits["memory_max_bytes"]
            or isinstance(limits["tasks_max"], bool)
            or not isinstance(limits["tasks_max"], int)
            or not 128 <= limits["tasks_max"] <= 8192
        ):
            raise InventoryError(f"{name} aggregate resource limits are unsafe")
        for field in ("machine_id", "docker_daemon_id", "postgres_volume_id", "audit_root_id"):
            if not str(role[field] or "").strip():
                raise InventoryError(f"{name} lacks {field}")
        if inventory_stage == "planned":
            if role["postgres_system_id"] is not None:
                raise InventoryError("planned inventory cannot predict PostgreSQL system identifiers")
        elif not str(role["postgres_system_id"] or "").strip():
            raise InventoryError(f"{name} lacks postgres_system_id")
        elif not re.fullmatch(r"[0-9]{10,20}", str(role["postgres_system_id"])):
            raise InventoryError(f"{name} postgres_system_id is malformed")
        for field, logical_name in ROLE_VOLUME_LOGICAL_NAMES[name].items():
            expected_volume = f"{ROLE_COMPOSE_PROJECT[name]}_{logical_name}"
            if role[field] != expected_volume:
                raise InventoryError(
                    f"{name} {field} differs from deterministic role Compose volume"
                )
        boundary_map = {
            "postgres_system_id": "postgres_system_ids",
            "postgres_volume_id": "volume_ids",
            "audit_root_id": "audit_root_ids",
        }
        if effective_host_destructive:
            boundary_map.update(
                {
                    "machine_id": "machine_ids",
                    "docker_daemon_id": "docker_daemon_ids",
                }
            )
        for role_field, boundary_field in boundary_map.items():
            if role[role_field] is None:
                continue
            if str(role[role_field]).strip().lower() in normalized_boundaries[boundary_field]:
                raise InventoryError(
                    f"staging role {name} overlaps production boundary {role_field}"
                )
        if name != "witness":
            for field in ("redis_volume_id", "uploads_volume_id"):
                if not str(role[field] or "").strip():
                    raise InventoryError(f"{name} lacks {field}")
                if str(role[field]).strip().lower() in normalized_boundaries["volume_ids"]:
                    raise InventoryError(f"staging role {name} overlaps production boundary {field}")
        elif role["redis_volume_id"] is not None or role["uploads_volume_id"] is not None:
            raise InventoryError("Witness must not own Redis/uploads")
        by_role[name] = role
    if set(by_role) != ROLES:
        raise InventoryError("role set does not match fixed topology")

    distinct_fields = ("postgres_volume_id", "audit_root_id")
    if inventory_stage == "provisioned":
        distinct_fields = ("postgres_system_id", *distinct_fields)
    for field in distinct_fields:
        values = [str(role[field]) for role in roles]
        if len(set(values)) != len(values):
            raise InventoryError(f"mutable staging boundary is shared: {field}")
    for field in ("redis_volume_id", "uploads_volume_id"):
        values = [str(role[field]) for role in roles if role[field] is not None]
        if len(set(values)) != len(values):
            raise InventoryError(f"mutable staging boundary is shared: {field}")
    storage_mount_uuids = [str(role["storage_mount_uuid"]).lower() for role in roles]
    if len(set(storage_mount_uuids)) != len(storage_mount_uuids):
        raise InventoryError("mutable staging storage mount is shared between roles")
    if effective_host_destructive:
        for field in ("host_ip", "machine_id", "docker_daemon_id"):
            values = [str(role[field]) for role in roles]
            if len(set(values)) != len(values):
                raise InventoryError(f"host-destructive matrix requires distinct {field}")
    return {
        "status": "approved",
        "inventory_stage": inventory_stage,
        "campaign_id": str(payload["campaign_id"]),
        "release_sha": release_sha,
        "deployment_id": payload["deployment_id"],
        "host_safety_mode": payload["host_safety_mode"],
        "host_destructive": effective_host_destructive,
        "role_count": len(roles),
    }


def _utc(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise InventoryError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise InventoryError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def verify_approved_inventory(
    payload: dict[str, Any],
    *,
    approval: dict[str, Any],
    approval_policy: dict[str, Any],
    host_destructive: bool | None = None,
    now: datetime | None = None,
    require_fresh_approval: bool = True,
) -> dict[str, Any]:
    """Require password+TOTP approval of the exact inventory bytes."""

    structural = verify_inventory(payload, host_destructive=host_destructive)
    inventory_hash = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    subject = approval_subject(
        artifact_type="three-site-staging-inventory-v3",
        artifact_sha256=inventory_hash,
        release_sha=structural["release_sha"],
        bindings={
            "campaign_id": structural["campaign_id"],
            "deployment_id": structural["deployment_id"],
            "host_safety_mode": structural["host_safety_mode"],
            "inventory_stage": structural["inventory_stage"],
        },
    )
    try:
        verified = verify_human_approval(
            approval,
            policy_payload=approval_policy,
            expected_action="approve_inventory",
            expected_environment="staging",
            expected_subject=subject,
            now=now,
            require_fresh=require_fresh_approval,
        )
    except Exception as exc:
        raise InventoryError("inventory human approval is invalid") from exc
    return {
        **structural,
        "inventory_sha256": inventory_hash,
        "approval_policy_sha256": approval_policy_hash(approval_policy),
        "approval_token_sha256": verified.token_hash,
        "approved_by": [verified.operator],
        "approval_id": verified.approval_id,
        "approval_expires_at": verified.expires_at.isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    safety = parser.add_mutually_exclusive_group()
    safety.add_argument("--host-destructive", action="store_true")
    safety.add_argument("--shared-host-safe", action="store_true")
    args = parser.parse_args(argv)
    try:
        expected_host_destructive = (
            True if args.host_destructive else False if args.shared_host_safe else None
        )
        result = verify_approved_inventory(
            load_inventory(args.inventory),
            approval=load_inventory(args.approval),
            approval_policy=load_inventory(args.approval_policy),
            host_destructive=expected_host_destructive,
        )
    except InventoryError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
