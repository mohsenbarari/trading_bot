#!/usr/bin/env python3
"""Verify an SSH-delivered Finland-local seed against signed object evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import sha256_secure_file
from scripts.fetch_three_site_staging_seed import ARTIFACT_FILENAME, _mapping
from scripts.publish_three_site_staging_seed import (
    MAX_ARTIFACT_BYTES,
    _prepare_output,
    _streaming_hash,
)
from scripts.render_three_site_staging_role_compose import _atomic_write
from scripts.run_three_site_staging_source_backup import verify_tar_artifact
from scripts.verify_three_site_staging_inventory import load_inventory
from scripts.verify_three_site_staging_migration_plan import verify_migration_plan


TARGET_ROLES = ("bot_fi", "webapp_fi")
KINDS = ("postgres", "uploads", "audit")
HOST_KEY_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
ROUTE_RE = re.compile(r"^[a-z0-9][a-z0-9._:+/-]{0,255}$")


class DirectSeedError(RuntimeError):
    pass


def confirmation_phrase(campaign_id: str, target_role: str, plan_hash: str) -> str:
    return f"stage-direct-seed:{campaign_id}:{target_role}:{plan_hash}"


def _artifact_mapping(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        kind, separator, raw_path = value.partition("=")
        if not separator or kind not in KINDS or kind in result or not raw_path:
            raise DirectSeedError("--artifact requires unique kind=/path mappings")
        result[kind] = Path(raw_path)
    if set(result) != set(KINDS):
        raise DirectSeedError("direct seed artifact set is incomplete")
    return result


def build_plan(
    *, campaign_id: str, target_role: str, plan_hash: str, transport_route: str
) -> dict[str, Any]:
    return {
        "status": "planned",
        "campaign_id": campaign_id,
        "target_role": target_role,
        "source_role": target_role,
        "transport": "ssh-host-key-pinned",
        "transport_route": transport_route,
        "artifact_count": 3,
        "required_confirmation": confirmation_phrase(
            campaign_id, target_role, plan_hash
        ),
    }


def execute(
    args: argparse.Namespace,
    *,
    verified_plan: dict[str, Any],
    seed_manifests: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    expected = confirmation_phrase(
        verified_plan["campaign_id"], args.target_role, verified_plan["plan_sha256"]
    )
    if args.confirm != expected:
        raise DirectSeedError("direct seed confirmation mismatch")
    if HOST_KEY_RE.fullmatch(args.source_host_key_sha256) is None:
        raise DirectSeedError("direct seed source host-key fingerprint is malformed")
    if ROUTE_RE.fullmatch(args.transport_route) is None:
        raise DirectSeedError("direct seed transport route is malformed")
    artifacts = _artifact_mapping(args.artifact)
    manifest = seed_manifests[args.target_role]
    objects = manifest.get("objects")
    if not isinstance(objects, list) or len(objects) != 3:
        raise DirectSeedError("signed seed manifest object set is incomplete")
    by_kind = {
        str(item.get("kind")): item for item in objects if isinstance(item, dict)
    }
    if set(by_kind) != set(KINDS):
        raise DirectSeedError("signed seed manifest kinds are incomplete")
    _prepare_output(args.output_dir, repo=args.repo.resolve())
    target_objects = []
    transfers = []
    for kind in KINDS:
        source = artifacts[kind]
        item = by_kind[kind]
        source_hash, source_size = sha256_secure_file(
            source,
            label=f"direct {kind} source seed",
            max_size=MAX_ARTIFACT_BYTES,
        )
        if (
            source_hash != item.get("plaintext_sha256")
            or source_size != item.get("plaintext_bytes")
        ):
            raise DirectSeedError(f"direct {kind} bytes differ from signed seed manifest")
        if kind in {"uploads", "audit"}:
            verify_tar_artifact(source)
        destination = args.output_dir / ARTIFACT_FILENAME[kind]
        with source.open("rb") as stream:
            copied_hash, copied_size = _streaming_hash(stream, destination)
        if copied_hash != source_hash or copied_size != source_size:
            destination.unlink(missing_ok=True)
            raise DirectSeedError(f"direct {kind} target copy differs")
        target_objects.append(
            {
                "kind": kind,
                "object_key": item["object_key"],
                "version_id": item["version_id"],
                "ciphertext_sha256": item["ciphertext_sha256"],
                "plaintext_sha256": copied_hash,
                "plaintext_bytes": copied_size,
                "path": str(destination),
            }
        )
        transfers.append(
            {
                "kind": kind,
                "source_path": str(source),
                "target_path": str(destination),
                "plaintext_sha256": copied_hash,
                "plaintext_bytes": copied_size,
                "object_version_id": item["version_id"],
            }
        )
    now = datetime.now(timezone.utc).isoformat()
    target_seed = {
        "schema": "three-site-staging-target-seed-v1",
        "campaign_id": verified_plan["campaign_id"],
        "release_sha": verified_plan["release_sha"],
        "target_role": args.target_role,
        "source_role": args.target_role,
        "mode": "restore",
        "verified_at": now,
        "objects": target_objects,
    }
    transfer = {
        "schema": "three-site-staging-direct-seed-transfer-v1",
        "campaign_id": verified_plan["campaign_id"],
        "release_sha": verified_plan["release_sha"],
        "plan_sha256": verified_plan["plan_sha256"],
        "target_role": args.target_role,
        "source_role": args.target_role,
        "transport": "ssh-host-key-pinned",
        "transport_route": args.transport_route,
        "source_host_key_sha256": args.source_host_key_sha256,
        "verified_at": now,
        "artifacts": transfers,
    }
    _atomic_write(
        args.output_dir / "target-seed.json",
        (json.dumps(target_seed, sort_keys=True, indent=2) + "\n").encode(),
        mode=0o600,
    )
    _atomic_write(
        args.output_dir / "direct-transfer.json",
        (json.dumps(transfer, sort_keys=True, indent=2) + "\n").encode(),
        mode=0o600,
    )
    return {
        "status": "target-seed-verified",
        "campaign_id": verified_plan["campaign_id"],
        "target_role": args.target_role,
        "source_role": args.target_role,
        "transport": "ssh-host-key-pinned",
        "evidence": str(args.output_dir / "target-seed.json"),
        "evidence_sha256": hashlib.sha256(
            json.dumps(target_seed, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "transfer_evidence": str(args.output_dir / "direct-transfer.json"),
        "object_count": 3,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-role", choices=TARGET_ROLES, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-approval", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--freeze-evidence", action="append", type=Path, required=True)
    parser.add_argument("--image-inventory", action="append", required=True)
    parser.add_argument("--backup-manifest", action="append", required=True)
    parser.add_argument("--seed-manifest", action="append", required=True)
    parser.add_argument("--artifact", action="append", required=True)
    parser.add_argument("--transport-route", required=True)
    parser.add_argument("--source-host-key-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        inventory = load_inventory(args.inventory)
        backups = _mapping(
            args.backup_manifest,
            roles=("bot_fi", "webapp_fi"),
            label="--backup-manifest",
        )
        seeds = _mapping(
            args.seed_manifest,
            roles=("bot_fi", "webapp_fi"),
            label="--seed-manifest",
        )
        verified = verify_migration_plan(
            load_inventory(args.plan),
            approval=load_inventory(args.plan_approval),
            inventory=inventory,
            inventory_approval=load_inventory(args.inventory_approval),
            approval_policy=load_inventory(args.approval_policy),
            freeze_evidence=[load_inventory(path) for path in args.freeze_evidence],
            image_inventories=_mapping(
                args.image_inventory,
                roles=("bot_fi", "webapp_fi", "webapp_ir", "witness"),
                label="--image-inventory",
            ),
            backup_manifests=backups,
            seed_manifests=seeds,
        )
        result = build_plan(
            campaign_id=verified["campaign_id"],
            target_role=args.target_role,
            plan_hash=verified["plan_sha256"],
            transport_route=args.transport_route,
        )
        if args.apply:
            result = execute(args, verified_plan=verified, seed_manifests=seeds)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
