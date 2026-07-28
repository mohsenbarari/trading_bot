#!/usr/bin/env python3
"""Fail-closed verifier for the side-by-side three-site production compose."""

from __future__ import annotations

import argparse
import base64
from functools import lru_cache
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import SecureFileError, read_secure_text, sha256_secure_file
from core.dr_sync_auth import DrSyncAuthError, PairwiseDrKey, parse_pairwise_keys
from core.three_site_topology import (
    BOT_FI_HOST,
    WEBAPP_FI_HOST,
    WEBAPP_IR_HOST,
)


DEFAULT_COMPOSE = REPO_ROOT / "deploy/production/docker-compose.three-site-shadow.yml"
PROJECT_ROOT_PREFIX = "/srv/trading-bot-three-site-production-shadow"
DATA_ROOT_PREFIX = "/srv/trading-bot-three-site-production-shadow-data"
SECRET_ROOT_PREFIX = "/root/secure-envs/trading-bot/three-site-production-shadow"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REQUIRED_INTERPOLATION_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*):\?[^}]*\}")
IMAGE_INTERPOLATION_RE = re.compile(
    r"^\$\{PRODUCTION_SHADOW_(APP|POSTGRES|REDIS|NGINX)_IMAGE_ID:\?[^}]+\}$"
)
FORBIDDEN_SOURCE_RE = re.compile(
    r"(?:"
    r"\bstaging\b|"
    r"gold-trading\.ir|"
    r"coin\.gold-trade\.ir|"
    r"coin\.362514\.ir|"
    r"/srv/trading-bot/current|"
    r"/srv/trading-bot-three-site-staging|"
    r"\btrading_bot_(?:db|redis|api|bot|postgres|uploads|audit)\b"
    r")",
    re.IGNORECASE,
)
FORBIDDEN_KEY_NAMES = {
    "build",
    "container_name",
    "privileged",
    "network_mode",
    "pid",
    "ipc",
}
BANNED_HOST_PORTS = {
    80,
    443,
    8000,
    8088,
    8211,
    8212,
    8213,
    8214,
    8443,
    8444,
}
IMAGE_KEYS = (
    "PRODUCTION_SHADOW_APP_IMAGE_ID",
    "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID",
    "PRODUCTION_SHADOW_REDIS_IMAGE_ID",
    "PRODUCTION_SHADOW_NGINX_IMAGE_ID",
)
PORT_KEYS = (
    "BOT_FI_SHADOW_DR_PORT",
    "BOT_FI_SHADOW_API_PORT",
    "WEBAPP_FI_SHADOW_API_PORT",
    "WEBAPP_IR_SHADOW_API_PORT",
    "WEBAPP_FI_SHADOW_DR_PORT",
    "WEBAPP_IR_SHADOW_DR_PORT",
)
EXPECTED_DR_ADDRESSES = {
    "BOT_FI_SHADOW_DR_BIND_ADDRESS": BOT_FI_HOST,
    "WEBAPP_FI_SHADOW_DR_BIND_ADDRESS": WEBAPP_FI_HOST,
    "WEBAPP_IR_SHADOW_DR_BIND_ADDRESS": WEBAPP_IR_HOST,
    "BOT_FI_PEER_WEBAPP_FI_IP": WEBAPP_FI_HOST,
    "WEBAPP_FI_PEER_BOT_FI_IP": BOT_FI_HOST,
    "WEBAPP_FI_PEER_WEBAPP_IR_IP": WEBAPP_IR_HOST,
    "WEBAPP_IR_PEER_WEBAPP_FI_IP": WEBAPP_FI_HOST,
}
ARVAN_BLOB_ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
ARVAN_BLOB_REGION = "ir-thr-at1"
ARVAN_BLOB_BUCKET = "production-sync-coin"
ROLE_API_ENV_FILES = {
    "bot_fi": "bot-fi/runtime.env.api",
    "webapp_fi": "webapp-fi/runtime.env.api",
    "webapp_ir": "webapp-ir/runtime.env.api",
}
ROLE_PHYSICAL_SITES = {
    "bot_fi": "bot_fi",
    "webapp_fi": "webapp_fi",
    "webapp_ir": "webapp_ir",
}
EXPECTED_DR_PEER_SITES = {
    "bot_fi": {"webapp_fi"},
    "webapp_fi": {"bot_fi", "webapp_ir"},
    "webapp_ir": {"webapp_fi"},
}
EXPECTED_DR_DIRECTED_PAIRS = {
    "bot_fi": {
        ("bot_fi", "webapp_fi"),
        ("webapp_fi", "bot_fi"),
    },
    "webapp_fi": {
        ("bot_fi", "webapp_fi"),
        ("webapp_fi", "bot_fi"),
        ("webapp_fi", "webapp_ir"),
        ("webapp_ir", "webapp_fi"),
    },
    "webapp_ir": {
        ("webapp_fi", "webapp_ir"),
        ("webapp_ir", "webapp_fi"),
    },
}
API_FORBIDDEN_PROVIDER_KEYS = frozenset(
    {
        "BOT_TOKEN",
        "TELEGRAM_DELIVERY_EXECUTION_OWNER",
        "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED",
        "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY",
        "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED",
        "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN",
        "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID",
        "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_EDITOR_BOT_ID",
        "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID",
    }
)
WEBAPP_PROVIDER_KEYS = (
    "SMSIR_API_KEY",
    "SMSIR_LINE_NUMBER",
    "SMSIR_OTP_TEMPLATE_ID",
    "SMSIR_OTP_TEMPLATE_PARAMETER",
    "SMSIR_INVITATION_TEMPLATE_ID",
    "SMSIR_INVITATION_TEMPLATE_PARAMETER",
    "SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID",
    "SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID",
    "WEB_PUSH_VAPID_PUBLIC_KEY",
    "WEB_PUSH_VAPID_PRIVATE_KEY",
    "WEB_PUSH_VAPID_SUBJECT",
)
WEBAPP_API_PROVIDER_KEYS = tuple(
    key
    for key in WEBAPP_PROVIDER_KEYS
    if key != "WEB_PUSH_VAPID_PRIVATE_KEY"
)
API_BACKGROUND_EXPRESSIONS = {
    "bot_fi_api": "${BOT_FI_BACKGROUND_JOBS_ENABLED:?required}",
    "webapp_fi_api": "${WEBAPP_FI_BACKGROUND_JOBS_ENABLED:?required}",
    "webapp_ir_api": "${WEBAPP_IR_BACKGROUND_JOBS_ENABLED:?required}",
}
RUNTIME_PARSER_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "DATABASE_URL": "postgresql+asyncpg://verify:verify@127.0.0.1/verify",
    "SYNC_DATABASE_URL": "postgresql://verify:verify@127.0.0.1/verify",
    "POSTGRES_DB": "verify",
    "POSTGRES_USER": "verify",
    "POSTGRES_PASSWORD": "verify-only-not-a-runtime-password",
    "FRONTEND_URL": "https://verifier.invalid",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "JWT_SECRET_KEY": "verify-only-not-a-runtime-jwt-secret",
}


def _role_profiles(role: str) -> dict[str, set[str]]:
    prefix = role.replace("_", "-")
    all_profiles = {
        f"{prefix}-data-ready",
        f"{prefix}-restore",
        f"{prefix}-prepare",
        f"{prefix}-private",
        f"{prefix}-acceptance",
        f"{prefix}-activation",
        f"{prefix}-effects",
        f"{prefix}-observe",
    }
    return {
        f"{role}_db": all_profiles,
        f"{role}_redis": {
            f"{prefix}-data-ready",
            f"{prefix}-private",
            f"{prefix}-acceptance",
            f"{prefix}-activation",
            f"{prefix}-effects",
        },
        f"{role}_restore_tool": {f"{prefix}-restore"},
        f"{role}_db_roles": {f"{prefix}-prepare"},
        f"{role}_migration": {f"{prefix}-prepare"},
        f"{role}_db_roles_post_migration": {f"{prefix}-prepare"},
        f"{role}_db_fencing": {f"{prefix}-prepare"},
        f"{role}_dr_receiver": {
            f"{prefix}-private",
            f"{prefix}-acceptance",
            f"{prefix}-activation",
            f"{prefix}-effects",
        },
        f"{role}_dr_delivery": {
            f"{prefix}-private",
            f"{prefix}-acceptance",
            f"{prefix}-activation",
            f"{prefix}-effects",
        },
        f"{role}_dr_projection": {
            f"{prefix}-private",
            f"{prefix}-acceptance",
            f"{prefix}-activation",
            f"{prefix}-effects",
        },
        f"{role}_blobs": {
            f"{prefix}-private",
            f"{prefix}-acceptance",
            f"{prefix}-activation",
            f"{prefix}-effects",
        },
        f"{role}_writer_control": {
            f"{prefix}-private",
            f"{prefix}-acceptance",
            f"{prefix}-activation",
            f"{prefix}-effects",
        },
        f"{role}_dr_tls": {
            f"{prefix}-private",
            f"{prefix}-acceptance",
            f"{prefix}-activation",
            f"{prefix}-effects",
        },
        f"{role}_api": {f"{prefix}-activation", f"{prefix}-effects"},
        f"{role}_api_acceptance": {f"{prefix}-acceptance"},
        f"{role}_effects": {f"{prefix}-effects"},
        f"{role}_sync_observer": {f"{prefix}-observe"},
    }


def _bot_profiles() -> dict[str, set[str]]:
    all_profiles = {
        "bot-fi-data-ready",
        "bot-fi-restore",
        "bot-fi-prepare",
        "bot-fi-private",
        "bot-fi-acceptance",
        "bot-fi-public",
        "bot-fi-worker",
        "bot-fi-observe",
    }
    private_profiles = {
        "bot-fi-private",
        "bot-fi-acceptance",
        "bot-fi-public",
        "bot-fi-worker",
    }
    return {
        "bot_fi_db": all_profiles,
        "bot_fi_redis": {
            "bot-fi-data-ready",
            "bot-fi-private",
            "bot-fi-acceptance",
            "bot-fi-public",
            "bot-fi-worker",
        },
        "bot_fi_restore_tool": {"bot-fi-restore"},
        "bot_fi_migration": {"bot-fi-prepare"},
        "bot_fi_db_roles": {"bot-fi-prepare"},
        "bot_fi_dr_receiver": private_profiles,
        "bot_fi_dr_delivery": private_profiles,
        "bot_fi_dr_projection": private_profiles,
        "bot_fi_dr_tls": private_profiles,
        "bot_fi_api_acceptance": {"bot-fi-acceptance"},
        "bot_fi_api": {"bot-fi-public"},
        "bot_fi_bot": {"bot-fi-worker"},
        "bot_fi_sync_observer": {"bot-fi-observe"},
    }


EXPECTED_SERVICE_PROFILES = {
    **_bot_profiles(),
    **_role_profiles("webapp_fi"),
    **_role_profiles("webapp_ir"),
    "webapp_ir_writer_fence": {"webapp-ir-prepare"},
    "webapp_ir_convergence_exporter": {"webapp-ir-observe"},
}
EXPECTED_VOLUMES: set[str] = set()
EXPECTED_NETWORKS = {
    "bot_fi",
    "bot_fi_dr_egress",
    "bot_fi_api_egress",
    "bot_fi_bot_egress",
    "webapp_fi",
    "webapp_fi_dr_egress",
    "webapp_fi_blob_egress",
    "webapp_fi_witness_egress",
    "webapp_fi_api_egress",
    "webapp_fi_effect_egress",
    "webapp_ir",
    "webapp_ir_dr_egress",
    "webapp_ir_blob_egress",
    "webapp_ir_witness_egress",
    "webapp_ir_api_egress",
    "webapp_ir_effect_egress",
    "webapp_ir_observe_egress",
}
API_PORT_EXPRESSIONS = {
    "bot_fi_api": "127.0.0.1:${BOT_FI_SHADOW_API_PORT:?required}:8000",
    "webapp_fi_api": "127.0.0.1:${WEBAPP_FI_SHADOW_API_PORT:?required}:8000",
    "webapp_ir_api": "127.0.0.1:${WEBAPP_IR_SHADOW_API_PORT:?required}:8000",
}
DR_PORT_EXPRESSIONS = {
    "bot_fi_dr_tls": (
        "${BOT_FI_SHADOW_DR_BIND_ADDRESS:?required}:"
        "${BOT_FI_SHADOW_DR_PORT:?required}:443"
    ),
    "webapp_fi_dr_tls": (
        "${WEBAPP_FI_SHADOW_DR_BIND_ADDRESS:?required}:"
        "${WEBAPP_FI_SHADOW_DR_PORT:?required}:443"
    ),
    "webapp_ir_dr_tls": (
        "${WEBAPP_IR_SHADOW_DR_BIND_ADDRESS:?required}:"
        "${WEBAPP_IR_SHADOW_DR_PORT:?required}:443"
    ),
}


class ProductionShadowComposeError(ValueError):
    """Raised when the production shadow contract is not fail-closed."""


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ProductionShadowComposeError(
                f"environment line {line_number} is not KEY=VALUE"
            )
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise ProductionShadowComposeError(
                f"environment line {line_number} has an invalid or duplicate key"
            )
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        values[key] = value
    return values


def load_compose(path: Path) -> tuple[str, dict[str, Any]]:
    source = path.read_text(encoding="utf-8")
    document = yaml.safe_load(source)
    if not isinstance(document, dict):
        raise ProductionShadowComposeError("compose document must be a mapping")
    return source, document


def _walk_keys(value: Any, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            found.append((str(key), child_path))
            found.extend(_walk_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_keys(child, f"{path}[{index}]"))
    return found


def _service_volumes(service: Mapping[str, Any]) -> list[str]:
    entries = service.get("volumes", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, str)]


def _image_kind(service: Mapping[str, Any]) -> str | None:
    image = service.get("image")
    if not isinstance(image, str):
        return None
    match = IMAGE_INTERPOLATION_RE.fullmatch(image)
    return match.group(1).lower() if match else None


def _dependency_names(service: Mapping[str, Any]) -> set[str]:
    depends_on = service.get("depends_on", {})
    if isinstance(depends_on, dict):
        return {str(name) for name in depends_on}
    if isinstance(depends_on, list):
        return {str(name) for name in depends_on}
    return set()


def _dependency_cycle(services: Mapping[str, Any]) -> list[str] | None:
    graph = {
        str(name): _dependency_names(service)
        for name, service in services.items()
        if isinstance(service, dict)
    }
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(name: str) -> list[str] | None:
        if name in visiting:
            start = stack.index(name)
            return [*stack[start:], name]
        if name in visited:
            return None
        visiting.add(name)
        stack.append(name)
        for dependency in sorted(graph.get(name, set())):
            if dependency in graph:
                cycle = visit(dependency)
                if cycle is not None:
                    return cycle
        stack.pop()
        visiting.remove(name)
        visited.add(name)
        return None

    for name in sorted(graph):
        cycle = visit(name)
        if cycle is not None:
            return cycle
    return None


def collect_source_failures(
    document: Mapping[str, Any],
    source_text: str,
) -> list[str]:
    failures: list[str] = []
    if FORBIDDEN_SOURCE_RE.search(source_text):
        failures.append("compose source contains a legacy, test, or non-production identity")

    for key, key_path in _walk_keys(document):
        if key in FORBIDDEN_KEY_NAMES:
            failures.append(f"forbidden compose key at {key_path}: {key}")

    if document.get("name") != (
        "${PRODUCTION_SHADOW_PROJECT:?operation-bound project is required}"
    ):
        failures.append("compose name must be the required operation-bound project")

    services = document.get("services")
    if not isinstance(services, dict):
        return failures + ["services must be a mapping"]
    if set(services) != set(EXPECTED_SERVICE_PROFILES):
        missing = sorted(set(EXPECTED_SERVICE_PROFILES) - set(services))
        extra = sorted(set(services) - set(EXPECTED_SERVICE_PROFILES))
        failures.append(f"service set mismatch; missing={missing}, extra={extra}")
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        missing_dependencies = sorted(
            dependency
            for dependency in _dependency_names(service)
            if dependency not in services
        )
        if missing_dependencies:
            failures.append(
                f"{name} has unknown dependencies: {missing_dependencies}"
            )
    cycle = _dependency_cycle(services)
    if cycle is not None:
        failures.append(f"compose dependency graph contains a cycle: {' -> '.join(cycle)}")

    for name, service in services.items():
        if not isinstance(service, dict):
            failures.append(f"{name} must be a mapping")
            continue
        expected_profiles = EXPECTED_SERVICE_PROFILES.get(name)
        actual_profiles = service.get("profiles")
        if (
            not isinstance(actual_profiles, list)
            or not actual_profiles
            or set(actual_profiles) != expected_profiles
        ):
            failures.append(
                f"{name} profiles must be exactly {sorted(expected_profiles or [])}"
            )
        if service.get("pull_policy") != "never":
            failures.append(f"{name} must use pull_policy=never")
        kind = _image_kind(service)
        if kind is None:
            failures.append(f"{name} image must be a required immutable local image ID")
        if service.get("cgroup_parent") != (
            "${PRODUCTION_SHADOW_CGROUP_PARENT:?operation-bound cgroup is required}"
        ):
            failures.append(f"{name} must use the operation-bound cgroup")
        if service.get("labels") != {
            "trading-bot.production.operation-id": (
                "${PRODUCTION_SHADOW_OPERATION_ID:?operation UUID is required}"
            )
        }:
            failures.append(
                f"{name} must carry only the exact operation ownership label"
            )

        ports = service.get("ports", [])
        expected_port = API_PORT_EXPRESSIONS.get(name) or DR_PORT_EXPRESSIONS.get(name)
        if expected_port is None:
            if ports:
                failures.append(f"{name} must not publish a host port")
        elif ports != [expected_port]:
            failures.append(f"{name} must publish only {expected_port!r}")

        if kind == "app":
            environment = service.get("environment")
            if not isinstance(environment, dict):
                failures.append(f"{name} must have an explicit application environment")
            else:
                if environment.get("ENVIRONMENT") != "production":
                    failures.append(f"{name} must set ENVIRONMENT=production")
                expected_background = API_BACKGROUND_EXPRESSIONS.get(name, "false")
                if environment.get("BACKGROUND_JOBS_ENABLED") != expected_background:
                    failures.append(
                        f"{name} must set BACKGROUND_JOBS_ENABLED={expected_background}"
                    )
                if environment.get("RELEASE_SHA") != (
                    "${PRODUCTION_SHADOW_RELEASE_SHA:?exact release SHA is required}"
                ):
                    failures.append(f"{name} must bind its release SHA")
                validation_key_services = {
                    "webapp_fi_api",
                    "webapp_fi_api_acceptance",
                    "webapp_ir_api",
                    "webapp_ir_api_acceptance",
                }
                if (
                    name not in validation_key_services
                    and "TELEGRAM_WEBAPP_VALIDATION_KEY" in environment
                ):
                    failures.append(
                        f"{name} must not receive TELEGRAM_WEBAPP_VALIDATION_KEY"
                    )
            if (
                not name.endswith("_api")
                and not name.endswith("_api_acceptance")
                and name != "bot_fi_bot"
                and service.get("env_file")
            ):
                failures.append(
                    f"{name} must not receive an unreviewed service env file"
                )
            ca_mount = (
                "${PRODUCTION_SHADOW_SECRET_ROOT:?operation-bound secret root is required}/"
                "tls/ca.crt:"
                "/run/production-dr-ca/ca.crt:ro"
            )
            if ca_mount not in _service_volumes(service):
                failures.append(f"{name} must mount the operation CA read-only")

        if name.endswith("_api"):
            environment = service.get("environment", {})
            if environment.get("BACKGROUND_JOBS_ENABLED") != (
                API_BACKGROUND_EXPRESSIONS[name]
            ):
                failures.append(
                    f"{name} must use its explicit active/standby background-job gate"
                )
            if name == "bot_fi_api":
                if "TELEGRAM_WEBAPP_VALIDATION_KEY" in environment:
                    failures.append(
                        "bot_fi_api must receive TELEGRAM_WEBAPP_VALIDATION_KEY "
                        "only from its operation-scoped API env"
                    )
            elif environment.get("TELEGRAM_WEBAPP_VALIDATION_KEY") != (
                "${TELEGRAM_WEBAPP_VALIDATION_KEY:?required}"
            ):
                failures.append(
                    f"{name} must receive the required derived "
                    "TELEGRAM_WEBAPP_VALIDATION_KEY"
                )
            for secret_key in (
                "BOT_TOKEN",
                "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN",
            ):
                if environment.get(secret_key) != "":
                    failures.append(f"{name} must scrub provider credential {secret_key}")
            if name == "bot_fi_api":
                if environment.get("JWT_SECRET_KEY") != (
                    "${BOT_FI_JWT_SECRET_KEY:?required}"
                ):
                    failures.append(
                        "bot_fi_api must use the required Bot-FI production JWT secret"
                    )
                for secret_key in WEBAPP_PROVIDER_KEYS:
                    if environment.get(secret_key) != "":
                        failures.append(
                            f"bot_fi_api must scrub WebApp provider credential {secret_key}"
                        )
            else:
                if environment.get("JWT_SECRET_KEY") != (
                    "${WEBAPP_JWT_SECRET_KEY:?required}"
                ):
                    failures.append(
                        f"{name} must use the required shared production JWT secret"
                    )
                if environment.get("WEB_PUSH_ENABLED") != "true":
                    failures.append(
                        f"{name} must advertise the production Web Push public key"
                    )
                for provider_key in WEBAPP_API_PROVIDER_KEYS:
                    if environment.get(provider_key) != f"${{{provider_key}:?required}}":
                        failures.append(
                            f"{name} must receive required production provider field "
                            f"{provider_key}"
                        )
                if environment.get("WEB_PUSH_VAPID_PRIVATE_KEY") != "":
                    failures.append(
                        f"{name} must scrub WEB_PUSH_VAPID_PRIVATE_KEY; "
                        "only its effect worker may receive it"
                    )
            role_path = name.removesuffix("_api").replace("_", "-")
            expected_env_file = [
                {
                    "path": (
                        "${PRODUCTION_SHADOW_SECRET_ROOT:"
                        "?operation-bound secret root is required}/"
                        f"{role_path}/runtime.env.api"
                    ),
                    "required": True,
                }
            ]
            if service.get("env_file") != expected_env_file:
                failures.append(f"{name} must use its required operation-scoped API env")
            healthcheck = json.dumps(service.get("healthcheck", {}), sort_keys=True)
            if "/health/ready" not in healthcheck:
                failures.append(f"{name} must probe the real API readiness route")
            role = name.removesuffix("_api")
            expected_dependencies = (
                {
                    "bot_fi_db",
                    "bot_fi_redis",
                    "bot_fi_dr_tls",
                    "bot_fi_dr_delivery",
                    "bot_fi_dr_projection",
                }
                if role == "bot_fi"
                else {
                    f"{role}_db",
                    f"{role}_redis",
                    f"{role}_dr_tls",
                    f"{role}_dr_delivery",
                    f"{role}_dr_projection",
                    f"{role}_blobs",
                    f"{role}_writer_control",
                }
            )
            if _dependency_names(service) != expected_dependencies:
                failures.append(
                    f"{name} must depend on the complete role-local private plane"
                )
        if name.endswith("_api_acceptance"):
            environment = service.get("environment", {})
            role = name.removesuffix("_api_acceptance")
            expected_jwt = (
                "${BOT_FI_JWT_SECRET_KEY:?required}"
                if role == "bot_fi"
                else "${WEBAPP_JWT_SECRET_KEY:?required}"
            )
            if (
                service.get("restart") != "no"
                or environment.get("BACKGROUND_JOBS_ENABLED") != "false"
                or environment.get("JWT_SECRET_KEY") != expected_jwt
            ):
                failures.append(
                    f"{name} must be a one-shot background-disabled acceptance API"
                )
            expected_validation_key = (
                None
                if role == "bot_fi"
                else "${TELEGRAM_WEBAPP_VALIDATION_KEY:?required}"
            )
            if environment.get("TELEGRAM_WEBAPP_VALIDATION_KEY") != (
                expected_validation_key
            ):
                failures.append(
                    f"{name} has an invalid derived Telegram WebApp key scope"
                )
            for key in (
                "BOT_TOKEN",
                "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN",
                *WEBAPP_PROVIDER_KEYS,
            ):
                if environment.get(key) != "":
                    failures.append(
                        f"{name} must scrub pre-commit provider field {key}"
                    )
            role_path = role.replace("_", "-")
            expected_env_file = [
                {
                    "path": (
                        "${PRODUCTION_SHADOW_SECRET_ROOT:"
                        "?operation-bound secret root is required}/"
                        f"{role_path}/runtime.env.api"
                    ),
                    "required": True,
                }
            ]
            if service.get("env_file") != expected_env_file:
                failures.append(
                    f"{name} must use its reviewed operation-scoped API env"
                )
            expected_dependencies = (
                {
                    "bot_fi_db",
                    "bot_fi_redis",
                    "bot_fi_dr_tls",
                    "bot_fi_dr_delivery",
                    "bot_fi_dr_projection",
                }
                if role == "bot_fi"
                else {
                    f"{role}_db",
                    f"{role}_redis",
                    f"{role}_dr_tls",
                    f"{role}_dr_delivery",
                    f"{role}_dr_projection",
                    f"{role}_blobs",
                    f"{role}_writer_control",
                }
            )
            healthcheck = json.dumps(service.get("healthcheck", {}), sort_keys=True)
            if (
                _dependency_names(service) != expected_dependencies
                or set(service.get("networks", [])) != {role}
                or service.get("ports")
                or "/health/ready" not in healthcheck
            ):
                failures.append(
                    f"{name} must be internal-only and wait for the complete private plane"
                )
        if name.endswith("_effects"):
            environment = service.get("environment", {})
            if environment.get("DR_EFFECT_WORKER_ENABLED") != "true":
                failures.append(f"{name} must be an explicit effect worker")
            if environment.get("WEB_PUSH_ENABLED") != "true":
                failures.append(f"{name} must receive enabled production provider config")
            for provider_key in WEBAPP_PROVIDER_KEYS:
                if environment.get(provider_key) != f"${{{provider_key}:?required}}":
                    failures.append(
                        f"{name} must receive required production provider field "
                        f"{provider_key}"
                    )
            role = name.removesuffix("_effects")
            if _dependency_names(service) != {
                f"{role}_db",
                f"{role}_redis",
                f"{role}_api",
            }:
                failures.append(
                    f"{name} must wait for its role-local healthy API"
                )
            api_dependency = service.get("depends_on", {}).get(f"{role}_api", {})
            if (
                not isinstance(api_dependency, dict)
                or api_dependency.get("condition") != "service_healthy"
            ):
                failures.append(
                    f"{name} must require its role-local API to be healthy"
                )
        if name.endswith("_restore_tool"):
            if service.get("restart") != "no":
                failures.append(f"{name} must be one-shot")
            rendered_command = json.dumps(service.get("command", []))
            if "explicit restore command" not in rendered_command or "exit 64" not in rendered_command:
                failures.append(f"{name} default command must fail closed")
        if name.endswith(
            (
                "_db_roles",
                "_db_roles_post_migration",
                "_migration",
                "_db_fencing",
                "_writer_fence",
            )
        ):
            if service.get("restart") != "no":
                failures.append(f"{name} must be one-shot")
        if name == "webapp_ir_writer_fence":
            command = service.get("command")
            environment = service.get("environment", {})
            required_arguments = {
                "scripts/manage_webapp_writer.py",
                "fence",
                "--expected-epoch",
                "--expected-active-site",
                "webapp_fi",
                "--apply",
                "--confirm",
                "writer:fence:webapp_ir:1:1",
            }
            if (
                not isinstance(command, list)
                or not required_arguments.issubset(set(map(str, command)))
                or _dependency_names(service) != {"webapp_ir_db_fencing"}
                or set(service.get("networks", [])) != {"webapp_ir"}
                or environment.get("WRITER_WITNESS_REQUIRED") != "false"
                or environment.get("WRITER_WITNESS_AUTO_RENEW_ENABLED") != "false"
                or any(
                    key in environment
                    for key in (
                        "WRITER_WITNESS_INTERNAL_URL",
                        "WRITER_WITNESS_CA_BUNDLE",
                        "WRITER_WITNESS_CLIENT_KEY_ID",
                        "WRITER_WITNESS_CLIENT_SECRET",
                        "WRITER_WITNESS_PUBLIC_KEY",
                    )
                )
            ):
                failures.append(
                    "webapp_ir_writer_fence must apply the exact epoch-1 local standby gate"
                )
        if name == "webapp_ir_convergence_exporter":
            if service.get("restart") != "no":
                failures.append(f"{name} must be one-shot")

    for role in ("webapp_fi", "webapp_ir"):
        prepare_chain = (
            (f"{role}_migration", {f"{role}_db_roles"}),
            (
                f"{role}_db_roles_post_migration",
                {f"{role}_migration"},
            ),
            (
                f"{role}_db_fencing",
                {f"{role}_db_roles_post_migration"},
            ),
        )
        for service_name, expected_dependencies in prepare_chain:
            if _dependency_names(
                services.get(service_name, {})
            ) != expected_dependencies:
                failures.append(
                    f"{service_name} must preserve the exact "
                    "roles-to-migration-to-roles-to-fencing sequence"
                )

    for role in ("bot_fi", "webapp_fi", "webapp_ir"):
        profile = f"{role.replace('_', '-')}-data-ready"
        active = {
            name
            for name, service in services.items()
            if profile in service.get("profiles", [])
        }
        expected = {f"{role}_db", f"{role}_redis"}
        if active != expected:
            failures.append(
                f"{profile} must contain only PostgreSQL and Redis; got {sorted(active)}"
            )
        acceptance_profile = f"{role.replace('_', '-')}-acceptance"
        acceptance_services = {
            name
            for name, service in services.items()
            if acceptance_profile in service.get("profiles", [])
        }
        expected_acceptance = (
            {
                "bot_fi_db",
                "bot_fi_redis",
                "bot_fi_dr_receiver",
                "bot_fi_dr_delivery",
                "bot_fi_dr_projection",
                "bot_fi_dr_tls",
                "bot_fi_api_acceptance",
            }
            if role == "bot_fi"
            else {
                f"{role}_db",
                f"{role}_redis",
                f"{role}_dr_receiver",
                f"{role}_dr_delivery",
                f"{role}_dr_projection",
                f"{role}_blobs",
                f"{role}_writer_control",
                f"{role}_dr_tls",
                f"{role}_api_acceptance",
            }
        )
        if acceptance_services != expected_acceptance:
            failures.append(
                f"{acceptance_profile} must contain only the private plane and "
                "background-disabled acceptance API"
            )

    data_fragment = (
        "${PRODUCTION_SHADOW_DATA_ROOT:"
        "?operation-bound data root is required}/"
    )
    for role in ("bot_fi", "webapp_fi", "webapp_ir"):
        role_path = role.replace("_", "-")
        redis_service = services.get(f"{role}_redis", {})
        expected_redis = f"{data_fragment}{role_path}/redis:/data"
        expected_postgres = (
            f"{data_fragment}{role_path}/postgres:"
            "/var/lib/postgresql/data"
        )
        if _service_volumes(redis_service) != [expected_redis]:
            failures.append(
                f"{role}_redis must bind only its exact canonical Redis directory"
            )
        if _service_volumes(services.get(f"{role}_db", {})) != [
            expected_postgres
        ]:
            failures.append(
                f"{role}_db must bind only its exact canonical PostgreSQL directory"
            )
        expected_restore_mounts = {
            (
                f"{data_fragment}restore-input/{role_path}:"
                "/run/restore-input:ro"
            ),
            (
                f"{data_fragment}{role_path}/uploads:"
                "/run/restore-target/uploads"
            ),
            (
                f"{data_fragment}{role_path}/audit:"
                "/run/restore-target/audit"
            ),
        }
        observed_restore_mounts = _service_volumes(
            services.get(f"{role}_restore_tool", {})
        )
        if (
            len(observed_restore_mounts) != len(expected_restore_mounts)
            or set(observed_restore_mounts) != expected_restore_mounts
        ):
            failures.append(
                f"{role}_restore_tool must bind only its exact restore input, "
                "uploads, and audit directories"
            )
        restore_material = json.dumps(
            services.get(f"{role}_restore_tool", {}),
            sort_keys=True,
        ).lower()
        if any(marker in restore_material for marker in (".rdb", ".aof", "redis-input")):
            failures.append(
                f"{role}_restore_tool must not accept legacy Redis state"
            )
        for service_name, service in services.items():
            if not service_name.startswith(f"{role}_"):
                continue
            for mount in _service_volumes(service):
                if mount.startswith("${"):
                    closing = mount.find("}")
                    separator = mount.find(":", closing + 1)
                else:
                    separator = mount.find(":")
                if separator <= 0:
                    failures.append(
                        f"{service_name} contains an invalid volume mount"
                    )
                    continue
                source = mount[:separator]
                remainder = mount[separator + 1 :]
                destination, mode_separator, mode = remainder.partition(":")
                if (
                    not destination
                    or (mode_separator and mode != "ro")
                ):
                    failures.append(
                        f"{service_name} contains an invalid volume mount"
                    )
                    continue
                read_only = bool(mode_separator)
                store = None
                if destination == "/var/lib/postgresql/data":
                    store = "postgres"
                elif destination == "/data" and service_name == f"{role}_redis":
                    store = "redis"
                elif destination in {
                    "/run/restore-target/uploads",
                    "/app/uploads",
                }:
                    store = "uploads"
                elif destination in {
                    "/run/restore-target/audit",
                    "/app/audit_trail",
                }:
                    store = "audit"
                if store is not None:
                    expected_source = f"{data_fragment}{role_path}/{store}"
                    if source != expected_source:
                        failures.append(
                            f"{service_name} {store} mount must use "
                            "its exact role/store directory"
                        )
                    if destination in {
                        "/var/lib/postgresql/data",
                        "/data",
                        "/run/restore-target/uploads",
                        "/run/restore-target/audit",
                    } and read_only:
                        failures.append(
                            f"{service_name} required writable store is read-only"
                        )
                elif source.startswith(data_fragment):
                    expected_restore = (
                        f"{data_fragment}restore-input/{role_path}"
                    )
                    if (
                        service_name != f"{role}_restore_tool"
                        or source != expected_restore
                        or destination != "/run/restore-input"
                        or not read_only
                    ):
                        failures.append(
                            f"{service_name} has an unapproved operation data bind"
                        )

    volumes = document.get("volumes")
    if not isinstance(volumes, dict) or volumes:
        failures.append(
            "top-level named volumes are forbidden; all stores must be exact direct binds"
        )

    networks = document.get("networks")
    if not isinstance(networks, dict) or set(networks) != EXPECTED_NETWORKS:
        failures.append("network set must be the exact project-scoped three-site set")
    else:
        for name, network in networks.items():
            if (
                not isinstance(network, dict)
                or "name" in network
                or network.get("labels")
                != {
                    "trading-bot.production.operation-id": (
                        "${PRODUCTION_SHADOW_OPERATION_ID:"
                        "?operation UUID is required}"
                    )
                }
            ):
                failures.append(f"network {name} must remain project-scoped")
        for role in ("bot_fi", "webapp_fi", "webapp_ir"):
            if networks.get(role, {}).get("internal") is not True:
                failures.append(f"network {role} must be internal")

    secret_fragment = (
        "${PRODUCTION_SHADOW_SECRET_ROOT:?operation-bound secret root is required}/"
    )
    release_fragment = (
        "${PRODUCTION_SHADOW_RELEASE_ROOT:?exact immutable release root is required}/"
        "deploy/production/three-site-shadow/"
    )
    for name in ("bot_fi_dr_tls", "webapp_fi_dr_tls", "webapp_ir_dr_tls"):
        mounts = _service_volumes(services.get(name, {}))
        if not any(item.startswith(release_fragment) and item.endswith(":ro") for item in mounts):
            failures.append(f"{name} must mount its immutable production TLS config")
        if not any(
            item.startswith(secret_fragment) and "/tls/server.crt:" in item and item.endswith(":ro")
            for item in mounts
        ):
            failures.append(f"{name} must mount its operation TLS certificate")
        if not any(
            item.startswith(secret_fragment) and "/tls/server.key:" in item and item.endswith(":ro")
            for item in mounts
        ):
            failures.append(f"{name} must mount its operation TLS key")

    witness_ca_mount = (
        "${PRODUCTION_SHADOW_SECRET_ROOT:?operation-bound secret root is required}/"
        "witness/ca.crt:/run/production-witness-ca/ca.crt:ro"
    )
    witness_services = {
        "webapp_fi_writer_control",
        "webapp_ir_writer_control",
    }
    for name, service in services.items():
        mounts = _service_volumes(service)
        if name in witness_services:
            if (
                witness_ca_mount not in mounts
                or service.get("environment", {}).get("WRITER_WITNESS_CA_BUNDLE")
                != "/run/production-witness-ca/ca.crt"
            ):
                failures.append(
                    f"{name} must use the separate canonical Witness CA bundle"
                )
        elif any("/run/production-witness-ca/" in mount for mount in mounts):
            failures.append(f"{name} must not mount the canonical Witness CA")

    for role in ("webapp_fi", "webapp_ir"):
        mounts = _service_volumes(services.get(f"{role}_blobs", {}))
        if not any(
            item.startswith(secret_fragment)
            and item.endswith("/run/production-secrets/dr-blob-s3.json:ro")
            for item in mounts
        ):
            failures.append(f"{role}_blobs must mount operation S3 credentials read-only")
        if not any(
            item.startswith(secret_fragment)
            and item.endswith("/run/production-secrets/dr-blob-keyring.json:ro")
            for item in mounts
        ):
            failures.append(f"{role}_blobs must mount the operation keyring read-only")
        expected_capability_networks = {
            f"{role}_dr_delivery": {role, f"{role}_dr_egress"},
            f"{role}_blobs": {role, f"{role}_blob_egress"},
            f"{role}_writer_control": {role, f"{role}_witness_egress"},
            f"{role}_api": {role, f"{role}_api_egress"},
            f"{role}_effects": {role, f"{role}_effect_egress"},
        }
        if role == "webapp_ir":
            expected_capability_networks["webapp_ir_convergence_exporter"] = {
                role,
                "webapp_ir_observe_egress",
            }
        for service_name, expected in expected_capability_networks.items():
            actual = set(services.get(service_name, {}).get("networks", []))
            if actual != expected:
                failures.append(
                    f"{service_name} must use only its dedicated capability egress"
                )

    bot_capability_networks = {
        "bot_fi_dr_delivery": {"bot_fi", "bot_fi_dr_egress"},
        "bot_fi_api": {"bot_fi", "bot_fi_api_egress"},
        "bot_fi_bot": {"bot_fi", "bot_fi_bot_egress"},
    }
    for service_name, expected in bot_capability_networks.items():
        actual = set(services.get(service_name, {}).get("networks", []))
        if actual != expected:
            failures.append(
                f"{service_name} must use only its dedicated capability egress"
            )

    expected_bot_env_file = [
        {
            "path": (
                "${PRODUCTION_SHADOW_SECRET_ROOT:"
                "?operation-bound secret root is required}/"
                "bot-fi/runtime.env.bot"
            ),
            "required": True,
        }
    ]
    if services.get("bot_fi_bot", {}).get("env_file") != expected_bot_env_file:
        failures.append(
            "bot_fi_bot must use its required operation-scoped Bot env"
        )

    allowed_api_provider_services = {
        "webapp_fi_api",
        "webapp_ir_api",
    }
    allowed_effect_provider_services = {
        "webapp_fi_effects",
        "webapp_ir_effects",
    }
    for service_name, service in services.items():
        environment = service.get("environment", {})
        if not isinstance(environment, dict):
            continue
        for provider_key in WEBAPP_PROVIDER_KEYS:
            value = environment.get(provider_key)
            if service_name in allowed_effect_provider_services:
                continue
            if (
                service_name in allowed_api_provider_services
                and provider_key in WEBAPP_API_PROVIDER_KEYS
            ):
                continue
            if value not in {None, ""}:
                failures.append(
                    f"{service_name} must not receive WebApp provider field {provider_key}"
                )

    return failures


def _required_values(source_text: str) -> set[str]:
    return set(REQUIRED_INTERPOLATION_RE.findall(source_text))


def _valid_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _strict_json(raw: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=reject_duplicates)


@lru_cache(maxsize=32)
def _runtime_peer_parser_accepts(raw: str, local_site: str) -> bool:
    environment = {
        **RUNTIME_PARSER_ENV,
        "DR_PEERS_JSON": raw,
        "LOCAL_SITE": local_site,
    }
    try:
        completed = subprocess.run(
            [
                "/usr/bin/python3",
                "-c",
                (
                    "import os;"
                    "from core.dr_delivery_worker import parse_peer_urls;"
                    "parse_peer_urls("
                    "os.environ['DR_PEERS_JSON'],"
                    "local_site=os.environ['LOCAL_SITE']"
                    ")"
                ),
            ],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _parse_peer_entries(raw: str, *, local_site: str) -> dict[str, str] | None:
    if not _runtime_peer_parser_accepts(raw, local_site):
        return None
    try:
        payload = _strict_json(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, list):
        return None
    return {
        str(item["site"]): str(item["base_url"]).rstrip("/")
        for item in payload
        if isinstance(item, dict)
    }


def _parse_pairwise_entries(raw: str) -> dict[tuple[str, str], PairwiseDrKey] | None:
    try:
        keys = parse_pairwise_keys(raw)
    except DrSyncAuthError:
        return None
    return {
        (key.source_site, key.destination_site): key
        for key in keys.values()
    }


def _provider_config_sha256(values: Mapping[str, str]) -> str:
    payload = {key: values.get(key, "") for key in WEBAPP_PROVIDER_KEYS}
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def collect_environment_failures(
    values: Mapping[str, str],
    source_text: str,
) -> list[str]:
    failures: list[str] = []
    for key in sorted(_required_values(source_text)):
        if not values.get(key):
            failures.append(f"missing required environment value: {key}")

    operation_raw = values.get("PRODUCTION_SHADOW_OPERATION_ID", "")
    try:
        operation = uuid.UUID(operation_raw)
    except (ValueError, AttributeError):
        operation = None
    if (
        operation is None
        or operation.version != 4
        or str(operation) != operation_raw
    ):
        failures.append("PRODUCTION_SHADOW_OPERATION_ID must be a canonical lowercase UUIDv4")

    release_sha = values.get("PRODUCTION_SHADOW_RELEASE_SHA", "")
    if not SHA_RE.fullmatch(release_sha):
        failures.append("PRODUCTION_SHADOW_RELEASE_SHA must be exactly 40 lowercase hex")

    if operation is not None:
        expected_project = f"tb3p-{operation.hex}"
        if values.get("PRODUCTION_SHADOW_PROJECT") != expected_project:
            failures.append(f"PRODUCTION_SHADOW_PROJECT must be exactly {expected_project}")
        if values.get("PRODUCTION_SHADOW_CGROUP_PARENT") != expected_project:
            failures.append(f"PRODUCTION_SHADOW_CGROUP_PARENT must be exactly {expected_project}")
        expected_prefix = f"production-shadow/{operation}/blobs"
        if values.get("DR_BLOB_OBJECT_PREFIX") != expected_prefix:
            failures.append(f"DR_BLOB_OBJECT_PREFIX must be exactly {expected_prefix}")
        expected_project_root = f"{PROJECT_ROOT_PREFIX}/{operation}"
        if values.get("PRODUCTION_SHADOW_PROJECT_ROOT") != expected_project_root:
            failures.append(
                f"PRODUCTION_SHADOW_PROJECT_ROOT must be exactly {expected_project_root}"
            )
        expected_data_root = f"{DATA_ROOT_PREFIX}/{operation}"
        if values.get("PRODUCTION_SHADOW_DATA_ROOT") != expected_data_root:
            failures.append(
                f"PRODUCTION_SHADOW_DATA_ROOT must be exactly {expected_data_root}"
            )
        expected_secret_root = f"{SECRET_ROOT_PREFIX}/{operation}"
        if values.get("PRODUCTION_SHADOW_SECRET_ROOT") != expected_secret_root:
            failures.append(
                f"PRODUCTION_SHADOW_SECRET_ROOT must be exactly {expected_secret_root}"
            )
        if release_sha and values.get("PRODUCTION_SHADOW_RELEASE_ROOT") != (
            f"{expected_project_root}/releases/{release_sha}"
        ):
            failures.append(
                "PRODUCTION_SHADOW_RELEASE_ROOT must bind the operation and exact release SHA"
            )

    image_ids: list[str] = []
    for key in IMAGE_KEYS:
        value = values.get(key, "")
        if not IMAGE_ID_RE.fullmatch(value):
            failures.append(f"{key} must be a sha256:<64 lowercase hex> local image ID")
        else:
            image_ids.append(value)
    if len(image_ids) == len(IMAGE_KEYS) and len(set(image_ids)) != len(image_ids):
        failures.append("every image class must use a distinct immutable image ID")

    ports: list[int] = []
    for key in PORT_KEYS:
        raw = values.get(key, "")
        try:
            port = int(raw)
        except ValueError:
            port = 0
        if port < 1024 or port > 65535 or port in BANNED_HOST_PORTS:
            failures.append(f"{key} must be a nonlegacy unprivileged host port")
        else:
            ports.append(port)
    if len(ports) == len(PORT_KEYS) and len(set(ports)) != len(ports):
        failures.append("all shadow host ports must be distinct")

    for key, expected_address in EXPECTED_DR_ADDRESSES.items():
        if values.get(key) != expected_address:
            failures.append(
                f"{key} must match the canonical production topology address"
            )

    for key in (
        "BOT_FI_PUBLIC_WEBAPP_URL",
        "WEBAPP_FI_PUBLIC_WEBAPP_URL",
        "WEBAPP_IR_PUBLIC_WEBAPP_URL",
    ):
        value = values.get(key, "")
        if not _valid_https_url(value) or "staging" in value.lower():
            failures.append(f"{key} must be a production HTTPS URL")
    if (
        values.get("DR_BLOB_OBJECT_ENDPOINT") != ARVAN_BLOB_ENDPOINT
        or values.get("DR_BLOB_OBJECT_REGION") != ARVAN_BLOB_REGION
        or values.get("DR_BLOB_OBJECT_BUCKET") != ARVAN_BLOB_BUCKET
    ):
        failures.append(
            "DR blob storage must use the exact reviewed private/versioned "
            "Arvan endpoint, region, and bucket"
        )
    for digest_key, epoch_key, label in (
        (
            "DR_BLOB_POLICY_ATTESTATION_SHA256",
            "DR_BLOB_POLICY_ATTESTED_AT_EPOCH",
            "DR blob private/versioning readback",
        ),
        (
            "DR_BLOB_COMPATIBILITY_ATTESTATION_SHA256",
            "DR_BLOB_COMPATIBILITY_ATTESTED_AT_EPOCH",
            "cross-site blob keyring/version round-trip",
        ),
        (
            "PRODUCTION_SHADOW_DR_TLS_ATTESTATION_SHA256",
            "PRODUCTION_SHADOW_DR_TLS_ATTESTED_AT_EPOCH",
            "three-site DR TLS certificate and handshake",
        ),
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", values.get(digest_key, "")):
            failures.append(f"{digest_key} must bind {label} evidence")
        try:
            attested_at = int(values.get(epoch_key, ""))
        except ValueError:
            attested_at = 0
        if abs(int(time.time()) - attested_at) > 300:
            failures.append(f"{label} evidence must be at most 300 seconds old")

    background_values = {
        "bot_fi": values.get("BOT_FI_BACKGROUND_JOBS_ENABLED", "").lower(),
        "webapp_fi": values.get(
            "WEBAPP_FI_BACKGROUND_JOBS_ENABLED", ""
        ).lower(),
        "webapp_ir": values.get(
            "WEBAPP_IR_BACKGROUND_JOBS_ENABLED", ""
        ).lower(),
    }
    if background_values["bot_fi"] != "true":
        failures.append(
            "BOT_FI_BACKGROUND_JOBS_ENABLED must be true for the public API"
        )
    for role in ("webapp_fi", "webapp_ir"):
        if background_values[role] != "true":
            failures.append(
                f"{role.upper()}_BACKGROUND_JOBS_ENABLED must be true only "
                "for the post-commit API profile"
            )

    for key in ("BOT_FI_API_WORKERS", "WEBAPP_FI_API_WORKERS", "WEBAPP_IR_API_WORKERS"):
        try:
            workers = int(values.get(key, ""))
        except ValueError:
            workers = 0
        if workers < 1 or workers > 16:
            failures.append(f"{key} must be an integer from 1 through 16")

    if (
        values.get("TELEGRAM_DELIVERY_PRODUCER_MODE") != "queue-v1"
        or values.get("TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER") != "queue-v1"
    ):
        failures.append(
            "three-site production must use the queue-v1 Telegram producer contract"
        )
    if not re.fullmatch(
        r"[0-9a-f]{64}",
        values.get("TELEGRAM_WEBAPP_VALIDATION_KEY", ""),
    ):
        failures.append(
            "TELEGRAM_WEBAPP_VALIDATION_KEY must be a 64-character "
            "lowercase hex derived key"
        )

    expected_provider_hash = _provider_config_sha256(values)
    if values.get("WEBAPP_PROVIDER_CONFIG_SHA256") != expected_provider_hash:
        failures.append(
            "WEBAPP_PROVIDER_CONFIG_SHA256 does not match the required "
            "production provider fields"
        )

    witness_url = values.get("PRODUCTION_SHADOW_WITNESS_URL", "")
    if witness_url.rstrip("/") != "https://37.152.191.11":
        failures.append(
            "PRODUCTION_SHADOW_WITNESS_URL must bind the canonical external "
            "Witness IP SAN on TLS port 443"
        )
    if values.get("PRODUCTION_SHADOW_WITNESS_IP") != "37.152.191.11":
        failures.append(
            "PRODUCTION_SHADOW_WITNESS_IP must match the pinned canonical Witness"
        )
    if (
        values.get("PRODUCTION_SHADOW_WITNESS_TLS_SAN")
        != "IP:37.152.191.11"
    ):
        failures.append(
            "PRODUCTION_SHADOW_WITNESS_TLS_SAN must match the canonical Witness name"
        )
    if values.get("PRODUCTION_SHADOW_WITNESS_RELEASE_SHA") != release_sha:
        failures.append(
            "canonical Witness release must match PRODUCTION_SHADOW_RELEASE_SHA"
        )
    for key in (
        "PRODUCTION_SHADOW_DR_CA_SHA256",
        "PRODUCTION_SHADOW_WITNESS_CA_SHA256",
        "PRODUCTION_SHADOW_WITNESS_SERVER_CERT_SHA256",
        "PRODUCTION_SHADOW_WITNESS_RELEASE_MANIFEST_SHA256",
        "PRODUCTION_SHADOW_WITNESS_HEALTH_ATTESTATION_SHA256",
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", values.get(key, "")):
            failures.append(f"{key} must be exactly 64 lowercase hex")
    try:
        witness_attested_at = int(
            values.get("PRODUCTION_SHADOW_WITNESS_HEALTH_ATTESTED_AT_EPOCH", "")
        )
    except ValueError:
        witness_attested_at = 0
    if abs(int(time.time()) - witness_attested_at) > 300:
        failures.append(
            "canonical Witness health attestation must be at most 300 seconds old"
        )
    try:
        witness_public_key = base64.b64decode(
            values.get("WRITER_WITNESS_PUBLIC_KEY", ""),
            validate=True,
        )
    except (ValueError, base64.binascii.Error):
        witness_public_key = b""
    if len(witness_public_key) != 32:
        failures.append(
            "WRITER_WITNESS_PUBLIC_KEY must be a 32-byte base64 Ed25519 key"
        )
    witness_key_ids = [
        values.get("WEBAPP_FI_WITNESS_KEY_ID", ""),
        values.get("WEBAPP_IR_WITNESS_KEY_ID", ""),
    ]
    if (
        any(not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", item) for item in witness_key_ids)
        or len(set(witness_key_ids)) != 2
    ):
        failures.append(
            "WebApp Witness client key IDs must be distinct bounded identifiers"
        )
    for key in ("WEBAPP_FI_WITNESS_SECRET", "WEBAPP_IR_WITNESS_SECRET"):
        if len(values.get(key, "").encode("utf-8")) < 32:
            failures.append(f"{key} must contain at least 32 bytes")

    expected_peer_urls = {
        "bot_fi": {
            "webapp_fi": (
                "https://webapp-fi-dr.production.internal:"
                f"{values.get('WEBAPP_FI_SHADOW_DR_PORT', '')}"
            ),
        },
        "webapp_fi": {
            "bot_fi": (
                "https://bot-fi-dr.production.internal:"
                f"{values.get('BOT_FI_SHADOW_DR_PORT', '')}"
            ),
            "webapp_ir": (
                "https://webapp-ir-dr.production.internal:"
                f"{values.get('WEBAPP_IR_SHADOW_DR_PORT', '')}"
            ),
        },
        "webapp_ir": {
            "webapp_fi": (
                "https://webapp-fi-dr.production.internal:"
                f"{values.get('WEBAPP_FI_SHADOW_DR_PORT', '')}"
            ),
        },
    }
    peer_entries: dict[str, dict[str, str]] = {}
    for role in ("bot_fi", "webapp_fi", "webapp_ir"):
        key = f"{role.upper()}_DR_PEERS_JSON"
        payload = _parse_peer_entries(
            values.get(key, ""),
            local_site=ROLE_PHYSICAL_SITES[role],
        )
        if payload is None:
            failures.append(
                f"{key} must pass the real runtime sparse-topology list parser"
            )
            continue
        peer_entries[role] = payload
        if set(payload) != EXPECTED_DR_PEER_SITES[role]:
            failures.append(f"{key} must contain the exact fixed peer site set")
        if payload != expected_peer_urls[role]:
            failures.append(
                f"{key} must bind the exact operation DR peer origins and ports"
            )

    pairwise_entries: dict[str, dict[tuple[str, str], PairwiseDrKey]] = {}
    for role in ("bot_fi", "webapp_fi", "webapp_ir"):
        key = f"{role.upper()}_DR_PAIRWISE_KEYS_JSON"
        payload = _parse_pairwise_entries(values.get(key, ""))
        if payload is None:
            failures.append(
                f"{key} must pass the real runtime pairwise-key list parser"
            )
            continue
        pairwise_entries[role] = payload
        if set(payload) != EXPECTED_DR_DIRECTED_PAIRS[role]:
            failures.append(
                f"{key} must contain exactly one key for every required directed pair"
            )
    if set(pairwise_entries) == {"bot_fi", "webapp_fi", "webapp_ir"}:
        for left, right in (
            ("bot_fi", "webapp_fi"),
            ("webapp_fi", "webapp_ir"),
        ):
            overlap = (
                EXPECTED_DR_DIRECTED_PAIRS[left]
                & EXPECTED_DR_DIRECTED_PAIRS[right]
            )
            if any(
                pairwise_entries[left].get(pair)
                != pairwise_entries[right].get(pair)
                for pair in overlap
            ):
                failures.append(
                    f"{left} and {right} must share identical keys for "
                    "their overlapping directed pairs"
                )

    return failures


def verify_contract(
    *,
    document: Mapping[str, Any],
    source_text: str,
    values: Mapping[str, str],
) -> dict[str, Any]:
    failures = collect_source_failures(document, source_text)
    failures.extend(collect_environment_failures(values, source_text))
    if failures:
        raise ProductionShadowComposeError("\n".join(dict.fromkeys(failures)))
    operation = uuid.UUID(values["PRODUCTION_SHADOW_OPERATION_ID"])
    return {
        "status": "passed",
        "operation_id": str(operation),
        "release_sha": values["PRODUCTION_SHADOW_RELEASE_SHA"],
        "project": values["PRODUCTION_SHADOW_PROJECT"],
        "topology_scope": "three-product-sites-with-external-canonical-witness",
        "full_product_topology": True,
        "witness_mode": "external-canonical-attestation-values-bound-only",
        "activation_status": (
            "blocked_until_exact-release-role-artifacts-route-transaction-and-commit"
        ),
        "service_count": len(document["services"]),
        "profile_count": len(
            {
                profile
                for service in document["services"].values()
                for profile in service["profiles"]
            }
        ),
        "image_ids": {key: values[key] for key in IMAGE_KEYS},
    }


def run_compose_config(
    *,
    compose_path: Path,
    env_file: Path,
    values: Mapping[str, str],
    resolve_service_env_files: bool = True,
) -> None:
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        **values,
    }
    command = [
        "/usr/bin/docker",
        "compose",
        "--env-file",
        str(env_file),
        "--file",
        str(compose_path),
        "--profile",
        "*",
        "config",
    ]
    if not resolve_service_env_files:
        command.append("--no-env-resolution")
    command.append("--quiet")
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
        env=environment,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        raise ProductionShadowComposeError(
            "docker compose config rejected the manifest"
            + (f": {detail[-1]}" if detail else "")
        )


def inspect_local_images(values: Mapping[str, str]) -> None:
    for key in IMAGE_KEYS:
        image_id = values[key]
        completed = subprocess.run(
            ["/usr/bin/docker", "image", "inspect", image_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=15,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/root",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = None
        if (
            completed.returncode != 0
            or not isinstance(payload, list)
            or len(payload) != 1
            or payload[0].get("Id") != image_id
        ):
            raise ProductionShadowComposeError(
                f"{key} is not present locally under its exact immutable ID"
            )
        if key in {
            "PRODUCTION_SHADOW_APP_IMAGE_ID",
            "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID",
        }:
            labels = payload[0].get("Config", {}).get("Labels") or {}
            if labels.get("org.opencontainers.image.revision") != values.get(
                "PRODUCTION_SHADOW_RELEASE_SHA"
            ):
                raise ProductionShadowComposeError(
                    f"{key} release label does not match PRODUCTION_SHADOW_RELEASE_SHA"
                )


def _read_production_env(path: Path) -> dict[str, str]:
    text = read_secure_text(path, label="production shadow compose environment")
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise SecureFileError(
            "production shadow compose environment must be root-owned mode 0600"
        )
    return parse_env_text(text)


def _read_runtime_env(path: Path, *, label: str) -> dict[str, str]:
    text = read_secure_text(path, label=label)
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise SecureFileError(
            f"{label} must be root-owned mode 0600"
        )
    return parse_env_text(text)


def validate_api_runtime_envs(values: Mapping[str, str]) -> None:
    secret_root = Path(values.get("PRODUCTION_SHADOW_SECRET_ROOT", ""))
    validation_keys: dict[str, str] = {}
    for role, relative_path in ROLE_API_ENV_FILES.items():
        path = secret_root / relative_path
        runtime = _read_runtime_env(
            path,
            label=f"{role} production API environment",
        )
        is_bot = role == "bot_fi"
        expected = {
            "SERVER_MODE": "foreign" if is_bot else "iran",
            "LOGICAL_AUTHORITY": "foreign" if is_bot else "webapp",
            "PHYSICAL_SITE": ROLE_PHYSICAL_SITES[role],
            "RELEASE_SHA": values.get("PRODUCTION_SHADOW_RELEASE_SHA", ""),
            "JWT_SECRET_KEY": values.get(
                "BOT_FI_JWT_SECRET_KEY" if is_bot else "WEBAPP_JWT_SECRET_KEY",
                "",
            ),
            "TELEGRAM_DELIVERY_PRODUCER_MODE": values.get(
                "TELEGRAM_DELIVERY_PRODUCER_MODE", ""
            ),
            "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": values.get(
                "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER", ""
            ),
        }
        if not is_bot:
            expected["ORIGIN_READINESS_API_KEY"] = values.get(
                "ORIGIN_READINESS_API_KEY", ""
            )
            expected.update(
                {key: values.get(key, "") for key in WEBAPP_API_PROVIDER_KEYS}
            )
            if "WEB_PUSH_VAPID_PRIVATE_KEY" in runtime:
                raise ProductionShadowComposeError(
                    f"{role} API environment must not receive "
                    "WEB_PUSH_VAPID_PRIVATE_KEY"
                )
        for key, expected_value in expected.items():
            if not expected_value or runtime.get(key) != expected_value:
                raise ProductionShadowComposeError(
                    f"{role} API environment does not preserve required runtime field {key}"
                )
        forbidden = sorted(API_FORBIDDEN_PROVIDER_KEYS & set(runtime))
        if forbidden:
            raise ProductionShadowComposeError(
                f"{role} API environment contains Telegram executor-only fields"
            )
        validation_key = runtime.get("TELEGRAM_WEBAPP_VALIDATION_KEY", "")
        if (
            not re.fullmatch(r"[0-9a-f]{64}", validation_key)
            or validation_key
            != values.get("TELEGRAM_WEBAPP_VALIDATION_KEY", "")
        ):
            raise ProductionShadowComposeError(
                f"{role} API environment lacks a valid derived "
                "TELEGRAM_WEBAPP_VALIDATION_KEY"
            )
        validation_keys[role] = validation_key
    if len(set(validation_keys.values())) != 1:
        raise ProductionShadowComposeError(
            "all three API environments must use the same derived "
            "TELEGRAM_WEBAPP_VALIDATION_KEY"
        )

    bot_path = secret_root / "bot-fi/runtime.env.bot"
    bot_runtime = _read_runtime_env(
        bot_path,
        label="Bot-FI production Bot environment",
    )
    bot_expected = {
        "SERVER_MODE": "foreign",
        "LOGICAL_AUTHORITY": "foreign",
        "PHYSICAL_SITE": "bot_fi",
        "RELEASE_SHA": values.get("PRODUCTION_SHADOW_RELEASE_SHA", ""),
        "TELEGRAM_DELIVERY_PRODUCER_MODE": "queue-v1",
        "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "queue-v1",
        "TELEGRAM_DELIVERY_EXECUTION_OWNER": "queue-v1",
        "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "true",
        "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "true",
    }
    for key, expected_value in bot_expected.items():
        if bot_runtime.get(key) != expected_value:
            raise ProductionShadowComposeError(
                f"Bot-FI Bot environment does not preserve required runtime field {key}"
            )
    for key in ("BOT_TOKEN", "BOT_USERNAME", "CHANNEL_ID"):
        if not bot_runtime.get(key):
            raise ProductionShadowComposeError(
                f"Bot-FI Bot environment lacks required provider field {key}"
            )
    if "TELEGRAM_WEBAPP_VALIDATION_KEY" in bot_runtime:
        raise ProductionShadowComposeError(
            "Bot-FI Bot environment must not receive TELEGRAM_WEBAPP_VALIDATION_KEY"
        )

    for role_path in ("bot-fi", "webapp-fi", "webapp-ir"):
        for service_scope in ("sync", "migration"):
            path = secret_root / role_path / f"runtime.env.{service_scope}"
            if not path.exists():
                continue
            runtime = _read_runtime_env(
                path,
                label=f"{role_path} production {service_scope} environment",
            )
            if "TELEGRAM_WEBAPP_VALIDATION_KEY" in runtime:
                raise ProductionShadowComposeError(
                    "derived Telegram WebApp key leaked outside API environments"
                )


def validate_ca_material(values: Mapping[str, str]) -> None:
    secret_root = Path(values.get("PRODUCTION_SHADOW_SECRET_ROOT", ""))
    expected = {
        secret_root / "tls/ca.crt": values.get(
            "PRODUCTION_SHADOW_DR_CA_SHA256", ""
        ),
        secret_root / "witness/ca.crt": values.get(
            "PRODUCTION_SHADOW_WITNESS_CA_SHA256", ""
        ),
    }
    for path, expected_sha256 in expected.items():
        observed, _ = sha256_secure_file(
            path,
            label="production shadow CA material",
            max_size=1024 * 1024,
        )
        if observed != expected_sha256:
            raise ProductionShadowComposeError(
                "production shadow CA material hash does not match its bound value"
            )


def validate_pristine_redis_targets(values: Mapping[str, str]) -> None:
    data_root = Path(values.get("PRODUCTION_SHADOW_DATA_ROOT", ""))
    if not data_root.is_absolute() or ".." in data_root.parts:
        raise ProductionShadowComposeError(
            "Redis operation data root must be an absolute normalized path"
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open("/", flags)
    try:
        for component in data_root.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        data_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(data_metadata.st_mode)
            or data_metadata.st_uid != 0
            or stat.S_IMODE(data_metadata.st_mode) != 0o700
            or data_metadata.st_nlink < 2
        ):
            raise ProductionShadowComposeError(
                "Redis operation data root must be a real root-owned "
                "mode-0700 directory"
            )
        for role_path in ("bot-fi", "webapp-fi", "webapp-ir"):
            role_descriptor = -1
            target_descriptor = -1
            try:
                role_descriptor = os.open(
                    role_path,
                    flags,
                    dir_fd=descriptor,
                )
                target_descriptor = os.open(
                    "redis",
                    flags,
                    dir_fd=role_descriptor,
                )
                for candidate in (role_descriptor, target_descriptor):
                    metadata = os.fstat(candidate)
                    if (
                        not stat.S_ISDIR(metadata.st_mode)
                        or metadata.st_uid != 0
                        or stat.S_IMODE(metadata.st_mode) != 0o700
                        or metadata.st_nlink < 2
                    ):
                        raise ProductionShadowComposeError(
                            f"{role_path} Redis operation directory chain "
                            "must use real root-owned mode-0700 directories"
                        )
                before = os.fstat(target_descriptor)
                entries = os.listdir(target_descriptor)
                after = os.fstat(target_descriptor)
                stable_fields = (
                    "st_dev",
                    "st_ino",
                    "st_mode",
                    "st_uid",
                    "st_gid",
                    "st_nlink",
                )
                if any(
                    getattr(before, field) != getattr(after, field)
                    for field in stable_fields
                ):
                    raise ProductionShadowComposeError(
                        f"{role_path} Redis target changed during inspection"
                    )
                if entries:
                    raise ProductionShadowComposeError(
                        f"{role_path} Redis target must be pristine-empty; "
                        "legacy RDB/AOF state is rollback evidence only"
                    )
            finally:
                if target_descriptor >= 0:
                    os.close(target_descriptor)
                if role_descriptor >= 0:
                    os.close(role_descriptor)
    except OSError as exc:
        raise ProductionShadowComposeError(
            "Redis operation directory chain cannot be opened without "
            "following symlinks"
        ) from exc
    finally:
        os.close(descriptor)


def _validate_env_location(path: Path, values: Mapping[str, str]) -> None:
    expected_parent = Path(values.get("PRODUCTION_SHADOW_SECRET_ROOT", ""))
    if path.parent != expected_parent or path.suffix != ".env":
        raise ProductionShadowComposeError(
            f"environment must be an .env file directly below {expected_parent}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", type=Path, default=DEFAULT_COMPOSE)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--check-local-images", action="store_true")
    parser.add_argument(
        "--require-pristine-redis-targets",
        action="store_true",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        source_text, document = load_compose(args.compose)
        values = _read_production_env(args.env_file)
        _validate_env_location(args.env_file, values)
        summary = verify_contract(
            document=document,
            source_text=source_text,
            values=values,
        )
        validate_api_runtime_envs(values)
        validate_ca_material(values)
        if args.require_pristine_redis_targets:
            validate_pristine_redis_targets(values)
        run_compose_config(
            compose_path=args.compose,
            env_file=args.env_file,
            values=values,
        )
        if args.check_local_images:
            inspect_local_images(values)
        summary["compose_sha256"] = hashlib.sha256(
            source_text.encode("utf-8")
        ).hexdigest()
        summary["compose_config"] = "passed"
        summary["api_runtime_envs_checked"] = True
        summary["separate_ca_material_checked"] = True
        summary["local_images_checked"] = bool(args.check_local_images)
        summary["pristine_redis_targets_checked"] = bool(
            args.require_pristine_redis_targets
        )
    except (OSError, SecureFileError, ProductionShadowComposeError, yaml.YAMLError) as exc:
        summary = {
            "status": "failed",
            "error": str(exc),
            "error_class": type(exc).__name__,
        }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"production shadow compose: {summary['status']}")
        if summary["status"] == "failed":
            print(f"FAIL: {summary['error']}")
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
