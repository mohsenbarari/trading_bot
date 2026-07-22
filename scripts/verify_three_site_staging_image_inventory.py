#!/usr/bin/env python3
"""Attest exact local image IDs/digests for one signed staging role bundle."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from scripts.render_three_site_staging_role_compose import _atomic_write
from scripts.verify_three_site_staging_inventory import load_inventory
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


class ImageInventoryError(RuntimeError):
    pass


def _run(arguments: list[str], *, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            arguments,
            text=True,
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ImageInventoryError(f"image inventory command unavailable: {arguments[0]}") from exc
    if result.returncode != 0:
        raise ImageInventoryError(f"image inventory command failed: {Path(arguments[0]).name}")
    return result.stdout.strip()


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
        or document["schema"] != "three-site-staging-image-inventory-v1"
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
    for item in images:
        if not isinstance(item, dict) or set(item) != {
            "reference", "image_id", "repo_digests", "release_label"
        }:
            raise ImageInventoryError("image inventory entry fields are invalid")
        reference = str(item["reference"])
        image_id = str(item["image_id"])
        digests = item["repo_digests"]
        if (
            not reference
            or reference in references
            or not IMAGE_ID_RE.fullmatch(image_id)
            or not isinstance(digests, list)
            or any(not isinstance(value, str) or "@sha256:" not in value for value in digests)
            or len(set(digests)) != len(digests)
        ):
            raise ImageInventoryError("image reference/ID/digest is invalid")
        if reference.startswith(("trading_bot_three_site_staging:", "trading_bot_postgres_boottime:")):
            if item["release_label"] != release_sha:
                raise ImageInventoryError("locally built image lacks the exact release label")
        elif not digests:
            raise ImageInventoryError("third-party image lacks a pinned repository digest")
        references.add(reference)
        ids[reference] = image_id
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
        "document_sha256": hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def collect_image_document(
    *,
    role: str,
    campaign_id: str,
    release_sha: str,
    role_compose: Path,
    env_file: Path,
) -> dict[str, Any]:
    references = sorted(
        {
            value for value in _run(
                [
                    DOCKER, "compose", "-f", str(role_compose),
                    "--env-file", str(env_file), "config", "--images",
                ]
            ).splitlines() if value
        }
    )
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
        images.append(
            {
                "reference": reference,
                "image_id": str(raw.get("Id") or ""),
                "repo_digests": sorted(str(value) for value in (raw.get("RepoDigests") or [])),
                "release_label": labels.get("org.opencontainers.image.revision"),
            }
        )
    return {
        "schema": "three-site-staging-image-inventory-v1",
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "role": role,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "role_compose_sha256": hashlib.sha256(role_compose.read_bytes()).hexdigest(),
        "role_env_sha256": hashlib.sha256(env_file.read_bytes()).hexdigest(),
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
    parser.add_argument("--signer-policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        inventory = load_inventory(args.inventory)
        role_compose_bytes = _verify_bundle_source(args.role_compose, expected_mode=0o640)
        env_bytes = _verify_bundle_source(args.env_file, expected_mode=0o600)
        role_bundle = verify_role_bundle(
            role=args.role,
            canonical_compose=yaml.safe_load(args.canonical_compose.read_text(encoding="utf-8")),
            role_compose_bytes=role_compose_bytes,
            env_bytes=env_bytes,
            inventory=inventory,
            approval=load_inventory(args.inventory_approval),
            signer_policy=load_inventory(args.signer_policy),
            verify_files=True,
            required_inventory_stage="provisioned",
        )
        document = collect_image_document(
            role=args.role,
            campaign_id=str(inventory["campaign_id"]),
            release_sha=role_bundle["release_sha"],
            role_compose=args.role_compose,
            env_file=args.env_file,
        )
        result = verify_image_document(
            document,
            role=args.role,
            campaign_id=str(inventory["campaign_id"]),
            release_sha=role_bundle["release_sha"],
            role_compose_sha256=role_bundle["compose_sha256"],
            role_env_sha256=role_bundle["environment_sha256"],
        )
        _atomic_write(
            args.output,
            (json.dumps(document, sort_keys=True, indent=2) + "\n").encode(),
            mode=0o600,
        )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps({**result, "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
