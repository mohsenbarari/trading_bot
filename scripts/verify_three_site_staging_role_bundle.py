#!/usr/bin/env python3
"""Verify one host's exact Compose, environment, topology, and secret bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from scripts.render_three_site_staging_role_compose import (
    canonical_role_compose_bytes,
    parse_env_values,
    referenced_environment_names,
    render_role_compose,
)
from scripts.verify_three_site_staging_inventory import (
    load_inventory,
    verify_signed_inventory,
)


EXPECTED_PEERS = {
    "bot-fi": {"webapp_fi"},
    "webapp-fi": {"bot_fi", "webapp_ir"},
    "webapp-ir": {"webapp_fi"},
    "witness": set(),
}
EXPECTED_PEER_URLS = {
    ("bot-fi", "webapp_fi"): "https://webapp-fi-dr.staging.internal:8443",
    ("webapp-fi", "bot_fi"): "https://bot-fi-dr.staging.internal:8443",
    ("webapp-fi", "webapp_ir"): "https://webapp-ir-dr.staging.internal:8443",
    ("webapp-ir", "webapp_fi"): "https://webapp-fi-dr.staging.internal:8443",
}
EXPECTED_WITNESS_URL = "https://witness-dr.staging.internal:8444"
BIND_ENV = {
    "bot-fi": "BOT_FI_DR_BIND_ADDRESS",
    "webapp-fi": "WEBAPP_FI_DR_BIND_ADDRESS",
    "webapp-ir": "WEBAPP_IR_DR_BIND_ADDRESS",
    "witness": "WITNESS_DR_BIND_ADDRESS",
}
PEER_IP_ENV = {
    "bot-fi": {"BOT_FI_PEER_WEBAPP_FI_IP": "webapp_fi"},
    "webapp-fi": {
        "WEBAPP_FI_PEER_BOT_FI_IP": "bot_fi",
        "WEBAPP_FI_PEER_WEBAPP_IR_IP": "webapp_ir",
        "WEBAPP_FI_WITNESS_IP": "witness",
    },
    "webapp-ir": {
        "WEBAPP_IR_PEER_WEBAPP_FI_IP": "webapp_fi",
        "WEBAPP_IR_WITNESS_IP": "witness",
    },
    "witness": {},
}
PHYSICAL_SITE = {
    "bot-fi": "bot_fi",
    "webapp-fi": "webapp_fi",
    "webapp-ir": "webapp_ir",
    "witness": "witness",
}
ENV_PREFIX = {
    "bot-fi": "BOT_FI",
    "webapp-fi": "WEBAPP_FI",
    "webapp-ir": "WEBAPP_IR",
}
REQUIRED_REFERENCE_RE = re.compile(r"(?<!\$)\$\{([A-Z][A-Z0-9_]*):\?")
PRIVATE_FILE_KEYS = frozenset(
    {
        "STAGING_BOT_FI_TLS_KEY",
        "STAGING_WEBAPP_FI_TLS_KEY",
        "STAGING_WEBAPP_IR_TLS_KEY",
        "STAGING_WITNESS_TLS_KEY",
        "STAGING_WITNESS_SIGNING_KEY",
        "STAGING_DR_BLOB_CREDENTIALS_FILE",
        "STAGING_DR_BLOB_ENCRYPTION_KEYRING_FILE",
    }
)
PUBLIC_FILE_KEYS = frozenset(
    {
        "STAGING_DR_CA_CERT",
        "STAGING_BOT_FI_TLS_CERT",
        "STAGING_WEBAPP_FI_TLS_CERT",
        "STAGING_WEBAPP_IR_TLS_CERT",
        "STAGING_WITNESS_TLS_CERT",
    }
)


class RoleBundleError(RuntimeError):
    pass


def _strict_json(raw: str, *, label: str) -> Any:
    def hook(pairs):  # noqa: ANN001
        result = {}
        for key, value in pairs:
            if key in result:
                raise RoleBundleError(f"duplicate {label} field: {key}")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=hook)
    except (json.JSONDecodeError, RoleBundleError) as exc:
        raise RoleBundleError(f"{label} is not strict JSON") from exc


def _verify_transport(values: dict[str, str], *, role: str) -> None:
    if role == "witness":
        return
    site = PHYSICAL_SITE[role]
    prefix = ENV_PREFIX[role]
    expected = EXPECTED_PEERS[role]
    peer_items = _strict_json(values[f"{prefix}_DR_PEERS_JSON"], label="peer URL")
    if not isinstance(peer_items, list):
        raise RoleBundleError("peer URL configuration must be a list")
    peers: set[str] = set()
    for item in peer_items:
        if not isinstance(item, dict) or set(item) != {"site", "base_url"}:
            raise RoleBundleError("peer URL entry fields are invalid")
        peer = str(item["site"])
        url = str(item["base_url"])
        if (
            peer in peers
            or url != EXPECTED_PEER_URLS.get((role, peer))
            or "@" in url
        ):
            raise RoleBundleError("peer URL identity is duplicate or unsafe")
        peers.add(peer)
    if peers != expected:
        raise RoleBundleError("peer URL set differs from the fixed sparse topology")

    key_items = _strict_json(values[f"{prefix}_DR_PAIRWISE_KEYS_JSON"], label="pairwise key")
    if not isinstance(key_items, list):
        raise RoleBundleError("pairwise key configuration must be a list")
    pairs: set[tuple[str, str]] = set()
    key_ids: set[str] = set()
    secrets: set[str] = set()
    for item in key_items:
        if not isinstance(item, dict) or set(item) != {
            "key_id", "source_site", "destination_site", "secret"
        }:
            raise RoleBundleError("pairwise key entry fields are invalid")
        key_id = str(item["key_id"])
        source = str(item["source_site"])
        destination = str(item["destination_site"])
        secret = str(item["secret"])
        pair = (source, destination)
        if (
            not key_id
            or key_id in key_ids
            or pair in pairs
            or site not in pair
            or source == destination
            or len(secret.encode()) < 32
            or secret in secrets
        ):
            raise RoleBundleError("pairwise key identity/secret is invalid or reused")
        key_ids.add(key_id)
        pairs.add(pair)
        secrets.add(secret)
    expected_pairs = {
        pair
        for peer in expected
        for pair in ((site, peer), (peer, site))
    }
    if pairs != expected_pairs:
        raise RoleBundleError("directed pairwise keys differ from the fixed topology")


def _verify_file(path_value: str, *, private: bool) -> None:
    path = Path(path_value)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RoleBundleError(f"required role file is unavailable: {path}") from exc
    mode = stat.S_IMODE(metadata.st_mode)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RoleBundleError(f"required role file is not a regular non-symlink: {path}")
    if private and (mode & 0o077):
        raise RoleBundleError(f"private role file permissions are too broad: {path}")
    if not private and (mode & 0o022):
        raise RoleBundleError(f"public trust file is group/world writable: {path}")
    if metadata.st_size <= 0:
        raise RoleBundleError(f"required role file is empty: {path}")


def _verify_bundle_source(path: Path, *, expected_mode: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RoleBundleError(f"role bundle source is unavailable: {path}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != expected_mode
        or metadata.st_size <= 0
    ):
        raise RoleBundleError(
            f"role bundle source must be a non-linked mode-{expected_mode:04o} file: {path}"
        )
    return path.read_bytes()


def verify_role_bundle(
    *,
    role: str,
    canonical_compose: dict[str, Any],
    role_compose_bytes: bytes,
    env_bytes: bytes,
    inventory: dict[str, Any],
    approval: dict[str, Any],
    signer_policy: dict[str, Any],
    verify_files: bool,
    required_inventory_stage: str = "provisioned",
) -> dict[str, Any]:
    inventory_result = verify_signed_inventory(
        inventory,
        approval=approval,
        signer_policy=signer_policy,
        host_destructive=None,
    )
    if required_inventory_stage not in {"planned", "provisioned"}:
        raise RoleBundleError("role bundle inventory-stage requirement is invalid")
    if inventory_result["inventory_stage"] != required_inventory_stage:
        raise RoleBundleError(
            f"role bundle requires a signed {required_inventory_stage} inventory"
        )
    role_payload = render_role_compose(canonical_compose, role=role)
    expected_compose = canonical_role_compose_bytes(role_payload)
    if role_compose_bytes != expected_compose:
        raise RoleBundleError("role Compose bytes differ from the canonical renderer")
    try:
        values = parse_env_values(env_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise RoleBundleError("role environment is not UTF-8") from exc
    referenced = referenced_environment_names(role_payload)
    if set(values) != set(referenced):
        raise RoleBundleError("role environment is not the exact closed variable set")
    if any("change_me" in value.lower() for value in values.values()):
        raise RoleBundleError("role environment still contains template placeholders")
    required = frozenset(REQUIRED_REFERENCE_RE.findall(expected_compose.decode("utf-8")))
    if any(not values.get(name) for name in required):
        raise RoleBundleError("role environment has an empty required value")
    if values["STAGING_RELEASE_SHA"].lower() != inventory_result["release_sha"]:
        raise RoleBundleError("role environment release SHA differs from signed inventory")
    source_root = Path(values.get("STAGING_SOURCE_ROOT", ""))
    if not source_root.is_absolute() or ".." in source_root.parts:
        raise RoleBundleError("staging source root must be an absolute normalized path")
    if values.get("ORIGIN_EXPECTED_MIGRATION_REVISION") not in {
        None,
        "b986c7d8e0f1",
    }:
        raise RoleBundleError("role environment migration head is not the integration head")
    role_inventory = next(
        (item for item in inventory["roles"] if item["role"] == PHYSICAL_SITE[role]),
        None,
    )
    if (
        role_inventory is None
        or values.get(BIND_ENV[role]) != role_inventory["host_ip"]
    ):
        raise RoleBundleError("role bind address differs from signed host inventory")
    inventory_by_role = {item["role"]: item for item in inventory["roles"]}
    for env_name, peer_role in PEER_IP_ENV[role].items():
        if values.get(env_name) != inventory_by_role[peer_role]["host_ip"]:
            raise RoleBundleError("peer host mapping differs from signed host inventory")
    if role in {"webapp-fi", "webapp-ir"} and (
        values.get("STAGING_WITNESS_URL") != EXPECTED_WITNESS_URL
    ):
        raise RoleBundleError("Writer-Witness URL differs from fixed staging endpoint")
    canonical_url = f"https://{inventory['canonical_domain']}"
    for name in ("FRONTEND_URL", "PUBLIC_WEBAPP_URL"):
        if name in values and values[name] != canonical_url:
            raise RoleBundleError(f"{name} differs from the signed canonical staging domain")
    if role != "witness" and (
        values["TELEGRAM_DELIVERY_PRODUCER_MODE"] != "legacy"
        or values["TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER"] != "legacy"
    ):
        raise RoleBundleError("initial staging migration must retain legacy Telegram ownership")
    if role == "bot-fi" and (
        values["TELEGRAM_DELIVERY_EXECUTION_OWNER"] != "legacy"
        or values["TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED"] != "false"
        or values["TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY"] != "false"
    ):
        raise RoleBundleError("initial staging migration must retain legacy Telegram ownership")
    if role in {"webapp-fi", "webapp-ir"}:
        storage = inventory["object_storage"]
        if (
            values["DR_BLOB_OBJECT_BUCKET"] != storage["bucket"]
            or not values["DR_BLOB_OBJECT_PREFIX"].startswith(storage["prefix"])
            or values["DR_BLOB_REQUIRE_VERSIONING"] != "true"
        ):
            raise RoleBundleError("role Object Storage settings differ from signed inventory")
    _verify_transport(values, role=role)
    database_passwords = {
        value
        for name, value in values.items()
        if name.endswith("_DB_PASSWORD") or name.endswith("_POSTGRES_PASSWORD")
    }
    expected_database_password_count = sum(
        name.endswith("_DB_PASSWORD") or name.endswith("_POSTGRES_PASSWORD")
        for name in values
    )
    if len(database_passwords) != expected_database_password_count:
        raise RoleBundleError("database credentials are reused inside one role bundle")
    if verify_files:
        for name in sorted(PRIVATE_FILE_KEYS & set(values)):
            _verify_file(values[name], private=True)
        for name in sorted(PUBLIC_FILE_KEYS & set(values)):
            _verify_file(values[name], private=False)
    return {
        "status": "verified",
        "role": role,
        "release_sha": inventory_result["release_sha"],
        "inventory_sha256": inventory_result["inventory_sha256"],
        "compose_sha256": hashlib.sha256(role_compose_bytes).hexdigest(),
        "environment_sha256": hashlib.sha256(env_bytes).hexdigest(),
        "environment_variable_count": len(values),
        "file_attestation": verify_files,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=sorted(EXPECTED_PEERS), required=True)
    parser.add_argument("--canonical-compose", type=Path, required=True)
    parser.add_argument("--role-compose", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--signer-policy", type=Path, required=True)
    parser.add_argument("--skip-file-attestation", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = verify_role_bundle(
            role=args.role,
            canonical_compose=yaml.safe_load(
                args.canonical_compose.read_text(encoding="utf-8")
            ),
            role_compose_bytes=_verify_bundle_source(
                args.role_compose, expected_mode=0o640
            ),
            env_bytes=_verify_bundle_source(args.env_file, expected_mode=0o600),
            inventory=load_inventory(args.inventory),
            approval=load_inventory(args.approval),
            signer_policy=load_inventory(args.signer_policy),
            verify_files=not args.skip_file_attestation,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
