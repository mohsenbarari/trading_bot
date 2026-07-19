#!/usr/bin/env python3
"""Verify the effective Compose environment against closed secret boundaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

import yaml


class SecretBoundaryError(RuntimeError):
    pass


SECRET_REFERENCES = {
    "BOT_TOKEN": lambda service: service == "bot_fi_bot",
    "BOT_FI_JWT_SECRET_KEY": lambda service: service == "bot_fi_api",
    "WEBAPP_JWT_SECRET_KEY": lambda service: service in {"webapp_fi_api", "webapp_ir_api"},
    "STAGING_WITNESS_SIGNING_KEY": lambda service: service == "witness_api",
    "STAGING_DR_BLOB_CREDENTIALS_FILE": lambda service: service.endswith("_blobs"),
    "STAGING_DR_BLOB_ENCRYPTION_KEYRING_FILE": lambda service: service.endswith("_blobs"),
    "WEBAPP_FI_WITNESS_SECRET": lambda service: service in {"webapp_fi_api", "witness_api"},
    "WEBAPP_IR_WITNESS_SECRET": lambda service: service in {"webapp_ir_api", "witness_api"},
    "WEBAPP_FI_CONTROL_DB_PASSWORD": lambda service: service in {"webapp_fi_api", "webapp_fi_db_roles"},
    "WEBAPP_IR_CONTROL_DB_PASSWORD": lambda service: service in {"webapp_ir_api", "webapp_ir_db_roles"},
    "WEBAPP_FI_APP_DB_PASSWORD": lambda service: service in {
        "webapp_fi_api", "webapp_fi_effects", "webapp_fi_db_roles",
    },
    "WEBAPP_IR_APP_DB_PASSWORD": lambda service: service in {
        "webapp_ir_api", "webapp_ir_effects", "webapp_ir_db_roles",
    },
    "BOT_FI_APP_DB_PASSWORD": lambda service: service in {
        "bot_fi_api", "bot_fi_bot", "bot_fi_db_roles",
    },
    "WEBAPP_FI_PROJECTION_DB_PASSWORD": lambda service: service in {
        "webapp_fi_db_roles", "webapp_fi_dr_receiver", "webapp_fi_dr_delivery",
        "webapp_fi_dr_projection", "webapp_fi_effects", "webapp_fi_blobs",
    },
    "WEBAPP_IR_PROJECTION_DB_PASSWORD": lambda service: service in {
        "webapp_ir_db_roles", "webapp_ir_dr_receiver", "webapp_ir_dr_delivery",
        "webapp_ir_dr_projection", "webapp_ir_effects", "webapp_ir_blobs",
    },
    "BOT_FI_PROJECTION_DB_PASSWORD": lambda service: service in {
        "bot_fi_db_roles", "bot_fi_dr_receiver", "bot_fi_dr_delivery", "bot_fi_dr_projection",
    },
    "BOT_FI_DR_PAIRWISE_KEYS_JSON": lambda service: service in {
        "bot_fi_dr_receiver", "bot_fi_dr_delivery",
    },
    "WEBAPP_FI_DR_PAIRWISE_KEYS_JSON": lambda service: service in {
        "webapp_fi_dr_receiver", "webapp_fi_dr_delivery", "webapp_fi_blobs",
    },
    "WEBAPP_IR_DR_PAIRWISE_KEYS_JSON": lambda service: service in {
        "webapp_ir_dr_receiver", "webapp_ir_dr_delivery", "webapp_ir_blobs",
    },
    "WITNESS_POSTGRES_PASSWORD": lambda service: service in {
        "witness_db", "witness_api", "witness_migration",
    },
    "ORIGIN_READINESS_API_KEY": lambda service: service in {
        "webapp_fi_api", "webapp_ir_api",
    },
    "SMSIR_API_KEY": lambda service: service in {
        "webapp_fi_effects", "webapp_ir_effects",
    },
    "WEB_PUSH_VAPID_PRIVATE_KEY": lambda service: service in {
        "webapp_fi_effects", "webapp_ir_effects",
    },
    "STAGING_BOT_FI_TLS_KEY": lambda service: service == "bot_fi_dr_tls",
    "STAGING_WEBAPP_FI_TLS_KEY": lambda service: service == "webapp_fi_dr_tls",
    "STAGING_WEBAPP_IR_TLS_KEY": lambda service: service == "webapp_ir_dr_tls",
    "STAGING_WITNESS_TLS_KEY": lambda service: service == "witness_dr_tls",
}
OWNER_REFERENCE = re.compile(r"(?:BOT_FI|WEBAPP_FI|WEBAPP_IR)_POSTGRES_PASSWORD")
MANAGED_NETWORK_MEMBERS = {
    "dr_bot_webapp_fi": {
        "bot_fi_dr_delivery",
        "bot_fi_dr_tls",
        "webapp_fi_dr_delivery",
        "webapp_fi_dr_tls",
    },
    "dr_webapp_fi_ir": {
        "webapp_fi_blobs",
        "webapp_fi_dr_delivery",
        "webapp_fi_dr_tls",
        "webapp_ir_blobs",
        "webapp_ir_dr_delivery",
        "webapp_ir_dr_tls",
    },
    "writer_witness": {
        "webapp_fi_api",
        "webapp_ir_api",
        "witness_dr_tls",
    },
    "bot_fi_egress": {"bot_fi_api", "bot_fi_bot"},
    "webapp_fi_egress": {"webapp_fi_api", "webapp_fi_blobs", "webapp_fi_effects"},
    "webapp_ir_egress": {"webapp_ir_api", "webapp_ir_blobs", "webapp_ir_effects"},
}


def _service_networks(config: dict[str, object]) -> set[str]:
    networks = config.get("networks", [])
    if isinstance(networks, dict):
        return {str(name) for name in networks}
    if isinstance(networks, list):
        return {str(name) for name in networks}
    return set()


def verify_compose(path: Path) -> dict[str, object]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    services = payload.get("services") if isinstance(payload, dict) else None
    if not isinstance(services, dict) or not services:
        raise SecretBoundaryError("Compose services are missing")
    violations: list[str] = []
    network_members: dict[str, set[str]] = {
        network: set() for network in MANAGED_NETWORK_MEMBERS
    }
    for service, config in services.items():
        if not isinstance(config, dict):
            violations.append(f"{service}:invalid_config")
            continue
        if "env_file" in config:
            violations.append(f"{service}:env_file_forbidden")
        service_networks = _service_networks(config)
        if "dr_control" in service_networks:
            violations.append(f"{service}:shared_dr_control_network_forbidden")
        for network in service_networks & set(MANAGED_NETWORK_MEMBERS):
            network_members[network].add(str(service))
            if service not in MANAGED_NETWORK_MEMBERS[network]:
                violations.append(f"{service}:forbidden_network:{network}")
        material = json.dumps(
            {"environment": config.get("environment", {}), "volumes": config.get("volumes", [])},
            sort_keys=True,
        )
        for reference, allowed in SECRET_REFERENCES.items():
            if reference in material and not allowed(str(service)):
                violations.append(f"{service}:forbidden:{reference}")
        if OWNER_REFERENCE.search(material) and not (
            service.endswith("_db")
            or service.endswith("_migration")
            or service.endswith("_db_roles")
            or service.endswith("_db_fencing")
        ):
            violations.append(f"{service}:owner_database_secret_forbidden")
        if "${JWT_SECRET_KEY" in material:
            violations.append(f"{service}:legacy_global_jwt_secret_forbidden")
    declared_networks = payload.get("networks", {})
    if isinstance(declared_networks, dict) and "dr_control" in declared_networks:
        violations.append("compose:shared_dr_control_network_forbidden")
    if isinstance(declared_networks, dict):
        for network, expected in MANAGED_NETWORK_MEMBERS.items():
            if network not in declared_networks:
                continue
            missing = expected - network_members[network]
            extra = network_members[network] - expected
            for service in sorted(missing):
                violations.append(f"{service}:missing_network:{network}")
            for service in sorted(extra):
                violations.append(f"{service}:forbidden_network:{network}")
    if violations:
        raise SecretBoundaryError(";".join(sorted(violations)))
    return {
        "status": "verified",
        "service_count": len(services),
        "managed_network_count": sum(
            1 for network in MANAGED_NETWORK_MEMBERS if network in (declared_networks or {})
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--compose",
        type=Path,
        default=Path("deploy/staging/docker-compose.three-site.yml"),
    )
    args = parser.parse_args()
    try:
        result = verify_compose(args.compose)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
