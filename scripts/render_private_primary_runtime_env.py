#!/usr/bin/env python3
"""Render official old env and PRIVATE_PRIMARY topology sources from live inventory.

Old env is bound to the active Shadow project, feed mode, image, and adopted
data root.  New topology sources keep that same data root and point secrets at
the canonical production secret root.  Secret values are never written.
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
import sys
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.inventory_private_primary_active_runtime import (
        ALLOWED_ADOPTED_DATA_ROOTS,
        CANONICAL_SECRET_ROOT,
        EXPECTED_FEED_MODE,
        EXPECTED_PROJECT,
        INVENTORY_SCHEMA,
        validate_inventory,
    )
    from scripts.prepare_market_pipeline_primary_release import (
        AUTHORIZED_BACKFILL_NOT_BEFORE_UTC,
        AUTHORIZED_BACKFILL_SOURCE_CODES,
    )
    from scripts.provision_private_primary_secrets import SECRET_SPECS
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.inventory_private_primary_active_runtime import (
        ALLOWED_ADOPTED_DATA_ROOTS,
        CANONICAL_SECRET_ROOT,
        EXPECTED_FEED_MODE,
        EXPECTED_PROJECT,
        INVENTORY_SCHEMA,
        validate_inventory,
    )
    from scripts.prepare_market_pipeline_primary_release import (
        AUTHORIZED_BACKFILL_NOT_BEFORE_UTC,
        AUTHORIZED_BACKFILL_SOURCE_CODES,
    )
    from scripts.provision_private_primary_secrets import SECRET_SPECS

AUTHORIZED_BACKFILL_MAX_MESSAGES = "100000"


CONFIRMATION = "render-production-private-primary-runtime-env"
OLD_ENV_SCHEMA = "private_primary_old_env/1.0"
TOPOLOGY_SCHEMA = "private_primary_topology_source/1.0"
NEW_PROJECT = "market-private-pipeline-primary"
ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./:=,@+%-]+$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
OLD_REQUIRED = {
    "bot": (
        "MARKET_PIPELINE_PROJECT_NAME",
        "MARKET_PIPELINE_FEED_MODE",
        "MARKET_PIPELINE_MODE",
        "MARKET_PIPELINE_IMAGE",
        "MARKET_PIPELINE_RELEASE_SHA",
        "MARKET_BOT_DATA_ROOT",
        "MARKET_PRODUCT_SNAPSHOT_ROOT",
        "MARKET_PRIVATE_BIND_IP",
    ),
    "web": (
        "MARKET_PIPELINE_PROJECT_NAME",
        "MARKET_PIPELINE_FEED_MODE",
        "MARKET_PIPELINE_MODE",
        "MARKET_PIPELINE_IMAGE",
        "MARKET_PIPELINE_RELEASE_SHA",
        "MARKET_WEB_DATA_ROOT",
        "MARKET_PRODUCT_SNAPSHOT_ROOT",
        "MARKET_PRIVATE_BIND_IP",
        "MARKET_POSTGRES_USER",
        "MARKET_POSTGRES_DB",
    ),
}


class RenderError(RuntimeError):
    """Stable, secret-free refusal."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _atomic_write(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    parent = path.parent
    if not parent.is_absolute() or parent in {Path("/"), Path("/root"), Path("/srv")}:
        raise RenderError("output_parent_invalid")
    if any(part in str(parent) for part in ("/tmp/", "/var/tmp/")):
        raise RenderError("output_tmp_forbidden")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(parent, 0o700)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RenderError("output_invalid")
    candidate = parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
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
    os.chmod(path, mode)


def _load_inventory(path: Path, *, role: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return validate_inventory(document, role=role)


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not ENV_KEY.fullmatch(key):
            raise RenderError("env_key_invalid")
        if key.endswith(("_TOKEN", "_PASSWORD", "_SECRET")) and value:
            raise RenderError("plaintext_secret_forbidden")
        values[key] = value
    return values


def _encode_env(values: Mapping[str, str]) -> bytes:
    lines = []
    for key in sorted(values):
        value = values[key]
        if not SAFE_VALUE.fullmatch(value):
            raise RenderError("env_value_unsafe")
        lines.append(f"{key}={value}")
    return ("\n".join(lines) + "\n").encode()


def _live_image(inventory: Mapping[str, Any]) -> str:
    safe = inventory.get("safe_env") or {}
    image = str(safe.get("MARKET_PIPELINE_IMAGE") or "")
    if IMAGE_ID.fullmatch(image):
        return image
    for container in inventory.get("containers") or []:
        image_id = str(container.get("image_id") or "")
        if IMAGE_ID.fullmatch(image_id):
            return image_id
    raise RenderError("live_image_missing")


def _live_release(inventory: Mapping[str, Any]) -> str:
    safe = inventory.get("safe_env") or {}
    sha = str(safe.get("MARKET_PIPELINE_RELEASE_SHA") or "")
    if HEX40.fullmatch(sha):
        return sha
    for container in inventory.get("containers") or []:
        candidate = str(container.get("release_sha") or "")
        if HEX40.fullmatch(candidate):
            return candidate
    raise RenderError("live_release_missing")


def render_old_env(*, role: str, inventory: Mapping[str, Any], live_env: Mapping[str, str] | None) -> dict[str, str]:
    adopted = ALLOWED_ADOPTED_DATA_ROOTS[role]
    root_key = "MARKET_WEB_DATA_ROOT" if role == "web" else "MARKET_BOT_DATA_ROOT"
    values: dict[str, str] = dict(live_env or {})
    values["MARKET_PIPELINE_PROJECT_NAME"] = EXPECTED_PROJECT
    values["MARKET_PIPELINE_FEED_MODE"] = EXPECTED_FEED_MODE
    values["MARKET_PIPELINE_MODE"] = str(inventory.get("pipeline_mode") or values.get("MARKET_PIPELINE_MODE") or "live")
    values["MARKET_PIPELINE_IMAGE"] = values.get("MARKET_PIPELINE_IMAGE") or _live_image(inventory)
    if not IMAGE_ID.fullmatch(values["MARKET_PIPELINE_IMAGE"]):
        values["MARKET_PIPELINE_IMAGE"] = _live_image(inventory)
    values["MARKET_PIPELINE_RELEASE_SHA"] = values.get("MARKET_PIPELINE_RELEASE_SHA") or _live_release(inventory)
    if not HEX40.fullmatch(values["MARKET_PIPELINE_RELEASE_SHA"]):
        values["MARKET_PIPELINE_RELEASE_SHA"] = _live_release(inventory)
    values[root_key] = adopted
    values["MARKET_PRODUCT_SNAPSHOT_ROOT"] = f"{adopted}/snapshots"
    values["MARKET_PRIVATE_BIND_IP"] = str(
        inventory.get("bind_ip") or values.get("MARKET_PRIVATE_BIND_IP") or ""
    )
    if role == "web":
        values.setdefault("MARKET_POSTGRES_USER", "market_data")
        values.setdefault("MARKET_POSTGRES_DB", "market_archive")
    for env_key, filename, _, _ in SECRET_SPECS[role]:
        historical = f"{inventory['historical_secret_root']}/{filename}"
        values[env_key] = historical
    for key in OLD_REQUIRED[role]:
        if not values.get(key):
            raise RenderError(f"old_env_{key}_missing")
    if values["MARKET_PIPELINE_FEED_MODE"] == "PRIVATE_PRIMARY":
        raise RenderError("old_env_feed_is_primary")
    if values["MARKET_PIPELINE_PROJECT_NAME"] == NEW_PROJECT:
        raise RenderError("old_env_project_is_new")
    # Drop unknown unsafe or secret-bearing leftovers.
    cleaned = {}
    for key, value in values.items():
        if key.endswith(("_TOKEN", "_PASSWORD", "_SECRET")):
            continue
        if not SAFE_VALUE.fullmatch(value):
            continue
        cleaned[key] = value
    return cleaned


def render_topology_source(*, role: str, inventory: Mapping[str, Any], live_env: Mapping[str, str] | None) -> dict[str, str]:
    adopted = ALLOWED_ADOPTED_DATA_ROOTS[role]
    root_key = "MARKET_WEB_DATA_ROOT" if role == "web" else "MARKET_BOT_DATA_ROOT"
    values: dict[str, str] = {}
    source = live_env or {}
    for key, value in source.items():
        if key.endswith(("_TOKEN", "_PASSWORD", "_SECRET")):
            continue
        if key.startswith("MARKET_") and SAFE_VALUE.fullmatch(value):
            values[key] = value
    values[root_key] = adopted
    values["MARKET_PRODUCT_SNAPSHOT_ROOT"] = f"{adopted}/snapshots"
    values["MARKET_PRIVATE_BIND_IP"] = str(
        inventory.get("bind_ip") or values.get("MARKET_PRIVATE_BIND_IP") or ""
    )
    if role == "bot":
        values["MARKET_BOT_PRIVATE_IP"] = values.get("MARKET_BOT_PRIVATE_IP") or "10.240.1.10"
        values["MARKET_WEB_PRIVATE_IP"] = values.get("MARKET_WEB_PRIVATE_IP") or "10.240.1.20"
    else:
        values["MARKET_WEB_PRIVATE_IP"] = values.get("MARKET_WEB_PRIVATE_IP") or "10.240.1.20"
        values["MARKET_BOT_PRIVATE_IP"] = values.get("MARKET_BOT_PRIVATE_IP") or "10.240.1.10"
        values.setdefault("MARKET_POSTGRES_USER", "market_data")
        values.setdefault("MARKET_POSTGRES_DB", "market_archive")
        values["MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC"] = AUTHORIZED_BACKFILL_NOT_BEFORE_UTC
        values["MARKET_CAPTURE_BACKFILL_SOURCE_CODES"] = AUTHORIZED_BACKFILL_SOURCE_CODES
        values["MARKET_CAPTURE_BACKFILL_MAX_MESSAGES"] = AUTHORIZED_BACKFILL_MAX_MESSAGES
    for env_key, filename, _, _ in SECRET_SPECS[role]:
        values[env_key] = f"{CANONICAL_SECRET_ROOT}/{filename}"
    values.pop("MARKET_PIPELINE_PROJECT_NAME", None)
    values.pop("MARKET_PIPELINE_FEED_MODE", None)
    values.pop("MARKET_PIPELINE_MODE", None)
    values.pop("MARKET_PIPELINE_IMAGE", None)
    values.pop("MARKET_PIPELINE_RELEASE_SHA", None)
    values.pop("MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY", None)
    values.pop("MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE", None)
    return values


def render(
    *,
    role: str,
    inventory_path: Path,
    live_env_path: Path | None,
    old_env_path: Path,
    topology_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    inventory = _load_inventory(inventory_path, role=role)
    live_env = _parse_env(live_env_path) if live_env_path is not None else dict(inventory.get("safe_env") or {})
    old_values = render_old_env(role=role, inventory=inventory, live_env=live_env)
    topology_values = render_topology_source(role=role, inventory=inventory, live_env=live_env)
    old_bytes = _encode_env(old_values)
    topology_bytes = _encode_env(topology_values)
    _atomic_write(old_env_path, old_bytes)
    _atomic_write(topology_path, topology_bytes)
    payload = {
        "schema": OLD_ENV_SCHEMA,
        "environment": "production",
        "status": "PASS",
        "role": role,
        "project_name": old_values["MARKET_PIPELINE_PROJECT_NAME"],
        "feed_mode": old_values["MARKET_PIPELINE_FEED_MODE"],
        "adopted_data_root": ALLOWED_ADOPTED_DATA_ROOTS[role],
        "old_env_path": str(old_env_path),
        "old_env_sha256": sha256(old_bytes).hexdigest(),
        "topology_source_path": str(topology_path),
        "topology_source_sha256": sha256(topology_bytes).hexdigest(),
        "runtime_inventory_schema": INVENTORY_SCHEMA,
        "runtime_mount_identity_sha256": inventory["mount_identity_sha256"],
        "new_project_name": NEW_PROJECT,
        "data_root_matches_new": True,
        "secrets_disclosed": False,
        "rendered_at_utc": _now(),
    }
    _atomic_write(receipt_path, _canonical(payload))
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--role", choices=("bot", "web"), required=True)
    parser.add_argument("--runtime-inventory", type=Path, required=True)
    parser.add_argument("--live-env", type=Path)
    parser.add_argument("--old-env", type=Path, required=True)
    parser.add_argument("--topology-source", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.confirm != CONFIRMATION:
            raise RenderError("confirmation_invalid")
        payload = render(
            role=args.role,
            inventory_path=args.runtime_inventory,
            live_env_path=args.live_env,
            old_env_path=args.old_env,
            topology_path=args.topology_source,
            receipt_path=args.receipt,
        )
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "role": args.role,
                    "old_env_sha256": payload["old_env_sha256"],
                    "topology_source_sha256": payload["topology_source_sha256"],
                    "secrets_disclosed": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError, RenderError) as exc:
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
