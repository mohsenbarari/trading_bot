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
    "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN": lambda service: service == "bot_fi_bot",
    "BOT_FI_JWT_SECRET_KEY": lambda service: service == "bot_fi_api",
    "WEBAPP_JWT_SECRET_KEY": lambda service: service in {"webapp_fi_api", "webapp_ir_api"},
    "STAGING_WITNESS_SIGNING_KEY": lambda service: service == "witness_api",
    "STAGING_DR_BLOB_CREDENTIALS_FILE": lambda service: service.endswith("_blobs"),
    "STAGING_DR_BLOB_ENCRYPTION_KEYRING_FILE": lambda service: service.endswith("_blobs"),
    "WEBAPP_FI_WITNESS_SECRET": lambda service: service in {
        "webapp_fi_writer_control", "witness_api",
    },
    "WEBAPP_IR_WITNESS_SECRET": lambda service: service in {
        "webapp_ir_writer_control", "witness_api",
    },
    "WEBAPP_FI_CONTROL_DB_PASSWORD": lambda service: service in {
        "webapp_fi_writer_control", "webapp_fi_db_roles",
    },
    "WEBAPP_IR_CONTROL_DB_PASSWORD": lambda service: service in {
        "webapp_ir_writer_control", "webapp_ir_db_roles",
    },
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
        "webapp_fi_db_roles", "webapp_fi_dr_projection",
    },
    "WEBAPP_IR_PROJECTION_DB_PASSWORD": lambda service: service in {
        "webapp_ir_db_roles", "webapp_ir_dr_projection",
    },
    "BOT_FI_PROJECTION_DB_PASSWORD": lambda service: service in {
        "bot_fi_db_roles", "bot_fi_dr_projection",
    },
    "BOT_FI_OBSERVER_DB_PASSWORD": lambda service: service in {
        "bot_fi_db_roles", "bot_fi_sync_observer",
    },
    "WEBAPP_FI_OBSERVER_DB_PASSWORD": lambda service: service in {
        "webapp_fi_db_roles", "webapp_fi_sync_observer",
    },
    "WEBAPP_IR_OBSERVER_DB_PASSWORD": lambda service: service in {
        "webapp_ir_db_roles", "webapp_ir_sync_observer", "webapp_ir_convergence_exporter",
    },
    "WEBAPP_FI_RECEIVER_DB_PASSWORD": lambda service: service in {
        "webapp_fi_db_roles", "webapp_fi_dr_receiver",
    },
    "WEBAPP_FI_DELIVERY_DB_PASSWORD": lambda service: service in {
        "webapp_fi_db_roles", "webapp_fi_dr_delivery",
    },
    "WEBAPP_FI_BLOB_DB_PASSWORD": lambda service: service in {
        "webapp_fi_db_roles", "webapp_fi_blobs",
    },
    "WEBAPP_FI_EFFECT_DB_PASSWORD": lambda service: service in {
        "webapp_fi_db_roles", "webapp_fi_effects",
    },
    "WEBAPP_IR_RECEIVER_DB_PASSWORD": lambda service: service in {
        "webapp_ir_db_roles", "webapp_ir_dr_receiver",
    },
    "WEBAPP_IR_DELIVERY_DB_PASSWORD": lambda service: service in {
        "webapp_ir_db_roles", "webapp_ir_dr_delivery",
    },
    "WEBAPP_IR_BLOB_DB_PASSWORD": lambda service: service in {
        "webapp_ir_db_roles", "webapp_ir_blobs",
    },
    "WEBAPP_IR_EFFECT_DB_PASSWORD": lambda service: service in {
        "webapp_ir_db_roles", "webapp_ir_effects",
    },
    "BOT_FI_RECEIVER_DB_PASSWORD": lambda service: service in {
        "bot_fi_db_roles", "bot_fi_dr_receiver",
    },
    "BOT_FI_DELIVERY_DB_PASSWORD": lambda service: service in {
        "bot_fi_db_roles", "bot_fi_dr_delivery",
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
        "witness_db", "witness_role_bootstrap",
    },
    "WITNESS_MIGRATOR_DB_PASSWORD": lambda service: service in {
        "witness_role_bootstrap", "witness_migration",
    },
    "WITNESS_RUNTIME_DB_PASSWORD": lambda service: service in {
        "witness_role_bootstrap", "witness_api",
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
    "bot_fi_dr_egress": {"bot_fi_dr_delivery"},
    "webapp_fi_dr_egress": {"webapp_fi_dr_delivery"},
    "webapp_ir_dr_egress": {"webapp_ir_dr_delivery"},
    "writer_witness_egress": {
        "webapp_fi_writer_control", "webapp_ir_writer_control",
    },
    "bot_fi_ingress": {"bot_fi_dr_tls"},
    "webapp_fi_ingress": {"webapp_fi_dr_tls"},
    "webapp_ir_ingress": {"webapp_ir_dr_tls"},
    "witness_ingress": {"witness_dr_tls"},
    "bot_fi_egress": {"bot_fi_api", "bot_fi_bot"},
    "webapp_fi_egress": {"webapp_fi_api", "webapp_fi_blobs", "webapp_fi_effects"},
    "webapp_ir_egress": {
        "webapp_ir_api", "webapp_ir_blobs", "webapp_ir_effects",
        "webapp_ir_convergence_exporter",
    },
}
WRITER_CONTROL_SERVICES = {
    "webapp_fi_writer_control",
    "webapp_ir_writer_control",
}
WRITER_CONTROL_ONLY_ENV_KEYS = {
    "DR_CONTROL_DATABASE_URL",
    "WRITER_WITNESS_CLIENT_KEY_ID",
    "WRITER_WITNESS_CLIENT_SECRET",
    "WRITER_WITNESS_INTERNAL_URL",
}
GENERAL_EGRESS_NETWORKS = {
    "bot_fi_egress", "webapp_fi_egress", "webapp_ir_egress",
}
CONVERGENCE_EXPORTER_SERVICES = {"webapp_ir_convergence_exporter"}
CONVERGENCE_EXPORTER_FORBIDDEN_ENV = {
    "ARVAN_S3_ACCESS_KEY", "ARVAN_S3_SECRET_KEY", "ARVAN_S3_ENDPOINT", "ARVAN_S3_REGION",
    "STAGING_DR_BLOB_CREDENTIALS_FILE", "STAGING_DR_BLOB_ENCRYPTION_KEYRING_FILE",
}
EXPECTED_TLS_PORTS = {
    "bot_fi_dr_tls": "${BOT_FI_DR_BIND_ADDRESS:?required}:8443:443",
    "webapp_fi_dr_tls": "${WEBAPP_FI_DR_BIND_ADDRESS:?required}:8443:443",
    "webapp_ir_dr_tls": "${WEBAPP_IR_DR_BIND_ADDRESS:?required}:8443:443",
    "witness_dr_tls": "${WITNESS_DR_BIND_ADDRESS:?required}:8444:443",
}
EXPECTED_CROSS_HOSTS = {
    "bot_fi_dr_delivery": [
        "webapp-fi-dr.staging.internal:${BOT_FI_PEER_WEBAPP_FI_IP:?required}",
    ],
    "webapp_fi_dr_delivery": [
        "bot-fi-dr.staging.internal:${WEBAPP_FI_PEER_BOT_FI_IP:?required}",
        "webapp-ir-dr.staging.internal:${WEBAPP_FI_PEER_WEBAPP_IR_IP:?required}",
    ],
    "webapp_fi_blobs": [
        "bot-fi-dr.staging.internal:${WEBAPP_FI_PEER_BOT_FI_IP:?required}",
        "webapp-ir-dr.staging.internal:${WEBAPP_FI_PEER_WEBAPP_IR_IP:?required}",
    ],
    "webapp_fi_writer_control": [
        "witness-dr.staging.internal:${WEBAPP_FI_WITNESS_IP:?required}",
    ],
    "webapp_ir_dr_delivery": [
        "webapp-fi-dr.staging.internal:${WEBAPP_IR_PEER_WEBAPP_FI_IP:?required}",
    ],
    "webapp_ir_blobs": [
        "webapp-fi-dr.staging.internal:${WEBAPP_IR_PEER_WEBAPP_FI_IP:?required}",
    ],
    "webapp_ir_writer_control": [
        "witness-dr.staging.internal:${WEBAPP_IR_WITNESS_IP:?required}",
    ],
}
ROLE_PROFILE_PREFIXES = {
    "bot_fi_": "bot-fi",
    "webapp_fi_": "webapp-fi",
    "webapp_ir_": "webapp-ir",
    "witness_": "witness",
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
        expected_profile = next(
            (
                profile
                for prefix, profile in ROLE_PROFILE_PREFIXES.items()
                if str(service).startswith(prefix)
            ),
            None,
        )
        if expected_profile is not None and config.get("profiles") != [expected_profile]:
            violations.append(f"{service}:role_profile_missing_or_mixed:{expected_profile}")
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
        environment = config.get("environment", {})
        if isinstance(environment, dict):
            forbidden_control_keys = (
                set(environment) & WRITER_CONTROL_ONLY_ENV_KEYS
                if str(service) not in WRITER_CONTROL_SERVICES
                else set()
            )
            for key in sorted(forbidden_control_keys):
                violations.append(f"{service}:writer_control_environment_forbidden:{key}")
        if str(service) in WRITER_CONTROL_SERVICES:
            if config.get("ports") or config.get("expose"):
                violations.append(f"{service}:inbound_surface_forbidden")
            if service_networks & GENERAL_EGRESS_NETWORKS:
                violations.append(f"{service}:public_egress_forbidden")
            if "writer_witness_egress" not in service_networks:
                violations.append(f"{service}:witness_egress_missing")
        if str(service) in CONVERGENCE_EXPORTER_SERVICES:
            if config.get("ports") or config.get("expose"):
                violations.append(f"{service}:inbound_surface_forbidden")
            if service_networks != {"webapp_ir", "webapp_ir_egress"}:
                violations.append(f"{service}:closed_egress_topology_missing")
            if isinstance(environment, dict):
                for key in sorted(set(environment) & CONVERGENCE_EXPORTER_FORBIDDEN_ENV):
                    violations.append(f"{service}:object_storage_credential_forbidden:{key}")
        if str(service) in EXPECTED_TLS_PORTS:
            if config.get("ports") != [EXPECTED_TLS_PORTS[str(service)]]:
                violations.append(f"{service}:fixed_inventory_bound_port_missing")
        elif config.get("ports") and str(service) not in {
            "bot_fi_api", "webapp_fi_api", "webapp_ir_api", "witness_api",
        }:
            violations.append(f"{service}:unexpected_published_port")
        if str(service) in EXPECTED_CROSS_HOSTS and (
            config.get("extra_hosts") != EXPECTED_CROSS_HOSTS[str(service)]
        ):
            violations.append(f"{service}:signed_peer_host_mapping_missing")
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
