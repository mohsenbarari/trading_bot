#!/usr/bin/env python3
"""Fail-closed isolation gate for the authoritative three-site Full Matrix."""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID


PRODUCTION_IPS = frozenset(
    {"65.109.216.187", "65.109.220.59", "185.206.95.250", "185.206.95.94", "185.231.182.6"}
)
PRODUCTION_DOMAINS = frozenset({"gold-trade.ir", "coin.gold-trade.ir"})
PRODUCTION_BUCKETS = frozenset({"production-sync-coin"})
ROLES = frozenset({"bot_fi", "webapp_fi", "webapp_ir", "witness"})
SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)


class InventoryError(RuntimeError):
    pass


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


def verify_inventory(payload: dict[str, Any], *, host_destructive: bool) -> dict[str, Any]:
    required = {
        "schema", "campaign_id", "release_sha", "canonical_domain", "optional_ingress",
        "deployment_id", "object_storage", "roles", "credential_scope",
        "production_boundaries",
    }
    if set(payload) != required or payload["schema"] != "three-site-staging-inventory-v1":
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
        "volume_ids", "audit_root_ids", "domains", "buckets",
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
        "uploads_volume_id", "audit_root_id", "release_sha", "deployment_id",
    }
    by_role: dict[str, dict[str, Any]] = {}
    for role in roles:
        if not isinstance(role, dict) or set(role) != expected_fields:
            raise InventoryError("role inventory fields are invalid")
        name = str(role["role"])
        if name not in ROLES or name in by_role or role["physical_site"] != name:
            raise InventoryError("role/physical identity is unknown or duplicate")
        try:
            ip_value = ipaddress.ip_address(str(role["host_ip"]))
            ip = str(ip_value)
        except ValueError as exc:
            raise InventoryError(f"invalid host IP for {name}") from exc
        if any(ip_value in network for network in DOCUMENTATION_NETWORKS):
            raise InventoryError(f"documentation-only host IP is forbidden for staging role {name}")
        if ip in PRODUCTION_IPS:
            raise InventoryError(f"production host is forbidden for staging role {name}")
        if ip.lower() in normalized_boundaries["host_ips"]:
            raise InventoryError(f"declared production host is forbidden for staging role {name}")
        if str(role["release_sha"]).lower() != release_sha:
            raise InventoryError("mixed release SHA detected")
        if role["deployment_id"] != payload["deployment_id"]:
            raise InventoryError("mixed deployment identity detected")
        for field in ("machine_id", "docker_daemon_id", "postgres_system_id", "postgres_volume_id", "audit_root_id"):
            if not str(role[field] or "").strip():
                raise InventoryError(f"{name} lacks {field}")
        boundary_map = {
            "machine_id": "machine_ids",
            "docker_daemon_id": "docker_daemon_ids",
            "postgres_system_id": "postgres_system_ids",
            "postgres_volume_id": "volume_ids",
            "audit_root_id": "audit_root_ids",
        }
        for role_field, boundary_field in boundary_map.items():
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

    distinct_fields = ("postgres_system_id", "postgres_volume_id", "audit_root_id")
    for field in distinct_fields:
        values = [str(role[field]) for role in roles]
        if len(set(values)) != len(values):
            raise InventoryError(f"mutable staging boundary is shared: {field}")
    for field in ("redis_volume_id", "uploads_volume_id"):
        values = [str(role[field]) for role in roles if role[field] is not None]
        if len(set(values)) != len(values):
            raise InventoryError(f"mutable staging boundary is shared: {field}")
    if host_destructive:
        for field in ("host_ip", "machine_id", "docker_daemon_id"):
            values = [str(role[field]) for role in roles]
            if len(set(values)) != len(values):
                raise InventoryError(f"host-destructive matrix requires distinct {field}")
    return {
        "status": "approved",
        "campaign_id": str(payload["campaign_id"]),
        "release_sha": release_sha,
        "deployment_id": payload["deployment_id"],
        "host_destructive": host_destructive,
        "role_count": len(roles),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--host-destructive", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = verify_inventory(load_inventory(args.inventory), host_destructive=args.host_destructive)
    except InventoryError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
