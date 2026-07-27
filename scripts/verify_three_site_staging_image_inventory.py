#!/usr/bin/env python3
"""Attest exact local image IDs/digests for one signed staging role bundle."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from core.secure_file_io import write_secure_new_bytes
from core.three_site_full_matrix_campaign import secure_json
from scripts.verify_three_site_staging_role_bundle import (
    _verify_bundle_source,
    verify_role_bundle,
)


DOCKER = "/usr/bin/docker"
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CONTENT_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REPOSITORY_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|[-]+)[a-z0-9]+)*"
REPO_DIGEST_RE = re.compile(
    rf"^(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]+)?/)?"
    rf"{REPOSITORY_COMPONENT}(?:/{REPOSITORY_COMPONENT})*"
    r"@sha256:[0-9a-f]{64}$"
)
LOCAL_RELEASE_IMAGE_PREFIXES = (
    "trading_bot_three_site_staging:",
    "trading_bot_postgres_boottime:",
)


class ImageInventoryError(RuntimeError):
    pass


def _canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def image_content_descriptor(raw: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Return a storage-driver-independent identity for one inspected image.

    Docker's ``Id`` is the config digest with the legacy image store but the
    manifest digest with the containerd image store.  The immutable runtime
    configuration and ordered rootfs diff IDs remain identical after an exact
    save/load, so bind the inventory to those canonical values instead.
    """
    config = raw.get("Config")
    rootfs = raw.get("RootFS")
    if not isinstance(config, dict) or not isinstance(rootfs, dict):
        raise ImageInventoryError("Docker image lacks canonical config/rootfs metadata")
    layers = rootfs.get("Layers")
    descriptor = {
        "architecture": str(raw.get("Architecture") or ""),
        "os": str(raw.get("Os") or ""),
        "created": str(raw.get("Created") or ""),
        "config_sha256": _canonical_sha256(config),
        "rootfs_type": str(rootfs.get("Type") or ""),
        "rootfs_layers": list(layers) if isinstance(layers, list) else layers,
    }
    return descriptor, _verify_content_descriptor(descriptor)


def _verify_content_descriptor(descriptor: Any) -> str:
    fields = {
        "architecture", "os", "created", "config_sha256", "rootfs_type",
        "rootfs_layers",
    }
    if not isinstance(descriptor, dict) or set(descriptor) != fields:
        raise ImageInventoryError("image content descriptor fields are invalid")
    layers = descriptor["rootfs_layers"]
    if (
        not descriptor["architecture"]
        or not descriptor["os"]
        or not descriptor["created"]
        or not CONTENT_HASH_RE.fullmatch(str(descriptor["config_sha256"]))
        or descriptor["rootfs_type"] != "layers"
        or not isinstance(layers, list)
        or not layers
        or any(not CONTENT_HASH_RE.fullmatch(str(layer)) for layer in layers)
    ):
        raise ImageInventoryError("image content descriptor is malformed")
    return _canonical_sha256(descriptor)


def _run(
    arguments: list[str],
    *,
    timeout: int = 60,
    pass_fds: tuple[int, ...] = (),
) -> str:
    try:
        result = subprocess.run(
            arguments,
            text=True,
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=SAFE_ENV,
            pass_fds=pass_fds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ImageInventoryError(f"image inventory command unavailable: {arguments[0]}") from exc
    if result.returncode != 0:
        raise ImageInventoryError(f"image inventory command failed: {Path(arguments[0]).name}")
    return result.stdout.strip()


def _sealed_memfd(payload: bytes, *, label: str) -> int:
    """Return a read-only sealed descriptor containing the already-verified bytes."""
    if not isinstance(payload, bytes) or not payload:
        raise ImageInventoryError(f"{label} bytes are empty")
    try:
        descriptor = os.memfd_create(
            f"three-site-{label}",
            flags=getattr(os, "MFD_CLOEXEC", 0) | getattr(os, "MFD_ALLOW_SEALING", 0),
        )
    except (AttributeError, OSError) as exc:
        raise ImageInventoryError("sealed in-memory Compose inputs are unavailable") from exc
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise ImageInventoryError(f"{label} memfd write made no progress")
            offset += written
        os.fchmod(descriptor, 0o600)
        seals = (
            getattr(fcntl, "F_SEAL_SEAL", 0)
            | getattr(fcntl, "F_SEAL_SHRINK", 0)
            | getattr(fcntl, "F_SEAL_GROW", 0)
            | getattr(fcntl, "F_SEAL_WRITE", 0)
        )
        if not seals or not hasattr(fcntl, "F_ADD_SEALS"):
            raise ImageInventoryError("kernel memfd sealing constants are unavailable")
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, seals)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def verify_image_document(
    document: dict[str, Any],
    *,
    role: str,
    campaign_id: str,
    release_sha: str,
    role_compose_sha256: str,
    role_env_sha256: str,
) -> dict[str, Any]:
    fields = {
        "schema", "campaign_id", "release_sha", "role", "observed_at",
        "role_compose_sha256", "role_env_sha256", "images",
    }
    if (
        not isinstance(document, dict)
        or set(document) != fields
        or document["schema"] != "three-site-staging-image-inventory-v2"
        or document["campaign_id"] != campaign_id
        or document["release_sha"] != release_sha
        or document["role"] != role
        or document["role_compose_sha256"] != role_compose_sha256
        or document["role_env_sha256"] != role_env_sha256
    ):
        raise ImageInventoryError("image inventory identity/bundle hash is invalid")
    try:
        observed = datetime.fromisoformat(str(document["observed_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ImageInventoryError("image inventory timestamp is invalid") from exc
    if observed.tzinfo is None:
        raise ImageInventoryError("image inventory timestamp lacks timezone")
    images = document["images"]
    if not isinstance(images, list) or len(images) < 2:
        raise ImageInventoryError("image inventory is incomplete")
    references: set[str] = set()
    ids: dict[str, str] = {}
    content_identities: dict[str, str] = {}
    for item in images:
        if not isinstance(item, dict) or set(item) != {
            "reference", "image_id", "repo_digests", "release_label",
            "content_descriptor", "content_identity",
        }:
            raise ImageInventoryError("image inventory entry fields are invalid")
        reference = str(item["reference"])
        image_id = str(item["image_id"])
        digests = item["repo_digests"]
        content_identity = _verify_content_descriptor(item["content_descriptor"])
        if item["content_identity"] != content_identity:
            raise ImageInventoryError(
                "image content_identity differs from its canonical descriptor"
            )
        if (
            not reference
            or reference in references
            or not IMAGE_ID_RE.fullmatch(image_id)
            or not isinstance(digests, list)
            or any(
                not isinstance(value, str)
                or REPO_DIGEST_RE.fullmatch(value) is None
                for value in digests
            )
            or len(set(digests)) != len(digests)
        ):
            raise ImageInventoryError("image reference/ID/digest is invalid")
        if reference.startswith(LOCAL_RELEASE_IMAGE_PREFIXES):
            if item["release_label"] != release_sha:
                raise ImageInventoryError("locally built image lacks the exact release label")
        elif not digests:
            raise ImageInventoryError("third-party image lacks a pinned repository digest")
        references.add(reference)
        ids[reference] = image_id
        content_identities[reference] = content_identity
    required = {
        f"trading_bot_three_site_staging:{release_sha}",
        f"trading_bot_postgres_boottime:15-{release_sha}",
    }
    if not required.issubset(references):
        raise ImageInventoryError("role lacks exact-release application/PostgreSQL images")
    return {
        "status": "verified",
        "role": role,
        "image_count": len(images),
        "image_ids": ids,
        "content_identities": content_identities,
        "document_sha256": hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def collect_image_document(
    *,
    role: str,
    campaign_id: str,
    release_sha: str,
    role_compose_bytes: bytes,
    env_bytes: bytes,
) -> dict[str, Any]:
    compose_fd = _sealed_memfd(role_compose_bytes, label="role-compose")
    env_fd = _sealed_memfd(env_bytes, label="role-env")
    try:
        references = sorted(
            {
                value
                for value in _run(
                    [
                        DOCKER,
                        "compose",
                        "-p",
                        "three-site-image-inventory-readonly",
                        "--project-directory",
                        str(REPO_ROOT),
                        "-f",
                        f"/proc/self/fd/{compose_fd}",
                        "--env-file",
                        f"/proc/self/fd/{env_fd}",
                        "config",
                        "--images",
                    ],
                    pass_fds=(compose_fd, env_fd),
                ).splitlines()
                if value
            }
        )
    finally:
        os.close(env_fd)
        os.close(compose_fd)
    images = []
    for reference in references:
        try:
            inspected = json.loads(_run([DOCKER, "image", "inspect", reference]))
        except json.JSONDecodeError as exc:
            raise ImageInventoryError("Docker image inspection returned invalid JSON") from exc
        if not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(inspected[0], dict):
            raise ImageInventoryError("Docker image inspection is ambiguous")
        raw = inspected[0]
        config = raw.get("Config") if isinstance(raw.get("Config"), dict) else {}
        labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
        content_descriptor, content_identity = image_content_descriptor(raw)
        images.append(
            {
                "reference": reference,
                "image_id": str(raw.get("Id") or ""),
                "repo_digests": sorted(str(value) for value in (raw.get("RepoDigests") or [])),
                "release_label": labels.get("org.opencontainers.image.revision"),
                "content_descriptor": content_descriptor,
                "content_identity": content_identity,
            }
        )
    return {
        "schema": "three-site-staging-image-inventory-v2",
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "role": role,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "role_compose_sha256": hashlib.sha256(role_compose_bytes).hexdigest(),
        "role_env_sha256": hashlib.sha256(env_bytes).hexdigest(),
        "images": images,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("bot-fi", "webapp-fi", "webapp-ir", "witness"), required=True)
    parser.add_argument("--canonical-compose", type=Path, required=True)
    parser.add_argument("--role-compose", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        inventory = secure_json(args.inventory, label="provisioned inventory")
        inventory_approval = secure_json(
            args.inventory_approval, label="provisioned inventory approval"
        )
        approval_policy = secure_json(
            args.approval_policy, label="human approval policy"
        )
        canonical_compose_bytes = _verify_bundle_source(
            args.canonical_compose, expected_mode=0o644
        )
        role_compose_bytes = _verify_bundle_source(args.role_compose, expected_mode=0o640)
        env_bytes = _verify_bundle_source(args.env_file, expected_mode=0o600)
        role_bundle = verify_role_bundle(
            role=args.role,
            canonical_compose=yaml.safe_load(canonical_compose_bytes),
            role_compose_bytes=role_compose_bytes,
            env_bytes=env_bytes,
            inventory=inventory,
            approval=inventory_approval,
            approval_policy=approval_policy,
            verify_files=True,
            required_inventory_stage="provisioned",
        )
        document = collect_image_document(
            role=args.role,
            campaign_id=str(inventory["campaign_id"]),
            release_sha=role_bundle["release_sha"],
            role_compose_bytes=role_compose_bytes,
            env_bytes=env_bytes,
        )
        result = verify_image_document(
            document,
            role=args.role,
            campaign_id=str(inventory["campaign_id"]),
            release_sha=role_bundle["release_sha"],
            role_compose_sha256=role_bundle["compose_sha256"],
            role_env_sha256=role_bundle["environment_sha256"],
        )
        write_secure_new_bytes(
            args.output,
            (json.dumps(document, sort_keys=True, indent=2) + "\n").encode(),
            label="staging image inventory",
            mode=0o600,
            max_size=16 * 1024 * 1024,
        )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps({**result, "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
