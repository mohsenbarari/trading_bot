#!/usr/bin/env python3
"""Build the evidence-bound, short-lived three-site staging migration plan."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.render_three_site_staging_role_compose import _atomic_write
from scripts.run_three_site_staging_source_backup import verify_backup_manifest
from scripts.verify_three_site_staging_image_inventory import verify_image_document
from scripts.verify_three_site_staging_inventory import (
    _canonical_bytes,
    load_inventory,
    verify_approved_inventory,
)
from scripts.verify_three_site_staging_migration_plan import (
    ORDERED_PHASES,
    SOURCE_ROLES,
    TARGET_SEED_MAP,
)


TARGET_ROLES = tuple(TARGET_SEED_MAP)


class MigrationPlanBuildError(RuntimeError):
    pass


def _mapping(values: list[str], *, roles: tuple[str, ...], label: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        role, separator, raw_path = value.partition("=")
        if not separator or role not in roles or role in result or not raw_path:
            raise MigrationPlanBuildError(f"{label} requires unique role=/path mappings")
        result[role] = Path(raw_path)
    if set(result) != set(roles):
        raise MigrationPlanBuildError(f"{label} role set is incomplete")
    return result


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def build_plan(
    *,
    inventory: dict[str, Any],
    freezes: dict[str, dict[str, Any]],
    backups: dict[str, dict[str, Any]],
    seeds: dict[str, dict[str, Any]],
    images: dict[str, dict[str, Any]],
    created_at: datetime,
    ttl_minutes: int,
) -> dict[str, Any]:
    if not 1 <= ttl_minutes <= 240:
        raise MigrationPlanBuildError("migration plan TTL must be between 1 and 240 minutes")
    created = created_at.astimezone(timezone.utc)
    source_rows = []
    freeze_rows = []
    seed_rows = []
    image_rows = []
    for role in SOURCE_ROLES:
        backup = backups[role]
        verify_backup_manifest(
            backup,
            campaign_id=inventory["campaign_id"],
            source_role=role,
            source_release_sha=str(backup["source_release_sha"]),
            target_release_sha=inventory["release_sha"],
            verify_files=False,
        )
        source_rows.append(
            {
                "source_role": role,
                "source_release_sha": backup["source_release_sha"],
                "manifest_sha256": _canonical_hash(backup),
                "postgres_system_id": backup["source_postgres_system_id"],
                "alembic_revision": backup["source_alembic_revision"],
                "database_fingerprint_sha256": backup["restore_drill"][
                    "database_fingerprint_sha256"
                ],
            }
        )
        freeze = freezes[role]
        rows = freeze.get("source_roles") if isinstance(freeze, dict) else None
        freeze_roles = [
            row.get("source_role") for row in rows or [] if isinstance(row, dict)
        ]
        if freeze_roles != [role]:
            raise MigrationPlanBuildError(f"source-freeze role identity differs for {role}")
        freeze_rows.append(
            {"evidence_sha256": _canonical_hash(freeze), "source_roles": freeze_roles}
        )
        seed = seeds[role]
        if seed.get("source_role") != role:
            raise MigrationPlanBuildError(f"seed manifest role identity differs for {role}")
        seed_rows.append(
            {
                "source_role": role,
                "manifest_sha256": _canonical_hash(seed),
                "object_prefix": seed["object_prefix"],
                "encryption": seed["encryption"],
                "recipient_fingerprint": seed["recipient_fingerprint"],
                "readback_evidence_sha256": seed["readback_evidence_sha256"],
            }
        )
    for role in TARGET_ROLES:
        document = images[role]
        result = verify_image_document(
            document,
            role=role.replace("_", "-"),
            campaign_id=inventory["campaign_id"],
            release_sha=inventory["release_sha"],
            role_compose_sha256=str(document.get("role_compose_sha256", "")),
            role_env_sha256=str(document.get("role_env_sha256", "")),
        )
        if result.get("status") != "verified":
            raise MigrationPlanBuildError(f"image inventory is not verified for {role}")
        image_rows.append(
            {
                "role": role,
                "document_sha256": _canonical_hash(document),
                "role_compose_sha256": document["role_compose_sha256"],
                "role_env_sha256": document["role_env_sha256"],
            }
        )
    return {
        "schema": "three-site-staging-migration-plan-v1",
        "campaign_id": inventory["campaign_id"],
        "release_sha": inventory["release_sha"],
        "deployment_id": inventory["deployment_id"],
        "provisioned_inventory_sha256": _canonical_hash(inventory),
        "created_at": created.isoformat(),
        "not_after": (created + timedelta(minutes=ttl_minutes)).isoformat(),
        "source_freeze": {
            "required": True,
            "evidence": freeze_rows,
            "redis_restore": False,
        },
        "source_backups": source_rows,
        "seed_bundles": seed_rows,
        "target_seed_map": [
            {"target_role": role, "source_role": source, "mode": mode}
            for role, (source, mode) in TARGET_SEED_MAP.items()
        ],
        "image_inventories": image_rows,
        "ordered_phases": list(ORDERED_PHASES),
        "rollback_policy": {
            "strategy": "forward-rollback-or-separate-restore",
            "alembic_downgrade": False,
            "routing_held_until_commit": True,
            "legacy_sources_retained": True,
            "cleanup_requires_explicit_finish": True,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--source-freeze", action="append", required=True)
    parser.add_argument("--source-backup", action="append", required=True)
    parser.add_argument("--seed-manifest", action="append", required=True)
    parser.add_argument("--image-inventory", action="append", required=True)
    parser.add_argument("--ttl-minutes", type=int, default=180)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise MigrationPlanBuildError("migration plan output already exists")
        inventory = load_inventory(args.inventory)
        verified = verify_approved_inventory(
            inventory,
            approval=load_inventory(args.inventory_approval),
            approval_policy=load_inventory(args.approval_policy),
            host_destructive=None,
        )
        if verified["inventory_stage"] != "provisioned":
            raise MigrationPlanBuildError("migration plan requires provisioned inventory")
        freeze_paths = _mapping(
            args.source_freeze, roles=SOURCE_ROLES, label="--source-freeze"
        )
        backup_paths = _mapping(
            args.source_backup, roles=SOURCE_ROLES, label="--source-backup"
        )
        seed_paths = _mapping(
            args.seed_manifest, roles=SOURCE_ROLES, label="--seed-manifest"
        )
        image_paths = _mapping(
            args.image_inventory, roles=TARGET_ROLES, label="--image-inventory"
        )
        plan = build_plan(
            inventory=inventory,
            freezes={role: load_inventory(path) for role, path in freeze_paths.items()},
            backups={role: load_inventory(path) for role, path in backup_paths.items()},
            seeds={role: load_inventory(path) for role, path in seed_paths.items()},
            images={role: load_inventory(path) for role, path in image_paths.items()},
            created_at=datetime.now(timezone.utc),
            ttl_minutes=args.ttl_minutes,
        )
        encoded = (json.dumps(plan, sort_keys=True, indent=2) + "\n").encode()
        _atomic_write(args.output, encoded, mode=0o600)
        result = {
            "status": "built",
            "campaign_id": plan["campaign_id"],
            "release_sha": plan["release_sha"],
            "plan": str(args.output),
            "plan_sha256": _canonical_hash(plan),
            "not_after": plan["not_after"],
        }
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
