#!/usr/bin/env python3
"""Inventory the live production-shadow Market Pipeline without disclosing secrets.

This tool records project, container, mount, and path metadata only.  It never
prints secret values, hashes secret bytes, or mutates a container, volume,
marker, Queue owner, or Product authority.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence


CONFIRMATION = "inventory-production-private-primary-active-runtime"
INVENTORY_SCHEMA = "private_primary_active_runtime_inventory/1.0"
COMBINED_SCHEMA = "private_primary_active_runtime_inventory_pair/1.0"
EXPECTED_PROJECT = "market-private-pipeline-stage13-shadow"
EXPECTED_FEED_MODE = "PRIVATE_SHADOW"
CANONICAL_SECRET_ROOT = "/srv/trading-bot/secure/market-data"
HISTORICAL_SECRET_ROOT = "/srv/trading-bot/secure/agent-access/market-data-staging"
ALLOWED_ADOPTED_DATA_ROOTS = {
    "bot": "/srv/trading-bot/staging-data/coin-intelligence/private-pipeline-shadow",
    "web": "/srv/trading-bot/market-data-staging-shadow",
}
ENV_FILE_LABEL = "com.docker.compose.project.environment_file"
SAFE_ENV_KEYS = {
    "MARKET_BOT_DATA_ROOT",
    "MARKET_BOT_PRIVATE_IP",
    "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY",
    "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE",
    "MARKET_PIPELINE_FEED_MODE",
    "MARKET_PIPELINE_IMAGE",
    "MARKET_PIPELINE_MODE",
    "MARKET_PIPELINE_PROJECT_NAME",
    "MARKET_PIPELINE_RELEASE_SHA",
    "MARKET_POSTGRES_DB",
    "MARKET_POSTGRES_USER",
    "MARKET_PRIVATE_BIND_IP",
    "MARKET_PRODUCT_SNAPSHOT_ROOT",
    "MARKET_WEB_DATA_ROOT",
    "MARKET_WEB_PRIVATE_IP",
}
SECRET_ENV_SUFFIX = "_FILE"
HEX12 = re.compile(r"^[0-9a-f]{12,64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")


class InventoryError(RuntimeError):
    """Stable, secret-free refusal."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _digest_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    parent = path.parent
    if not parent.is_absolute() or parent in {Path("/"), Path("/root"), Path("/srv"), Path("/tmp"), Path("/var/tmp")}:
        raise InventoryError("receipt_parent_invalid")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(parent, 0o700)
    info = parent.lstat()
    if parent.is_symlink() or not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
        raise InventoryError("receipt_parent_invalid")
    if path.exists() or path.is_symlink():
        existing = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
            raise InventoryError("receipt_output_invalid")
    candidate = parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_canonical(payload).decode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, path)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)
    os.chmod(path, 0o600)


def _run(arguments: Sequence[str], *, label: str) -> str:
    result = subprocess.run(
        list(arguments),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise InventoryError(f"{label}_failed")
    return result.stdout


def _parse_env(pairs: Sequence[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in pairs:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        values[key] = value
    return values


def _file_metadata(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError:
        return {
            "path": str(path),
            "present": False,
            "regular_file": False,
            "non_symlink": False,
            "single_link": False,
            "owner_group": None,
            "mode": None,
            "non_empty": False,
        }
    mode = stat.S_IMODE(info.st_mode)
    return {
        "path": str(path),
        "present": True,
        "regular_file": stat.S_ISREG(info.st_mode),
        "non_symlink": not path.is_symlink(),
        "single_link": info.st_nlink == 1,
        "owner_group": f"{info.st_uid}:{info.st_gid}",
        "mode": format(mode, "04o"),
        "non_empty": info.st_size > 0,
    }


def _classify_mount(source: str, destination: str, data_root: str) -> str:
    if source == HISTORICAL_SECRET_ROOT or source.startswith(HISTORICAL_SECRET_ROOT + "/"):
        return "secret"
    if source == CANONICAL_SECRET_ROOT or source.startswith(CANONICAL_SECRET_ROOT + "/"):
        return "secret"
    if data_root and (source == data_root or source.startswith(data_root + "/")):
        relative = source[len(data_root) :].lstrip("/")
        first = relative.split("/", 1)[0] if relative else ""
        if first in {"sessions"}:
            return "session"
        if first in {"capture"}:
            return "capture"
        if first in {"state"}:
            return "state"
        if first in {"snapshots"}:
            return "snapshot"
        if first in {"models"}:
            return "model"
        if first in {"postgres"}:
            return "database"
        if first in {"market-store"}:
            return "market_store"
        return "data"
    return "other"


def _env_file_secret_paths(path_text: str) -> list[dict[str, str]]:
    path = Path(path_text)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.endswith(SECRET_ENV_SUFFIX) and value.startswith("/"):
            rows.append({"env_key": key, "path": value})
    return rows


def inspect_project(*, role: str, project: str) -> dict[str, Any]:
    if role not in ALLOWED_ADOPTED_DATA_ROOTS:
        raise InventoryError("host_role_invalid")
    if project != EXPECTED_PROJECT:
        raise InventoryError("project_name_unexpected")
    expected_root = ALLOWED_ADOPTED_DATA_ROOTS[role]
    raw_ids = _run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        label="docker_ps",
    )
    container_ids = [item.strip() for item in raw_ids.splitlines() if item.strip()]
    if not container_ids:
        raise InventoryError("project_containers_missing")
    containers: list[dict[str, Any]] = []
    mounts: list[dict[str, Any]] = []
    secret_mounts: list[dict[str, Any]] = []
    env_file_secret_paths: list[dict[str, str]] = []
    seen_env_files: set[str] = set()
    safe_env: dict[str, str] = {}
    data_roots: set[str] = set()
    for container_id in container_ids:
        document = json.loads(
            _run(["docker", "inspect", container_id], label="docker_inspect")
        )[0]
        labels = document.get("Config", {}).get("Labels") or {}
        if labels.get("com.docker.compose.project") != project:
            raise InventoryError("container_project_mismatch")
        env_file = str(labels.get(ENV_FILE_LABEL) or "")
        if env_file and env_file not in seen_env_files:
            seen_env_files.add(env_file)
            env_file_secret_paths.extend(_env_file_secret_paths(env_file))
        service = str(labels.get("com.docker.compose.service") or "")
        image_id = str(document.get("Image") or "")
        if not IMAGE_ID.fullmatch(image_id):
            raise InventoryError("container_image_invalid")
        state = document.get("State") or {}
        host_config = document.get("HostConfig") or {}
        restart = host_config.get("RestartPolicy") or {}
        env_values = _parse_env(document.get("Config", {}).get("Env") or [])
        for key in SAFE_ENV_KEYS:
            if key in env_values and key not in safe_env:
                safe_env[key] = env_values[key]
        role_root_key = "MARKET_WEB_DATA_ROOT" if role == "web" else "MARKET_BOT_DATA_ROOT"
        if role_root_key in env_values:
            data_roots.add(env_values[role_root_key])
        release_sha = str(
            labels.get("org.opencontainers.image.revision")
            or env_values.get("MARKET_PIPELINE_RELEASE_SHA")
            or ""
        )
        containers.append(
            {
                "container_id": str(document.get("Id") or container_id),
                "short_id": str(document.get("Id") or container_id)[:12],
                "name": str((document.get("Name") or "").lstrip("/")),
                "service": service,
                "image_id": image_id,
                "release_sha": release_sha if HEX40.fullmatch(release_sha) else None,
                "restart_policy": str(restart.get("Name") or ""),
                "running": bool(state.get("Running")),
                "status": str(state.get("Status") or ""),
                "health": str((state.get("Health") or {}).get("Status") or ""),
            }
        )
        for mount in document.get("Mounts") or []:
            source = str(mount.get("Source") or "")
            destination = str(mount.get("Destination") or "")
            if not source or not destination:
                continue
            kind = _classify_mount(source, destination, expected_root)
            row = {
                "container_id": str(document.get("Id") or container_id)[:12],
                "service": service,
                "source": source,
                "destination": destination,
                "kind": kind,
                "rw": bool(mount.get("RW")),
            }
            mounts.append(row)
            if kind == "secret":
                metadata = _file_metadata(Path(source))
                secret_mounts.append(
                    {
                        **row,
                        **{key: metadata[key] for key in metadata if key != "path"},
                    }
                )
    if data_roots and data_roots != {expected_root}:
        raise InventoryError("live_data_root_unexpected")
    if not any(item["running"] for item in containers):
        raise InventoryError("no_running_container")
    live_secret_sources = sorted({item["source"] for item in secret_mounts})
    if not live_secret_sources:
        raise InventoryError("secret_mounts_missing")
    for source in live_secret_sources:
        if HISTORICAL_SECRET_ROOT not in source and not source.startswith(CANONICAL_SECRET_ROOT):
            raise InventoryError("secret_mount_source_unexpected")
    mount_identity = _digest_bytes(
        _canonical(
            {
                "project": project,
                "role": role,
                "data_root": expected_root,
                "mounts": sorted(
                    (item["source"], item["destination"], item["kind"])
                    for item in mounts
                ),
            }
        )
    )
    capture_markers = []
    if role == "web":
        for account in ("account1", "account2"):
            marker = Path(expected_root) / "sessions" / account / "authority-container.json"
            capture_markers.append(
                {
                    "account": account,
                    "present": marker.exists() and not marker.is_symlink(),
                    "regular_file": marker.is_file() and not marker.is_symlink(),
                }
            )
    return {
        "schema": INVENTORY_SCHEMA,
        "environment": "production",
        "status": "PASS",
        "host_role": role,
        "project_name": project,
        "feed_mode": safe_env.get("MARKET_PIPELINE_FEED_MODE") or EXPECTED_FEED_MODE,
        "pipeline_mode": safe_env.get("MARKET_PIPELINE_MODE"),
        "historical_path_name": True,
        "production_owned": True,
        "adopted_data_root": expected_root,
        "adopted_snapshot_root": f"{expected_root}/snapshots",
        "historical_secret_root": HISTORICAL_SECRET_ROOT,
        "canonical_secret_root": CANONICAL_SECRET_ROOT,
        "bind_ip": safe_env.get("MARKET_PRIVATE_BIND_IP"),
        "safe_env": {key: safe_env[key] for key in sorted(safe_env)},
        "containers": sorted(containers, key=lambda item: item["service"]),
        "container_ids": sorted(item["container_id"] for item in containers),
        "bind_mounts": sorted(mounts, key=lambda item: (item["service"], item["destination"])),
        "secret_mounts": sorted(secret_mounts, key=lambda item: item["destination"]),
        "env_file_secret_paths": sorted(env_file_secret_paths, key=lambda item: item["env_key"]),
        "capture_authority_markers": capture_markers,
        "mount_identity_sha256": mount_identity,
        "decision": "adopt_live_roots",
        "relocation_required": False,
        "state_copied": False,
        "services_started": False,
        "database_mutated": False,
        "authority_changed": False,
        "capture_owner_changed": False,
        "queue_owner_changed": False,
        "secrets_disclosed": False,
        "inventoried_at_utc": _now(),
    }


def validate_inventory(document: Mapping[str, Any], *, role: str) -> dict[str, Any]:
    if document.get("schema") != INVENTORY_SCHEMA:
        raise InventoryError("inventory_schema_invalid")
    if document.get("status") != "PASS" or document.get("environment") != "production":
        raise InventoryError("inventory_status_invalid")
    if document.get("host_role") != role:
        raise InventoryError("inventory_role_mismatch")
    if document.get("project_name") != EXPECTED_PROJECT:
        raise InventoryError("inventory_project_mismatch")
    if document.get("feed_mode") != EXPECTED_FEED_MODE:
        raise InventoryError("inventory_feed_mode_invalid")
    if document.get("adopted_data_root") != ALLOWED_ADOPTED_DATA_ROOTS[role]:
        raise InventoryError("inventory_data_root_not_adopted")
    if document.get("adopted_snapshot_root") != f"{ALLOWED_ADOPTED_DATA_ROOTS[role]}/snapshots":
        raise InventoryError("inventory_snapshot_root_invalid")
    if document.get("production_owned") is not True or document.get("historical_path_name") is not True:
        raise InventoryError("inventory_ownership_unproven")
    if document.get("decision") != "adopt_live_roots":
        raise InventoryError("inventory_decision_invalid")
    if document.get("relocation_required") is not False:
        raise InventoryError("inventory_relocation_required")
    if document.get("secrets_disclosed") is not False:
        raise InventoryError("inventory_disclosed_secrets")
    container_ids = document.get("container_ids")
    if not isinstance(container_ids, list) or not container_ids:
        raise InventoryError("inventory_containers_missing")
    if any(not HEX12.fullmatch(str(item)) for item in container_ids):
        raise InventoryError("inventory_container_id_invalid")
    if not re.fullmatch(r"^[0-9a-f]{64}$", str(document.get("mount_identity_sha256") or "")):
        raise InventoryError("inventory_mount_identity_invalid")
    return dict(document)


def combine_inventories(*, bot: Mapping[str, Any], web: Mapping[str, Any]) -> dict[str, Any]:
    validate_inventory(bot, role="bot")
    validate_inventory(web, role="web")
    return {
        "schema": COMBINED_SCHEMA,
        "environment": "production",
        "status": "PASS",
        "decision": "adopt_live_roots",
        "relocation_required": False,
        "project_name": EXPECTED_PROJECT,
        "hosts": {
            "bot": {
                "adopted_data_root": bot["adopted_data_root"],
                "adopted_snapshot_root": bot["adopted_snapshot_root"],
                "container_ids": bot["container_ids"],
                "mount_identity_sha256": bot["mount_identity_sha256"],
                "historical_secret_root": bot["historical_secret_root"],
            },
            "web": {
                "adopted_data_root": web["adopted_data_root"],
                "adopted_snapshot_root": web["adopted_snapshot_root"],
                "container_ids": web["container_ids"],
                "mount_identity_sha256": web["mount_identity_sha256"],
                "historical_secret_root": web["historical_secret_root"],
            },
        },
        "state_copied": False,
        "services_started": False,
        "database_mutated": False,
        "authority_changed": False,
        "capture_owner_changed": False,
        "queue_owner_changed": False,
        "secrets_disclosed": False,
        "inventoried_at_utc": _now(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--confirm", required=True)
    inventory.add_argument("--role", choices=("bot", "web"), required=True)
    inventory.add_argument("--project", default=EXPECTED_PROJECT)
    inventory.add_argument("--receipt", type=Path, required=True)
    combine = commands.add_parser("combine")
    combine.add_argument("--confirm", required=True)
    combine.add_argument("--bot-receipt", type=Path, required=True)
    combine.add_argument("--web-receipt", type=Path, required=True)
    combine.add_argument("--receipt", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.confirm != CONFIRMATION:
            raise InventoryError("confirmation_invalid")
        if args.command == "inventory":
            payload = inspect_project(role=args.role, project=args.project)
            _atomic_json(args.receipt, payload)
            result = {
                "status": "PASS",
                "role": args.role,
                "project_name": payload["project_name"],
                "adopted_data_root": payload["adopted_data_root"],
                "container_count": len(payload["container_ids"]),
                "mount_identity_sha256": payload["mount_identity_sha256"],
                "decision": payload["decision"],
                "secrets_disclosed": False,
            }
        else:
            bot = json.loads(args.bot_receipt.read_text(encoding="utf-8"))
            web = json.loads(args.web_receipt.read_text(encoding="utf-8"))
            payload = combine_inventories(bot=bot, web=web)
            _atomic_json(args.receipt, payload)
            result = {
                "status": "PASS",
                "decision": payload["decision"],
                "project_name": payload["project_name"],
                "secrets_disclosed": False,
            }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, InventoryError) as exc:
        print(
            json.dumps(
                {"status": "BLOCKED", "reason": str(exc), "secrets_disclosed": False},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
